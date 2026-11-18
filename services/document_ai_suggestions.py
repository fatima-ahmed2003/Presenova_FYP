"""Groq-first AI suggestions and coherence scoring for document units."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import threading
from collections import OrderedDict
from typing import Mapping, Sequence

logger = logging.getLogger(__name__)

_SYSTEM_INSTRUCTION = (
    "Review the supplied document units and deterministic findings. Return only "
    "JSON matching the requested schema. Do not invent source text or findings. "
    "Keep every original_line verbatim from its unit. Use severity low, medium, "
    "or high. Score coherence from 0 to 100 and briefly explain the score."
)
_MAX_PROMPT_CHARS = 80_000
_CACHE_LIMIT = 128
_CACHE: OrderedDict[str, dict[str, object]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_SEVERITIES = {"low", "medium", "high"}
_WORD_SPACE = re.compile(r"\s+")


def sha256_file(file_path: str) -> str:
    """Return a content hash without loading the complete upload into memory."""
    digest = hashlib.sha256()
    with open(file_path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_text(value: str) -> str:
    return _WORD_SPACE.sub(" ", value).strip().casefold()


def _build_prompt(
    units: Sequence[Mapping[str, object]], issues: Sequence[Mapping[str, object]]
) -> str:
    payload = {
        "units": [
            {
                "unit_no": unit.get("unit_no"),
                "type": unit.get("type"),
                "title": unit.get("title", ""),
                "text": unit.get("text", ""),
            }
            for unit in units
        ],
        "step_2_issues": list(issues),
        "required_json_schema": {
            "suggestions": [
                {
                    "unit_no": "integer from a supplied unit",
                    "original_line": "verbatim line from that unit",
                    "problem": "short explanation tied to a supplied issue",
                    "fix": "specific replacement or action",
                    "severity": "low | medium | high",
                }
            ],
            "coherence": {
                "score": "integer from 0 to 100",
                "reason": "brief explanation",
            },
        },
        "instructions": (
            "Create actionable suggestions for the supplied issues only. "
            "Return one JSON object with exactly the keys suggestions and "
            "coherence. Each suggestion must have exactly the five schema "
            "fields; do not wrap JSON in markdown."
        ),
    }
    return json.dumps(payload, ensure_ascii=False)


def _validate_response(
    response: object, units: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    if not isinstance(response, dict) or set(response) != {"suggestions", "coherence"}:
        raise ValueError("AI response must contain only suggestions and coherence.")

    units_by_no: dict[int, str] = {}
    for index, unit in enumerate(units, start=1):
        unit_no = unit.get("unit_no", index)
        if type(unit_no) is not int:
            continue
        title = unit.get("title", "")
        text = unit.get("text", "")
        source = "\n".join(
            value for value in (title, text) if isinstance(value, str)
        )
        units_by_no[unit_no] = _normalized_text(source)

    raw_suggestions = response["suggestions"]
    if not isinstance(raw_suggestions, list):
        raise ValueError("AI suggestions must be a list.")

    suggestions: list[dict[str, object]] = []
    required_suggestion_fields = {
        "unit_no",
        "original_line",
        "problem",
        "fix",
        "severity",
    }
    for item in raw_suggestions:
        if not isinstance(item, dict) or set(item) != required_suggestion_fields:
            raise ValueError("Each AI suggestion must match the strict schema.")
        unit_no = item["unit_no"]
        original_line = item["original_line"]
        problem = item["problem"]
        fix = item["fix"]
        severity = item["severity"]
        if (
            type(unit_no) is not int
            or unit_no not in units_by_no
            or not isinstance(original_line, str)
            or not original_line.strip()
            or _normalized_text(original_line) not in units_by_no[unit_no]
            or not isinstance(problem, str)
            or not problem.strip()
            or not isinstance(fix, str)
            or not fix.strip()
            or severity not in _SEVERITIES
        ):
            raise ValueError("AI suggestion failed schema or source validation.")
        suggestions.append(
            {
                "unit_no": unit_no,
                "original_line": original_line.strip(),
                "problem": problem.strip(),
                "fix": fix.strip(),
                "severity": severity,
            }
        )

    coherence = response["coherence"]
    if (
        not isinstance(coherence, dict)
        or set(coherence) != {"score", "reason"}
        or type(coherence.get("score")) is not int
        or not 0 <= coherence["score"] <= 100
        or not isinstance(coherence.get("reason"), str)
        or not coherence["reason"].strip()
        or len(coherence["reason"]) > 600
    ):
        raise ValueError("AI coherence result failed strict schema validation.")

    return {
        "suggestions": suggestions,
        "coherence": {
            "score": coherence["score"],
            "reason": coherence["reason"].strip(),
        },
    }


def _call_groq(prompt: str) -> object:
    key = os.getenv("GROQ_API_KEY", "").strip()
    model = os.getenv("GROQ_CHAT_MODEL", "").strip()
    if not key or not model:
        raise RuntimeError("Groq API key or chat model is not configured.")

    from groq import Groq

    client = Groq(api_key=key, timeout=12.0)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=4096,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Groq returned an empty JSON response.")
    return json.loads(content)


def _call_gemini(prompt: str) -> object:
    from services.ai.gemini_provider import GeminiProvider

    provider = GeminiProvider(
        timeout_seconds=12.0,
        max_retries=0,
        fallback_models=[],
    )
    if not provider.is_available():
        raise RuntimeError("Gemini fallback is not configured.")
    response = provider.generate_structured(
        prompt=prompt,
        temperature=0,
        max_output_tokens=4096,
        system_instruction=_SYSTEM_INSTRUCTION,
    )
    if not response:
        error = provider.last_structured_error or {}
        raise RuntimeError(
            f"Gemini returned no structured response ({error.get('type', 'unknown')})."
        )
    return response


def _unavailable_result() -> dict[str, object]:
    return {
        "ai_suggestions": [],
        "coherence": None,
        "ai_suggestions_unavailable": True,
        "ai_suggestions_provider": None,
        "ai_suggestions_cached": False,
    }


def analyze_document_with_ai(
    units: Sequence[Mapping[str, object]],
    quality_checks: Mapping[str, object],
    file_hash: str,
) -> dict[str, object]:
    """Generate strict AI output, falling back to rule-only results on failure."""
    if not re.fullmatch(r"[a-f0-9]{64}", file_hash):
        raise ValueError("file_hash must be a lowercase SHA-256 digest.")

    with _CACHE_LOCK:
        cached = _CACHE.get(file_hash)
        if cached is not None:
            _CACHE.move_to_end(file_hash)
            result = copy.deepcopy(cached)
            result["ai_suggestions_cached"] = True
            return result

    raw_issues = quality_checks.get("issues", [])
    if not isinstance(raw_issues, list) or not all(
        isinstance(issue, Mapping) for issue in raw_issues
    ):
        raise ValueError("quality_checks.issues must be a list of issue objects.")

    prompt = _build_prompt(units, raw_issues)
    if len(prompt) > _MAX_PROMPT_CHARS:
        logger.warning("AI document review skipped because its input exceeds the size limit.")
        return _unavailable_result()

    providers = (("groq", _call_groq), ("gemini", _call_gemini))
    for provider_name, call_provider in providers:
        try:
            validated = _validate_response(call_provider(prompt), units)
        except Exception as exc:
            logger.warning("Document AI review via %s failed: %s", provider_name, exc)
            continue

        result = {
            "ai_suggestions": validated["suggestions"],
            "coherence": validated["coherence"],
            "ai_suggestions_unavailable": False,
            "ai_suggestions_provider": provider_name,
            "ai_suggestions_cached": False,
        }
        with _CACHE_LOCK:
            _CACHE[file_hash] = copy.deepcopy(result)
            _CACHE.move_to_end(file_hash)
            while len(_CACHE) > _CACHE_LIMIT:
                _CACHE.popitem(last=False)
        return result

    return _unavailable_result()


def apply_ai_coherence(
    analysis: dict[str, object], coherence: object
) -> None:
    """Set the 7th C and include it in the overall score only when validated."""
    if not isinstance(coherence, dict):
        return
    score = coherence.get("score")
    reason = coherence.get("reason")
    if (
        type(score) is not int
        or not 0 <= score <= 100
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        return

    scores = analysis.get("seven_cs_scores")
    if not isinstance(scores, dict):
        raise ValueError("Analysis report is missing its 7Cs score map.")
    scores["Coherent"] = score

    score_values = [
        value
        for dimension, value in scores.items()
        if dimension != "Coherent" and type(value) is int
    ]
    score_values.append(score)
    analysis["overall_score"] = round(sum(score_values) / len(score_values))

    evaluation = analysis.get("seven_cs_evaluation")
    if isinstance(evaluation, dict):
        evaluation["Coherent"] = {
            "status": "AI evaluated",
            "feedback": reason.strip(),
        }

    evidence = analysis.get("seven_cs_evidence")
    if isinstance(evidence, dict):
        evidence["Coherent"] = {
            "issue_count": 0,
            "issues": [],
            "status": "ai_scored",
            "score": score,
            "reason": reason.strip(),
        }
