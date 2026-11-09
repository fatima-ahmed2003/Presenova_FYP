"""
Gemini Provider — AIProvider implementation for Google Gemini.

Supports both the current google.genai SDK and the legacy google.generativeai SDK.
Implements retry logic, model fallback chain, and timeout handling.

Improvements in this version:
- Real timeouts (the worker thread is no longer joined on timeout).
- Models that return 404/NOT_FOUND are removed from the candidate list for
  the rest of the process (no more repeated calls to retired models).
- Rate limits (429 / RESOURCE_EXHAUSTED) back off longer before retrying.
- JSON mode for structured output (fewer parse failures).
- If a model rejects thinking_budget=0 the call is retried without it.
- `last_error` is stored so callers/diagnostics can see WHY a call failed.
- `generate_structured()` now sets `last_structured_error` (type + detail)
  whenever it returns {}, so callers can surface the REAL reason instead
  of a silent/generic fallback.
"""

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Optional

from dotenv import load_dotenv

from services.ai.base_provider import AIProvider

load_dotenv()
logger = logging.getLogger(__name__)

# ── SDK Imports (graceful degradation) ──────────────────────────────────────
try:
    import google.genai as current_genai
except ImportError:
    current_genai = None

try:
    # Suppress the FutureWarning emitted on import of the legacy SDK.
    import warnings
    warnings.filterwarnings(
        "ignore",
        category=FutureWarning,
        message=r"(?s).*google\.generativeai.*",
    )
    import google.generativeai as legacy_genai
except ImportError:
    legacy_genai = None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _is_not_found(message: str) -> bool:
    low = message.lower()
    return '404' in message or 'not_found' in low or 'is not found' in low or 'no longer available' in low


def _is_rate_limited(message: str) -> bool:
    low = message.lower()
    return '429' in message or 'resource_exhausted' in low or 'quota' in low or 'rate limit' in low


