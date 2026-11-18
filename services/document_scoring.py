"""Deterministic, evidence-backed 7Cs scores for extracted document units."""

from __future__ import annotations

import re
from typing import Mapping, Sequence


_CATEGORY_TO_DIMENSION = {
    "Clarity": "Clear",
    "Conciseness": "Concise",
    "Concreteness": "Concrete",
    "Correctness": "Correct",
    "Completeness": "Complete",
    "Courtesy": "Courteous",
}
_PENALTIES = {
    "language_tool": 6,
    "word_count": 8,
    "bullet_count": 6,
    "missing_title": 10,
    "font_size": 3,
    "long_sentence": 4,
    "filler_phrase": 2,
    "vague_word": 5,
    "slang": 4,
    "passive_voice": 3,
    "readability": 6,
    "missing_agenda": 10,
    "missing_conclusion": 10,
    "missing_closing_material": 8,
}
_DOCUMENT_CHECKS = (
    (
        "agenda",
        re.compile(r"\b(?:agenda|table of contents|contents|outline)\b", re.I),
    ),
    (
        "conclusion",
        re.compile(r"\b(?:conclusion|summary|key takeaways|final remarks)\b", re.I),
    ),
    (
        "thank_you",
        re.compile(r"\b(?:thank you|thanks for|questions and answers)\b", re.I),
    ),
    (
        "references",
        re.compile(r"\b(?:references|bibliography|works cited|sources)\b", re.I),
    ),
)
_SEVEN_CS = ("Clear", "Concise", "Concrete", "Correct", "Coherent", "Complete", "Courteous")
_SCORED_DIMENSIONS = tuple(
    dimension for dimension in _SEVEN_CS if dimension != "Coherent"
)


def _unit_id(unit: Mapping[str, object], index: int) -> int:
    value = unit.get("unit_no", index)
    return value if isinstance(value, int) else index


def _document_completeness(
    units: Sequence[Mapping[str, object]],
) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    checks: dict[str, dict[str, object]] = {}
    searchable_units: list[tuple[int, str]] = []
    titled_units: list[int] = []
    for index, unit in enumerate(units, start=1):
        unit_no = _unit_id(unit, index)
        title = unit.get("title")
        text = unit.get("text")
        if isinstance(title, str) and title.strip():
            titled_units.append(unit_no)
        searchable_units.append(
            (
                unit_no,
                "\n".join(
                    part for part in (title, text) if isinstance(part, str)
                ),
            )
        )

    checks["title"] = {
        "present": bool(titled_units),
        "unit_numbers": titled_units,
    }

    for name, pattern in _DOCUMENT_CHECKS:
        matched_units = [
            unit_no
            for unit_no, text in searchable_units
            if pattern.search(text)
        ]
        checks[name] = {
            "present": bool(matched_units),
            "unit_numbers": matched_units,
        }

    has_closing_material = (
        checks["thank_you"]["present"] or checks["references"]["present"]
    )
    checks["closing_material"] = {
        "present": has_closing_material,
        "unit_numbers": sorted(
            set(checks["thank_you"]["unit_numbers"])
            | set(checks["references"]["unit_numbers"])
        ),
    }

    issues: list[dict[str, object]] = []
    for check_name, issue_rule, description, suggestion in (
        (
            "agenda",
            "missing_agenda",
            "No agenda or contents unit was found.",
            "Add an agenda or contents section.",
        ),
        (
            "conclusion",
            "missing_conclusion",
            "No conclusion or summary unit was found.",
            "Add a conclusion or summary section.",
        ),
        (
            "closing_material",
            "missing_closing_material",
            "No thank-you or references material was found.",
            "Add a thank-you or references section.",
        ),
    ):
        if not checks[check_name]["present"]:
            issues.append(
                {
                    "unit_no": 0,
                    "rule": issue_rule,
                    "issue": description,
                    "offset": 0,
                    "original": "",
                    "suggestions": [suggestion],
                    "category": "Completeness",
                }
            )
    return checks, issues


