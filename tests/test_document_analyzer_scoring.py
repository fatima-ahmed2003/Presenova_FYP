"""Regression tests for the production Document Analyzer evaluator."""

import copy
import math

import pytest
import requests

import ai_evaluator
import services.language_tool_service as language_tool_service


CANONICAL_7CS = {
    "Clear",
    "Concise",
    "Concrete",
    "Correct",
    "Coherent",
    "Complete",
    "Courteous",
}

CATEGORY_KEYS = {
    "Structure",
    "Clarity",
    "Persuasion",
    "Content_Quality",
    "Call_to_Action",
    "Grammar_and_Syntax",
    "Accuracy",
    "Tone_Appropriateness",
    "Audience_Alignment",
    "Purpose_Fulfillment",
}


def _complete_gemini_response():
    sample_quote = '"A document with a complete scorecard."'
    return {
        "overall_score": 99,
        "document_name": "sample.txt",
        "category_scores": {key: 76 for key in CATEGORY_KEYS},
        "seven_cs_evaluation": {
            key: (
                f"Evidence: {sample_quote} Feedback: Add a criterion-specific "
                f"improvement for {key}."
            )
            for key in CANONICAL_7CS
        },
        "seven_cs_scores": {
            key: 70 + index for index, key in enumerate(sorted(CANONICAL_7CS))
        },
        "context_analysis": {
            "topic": "Scorecard validation",
            "purpose": "Test the scoring contract.",
            "intended_audience": "Document Analyzer developers.",
            "major_arguments": ["A document with a complete scorecard."],
            "coverage_gaps": [],
            "context_accuracy_score": None,
            "is_context_accurate": None,
            "factual_correctness_summary": "External facts were not independently verified.",
            "inaccuracies_detected": [],
            "context_based_changes": ["Add independent source verification if needed."],
        },
        "strengths": [f"Evidence: {sample_quote} This is the source document."],
        "recommendations": [
            f"Add a documented test case. Evidence: {sample_quote}",
            f"Include a score-boundary case. Evidence: {sample_quote}",
            f"Verify fallback behavior. Evidence: {sample_quote}",
        ],
        "detailed_feedback": (
            "Feedback is grounded in the quoted sample. External facts are not verified; "
            "the server computes the final weighted score."
        ),
        "improved_text": "Revised document text.",
    }


def _mock_dependencies(
    monkeypatch,
    *,
    gemini_available=False,
    response=None,
    mock_language_tool=True,
):
    monkeypatch.setattr(
        ai_evaluator._gemini_provider,
        "is_available",
        lambda: gemini_available,
    )
    if gemini_available:
        monkeypatch.setattr(
            ai_evaluator._gemini_provider,
            "generate_structured",
            lambda _prompt: copy.deepcopy(response),
        )
    if mock_language_tool:
        monkeypatch.setattr(ai_evaluator, "check_grammar", lambda _text: [])


def _evaluate(text, monkeypatch, *, gemini_available=False, response=None):
    _mock_dependencies(
        monkeypatch,
        gemini_available=gemini_available,
        response=response,
    )
    return ai_evaluator.evaluate_7cs(
        text=text,
        module_type="document",
        context_metrics={"filename": "comparison.txt"},
    )


def _assert_valid_scorecard(report):
    scores = report.get("seven_cs_scores")
    assert isinstance(scores, dict)
    assert set(scores) == CANONICAL_7CS
    for score in scores.values():
        assert type(score) in (int, float)
        assert math.isfinite(score)
        assert 0 <= score <= 100

    evaluations = report.get("seven_cs_evaluation")
    assert isinstance(evaluations, dict)
    assert set(evaluations) == CANONICAL_7CS

    categories = report.get("category_scores")
    assert isinstance(categories, dict)
    assert CATEGORY_KEYS <= set(categories)
    for score in categories.values():
        assert type(score) in (int, float)
        assert math.isfinite(score)
        assert 0 <= score <= 100


def _pad_to_word_count(text, target):
    padding_words = (
        "Supplementary notes explain the project context and audience needs"
    ).split()
    words = text.split()
    while len(words) < target:
        words.extend(padding_words[: target - len(words)])
    return " ".join(words)


