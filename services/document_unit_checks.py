"""Deterministic quality checks for extracted document units."""

from __future__ import annotations

import re
from typing import Literal, Mapping, Sequence, TypedDict

import requests
import textstat


SEVEN_CS = Literal[
    "Correctness",
    "Conciseness",
    "Clarity",
    "Completeness",
    "Courtesy",
    "Concreteness",
    "Coherence",
]

LANGUAGETOOL_URL = "https://api.languagetool.org/v2/check"
LANGUAGETOOL_TIMEOUT_SECONDS = 8
READABILITY_MIN_FLESCH_SCORE = 50
_WORD = re.compile(r"\b[\w]+(?:['’][\w]+)*\b", re.UNICODE)
_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")
_RULE_PATTERNS: tuple[tuple[str, re.Pattern[str], SEVEN_CS, str], ...] = (
    (
        "filler_phrase",
        re.compile(r"\b(?:basically|very)\b|\bin order to\b|\ba lot of\b", re.I),
        "Conciseness",
        "Remove the filler phrase or replace it with a more direct expression.",
    ),
    (
        "vague_word",
        re.compile(r"\b(?:things|stuff)\b", re.I),
        "Concreteness",
        "Replace the vague word with a specific noun.",
    ),
    (
        "slang",
        re.compile(r"\b(?:gonna|yeah)\b", re.I),
        "Courtesy",
        "Use a more formal expression appropriate for the audience.",
    ),
)
_PASSIVE = re.compile(
    r"\b(?:am|is|are|was|were|be|been|being)\s+"
    r"(?:\w+(?:ed|en)|made|built|done|given|known|seen|shown|written|taken|"
    r"told|sent|found|held|left|kept|put|read|chosen|drawn|driven|eaten|"
    r"forgotten|grown|hidden|paid|sold|spent|taught|won)\b",
    re.I,
)


class QualityIssue(TypedDict):
    unit_no: int
    rule: str
    issue: str
    offset: int
    original: str
    suggestions: list[str]
    category: SEVEN_CS


def _issue(
    unit_no: int,
    rule: str,
    message: str,
    offset: int,
    original: str,
    suggestions: list[str],
    category: SEVEN_CS,
) -> QualityIssue:
    return {
        "unit_no": unit_no,
        "rule": rule,
        "issue": message,
        "offset": offset,
        "original": original,
        "suggestions": suggestions,
        "category": category,
    }


