"""Offline tests for deterministic Document Analyzer quality checks."""

import requests

from services.document_unit_checks import analyze_document_units


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_language_tool_marks_i_m_fine_as_grammar_issue(monkeypatch):
    calls = []

    def fake_post(url, *, data, timeout):
        calls.append((url, data, timeout))
        return _Response(
            {
                "matches": [
                    {
                        "offset": 2,
                        "length": 1,
                        "message": "Possible typo: use “I'm” or “am”.",
                        "replacements": [{"value": "am"}, {"value": "'m"}],
                    }
                ]
            }
        )

    monkeypatch.setattr("services.document_unit_checks.requests.post", fake_post)
    result = analyze_document_units(
        [
            {
                "unit_no": 1,
                "title": "Status",
                "text": "I m fine",
                "bullets": [],
                "min_font_size": 24,
            }
        ]
    )

    assert calls == [
        (
            "https://api.languagetool.org/v2/check",
            {"text": "I m fine", "language": "en-US"},
            8,
        )
    ]
    grammar_issue = next(
        issue for issue in result["issues"] if issue["rule"] == "language_tool"
    )
    assert grammar_issue["offset"] == 2
    assert grammar_issue["original"] == "m"
    assert grammar_issue["suggestions"] == ["am", "'m"]
    assert grammar_issue["category"] == "Correctness"


def test_rules_and_readability_are_generated_without_language_tool(monkeypatch):
    def no_matches(*_args, **_kwargs):
        return _Response({"matches": []})

    monkeypatch.setattr("services.document_unit_checks.requests.post", no_matches)
    long_sentence = (
        "Basically, stuff is gonna be reviewed by staff in order to improve things, "
        "yeah, and it is very important to do a lot of work, while the team evaluates "
        "the plan and explains the results to every participant in the local community "
        "before the next scheduled meeting."
    )
    text = "\n".join([long_sentence] * 4)
    result = analyze_document_units(
        [
            {
                "unit_no": 4,
                "title": "",
                "text": text,
                "bullets": [f"Bullet {number}" for number in range(7)],
                "min_font_size": 16,
            }
        ]
    )

    rules = {issue["rule"] for issue in result["issues"]}
    assert {
        "word_count",
        "bullet_count",
        "missing_title",
        "font_size",
        "long_sentence",
        "filler_phrase",
        "vague_word",
        "slang",
        "passive_voice",
        "readability",
    } <= rules
    assert result["language_tool"] == {
        "skipped": False,
        "units": [{"unit_no": 4, "status": "checked"}],
    }
    assert result["readability"][0]["flesch_reading_ease"] is not None
    assert all(
        issue["category"]
        in {
            "Correctness",
            "Conciseness",
            "Clarity",
            "Completeness",
            "Courtesy",
            "Concreteness",
            "Coherence",
        }
        for issue in result["issues"]
    )


def test_language_tool_timeout_is_flagged_while_local_checks_continue(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise requests.Timeout("offline test timeout")

    monkeypatch.setattr("services.document_unit_checks.requests.post", timeout)
    result = analyze_document_units(
        [
            {
                "unit_no": 2,
                "title": "",
                "text": "This has vague stuff and very little detail.",
                "bullets": [],
                "min_font_size": 14,
            }
        ]
    )

    assert result["language_tool"] == {
        "skipped": True,
        "units": [{"unit_no": 2, "status": "skipped", "reason": "timeout"}],
    }
    assert {issue["rule"] for issue in result["issues"]} >= {
        "missing_title",
        "vague_word",
        "filler_phrase",
        "font_size",
    }