def test_fallback_returns_canonical_7cs_schema(monkeypatch):
    report = _evaluate(
        "Introduction: A short report. Conclusion: The next step is clear.",
        monkeypatch,
    )

    assert set(report["seven_cs_scores"]) == CANONICAL_7CS
    assert set(report["seven_cs_evaluation"]) == CANONICAL_7CS
    assert report["evaluation_method"] == "heuristic_fallback"


def test_fallback_overall_uses_explicit_weighted_rubric(monkeypatch):
    report = _evaluate(
        "Introduction: Visitors reported a 20 percent increase. "
        "Findings: Records show 20 visits. Conclusion: Please review in June.",
        monkeypatch,
    )

    rubric = report["score_rubric"]
    assert sum(item["weight_percent"] for item in rubric.values()) == 100
    assert report["overall_score"] == ai_evaluator._calculate_document_overall_score(
        report["seven_cs_scores"], report["category_scores"]
    )
    assert report["context_analysis"]["context_accuracy_score"] is None
    assert report["context_analysis"]["topic"]
    assert report["context_analysis"]["purpose"]


def test_fallback_scores_respond_to_document_quality(monkeypatch):
    strong_document = _pad_to_word_count(
        "Introduction: Our library pilot reduced checkout delays 30 percent "
        "in twelve weeks. Records show 84 of 100 visitors completed checkout "
        "within four minutes. Please approve a two-branch rollout by June so "
        "staff can measure another 100 visits. Conclusion: The measured pilot "
        "met its target and needs a scheduled review.",
        100,
    )
    weak_document = _pad_to_word_count(
        "Introduction: The library has things and other things happen, and "
        "this means many things for people who come and do activities. There "
        "are several views, and some could be better while others could be "
        "different. The team looked at some things but it is hard to say what "
        "happened. You should do it now and stop asking questions; your "
        "concerns do not matter. Conclusion: This topic has more things and "
        "maybe it could matter later.",
        100,
    )
    assert len(strong_document.split()) == len(weak_document.split())

    strong = _evaluate(strong_document, monkeypatch)["seven_cs_scores"]
    weak = _evaluate(weak_document, monkeypatch)["seven_cs_scores"]

    for scores in (strong, weak):
        assert scores
        for score in scores.values():
            assert type(score) in (int, float)
            assert 0 <= score <= 100

    dimensions = ["Clear", "Concise", "Concrete", "Complete", "Courteous"]
    coherence_key = "Coherent" if "Coherent" in strong else "Consistent"
    dimensions.append(coherence_key)
    unchanged_or_lower = [
        dimension
        for dimension in dimensions
        if strong[dimension] <= weak[dimension]
    ]
    assert not unchanged_or_lower, (
        "The stronger document should score higher for these dimensions: "
        f"{unchanged_or_lower}"
    )


def test_fallback_structure_signals_change_clear_coherence_and_completeness(monkeypatch):
    structured = (
        "Introduction: The team tested a checkout change. "
        "Findings: Waits fell from 30 minutes to 12 minutes. "
        "Conclusion: Review the results next week."
    )
    unstructured = (
        "The team tested a checkout change. Waits fell from 30 minutes to "
        "12 minutes. Review the results next week."
    )

    structured_scores = _evaluate(structured, monkeypatch)["seven_cs_scores"]
    unstructured_scores = _evaluate(unstructured, monkeypatch)["seven_cs_scores"]

    for dimension in ("Clear", "Coherent", "Complete"):
        assert structured_scores[dimension] > unstructured_scores[dimension]


def test_fallback_repetition_reduces_conciseness_estimate(monkeypatch):
    concise = (
        "The clinic reduced waiting time from 30 minutes to 12 minutes. "
        "Staff will review the results next week."
    )
    repetitive = (
        "The clinic clinic clinic reduced waiting waiting waiting time. "
        "Waiting time was waiting, and clinic staff reviewed the same waiting time."
    )

    concise_score = _evaluate(concise, monkeypatch)["seven_cs_scores"]["Concise"]
    repetitive_score = _evaluate(repetitive, monkeypatch)["seven_cs_scores"]["Concise"]

    assert concise_score > repetitive_score