def _clean_json_response(raw: str) -> str:
    """Remove markdown fences and return the first valid JSON value."""
    if not isinstance(raw, str):
        return ''
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*```$', '', text).strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in '{[':
            continue
        try:
            _, end = decoder.raw_decode(text[index:])
            return text[index:index + end].strip()
        except json.JSONDecodeError:
            continue
    return text


class GeminiProvider(AIProvider):
    """Gemini AI provider with retry, fallback, and timeout support."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        fallback_models: Optional[list[str]] = None,
        timeout_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
    ):
        self.api_key = api_key or os.getenv('GEMINI_API_KEY', '').strip()
        self.model_name = model or os.getenv('GEMINI_MODEL', 'gemini-2.0-flash').strip()
        self.fallback_models = fallback_models if fallback_models is not None else [
            m.strip() for m in os.getenv('GEMINI_FALLBACK_MODELS', 'gemini-1.5-flash,gemini-1.5-pro').split(',') if m.strip()
        ]
        self.timeout_seconds = max(
            1.0,
            timeout_seconds if timeout_seconds is not None else _env_float('GEMINI_TIMEOUT_SECONDS', 45.0),
        )
        self.max_retries = max(
            0,
            max_retries if max_retries is not None else _env_int('GEMINI_MAX_RETRIES', 2),
        )
        seen = set()
        deduped = []
        for m in [self.model_name, *self.fallback_models]:
            m_clean = m.strip() if m else ""
            if m_clean and m_clean not in seen:
                seen.add(m_clean)
                deduped.append(m_clean)
        self.model_candidates = deduped

        self._dead_models: set[str] = set()
        self.last_error: Optional[Exception] = None
        self.last_structured_error: Optional[dict] = None

        self._new_client = None
        self._legacy_configured = False
        self._available = False
        self._init_clients()

    def _init_clients(self) -> None:
        """Initialize Gemini SDK clients (current and/or legacy)."""
        offline = _env_bool('FORCE_OFFLINE_MODE', False)
        if offline or not self.api_key:
            logger.info("[gemini_provider] Gemini disabled (offline=%s, api_key_set=%s).", offline, bool(self.api_key))
            return

        if current_genai is not None:
            try:
                self._new_client = current_genai.Client(api_key=self.api_key)
                logger.info("[gemini_provider] Current Gemini SDK initialized. Models: %s", self.model_candidates)
            except Exception as exc:
                logger.warning("[gemini_provider] Current SDK failed: %s", exc)

        if self._new_client is None and legacy_genai is not None:
            try:
                legacy_genai.configure(api_key=self.api_key, transport='rest')
                self._legacy_configured = True
                logger.info("[gemini_provider] Legacy Gemini SDK initialized.")
            except Exception as exc:
                logger.warning("[gemini_provider] Legacy SDK failed: %s", exc)

        self._available = bool(
            self.api_key and not offline and (self._new_client is not None or self._legacy_configured)
        )
        if not self._available:
            logger.info("[gemini_provider] Gemini unavailable; local fallback active.")

    # ── AIProvider interface ──────────────────────────────────────────────────

    def is_available(self) -> bool:
        return self._available

    def get_model_info(self) -> dict:
        return {
            "provider": "gemini",
            "model_name": self.model_name,
            "fallback_models": self.fallback_models,
            "dead_models": sorted(self._dead_models),
            "available": self._available,
            "sdk": "current" if self._new_client else ("legacy" if self._legacy_configured else "none"),
        }

    def count_tokens(self, text: str) -> int:
        """Rough token estimate: ~4 chars per token."""
        return len(text) // 4 if text else 0

    def get_capabilities(self) -> dict:
        return {
            "supports_structured": True,
            "supports_streaming": False,
            "supports_system_instruction": True,
            "max_context_tokens": 128000,
            "max_output_tokens": 65536,
        }

    def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
        system_instruction: Optional[str] = None,
        json_mode: bool = False,
    ) -> str:
        """Generate text with retry and model fallback."""
        self.last_error = None
        if not self._available:
            logger.warning("[gemini_provider] Provider not available; returning empty.")
            return ''

        for attempt in range(self.max_retries + 1):
            models = [m for m in self.model_candidates if m not in self._dead_models]
            if not models:
                logger.error("[gemini_provider] No usable models left (all returned NOT_FOUND). "
                             "Set GEMINI_MODEL in .env to a model your key can use.")
                break

            rate_limited = False
            for model_name in models:
                try:
                    result = self._call_model(
                        model_name=model_name,
                        prompt=prompt,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                        system_instruction=system_instruction,
                        json_mode=json_mode,
                    )
                    if result:
                        return result
                    logger.warning("[gemini_provider] %s returned an empty response (attempt %d).",
                                   model_name, attempt + 1)
                except Exception as exc:
                    self.last_error = exc
                    message = str(exc)
                    logger.warning(
                        "[gemini_provider] Attempt %d with %s failed: %s",
                        attempt + 1, model_name, message[:300],
                    )
                    if _is_not_found(message):
                        self._dead_models.add(model_name)
                        logger.error("[gemini_provider] Model %r not found; removing it from candidates.", model_name)
                    elif _is_rate_limited(message):
                        rate_limited = True

            if attempt < self.max_retries:
                wait = 5.0 * (attempt + 1) if rate_limited else 2.0 ** attempt
                logger.info("[gemini_provider] Retrying in %.1fs (rate_limited=%s)...", wait, rate_limited)
                time.sleep(wait)

        logger.error("[gemini_provider] All attempts exhausted. Last error: %s", self.last_error)
        return ''

    def generate_structured(
        self,
        prompt: str,
        temperature: float = 0.2,
        max_output_tokens: int = 8192,
        system_instruction: Optional[str] = None,
    ) -> dict | list:
        """Generate structured JSON output. Returns {} on any failure.

        Check `self.last_structured_error` right after a `{}` result to see
        WHY it failed — {'type': ..., 'detail': ...} where type is one of:
        rate_limited / model_not_found / timeout / provider_unavailable /
        provider_error / empty_response / empty_json / bad_json.
        """
        self.last_structured_error = None
        raw = self.generate(
            prompt=prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            system_instruction=system_instruction,
            json_mode=True,
        )
        if not raw:
            self.last_structured_error = self._classify_last_error()
            logger.error(
                "[gemini_provider] generate_structured FAILED (%s): %s",
                self.last_structured_error['type'], self.last_structured_error['detail'],
            )
            return {}

        cleaned = _clean_json_response(raw)
        if not cleaned:
            self.last_structured_error = {'type': 'empty_json', 'detail': raw[:300]}
            logger.error("[gemini_provider] generate_structured FAILED (empty_json): no JSON found in %r", raw[:300])
            return {}

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            self.last_structured_error = {'type': 'bad_json', 'detail': str(exc)}
            logger.error("[gemini_provider] generate_structured FAILED (bad_json): %s | response head: %r", exc, raw[:300])
            return {}

    def _classify_last_error(self) -> dict:
        """Turn self.last_error into a {'type', 'detail'} dict the caller can log/report."""
        if self.last_error is None:
            if not self._available:
                return {'type': 'provider_unavailable', 'detail': 'Gemini not configured (no API key, or FORCE_OFFLINE_MODE).'}
            return {'type': 'empty_response', 'detail': 'Model returned no text after all retries/models were exhausted.'}
        message = str(self.last_error)
        if _is_rate_limited(message):
            return {'type': 'rate_limited', 'detail': message[:300]}
        if _is_not_found(message):
            return {'type': 'model_not_found', 'detail': message[:300]}
        if isinstance(self.last_error, TimeoutError):
            return {'type': 'timeout', 'detail': message[:300]}
        return {'type': 'provider_error', 'detail': message[:300]}

    # ── Internal model call ───────────────────────────────────────────────────

    def _run_with_timeout(self, fn, model_name: str):
        """Run fn in a worker thread; on timeout return control immediately."""
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(fn)
        try:
            return future.result(timeout=self.timeout_seconds)
        except FutureTimeout:
            raise TimeoutError(f"Gemini {model_name} timed out after {self.timeout_seconds}s.")
        finally:
            pool.shutdown(wait=False)

    def _call_model(
        self,
        model_name: str,
        prompt: str,
        temperature: float,
        max_output_tokens: int,
        system_instruction: Optional[str] = None,
        json_mode: bool = False,
    ) -> str:
        """Call a specific Gemini model with timeout."""
        if self._new_client:
            return self._call_current_sdk(
                model_name, prompt, temperature, max_output_tokens, system_instruction, json_mode
            )
        elif self._legacy_configured:
            return self._call_legacy_sdk(
                model_name, prompt, temperature, max_output_tokens, system_instruction, json_mode
            )
        raise RuntimeError("No Gemini SDK available.")

    def _call_current_sdk(
        self,
        model_name: str,
        prompt: str,
        temperature: float,
        max_output_tokens: int,
        system_instruction: Optional[str] = None,
        json_mode: bool = False,
    ) -> str:
        """Use the current google.genai SDK."""
        from google.genai import types

        def build_config(thinking_off: bool):
            kwargs = {
                "temperature": temperature,
                "max_output_tokens": max(max_output_tokens, 512),
            }
            if system_instruction:
                kwargs["system_instruction"] = system_instruction
            if json_mode:
                kwargs["response_mime_type"] = "application/json"
            if thinking_off and "lite" not in model_name.lower():
                kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            return types.GenerateContentConfig(**kwargs)

        actual_model = f"models/{model_name}" if not model_name.startswith("models/") else model_name

        def run(thinking_off: bool):
            config = build_config(thinking_off)
            response = self._run_with_timeout(
                lambda: self._new_client.models.generate_content(
                    model=actual_model, contents=prompt, config=config
                ),
                model_name,
            )
            text = getattr(response, 'text', None)
            return text if text else ''

        try:
            return run(thinking_off=True)
        except Exception as exc:
            # Some models do not allow thinking_budget=0; retry without it.
            if 'thinking' in str(exc).lower():
                logger.info("[gemini_provider] %s rejected thinking_budget=0; retrying without it.", model_name)
                return run(thinking_off=False)
            raise

    def _call_legacy_sdk(
        self,
        model_name: str,
        prompt: str,
        temperature: float,
        max_output_tokens: int,
        system_instruction: Optional[str] = None,
        json_mode: bool = False,
    ) -> str:
        """Use the legacy google.generativeai SDK."""
        generation_config = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        if json_mode:
            generation_config["response_mime_type"] = "application/json"
        kwargs = {}
        if system_instruction:
            kwargs["system_instruction"] = system_instruction

        model = legacy_genai.GenerativeModel(
            model_name=model_name,
            generation_config=generation_config,
            **kwargs,
        )
        response = self._run_with_timeout(lambda: model.generate_content(prompt), model_name)
        try:
            return response.text or ''
        except Exception:
            return ''