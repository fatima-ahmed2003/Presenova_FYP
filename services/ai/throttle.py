"""
Gemini throttle + backoff (standalone module).

- Har Gemini call ke beech minimum gap rakhta hai (RPM limit se bachne ke liye)
- Agar call rate_limited type se fail ho to exponential backoff ke saath retry karta hai
- GeminiProvider.generate_structured ko runtime par wrap karta hai, isliye purani
  files edit karne ki zaroorat nahi.

Env vars (sab optional):
  GEMINI_MIN_INTERVAL_SEC   default 4.0  (0 = throttle band, paid tier ke liye)
  GEMINI_BACKOFF_RETRIES    default 3
  GEMINI_BACKOFF_BASE_SEC   default 5.0  (delay = base * 2^attempt + jitter)
"""

import functools
import logging
import os
import random
import threading
import time

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


MIN_INTERVAL = _env_float('GEMINI_MIN_INTERVAL_SEC', 4.0)
BACKOFF_RETRIES = int(_env_float('GEMINI_BACKOFF_RETRIES', 3))
BACKOFF_BASE = _env_float('GEMINI_BACKOFF_BASE_SEC', 5.0)

_slot_lock = threading.Lock()
_last_call = 0.0
_local = threading.local()
_installed = False

_RATE_MARKERS = (
    '429', 'rate_limit', 'rate limit', 'ratelimit',
    'quota', 'resource_exhausted', 'too many requests',
)


def _looks_rate_limited(value) -> bool:
    text = str(value or '').lower()
    return any(m in text for m in _RATE_MARKERS)


def _wait_for_slot() -> None:
    """Calls ke beech MIN_INTERVAL seconds ka gap enforce karo (thread-safe)."""
    global _last_call
    if MIN_INTERVAL <= 0:
        return
    with _slot_lock:
        gap = MIN_INTERVAL - (time.monotonic() - _last_call)
        if gap > 0:
            time.sleep(gap)
        _last_call = time.monotonic()


def install_gemini_throttle() -> None:
    """Idempotent: ek hi baar GeminiProvider.generate_structured ko wrap karta hai."""
    global _installed
    if _installed:
        return

    from services.ai.gemini_provider import GeminiProvider

    original = GeminiProvider.generate_structured

    @functools.wraps(original)
    def wrapped(self, *args, **kwargs):
        # Nested call (provider khud apne aap ko call kare) -> dobara wrap mat karo
        if getattr(_local, 'depth', 0) > 0:
            return original(self, *args, **kwargs)

        _local.depth = 1
        try:
            result = {}
            for attempt in range(BACKOFF_RETRIES + 1):
                _wait_for_slot()
                self.last_structured_error = None
                rate_limited = False
                try:
                    result = original(self, *args, **kwargs)
                    if not result:
                        err = getattr(self, 'last_structured_error', None) or {}
                        rate_limited = _looks_rate_limited(err.get('type', ''))
                except Exception as exc:
                    if attempt >= BACKOFF_RETRIES or not _looks_rate_limited(exc):
                        raise
                    rate_limited = True

                if not rate_limited or attempt >= BACKOFF_RETRIES:
                    return result

                delay = BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1.5)
                logger.warning(
                    "[throttle] Gemini rate-limited, retry %d/%d in %.1fs",
                    attempt + 1, BACKOFF_RETRIES, delay,
                )
                time.sleep(delay)
            return result
        finally:
            _local.depth = 0

    GeminiProvider.generate_structured = wrapped
    _installed = True
    logger.info(
        "[throttle] Gemini throttle installed (min_interval=%.1fs, retries=%d, base=%.1fs)",
        MIN_INTERVAL, BACKOFF_RETRIES, BACKOFF_BASE,
    )