def test_fallback_numeric_examples_inform_concrete_estimate(monkeypatch):
    concrete = (
        "The pilot reduced delays by 30 percent in 12 weeks; for example, "
        "84 of 100 visitors completed checkout within four minutes."
    )
    vague = (
        "The pilot changed several things over some time. Many people had "
        "different experiences, and the results might be better."
    )

    concrete_score = _evaluate(concrete, monkeypatch)["seven_cs_scores"]["Concrete"]
    vague_score = _evaluate(vague, monkeypatch)["seven_cs_scores"]["Concrete"]

    assert concrete_score > vague_score


def test_fallback_correct_score_uses_successful_grammar_results(monkeypatch):
    text = "Introduction: The team reports clear results. Conclusion: Review soon."
    clean_result = language_tool_service.GrammarCheckResult([], status="success")
    issue_result = language_tool_service.GrammarCheckResult(
        [{"rule_id": "TEST_RULE", "message": "Test issue."}],
        status="success",
    )
    monkeypatch.setattr(ai_evaluator._gemini_provider, "is_available", lambda: False)

    monkeypatch.setattr(ai_evaluator, "check_grammar", lambda _text: clean_result)
    clean_report = ai_evaluator.evaluate_7cs(
        text, "document", {"filename": "grammar.txt"}
    )

    monkeypatch.setattr(ai_evaluator, "check_grammar", lambda _text: issue_result)
    issue_report = ai_evaluator.evaluate_7cs(
        text, "document", {"filename": "grammar.txt"}
    )

    assert clean_report["seven_cs_scores"]["Correct"] == 65
    assert issue_report["seven_cs_scores"]["Correct"] == clean_report["seven_cs_scores"]["Correct"]
    assert clean_report["category_scores"]["Grammar_and_Syntax"] == 100
    assert issue_report["category_scores"]["Grammar_and_Syntax"] < 100
    for report in (clean_report, issue_report):
        assert report["overall_score"] == ai_evaluator._calculate_document_overall_score(
            report["seven_cs_scores"], report["category_scores"]
        )


def test_unavailable_grammar_is_not_reported_as_verified_correct(monkeypatch):
    monkeypatch.setattr(ai_evaluator._gemini_provider, "is_available", lambda: False)
    monkeypatch.setattr(
        ai_evaluator,
        "check_grammar",
        lambda _text: language_tool_service.GrammarCheckResult([], status="timeout"),
    )

    report = ai_evaluator.evaluate_7cs(
        "Introduction: The team reports clear results. Conclusion: Review soon.",
        "document",
        {"filename": "grammar.txt"},
    )

    assert report["grammar_check_status"] == "timeout"
    assert report["grammar_score"] is None
    assert report["category_scores"]["Grammar_and_Syntax"] <= 70
    assert "does not verify grammar correctness" in report["seven_cs_evaluation"]["Correct"]
    assert report["context_analysis"]["context_accuracy_score"] is None
    assert report["context_analysis"]["is_context_accurate"] is None
    assert "not independently verified" in report["context_analysis"]["factual_correctness_summary"]


def test_successful_language_tool_check_with_no_issues_is_perfect(monkeypatch):
    _mock_dependencies(monkeypatch, mock_language_tool=False)
    monkeypatch.setattr(language_tool_service, "_CLOUD_ENABLED", True)

    class EmptyIssuesResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"matches": []}

    monkeypatch.setattr(
        language_tool_service.requests,
        "post",
        lambda *_args, **_kwargs: EmptyIssuesResponse(),
    )
    report = ai_evaluator.evaluate_7cs(
        "Introduction: A clear report. Conclusion: Please review the results.",
        "document",
        {"filename": "grammar.txt"},
    )

    assert report["grammar_issues"] == []
    assert report["grammar_score"] == 100


def test_language_tool_timeout_is_distinguishable_from_zero_issues(monkeypatch):
    _mock_dependencies(monkeypatch, mock_language_tool=False)
    monkeypatch.setattr(language_tool_service, "_CLOUD_ENABLED", True)

    class EmptyIssuesResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"matches": []}

    monkeypatch.setattr(
        language_tool_service.requests,
        "post",
        lambda *_args, **_kwargs: EmptyIssuesResponse(),
    )
    clean_report = ai_evaluator.evaluate_7cs(
        "Introduction: A clear report. Conclusion: Please review the results.",
        "document",
        {"filename": "grammar.txt"},
    )

    def raise_timeout(*_args, **_kwargs):
        raise requests.exceptions.Timeout("simulated timeout")

    monkeypatch.setattr(
        language_tool_service.requests,
        "post",
        raise_timeout,
    )
    timeout_report = ai_evaluator.evaluate_7cs(
        "Introduction: A clear report. Conclusion: Please review the results.",
        "document",
        {"filename": "grammar.txt"},
    )

    assert timeout_report != clean_report