def score_document_units(
    units: Sequence[Mapping[str, object]],
    quality_checks: Mapping[str, object],
) -> dict[str, object]:
    """Score extracted units from deterministic findings; Coherent is unscored."""
    if not units:
        raise ValueError("At least one extracted document unit is required.")

    completeness_checks, document_issues = _document_completeness(units)
    check_issues = quality_checks.get("issues", [])
    if not isinstance(check_issues, list):
        raise ValueError("Quality-check issues must be a list.")
    all_issues = [issue for issue in check_issues if isinstance(issue, dict)]
    all_issues.extend(document_issues)

    unit_numbers = [
        _unit_id(unit, index) for index, unit in enumerate(units, start=1)
    ]
    penalties: dict[str, dict[int, int]] = {
        dimension: {unit_no: 0 for unit_no in unit_numbers}
        for dimension in _SCORED_DIMENSIONS
    }
    global_penalties = {dimension: 0 for dimension in _SCORED_DIMENSIONS}
    evidence: dict[str, list[dict[str, object]]] = {
        dimension: [] for dimension in _SEVEN_CS
    }

    for issue in all_issues:
        category = issue.get("category")
        dimension = (
            "Complete"
            if category == "Completeness"
            else _CATEGORY_TO_DIMENSION.get(str(category))
        )
        if dimension is None:
            continue
        rule = str(issue.get("rule", ""))
        severity_penalty = _PENALTIES.get(rule, 4)
        issue_evidence = dict(issue)
        issue_evidence["severity_penalty"] = severity_penalty
        evidence[dimension].append(issue_evidence)

        unit_no = issue.get("unit_no")
        if isinstance(unit_no, int) and unit_no in penalties[dimension]:
            penalties[dimension][unit_no] += severity_penalty
        else:
            global_penalties[dimension] += severity_penalty

    scores: dict[str, int | None] = {}
    dimension_evidence: dict[str, dict[str, object]] = {}
    for dimension in _SCORED_DIMENSIONS:
        per_unit = [
            {
                "unit_no": unit_no,
                "penalty": penalties[dimension][unit_no],
                "score": max(0, 100 - penalties[dimension][unit_no]),
            }
            for unit_no in unit_numbers
        ]
        unit_average = round(sum(item["score"] for item in per_unit) / len(per_unit))
        score = max(0, unit_average - global_penalties[dimension])
        scores[dimension] = score
        dimension_evidence[dimension] = {
            "issue_count": len(evidence[dimension]),
            "issues": evidence[dimension],
            "per_unit": per_unit,
            "document_penalty": global_penalties[dimension],
        }

    scores["Coherent"] = None
    dimension_evidence["Coherent"] = {
        "issue_count": 0,
        "issues": [],
        "status": "not_scored",
    }
    overall_score = round(
        sum(int(scores[dimension]) for dimension in _SCORED_DIMENSIONS)
        / len(_SCORED_DIMENSIONS)
    )

    evaluations: dict[str, dict[str, str]] = {}
    for dimension in _SEVEN_CS:
        if dimension == "Coherent":
            evaluations[dimension] = {
                "status": "Not scored",
                "feedback": "Coherence scoring is reserved for the upcoming AI phase.",
            }
            continue
        issue_count = len(evidence[dimension])
        score = scores[dimension]
        status = "Strong" if score >= 85 else "Developing" if score >= 60 else "Needs work"
        feedback = (
            f"{issue_count} issue(s) affected this score."
            if issue_count
            else f"No rule-based issues were found across {len(units)} unit(s)."
        )
        evaluations[dimension] = {"status": status, "feedback": feedback}

    recommendations = list(
        dict.fromkeys(
            suggestion
            for dimension in _SCORED_DIMENSIONS
            for issue in evidence[dimension]
            for suggestion in issue.get("suggestions", [])
            if isinstance(suggestion, str)
        )
    )
    strengths = [
        f"{dimension}: no rule-based issues detected."
        for dimension in _SCORED_DIMENSIONS
        if not evidence[dimension]
    ]
    if not recommendations:
        recommendations = ["No rule-based recommendations were generated."]

    language_tool = quality_checks.get("language_tool", {})
    language_tool_units = (
        language_tool.get("units", [])
        if isinstance(language_tool, dict)
        else []
    )
    all_grammar_checked = (
        isinstance(language_tool_units, list)
        and len(language_tool_units) == len(units)
        and all(
            isinstance(item, dict) and item.get("status") == "checked"
            for item in language_tool_units
        )
    )
    grammar_issues = [
        issue for issue in evidence["Correct"] if issue.get("rule") == "language_tool"
    ]
    total_issue_count = sum(
        len(evidence[dimension]) for dimension in _SCORED_DIMENSIONS
    )

    category_scores = {
        "Structure": scores["Complete"],
        "Clarity": scores["Clear"],
        "Content_Quality": round(
            (int(scores["Concrete"]) + int(scores["Correct"])) / 2
        ),
        "Grammar_and_Syntax": scores["Correct"],
        "Tone_Appropriateness": scores["Courteous"],
        "Audience_Alignment": scores["Clear"],
    }
    return {
        "overall_score": overall_score,
        "evaluation_method": "deterministic_rules",
        "seven_cs_scores": scores,
        "seven_cs_evaluation": evaluations,
        "seven_cs_evidence": dimension_evidence,
        "completeness_checks": completeness_checks,
        "category_scores": category_scores,
        "grammar_issues": grammar_issues,
        "grammar_score": scores["Correct"] if all_grammar_checked else None,
        "grammar_check_status": "success" if all_grammar_checked else "skipped",
        "strengths": strengths,
        "recommendations": recommendations,
        "detailed_feedback": (
            f"Deterministic rule-based assessment of {len(units)} unit(s); "
            f"{total_issue_count} rule-based issue(s) found. "
            "Coherence is not scored until the next AI phase."
        ),
    }
