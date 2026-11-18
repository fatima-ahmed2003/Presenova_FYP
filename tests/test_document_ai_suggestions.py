"""Offline tests for strict, cached Document Analyzer AI suggestions."""

import hashlib
import json

from services.document_ai_suggestions import (
    _CACHE,
    _call_groq,
    analyze_document_with_ai,
    apply_ai_coherence,
)


UNITS = [
    {
        "unit_no": 1,
        "type": "slide",
        "title": "Results",
        "text": "The results are very good.",
    }
]
CHECKS = {
    "issues": [
        {
            "unit_no": 1,
            "rule": "filler_phrase",
            "issue": "Consider revising 'very'.",
            "offset": 16,
            "original": "very",
            "suggestions": ["Remove the filler phrase."],
            "category": "Conciseness",
        }
    ]
}
VALID_RESPONSE = {
    "suggestions": [
        {
            "unit_no": 1,
            "original_line": "The results are very good.",
            "problem": "The wording is vague and includes a filler.",
            "fix": "The results are positive.",
            "severity": "low",
        }
    ],
    "coherence": {
        "score": 82,
        "reason": "The sentence presents a clear result but lacks supporting context.",
    },
}


def _file_hash(value="same-file"):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_groq_uses_temperature_zero_and_json_mode(monkeypatch):
    class _Message:
        content = json.dumps(VALID_RESPONSE)

    class _Completion:
        choices = [type("Choice", (), {"message": _Message()})()]

    class _Chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return _Completion()

    class _Groq:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = _Chat()

    captured = {}
    monkeypatch.setenv("GROQ_API_KEY", "local-test-key")
    monkeypatch.setenv("GROQ_CHAT_MODEL", "local-test-model")
    monkeypatch.setitem(
        __import__("sys").modules,
        "groq",
        type("GroqModule", (), {"Groq": _Groq}),
    )

    assert _call_groq("local prompt") == VALID_RESPONSE
    assert captured["temperature"] == 0
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["model"] == "local-test-model"


def test_gemini_fallback_and_hash_cache(monkeypatch):
    _CACHE.clear()
    calls = []

    def fail_groq(_prompt):
        calls.append("groq")
        raise RuntimeError("offline")

    def succeed_gemini(_prompt):
        calls.append("gemini")
        return VALID_RESPONSE

    monkeypatch.setattr(
        "services.document_ai_suggestions._call_groq", fail_groq
    )
    monkeypatch.setattr(
        "services.document_ai_suggestions._call_gemini", succeed_gemini
    )

    first = analyze_document_with_ai(UNITS, CHECKS, _file_hash())
    second = analyze_document_with_ai(UNITS, CHECKS, _file_hash())

    assert calls == ["groq", "gemini"]
    assert first["ai_suggestions_unavailable"] is False
    assert first["ai_suggestions_provider"] == "gemini"
    assert second["ai_suggestions_cached"] is True
    assert second["coherence"] == VALID_RESPONSE["coherence"]


def test_invalid_or_unavailable_ai_keeps_coherence_unscored(monkeypatch):
    _CACHE.clear()
    monkeypatch.setattr(
        "services.document_ai_suggestions._call_groq",
        lambda _prompt: {
            **VALID_RESPONSE,
            "suggestions": [
                {
                    **VALID_RESPONSE["suggestions"][0],
                    "original_line": "Invented source line.",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "services.document_ai_suggestions._call_gemini",
        lambda _prompt: {},
    )

    result = analyze_document_with_ai(UNITS, CHECKS, _file_hash("bad-ai"))
    assert result["ai_suggestions_unavailable"] is True
    assert result["ai_suggestions"] == []
    assert result["coherence"] is None

    analysis = {
        "overall_score": 90,
        "seven_cs_scores": {"Clear": 90, "Coherent": None},
    }
    apply_ai_coherence(analysis, result["coherence"])
    assert analysis["seven_cs_scores"]["Coherent"] is None
    assert analysis["overall_score"] == 90


def test_valid_coherence_updates_seventh_c_and_overall_score():
    analysis = {
        "overall_score": 80,
        "seven_cs_scores": {
            "Clear": 80,
            "Concise": 82,
            "Correct": 78,
            "Complete": 80,
            "Courteous": 84,
            "Concrete": 76,
            "Coherent": None,
        },
        "seven_cs_evaluation": {"Coherent": {"status": "Not scored", "feedback": ""}},
        "seven_cs_evidence": {"Coherent": {"issue_count": 0, "issues": []}},
    }

    apply_ai_coherence(analysis, VALID_RESPONSE["coherence"])

    assert analysis["seven_cs_scores"]["Coherent"] == 82
    assert analysis["overall_score"] == 80
    assert analysis["seven_cs_evaluation"]["Coherent"]["feedback"] == (
        VALID_RESPONSE["coherence"]["reason"]
    )