def _language_tool_matches(
    text: str,
) -> tuple[list[Mapping[str, object]], str | None]:
    try:
        response = requests.post(
            LANGUAGETOOL_URL,
            data={"text": text, "language": "en-US"},
            timeout=LANGUAGETOOL_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.Timeout:
        return [], "timeout"
    except requests.RequestException:
        return [], "request_error"
    except ValueError:
        return [], "invalid_response"

    matches = payload.get("matches") if isinstance(payload, dict) else None
    if not isinstance(matches, list) or not all(
        isinstance(match, dict) for match in matches
    ):
        return [], "invalid_response"
    for match in matches:
        offset = match.get("offset")
        length = match.get("length")
        replacements = match.get("replacements", [])
        if (
            type(offset) is not int
            or type(length) is not int
            or offset < 0
            or length < 0
            or offset + length > len(text)
            or not isinstance(match.get("message"), str)
            or not isinstance(replacements, list)
            or any(
                not isinstance(replacement, dict)
                or not isinstance(replacement.get("value", ""), str)
                for replacement in replacements
            )
        ):
            return [], "invalid_response"
    return matches, None


def _add_language_tool_issues(
    unit_no: int, text: str, matches: Sequence[Mapping[str, object]]
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for match in matches:
        offset = match.get("offset")
        length = match.get("length")
        message = match.get("message")
        replacements = match.get("replacements", [])
        if (
            not isinstance(offset, int)
            or not isinstance(length, int)
            or offset < 0
            or length < 0
            or offset + length > len(text)
            or not isinstance(message, str)
            or not isinstance(replacements, list)
        ):
            continue
        suggestions = [
            replacement["value"]
            for replacement in replacements[:3]
            if isinstance(replacement, dict)
            and isinstance(replacement.get("value"), str)
        ]
        issues.append(
            _issue(
                unit_no,
                "language_tool",
                message,
                offset,
                text[offset : offset + length],
                suggestions,
                "Correctness",
            )
        )
    return issues


def _rule_issues(
    unit_no: int, unit: Mapping[str, object], text: str
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    words = _WORD.findall(text)
    if len(words) > 60:
        issues.append(
            _issue(
                unit_no,
                "word_count",
                f"Unit contains {len(words)} words; the limit is 60.",
                0,
                text,
                ["Shorten or split this unit."],
                "Conciseness",
            )
        )

    bullets = unit.get("bullets", [])
    if isinstance(bullets, list) and len(bullets) > 6:
        first_bullet = str(bullets[0])
        issues.append(
            _issue(
                unit_no,
                "bullet_count",
                f"Unit contains {len(bullets)} bullets; the limit is 6.",
                max(0, text.find(first_bullet)),
                first_bullet,
                ["Reduce the number of bullets to six or fewer."],
                "Conciseness",
            )
        )

    title = unit.get("title")
    if not isinstance(title, str) or not title.strip():
        issues.append(
            _issue(
                unit_no,
                "missing_title",
                "Unit has no title.",
                0,
                "",
                ["Add a descriptive title."],
                "Completeness",
            )
        )

    font_size = unit.get("min_font_size")
    if isinstance(font_size, (int, float)) and not isinstance(font_size, bool):
        if font_size < 18:
            issues.append(
                _issue(
                    unit_no,
                    "font_size",
                    f"Minimum font size is {font_size:g} pt; the minimum is 18 pt.",
                    0,
                    text,
                    ["Increase the smallest font size to at least 18 pt."],
                    "Clarity",
                )
            )

    for sentence_match in _SENTENCE.finditer(text):
        sentence = sentence_match.group()
        leading_space = len(sentence) - len(sentence.lstrip())
        sentence = sentence.strip()
        start = sentence_match.start() + leading_space
        if len(_WORD.findall(sentence)) > 25:
            issues.append(
                _issue(
                    unit_no,
                    "long_sentence",
                    "Sentence contains more than 25 words.",
                    start,
                    sentence,
                    ["Split the sentence into shorter sentences."],
                    "Clarity",
                )
            )

    for rule, pattern, category, suggestion in _RULE_PATTERNS:
        for match in pattern.finditer(text):
            issues.append(
                _issue(
                    unit_no,
                    rule,
                    f"Consider revising '{match.group()}'.",
                    match.start(),
                    match.group(),
                    [suggestion],
                    category,
                )
            )

    for match in _PASSIVE.finditer(text):
        issues.append(
            _issue(
                unit_no,
                "passive_voice",
                "Possible passive voice.",
                match.start(),
                match.group(),
                ["Prefer an active construction when the actor is important."],
                "Clarity",
            )
        )
    return issues


def analyze_document_units(
    units: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Run LanguageTool and deterministic rules without any LLM calls.

    LanguageTool failures are recorded per unit; local rules and readability
    checks continue to run for that unit.
    """
    issues: list[QualityIssue] = []
    language_tool_units: list[dict[str, object]] = []
    readability: list[dict[str, object]] = []

    for index, unit in enumerate(units, start=1):
        unit_no_value = unit.get("unit_no", index)
        unit_no = unit_no_value if isinstance(unit_no_value, int) else index
        text_value = unit.get("text", "")
        text = text_value if isinstance(text_value, str) else ""

        if text.strip():
            matches, error = _language_tool_matches(text)
            if error is None:
                language_tool_units.append({"unit_no": unit_no, "status": "checked"})
                issues.extend(_add_language_tool_issues(unit_no, text, matches))
            else:
                language_tool_units.append(
                    {"unit_no": unit_no, "status": "skipped", "reason": error}
                )
            issues.extend(_rule_issues(unit_no, unit, text))

            word_count = len(_WORD.findall(text))
            ease = float(textstat.flesch_reading_ease(text))
            grade = float(textstat.flesch_kincaid_grade(text))
            readability.append(
                {
                    "unit_no": unit_no,
                    "flesch_reading_ease": round(ease, 2),
                    "flesch_kincaid_grade": round(grade, 2),
                }
            )
            if word_count >= 10 and ease < READABILITY_MIN_FLESCH_SCORE:
                issues.append(
                    _issue(
                        unit_no,
                        "readability",
                        f"Flesch Reading Ease is {ease:.1f}; text may be difficult to read.",
                        0,
                        text,
                        ["Use shorter sentences and simpler words."],
                        "Clarity",
                    )
                )
        else:
            language_tool_units.append(
                {"unit_no": unit_no, "status": "skipped", "reason": "empty_text"}
            )
            readability.append(
                {
                    "unit_no": unit_no,
                    "flesch_reading_ease": None,
                    "flesch_kincaid_grade": None,
                }
            )
            issues.extend(_rule_issues(unit_no, unit, text))

    return {
        "issues": issues,
        "language_tool": {
            "skipped": any(unit["status"] == "skipped" for unit in language_tool_units),
            "units": language_tool_units,
        },
        "readability": readability,
    }