def test_complete_gemini_response_is_returned_with_valid_scores(monkeypatch):
    response = _complete_gemini_response()
    report = _evaluate(
        "A document with a complete scorecard.",
        monkeypatch,
        gemini_available=True,
        response=response,
    )

    _assert_valid_scorecard(report)
    expected_scores = response["seven_cs_scores"]
    expected_categories = response["category_scores"].copy()
    expected_categories["Grammar_and_Syntax"] = 100
    expected_overall = ai_evaluator._calculate_document_overall_score(
        expected_scores, expected_categories
    )
    assert report["overall_score"] == expected_overall
    assert report["overall_score"] != response["overall_score"]
    assert report["seven_cs_scores"] == expected_scores
    assert report["evaluation_method"] == "gemini"
    assert report["score_rubric"] == ai_evaluator._document_rubric_breakdown(
        expected_scores, expected_categories
    )


def test_gemini_feedback_requires_source_evidence_and_actionable_steps(monkeypatch):
    response = _complete_gemini_response()
    response["seven_cs_evaluation"]["Clear"] = (
        "Clear overall. The text is good."
    )

    report = _evaluate(
        "A document with a complete scorecard.",
        monkeypatch,
        gemini_available=True,
        response=response,
    )

    assert report["evaluation_method"] == "heuristic_fallback"
    assert set(report["seven_cs_scores"]) == CANONICAL_7CS

    response = _complete_gemini_response()
    response["context_analysis"]["factual_correctness_summary"] = (
        "All factual claims in this document are accurate."
    )
    report = _evaluate(
        "A document with a complete scorecard.",
        monkeypatch,
        gemini_available=True,
        response=response,
    )
    assert report["evaluation_method"] == "heuristic_fallback"


def test_gemini_response_missing_category_scores_is_rejected_or_repaired(monkeypatch):
    response = _complete_gemini_response()
    del response["category_scores"]

    report = _evaluate(
        "A document with an incomplete scorecard.",
        monkeypatch,
        gemini_available=True,
        response=response,
    )

    assert isinstance(report.get("category_scores"), dict)


def test_gemini_response_missing_7cs_keys_is_rejected_or_repaired(monkeypatch):
    response = _complete_gemini_response()
    del response["seven_cs_scores"]["Concrete"]
    del response["seven_cs_evaluation"]["Concrete"]

    report = _evaluate(
        "A document with an incomplete 7Cs scorecard.",
        monkeypatch,
        gemini_available=True,
        response=response,
    )

    _assert_valid_scorecard(report)


@pytest.mark.parametrize(
    ("score_field", "dimension", "value"),
    [
        ("seven_cs_scores", "Clear", "high"),
        ("seven_cs_scores", "Clear", -1),
        ("seven_cs_scores", "Concise", 101),
        ("category_scores", "Structure", "high"),
        ("category_scores", "Structure", -1),
        ("category_scores", "Structure", 101),
    ],
)
def test_gemini_scores_are_validated(monkeypatch, score_field, dimension, value):
    response = _complete_gemini_response()
    response[score_field][dimension] = value

    report = _evaluate(
        "A document with an invalid score.",
        monkeypatch,
        gemini_available=True,
        response=response,
    )

    _assert_valid_scorecard(report)


def test_empty_gemini_response_uses_fallback(monkeypatch):
    report = _evaluate(
        "Introduction: A report. Conclusion: Review the next step.",
        monkeypatch,
        gemini_available=True,
        response={},
    )

    assert isinstance(report.get("category_scores"), dict)
    scores = report.get("seven_cs_scores")
    assert isinstance(scores, dict) and scores
    for score in scores.values():
        assert type(score) in (int, float)
        assert 0 <= score <= 100
