"""
AI Evaluator Service
Centralized Google Gemini 1.5 Flash analysis and smart fallbacks for all modules.
Now includes LanguageTool Cloud grammar pre-pass to enrich the 7Cs analysis.
"""

import sys
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import os
import json
import math
import random
import re
import logging
from collections import Counter
from dotenv import load_dotenv
from services.ai.gemini_provider import GeminiProvider
from services.language_tool_service import (
    check_grammar,
    summarise_grammar_issues,
    grammar_score as lt_grammar_score,
)
from services.ai.scoring_utils import clamp_score

logger = logging.getLogger(__name__)

# Load env variables
load_dotenv()

# Initialize Gemini Provider
_gemini_provider = GeminiProvider()
gemini_available = _gemini_provider.is_available()

if gemini_available:
    logger.info("[AI OK] Global AI Evaluator: Gemini Provider initialized successfully (%s)", _gemini_provider.model_name)
else:
    logger.warning("[AI WARN] Global AI Evaluator: GEMINI_API_KEY not configured or offline. Running with smart fallbacks.")

_DOCUMENT_7CS_KEYS = {
    "Clear",
    "Concise",
    "Concrete",
    "Correct",
    "Coherent",
    "Complete",
    "Courteous",
}
_DOCUMENT_CATEGORY_SCORE_KEYS = {
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
_DOCUMENT_RUBRIC_WEIGHTS = {
    "content_quality_relevance": 25,
    "seven_cs": 35,
    "organization": 15,
    "grammar_and_language": 10,
    "audience_and_purpose": 10,
    "evidence_and_support": 5,
}
_ACTIONABLE_FEEDBACK_PATTERN = re.compile(
    r"\b(add|include|identify|state|cite|define|reorganize|replace|specify|"
    r"explain|remove|clarify|connect|check|revise|support|provide|break|"
    r"shorten|reduce|expand|compare|name|describe|outline|verify)\b",
    re.IGNORECASE,
)


def _is_valid_document_score(value: object) -> bool:
    if type(value) is int:
        return 0 <= value <= 100
    return type(value) is float and math.isfinite(value) and 0 <= value <= 100


def _validate_document_evidence(value: str, source_text: str) -> bool:
    normalized_source = " ".join(source_text.split()).casefold()
    quoted_evidence = re.findall(r'["“](.{12,}?)["”]', value)
    if any(" ".join(quote.split()).casefold() in normalized_source for quote in quoted_evidence):
        return True
    return (
        bool(
            re.search(
                r"(?:^|[.;]\s*)\s*(?:no direct evidence|not present in document):",
                value,
                re.IGNORECASE,
            )
        )
        and len(value.strip()) >= 32
    )


def _validate_document_gemini_scores(analysis: dict, source_text: str) -> None:
    if not _is_valid_document_score(analysis.get("overall_score")):
        raise ValueError("Gemini document response has an invalid overall_score")

    seven_cs_scores = analysis.get("seven_cs_scores")
    if not isinstance(seven_cs_scores, dict) or set(seven_cs_scores) != _DOCUMENT_7CS_KEYS:
        raise ValueError("Gemini document response has an incomplete 7Cs scorecard")
    if not all(_is_valid_document_score(score) for score in seven_cs_scores.values()):
        raise ValueError("Gemini document response has an invalid 7Cs score")

    seven_cs_evaluation = analysis.get("seven_cs_evaluation")
    if not isinstance(seven_cs_evaluation, dict) or set(seven_cs_evaluation) != _DOCUMENT_7CS_KEYS:
        raise ValueError("Gemini document response has an incomplete 7Cs evaluation")
    for key, evaluation in seven_cs_evaluation.items():
        if (
            not isinstance(evaluation, str)
            or not evaluation.startswith("Evidence:")
            or "Feedback:" not in evaluation
        ):
            raise ValueError(f"Gemini document response has invalid evidence/feedback for {key}")
        evidence, feedback = evaluation[len("Evidence:"):].split("Feedback:", 1)
        if (
            not _validate_document_evidence(evidence, source_text)
            or len(feedback.strip()) < 12
        ):
            raise ValueError(f"Gemini document response has invalid evidence/feedback for {key}")
        if (
            seven_cs_scores[key] < 65
            and not _ACTIONABLE_FEEDBACK_PATTERN.search(feedback)
        ):
            raise ValueError(f"Gemini feedback for weak {key} score is not actionable")

    category_scores = analysis.get("category_scores")
    if (
        not isinstance(category_scores, dict)
        or not _DOCUMENT_CATEGORY_SCORE_KEYS.issubset(category_scores)
        or not all(_is_valid_document_score(score) for score in category_scores.values())
    ):
        raise ValueError("Gemini document response has invalid category_scores")

    for field in ("strengths", "recommendations"):
        entries = analysis.get(field)
        if (
            not isinstance(entries, list)
            or (field == "recommendations" and len(entries) < 3)
            or any(not isinstance(entry, str) or len(entry.strip()) < 12 for entry in entries)
        ):
            raise ValueError(f"Gemini document response has invalid {field}")
    if any(
        not _ACTIONABLE_FEEDBACK_PATTERN.search(item)
        or not _validate_document_evidence(item, source_text)
        for item in analysis["recommendations"]
    ):
        raise ValueError(
            "Gemini document recommendations must be actionable and evidence-grounded"
        )
    if not analysis["strengths"] or any(
        not _validate_document_evidence(item, source_text)
        for item in analysis["strengths"]
    ):
        raise ValueError("Gemini document strengths must cite source evidence")
    if not isinstance(analysis.get("detailed_feedback"), str) or len(
        analysis["detailed_feedback"].strip()
    ) < 40:
        raise ValueError("Gemini document response has invalid detailed_feedback")
    if not isinstance(analysis.get("improved_text"), str) or not analysis["improved_text"].strip():
        raise ValueError("Gemini document response has invalid improved_text")

    context_analysis = analysis.get("context_analysis")
    if not isinstance(context_analysis, dict):
        raise ValueError("Gemini document response has invalid context_analysis")
    for field in ("topic", "purpose", "intended_audience", "factual_correctness_summary"):
        if not isinstance(context_analysis.get(field), str) or not context_analysis[field].strip():
            raise ValueError(f"Gemini document response has invalid context_analysis.{field}")
    if not re.search(
        r"\b(?:not|never|cannot|can't)\b.{0,60}\b(?:independently\s+)?"
        r"(?:verify|verified|check|checked|fact-check|fact-checked)\b",
        context_analysis["factual_correctness_summary"],
        re.IGNORECASE,
    ):
        raise ValueError("External factual accuracy must remain explicitly unverified")
    for field in ("major_arguments", "coverage_gaps", "inaccuracies_detected", "context_based_changes"):
        entries = context_analysis.get(field)
        if not isinstance(entries, list) or any(
            not isinstance(entry, str) or not entry.strip() for entry in entries
        ):
            raise ValueError(f"Gemini document response has invalid context_analysis.{field}")
    if context_analysis.get("context_accuracy_score") is not None:
        raise ValueError("External factual accuracy must remain unverified")
    if context_analysis.get("is_context_accurate") is not None:
        raise ValueError("External factual accuracy must remain unverified")
    if any(
        not _validate_document_evidence(item, source_text)
        for item in context_analysis["inaccuracies_detected"]
    ):
        raise ValueError("Context inaccuracies must cite an internal contradiction")
    if any(
        not _ACTIONABLE_FEEDBACK_PATTERN.search(item)
        for item in context_analysis["context_based_changes"]
    ):
        raise ValueError("Context changes must be actionable")


def _calculate_document_overall_score(
    seven_cs_scores: dict, category_scores: dict
) -> int:
    rubric = _document_rubric_breakdown(seven_cs_scores, category_scores)
    return max(
        0,
        min(
            100,
            round(sum(item["score"] * item["weight_percent"] / 100 for item in rubric.values())),
        ),
    )


def _document_rubric_breakdown(seven_cs_scores: dict, category_scores: dict) -> dict:
    return {
        "content_quality_relevance": {
            "weight_percent": _DOCUMENT_RUBRIC_WEIGHTS["content_quality_relevance"],
            "score": category_scores["Content_Quality"],
        },
        "seven_cs": {
            "weight_percent": _DOCUMENT_RUBRIC_WEIGHTS["seven_cs"],
            "score": round(sum(seven_cs_scores.values()) / len(_DOCUMENT_7CS_KEYS)),
        },
        "organization": {
            "weight_percent": _DOCUMENT_RUBRIC_WEIGHTS["organization"],
            "score": category_scores["Structure"],
        },
        "grammar_and_language": {
            "weight_percent": _DOCUMENT_RUBRIC_WEIGHTS["grammar_and_language"],
            "score": category_scores["Grammar_and_Syntax"],
        },
        "audience_and_purpose": {
            "weight_percent": _DOCUMENT_RUBRIC_WEIGHTS["audience_and_purpose"],
            "score": round(
                (category_scores["Audience_Alignment"] + category_scores["Purpose_Fulfillment"])
                / 2
            ),
        },
        "evidence_and_support": {
            "weight_percent": _DOCUMENT_RUBRIC_WEIGHTS["evidence_and_support"],
            "score": category_scores["Accuracy"],
        },
    }


def _local_clean_transcript(text: str) -> str:
    """Safety net: remove fillers and back-to-back repeats WITHOUT adding any new words."""
    t = re.sub(r'\b(um+|uh+|erm|err)\b[,]?\s*', '', text, flags=re.I)
    t = re.sub(r'\b(you know|basically|actually|kind of)\b[,]?\s*', '', t, flags=re.I)
    t = re.sub(r'\b(\w+)(\s+\1\b)+', r'\1', t, flags=re.I)
    t = re.sub(r'\s+', ' ', t).strip()
    return t[:1].upper() + t[1:] if t else t


def _speech_fallback(text: str, context_metrics: dict, grammar_issues: list, grammar_status: str, grammar_score: int) -> dict:
    def bounded(value: float) -> int:
        return max(0, min(100, round(value)))

    tokens = re.findall(r"\b[\w'-]+\b", text.casefold())
    words = text.split()
    word_count = len(words)
    sentence_parts = [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+", text.strip())
        if part.strip()
    ]
    sentence_parts = sentence_parts or ([text.strip()] if text.strip() else [])
    sentence_lengths = [
        len(re.findall(r"\b[\w'-]+\b", sentence))
        for sentence in sentence_parts
    ]
    average_sentence_length = (
        sum(sentence_lengths) / len(sentence_lengths) if sentence_lengths else 0
    )
    sentence_length_variation = (
        sum(abs(length - average_sentence_length) for length in sentence_lengths)
        / len(sentence_lengths)
        if sentence_lengths
        else 0
    )
    capitalized_ratio = (
        sum(sentence[0].isupper() for sentence in sentence_parts) / len(sentence_parts)
        if sentence_parts
        else 0
    )
    punctuated_ratio = (
        sum(sentence[-1] in ".!?" for sentence in sentence_parts) / len(sentence_parts)
        if sentence_parts
        else 0
    )

    filler_count = max(0, int(context_metrics.get("filler_count", 0)))
    filler_percentage = max(0.0, float(context_metrics.get("filler_percentage", 0.0)))
    repetition_count = max(0, int(context_metrics.get("repetition_count", 0)))
    speech_speed_wpm = float(context_metrics.get("speech_speed_wpm", 0))
    if not math.isfinite(speech_speed_wpm):
        speech_speed_wpm = 0

    lower_text = text.casefold()
    vague_count = sum(
        len(re.findall(rf"\b{term}\b", lower_text))
        for term in ("thing", "things", "stuff", "some", "several", "maybe", "somehow")
    )
    numeric_count = len(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", lower_text))
    numeric_examples = re.findall(r"\b\d+(?:[.,]\d+)?%?\b", text)[:3]
    numeric_detail = (
        f", including {', '.join(numeric_examples)}" if numeric_examples else ""
    )
    example_count = len(
        re.findall(r"\b(?:for example|such as|case study|for instance)\b", lower_text)
    )
    evidence_count = len(
        re.findall(r"\b(?:survey|study|data|results|measured|records show|evidence)\b", lower_text)
    )
    transition_count = len(
        re.findall(
            r"\b(?:first|next|then|finally|therefore|because|however|as a result|in conclusion)\b",
            lower_text,
        )
    )
    opening_count = len(
        re.findall(r"\b(?:today|to begin|introduction|first|our purpose|i will discuss)\b", lower_text)
    )
    closing_count = len(
        re.findall(
            r"\b(?:in conclusion|to summarize|finally|thank you|any questions|next step)\b",
            lower_text,
        )
    )
    action_count = len(
        re.findall(
            r"\b(?:please|should|recommend|propose|approve|implement|need to|next step)\b",
            lower_text,
        )
    )
    courtesy_count = len(
        re.findall(r"\b(?:please|thank you|we appreciate|could you|would you)\b", lower_text)
    )
    hostile_count = len(
        re.findall(r"\b(?:shut up|you do not matter|stop asking questions)\b", lower_text)
    )
    stop_words = {
        "about", "after", "also", "and", "are", "because", "been", "before", "being",
        "but", "for", "from", "have", "into", "just", "like", "that", "the", "their",
        "them", "there", "these", "they", "this", "those", "through", "today", "with",
        "would", "you", "your", "um", "uh", "erm", "err",
    }
    content_tokens = [
        token for token in tokens if len(token) > 3 and token not in stop_words
    ]
    unique_ratio = len(set(content_tokens)) / max(1, len(content_tokens))
    repeated_content_count = sum(
        count - 2 for count in Counter(content_tokens).values() if count > 2
    )
    topic_terms = [term for term, _count in Counter(content_tokens).most_common(3)]
    topic_summary = ", ".join(topic_terms) if topic_terms else "no clear topic terms"

    pace_deviation = max(0, 120 - speech_speed_wpm, speech_speed_wpm - 160)
    pace_score = 100 - min(40, round(pace_deviation * 0.5))
    filler_penalty = min(30, round(filler_percentage * 1.5))
    repetition_penalty = min(25, repetition_count * 5)
    long_sentence_penalty = min(20, round(max(0, average_sentence_length - 22) * 1.5))

    clear_score = bounded(
        pace_score - filler_penalty - repetition_penalty - min(20, vague_count * 4)
        - long_sentence_penalty
    )
    concise_score = bounded(
        100 - min(35, filler_percentage * 2) - min(30, repetition_count * 6)
        - min(20, repeated_content_count * 4) - long_sentence_penalty
    )
    concrete_score = bounded(
        40 + min(30, numeric_count * 8) + min(20, example_count * 12)
        + min(15, evidence_count * 5)
    )
    if grammar_status == "success":
        correct_score = grammar_score
    else:
        correct_score = min(
            70,
            bounded(
                40 + round(15 * capitalized_ratio) + round(15 * punctuated_ratio)
                - min(15, repetition_count * 2) - min(15, filler_count)
            ),
        )
    complete_score = bounded(
        35 + min(20, word_count // 15 * 3) + min(15, len(sentence_parts) * 3)
        + (10 if opening_count else 0) + (10 if closing_count else 0)
        + min(10, transition_count * 3)
    )
    courteous_score = bounded(
        65 + min(20, courtesy_count * 5) - min(45, hostile_count * 15)
    )
    consistent_score = bounded(
        60 + min(20, transition_count * 4) + round(unique_ratio * 10)
        - min(25, repetition_count * 5)
        - min(20, round(sentence_length_variation * 0.8))
    )

    seven_cs_scores = {
        "Clear": clear_score,
        "Concise": concise_score,
        "Correct": correct_score,
        "Complete": complete_score,
        "Courteous": courteous_score,
        "Concrete": concrete_score,
        "Consistent": consistent_score,
    }
    structure_score = bounded(
        40 + min(20, opening_count * 7) + min(20, closing_count * 7)
        + min(20, transition_count * 5)
    )
    persuasion_score = bounded(
        40 + min(25, evidence_count * 5) + min(20, numeric_count * 5)
        + min(15, action_count * 5)
    )
    content_quality_score = bounded(
        (concrete_score + complete_score + consistent_score + correct_score) / 4
    )
    call_to_action_score = bounded(
        35 + min(45, action_count * 12) + (10 if closing_count else 0)
    )
    category_scores = {
        "Structure": structure_score,
        "Clarity": clear_score,
        "Persuasion": persuasion_score,
        "Content_Quality": content_quality_score,
        "Call_to_Action": call_to_action_score,
    }
    overall_score = max(30, min(95, round(sum(seven_cs_scores.values()) / 7)))

    if grammar_status == "success":
        correct_feedback = (
            f"LanguageTool checked this transcript and reported {len(grammar_issues)} issue(s) "
            f"with a score of {grammar_score}/100."
        )
    else:
        correct_feedback = (
            f"LanguageTool status is {grammar_status}; grammar was not verified. "
            "Visible punctuation and repetition signals are only a rough estimate."
        )
    seven_cs_evaluation = {
        "Clear": (
            f"Pace was {speech_speed_wpm:.1f} WPM with {filler_count} filler(s); "
            f"the transcript contains {vague_count} vague wording signal(s)."
        ),
        "Concise": (
            f"Detected {filler_count} filler(s), {repetition_count} consecutive repetition(s), "
            f"and {repeated_content_count} repeated content term(s)."
        ),
        "Correct": correct_feedback,
        "Complete": (
            f"The transcript has {len(sentence_parts)} sentence(s), "
            f"{word_count} word(s), and {closing_count} closing signal(s)."
        ),
        "Courteous": (
            f"Found {courtesy_count} explicit courtesy marker(s) and "
            f"{hostile_count} dismissive phrase(s); absence of markers is not proof of tone."
        ),
        "Concrete": (
            f"Found {numeric_count} numeric detail(s){numeric_detail}, "
            f"{example_count} example cue(s), and {evidence_count} evidence marker(s)."
        ),
        "Consistent": (
            f"Transcript-only estimate uses {transition_count} transition cue(s) and "
            f"{repeated_content_count} repeated content term(s); audio prosody was not measured."
        ),
    }

    recommendations = []
    if filler_count:
        recommendations.append(f"Reduce the {filler_count} detected filler word(s) with deliberate pauses.")
    if repetition_count or repeated_content_count:
        recommendations.append(
            "Replace repeated words or phrases with concise, specific wording."
        )
    if not numeric_count and not example_count:
        recommendations.append("Add one specific example or measurable detail to support the spoken point.")
    if not opening_count or not transition_count:
        recommendations.append("State the main point early and use clear transitions between ideas.")
    if not closing_count or not action_count:
        recommendations.append("End with a concise conclusion and a clear audience next step.")
    if speech_speed_wpm < 120 or speech_speed_wpm > 160:
        recommendations.append("Practice toward a speaking pace of 120-160 words per minute.")
    if grammar_status != "success":
        recommendations.append(
            f"Review grammar manually; LanguageTool status is {grammar_status}, so it is unverified."
        )
    recommendations = recommendations[:5]
    if not recommendations:
        recommendations.append("Review the transcript's examples and transitions before presenting.")

    strengths = []
    if numeric_count or example_count:
        strengths.append(
            f"The transcript includes {numeric_count} numeric detail(s) and {example_count} example cue(s)."
        )
    if transition_count:
        strengths.append(f"The transcript uses {transition_count} transition cue(s).")
    if not strengths:
        strengths.append(f"Transcript content terms include {topic_summary}.")

    return {
        "overall_score": overall_score,
        "evaluation_method": "heuristic_fallback",
        "category_scores": category_scores,
        "seven_cs_evaluation": seven_cs_evaluation,
        "seven_cs_scores": seven_cs_scores,
        "strengths": strengths,
        "recommendations": recommendations,
        "detailed_feedback": (
            f"Transcript-content estimate: recurring terms were {topic_summary}. "
            f"Measured pace was {speech_speed_wpm:.1f} WPM, with {filler_count} filler(s), "
            f"{repetition_count} consecutive repetition(s), and {len(sentence_parts)} sentence(s). "
            f"Scores reflect transcript and available delivery metrics, not verified meaning or "
            f"audio prosody. {correct_feedback}"
        ),
        "improved_text": _local_clean_transcript(text),
        "grammar_score": grammar_score if grammar_status == "success" else None,
        "grammar_check_status": grammar_status,
        "grammar_issues": grammar_issues,
        "grammar_issues_count": len(grammar_issues),
    }


def evaluate_7cs(text: str, module_type: str, context_metrics: dict) -> dict:
    """
    Evaluates text (document slides, transcripts, or live presentation speech)
    and returns a standardized 7Cs scorecard.

    Now includes a LanguageTool Cloud grammar pre-pass:
    - Detects specific grammar/spelling errors before calling Gemini.
    - Injects the error list into the Gemini prompt for richer corrections.
    - Adds 'grammar_issues' and 'grammar_score' fields to the returned report.

    module_type options: 'document', 'speech', 'live'
    """
    # Safeguard text input
    if not text or not text.strip():
        text = "No content provided."

    # ===== STEP 0: GRAMMAR PRE-PASS (LanguageTool Cloud API) =====
    # Runs for ALL module types: document, speech, and live transcript.
    grammar_issues = []
    grammar_summary_text = ""
    computed_grammar_score = 100
    grammar_check_status = 'success'

    try:
        grammar_result = check_grammar(text)
        grammar_check_status = getattr(grammar_result, 'status', 'success')
        grammar_issues = list(grammar_result)
        word_count_for_grammar = len(text.split())
        computed_grammar_score = lt_grammar_score(grammar_issues, word_count_for_grammar)
        grammar_summary_text = summarise_grammar_issues(grammar_issues)
        if grammar_check_status != 'success':
            logger.info(
                "[GRAMMAR] LanguageTool check unavailable (status=%s, module=%s).",
                grammar_check_status,
                module_type,
            )
        elif grammar_issues:
            logger.info("[GRAMMAR] LanguageTool detected %d issues (module=%s). Grammar score: %d/100", len(grammar_issues), module_type, computed_grammar_score)
        else:
            logger.info("[GRAMMAR] LanguageTool: No grammar issues detected (module=%s).", module_type)
    except Exception as _ge:
        logger.warning("[GRAMMAR WARN] Grammar pre-pass failed: %s. Continuing without grammar data.", _ge)
        grammar_issues = []
        grammar_summary_text = ""
        computed_grammar_score = 100
        grammar_check_status = 'error'

    # ===== HELPER: Grammar grade badge =====
    def _grammar_grade(score: int) -> str:
        if score >= 90: return 'A'
        if score >= 80: return 'B'
        if score >= 70: return 'C'
        if score >= 60: return 'D'
        return 'F'

    def _grammar_summary_short(score: int, issues: list) -> str:
        if score >= 90:
            return f"Excellent grammar ({score}/100). No significant issues detected."
        if score >= 80:
            return f"Good grammar ({score}/100). {len(issues)} minor issue(s) found."
        if score >= 70:
            return f"Acceptable grammar ({score}/100). {len(issues)} issue(s) should be corrected."
        return f"Grammar needs improvement ({score}/100). {len(issues)} issue(s) detected — review carefully."

    grammar_check_succeeded = grammar_check_status == 'success'
    grammar_unavailable_message = "LanguageTool grammar check was unavailable; grammar was not verified."
    grammar_quality_summary = (
        f"Grammar score is {computed_grammar_score}/100 (Grade {_grammar_grade(computed_grammar_score)})."
        if grammar_check_succeeded
        else grammar_unavailable_message
    )
    grammar_issues_summary = (
        "No significant grammar issues detected."
        if grammar_check_succeeded and not grammar_issues
        else (
            f"{len(grammar_issues)} grammar issue(s) were flagged. "
            f"Key areas: {grammar_summary_text[:600] or 'Review the grammar report.'}"
            if grammar_check_succeeded
            else grammar_unavailable_message
        )
    )

    # ===== BUILD DYNAMIC FALLBACK RESPONSE =====
    fallback_json = {}
    insufficient_live_data = False  # FIX: Initialize before any if/elif that references it
    
    if module_type == 'document':
        filename = context_metrics.get("filename", "presentation.pdf")
        
        # Local Heuristic Analysis (when Gemini is unavailable)
        word_count = len(text.split())
        sentences = [s.strip() for s in re.split(r'[.!?]+', text) if len(s.strip()) > 5]
        sentence_count = len(sentences)
        text_lower = text.lower()
        
        # Compute sentence length variety (higher = more varied = better)
        if sentences:
            avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
            variance = sum(abs(len(s.split()) - avg_len) for s in sentences) / len(sentences)
            sentence_variety_score = min(100, int(variance * 10))
        else:
            avg_len, variance, sentence_variety_score = 10, 0, 50

        has_structure = any(w in text_lower for w in ["slide", "introduction", "conclusion", "agenda", "overview", "summary"])
        filler_words = ['um', 'uh', 'like', 'you know', 'basically', 'actually']
        filler_count = sum(text_lower.count(w) for w in filler_words)

        section_patterns = {
            "introduction": r"\b(?:introduction|overview|agenda)\b",
            "evidence": r"\b(?:results?|findings?|records?|data)\b",
            "conclusion": r"\b(?:conclusion|summary|next steps?)\b",
        }
        present_sections = [
            section for section, pattern in section_patterns.items()
            if re.search(pattern, text_lower)
        ]
        vague_terms = (
            "thing", "things", "stuff", "some", "several", "maybe",
            "various", "certain",
        )
        vague_count = sum(
            len(re.findall(rf"\b{term}\b", text_lower))
            for term in vague_terms
        )
        content_words = [
            word for word in re.findall(r"\b[a-z][a-z'-]*\b", text_lower)
            if len(word) >= 4
            and word not in {
                "about", "after", "also", "and", "because", "been", "before",
                "being", "both", "could", "does", "from", "have", "into",
                "just", "more", "most", "only", "other", "over", "same",
                "should", "some", "than", "that", "their", "them", "then",
                "there", "these", "they", "this", "through", "under", "very",
                "were", "what", "when", "where", "which", "while", "with",
                "would", "your",
            }
        ]
        word_frequencies = Counter(content_words)
        repeated_term_count = sum(
            count - 2 for count in word_frequencies.values() if count > 2
        )
        numeric_evidence_count = len(
            re.findall(r"\b\d+(?:\.\d+)?%?\b", text_lower)
        )
        source_marker_count = len(
            re.findall(
                r"\b(?:according to|records show|data show|survey|study|report|source|"
                r"measured|observed|research)\b",
                text_lower,
            )
        )
        example_marker_count = len(
            re.findall(r"\b(?:for example|e\.g\.|such as|case study|pilot)\b", text_lower)
        )
        transition_count = sum(
            len(re.findall(rf"\b{phrase}\b", text_lower))
            for phrase in ("therefore", "however", "because", "as a result", "consequently")
        )
        courtesy_count = sum(
            len(re.findall(rf"\b{phrase}\b", text_lower))
            for phrase in ("please", "thank you", "we appreciate", "could you", "would you")
        )
        hostile_count = sum(
            len(re.findall(phrase, text_lower))
            for phrase in (
                r"\byour concerns do not matter\b",
                r"\bstop asking questions\b",
                r"\bshut up\b",
                r"\byou do not matter\b",
            )
        )
        has_action = bool(re.search(
            r"\b(?:approve|schedule|measure|review|implement|roll out|next step|by \w+)\b",
            text_lower,
        ))
        audience_terms = (
            "students", "staff", "visitors", "customers", "patients", "residents",
            "teachers", "researchers", "families", "users", "community", "audience",
            "stakeholders", "employees", "members",
        )
        audience_signal_count = sum(
            len(re.findall(rf"\b{term}\b", text_lower)) for term in audience_terms
        )
        purpose_signal_count = sum(
            len(re.findall(rf"\b{term}\b", text_lower))
            for term in ("purpose", "goal", "objective", "aim", "recommendation", "decision")
        )

        sentence_parts = [
            part.strip()
            for part in re.split(r"(?<=[.!?])\s+", text.strip())
            if part.strip()
        ]
        if sentence_parts:
            capitalized_sentence_ratio = sum(
                part[0].isupper() for part in sentence_parts
            ) / len(sentence_parts)
            punctuated_sentence_ratio = sum(
                part[-1] in ".!?" for part in sentence_parts
            ) / len(sentence_parts)
        else:
            capitalized_sentence_ratio = 0.0
            punctuated_sentence_ratio = 0.0

        def _bounded_document_score(value: float) -> int:
            return max(0, min(100, round(value)))

        document_grammar_score = (
            computed_grammar_score
            if grammar_check_succeeded
            else min(
                70,
                45
                + round(10 * capitalized_sentence_ratio)
                + round(10 * punctuated_sentence_ratio),
            )
        )
        qualifier_count = len(
            re.findall(r"\b(?:approximately|estimated|may|might|could|appears|suggests)\b", text_lower)
        )
        absolute_claim_count = len(
            re.findall(r"\b(?:always|never|guarantees|proves|undeniably)\b", text_lower)
        )
        document_correct_score = _bounded_document_score(
            65 + min(20, qualifier_count * 5) - min(30, absolute_claim_count * 10)
        )
        clear_score = _bounded_document_score(
            55 + min(2, len(present_sections)) * 6 - min(30, vague_count * 3)
        )
        concise_score = _bounded_document_score(
            75 - min(30, repeated_term_count * 4) - min(30, vague_count * 2)
        )
        concrete_score = _bounded_document_score(
            35
            + min(6, numeric_evidence_count) * 5
            + min(2, example_marker_count) * 8
        )
        coherent_score = _bounded_document_score(
            50
            + min(3, len(present_sections)) * 7
            + min(4, transition_count) * 5
            - min(20, vague_count * 2)
            - min(20, repeated_term_count * 2)
        )
        complete_score = _bounded_document_score(
            35
            + (10 if "introduction" in present_sections else 0)
            + (10 if "evidence" in present_sections else 0)
            + (10 if "conclusion" in present_sections else 0)
            + (15 if has_action else 0)
            + min(15, numeric_evidence_count * 3)
        )
        courteous_score = _bounded_document_score(
            60 + min(2, courtesy_count) * 10 - min(3, hostile_count) * 15
        )

        seven_cs_scores = {
            "Clear": clear_score,
            "Concise": concise_score,
            "Concrete": concrete_score,
            "Correct": document_correct_score,
            "Coherent": coherent_score,
            "Complete": complete_score,
            "Courteous": courteous_score,
        }

        def _evidence_for(pattern: str, missing_description: str) -> str:
            match = next(
                (sentence for sentence in sentence_parts if re.search(pattern, sentence, re.IGNORECASE)),
                None,
            )
            if match:
                excerpt = match[:180].replace('"', "'")
                return f'"{excerpt}"'
            return f"No direct evidence: {missing_description}"

        def _fallback_evaluation(evidence: str, feedback: str) -> str:
            return f"Evidence: {evidence} Feedback: {feedback}"

        content_quality_score = _bounded_document_score(
            45
            + min(20, numeric_evidence_count * 4)
            + min(15, example_marker_count * 7)
            + min(10, len(set(content_words)) // 3)
            - min(20, vague_count * 3)
            - min(15, repeated_term_count * 2)
        )
        organization_score = _bounded_document_score(
            40
            + min(3, len(present_sections)) * 12
            + min(10, transition_count * 5)
            - min(15, repeated_term_count * 2)
        )
        audience_alignment_score = _bounded_document_score(
            45 + min(30, audience_signal_count * 8) - min(15, vague_count * 2)
        )
        purpose_fulfillment_score = _bounded_document_score(
            40
            + (25 if has_action else 0)
            + min(20, purpose_signal_count * 8)
            + (10 if "conclusion" in present_sections else 0)
        )
        evidence_support_score = _bounded_document_score(
            30
            + min(35, numeric_evidence_count * 7)
            + min(20, source_marker_count * 10)
            + min(15, example_marker_count * 7)
        )
        category_scores = {
            "Structure": organization_score,
            "Clarity": clear_score,
            "Persuasion": _bounded_document_score(
                (content_quality_score + purpose_fulfillment_score) / 2
            ),
            "Content_Quality": content_quality_score,
            "Call_to_Action": _bounded_document_score(
                35 + (45 if has_action else 0) + (10 if "conclusion" in present_sections else 0)
            ),
            "Grammar_and_Syntax": document_grammar_score,
            "Accuracy": evidence_support_score,
            "Tone_Appropriateness": courteous_score,
            "Audience_Alignment": audience_alignment_score,
            "Purpose_Fulfillment": purpose_fulfillment_score,
        }
        overall_score = _calculate_document_overall_score(
            seven_cs_scores, category_scores
        )

        top_topic_terms = [
            term for term, _count in word_frequencies.most_common(3)
        ]
        topic_summary = (
            ", ".join(top_topic_terms)
            if top_topic_terms
            else "The document topic is not clear from its text."
        )
        purpose_sentence = next(
            (
                sentence
                for sentence in sentence_parts
                if re.search(
                    r"\b(?:approve|schedule|measure|review|implement|roll out|"
                    r"recommend|goal|purpose|objective|aim)\b",
                    sentence,
                    re.IGNORECASE,
                )
            ),
            "No explicit purpose or decision request was detected.",
        )
        argument_sentences = [
            sentence for sentence in sentence_parts
            if re.search(
                r"\d|because|therefore|however|according to|records show|data show",
                sentence,
                re.IGNORECASE,
            )
        ][:3]
        if not argument_sentences:
            argument_sentences = sentence_parts[:3]
        coverage_gaps = []
        if "introduction" not in present_sections:
            coverage_gaps.append("No explicit introduction or overview marker was detected.")
        if "evidence" not in present_sections:
            coverage_gaps.append("No explicit findings, results, or source marker was detected.")
        if "conclusion" not in present_sections:
            coverage_gaps.append("No explicit conclusion or summary marker was detected.")
        if not has_action:
            coverage_gaps.append("No explicit next step or decision request was detected.")
        context_changes = []
        if not audience_signal_count:
            context_changes.append("Name the intended audience and connect the proposal to its needs.")
        if not purpose_signal_count and not has_action:
            context_changes.append("State the document's purpose and the decision or outcome it seeks.")
        if not source_marker_count and not numeric_evidence_count:
            context_changes.append("Support key claims with attributable sources or measurable evidence.")
        factual_summary = (
            "External factual accuracy was not independently verified; the Accuracy category "
            "is an evidence/support estimate only. "
            f"The fallback inferred recurring topic terms: {topic_summary}."
        )
        seven_cs_eval = {
            "Clear": _fallback_evaluation(
                _evidence_for(
                    r"\b(?:introduction|overview|agenda|conclusion|summary)\b",
                    "no explicit organization heading was found.",
                ),
                (
                    "Replace vague terms with specific referents and state the main point directly."
                    if vague_count else
                    "Keep the identified section markers and make the document's main message explicit."
                ),
            ),
            "Concise": _fallback_evaluation(
                _evidence_for(
                    r"\b(?:thing|things|some|several|various|maybe)\b",
                    "no repeated or vague wording marker was found.",
                ),
                (
                    "Remove repeated terms and replace vague wording with shorter, precise phrasing."
                    if repeated_term_count or vague_count else
                    "Review each sentence for words that do not add meaning."
                ),
            ),
            "Concrete": _fallback_evaluation(
                _evidence_for(
                    r"\d|for example|such as|case study|records show|according to",
                    "no measurable example or attributed support was detected.",
                ),
                (
                    "Add a measurable example and identify its source; the figures are not fact-checked."
                    if not numeric_evidence_count and not source_marker_count else
                    "Explain what the cited measure or example demonstrates and verify it against its source."
                ),
            ),
            "Correct": _fallback_evaluation(
                _evidence_for(
                    r"\b(?:approximately|estimated|may|might|could|appears|suggests|always|never|guarantees|proves)\b",
                    "no explicit claim qualification or internal consistency evidence was detected.",
                ),
                "Check internal consistency and attribute or qualify claims; this analysis does not verify grammar correctness or external facts."
            ),
            "Coherent": _fallback_evaluation(
                _evidence_for(
                    r"\b(?:therefore|however|because|as a result|consequently|introduction|findings|conclusion)\b",
                    "no explicit transition or section sequence was detected.",
                ),
                (
                    "Add transitions that explain how each finding supports the conclusion."
                    if not transition_count else
                    "Make each transition's relationship between the claim and next point explicit."
                ),
            ),
            "Complete": _fallback_evaluation(
                _evidence_for(
                    r"\b(?:introduction|overview|findings|results|conclusion|summary|approve|schedule|review|next step)\b",
                    "no introduction, evidence section, conclusion, or next step was detected.",
                ),
                (
                    "Add the missing purpose, supporting findings, conclusion, and a specific next step."
                    if coverage_gaps else
                    "Confirm the conclusion answers the purpose and preserves the stated next step."
                ),
            ),
            "Courteous": _fallback_evaluation(
                _evidence_for(
                    r"\b(?:please|thank you|we appreciate|could you|would you|your concerns do not matter|stop asking questions|shut up)\b",
                    "no explicit courtesy or dismissive wording marker was detected.",
                ),
                (
                    "Replace dismissive wording with a respectful, audience-appropriate request."
                    if hostile_count else
                    "Keep requests respectful and name the audience's concerns where relevant."
                ),
            ),
        }

        # ===== TEXT-DERIVED CONTEXT, FEEDBACK, AND FALLBACK ESTIMATE =====
        dynamic_strengths = []
        if present_sections:
            dynamic_strengths.append(
                f"Detected document sections: {', '.join(present_sections)}."
            )
        if numeric_evidence_count or source_marker_count:
            dynamic_strengths.append(
                f"Found {numeric_evidence_count} numeric detail(s) and "
                f"{source_marker_count} source/measurement signal(s)."
            )
        if has_action:
            dynamic_strengths.append(f"Identified a next-step signal: {purpose_sentence}")
        if not dynamic_strengths:
            dynamic_strengths.append(
                f"Extracted {word_count} words; recurring topic terms are {topic_summary}."
            )

        dynamic_recs = []
        if len(present_sections) < 3:
            dynamic_recs.append(
                "Add clear opening, evidence/findings, and conclusion headings; "
                f"detected sections: {', '.join(present_sections) or 'none'}."
            )
        if not numeric_evidence_count and not source_marker_count:
            dynamic_recs.append(
                "Support the main claim with a measurable example and name the source."
            )
        if not audience_signal_count:
            dynamic_recs.append(
                "Name the intended audience and explain which of its needs the document addresses."
            )
        if not has_action:
            dynamic_recs.append(
                "State the decision or next step sought and assign a clear owner or timeframe."
            )
        if vague_count or repeated_term_count:
            dynamic_recs.append(
                f"Replace vague wording ({vague_count} signal(s)) and reduce repeated terms "
                f"({repeated_term_count} signal(s)) with precise descriptions."
            )
        if not grammar_check_succeeded:
            dynamic_recs.append(
                "Run a grammar check before submission; grammar was not verified in this analysis."
            )
        elif grammar_issues:
            dynamic_recs.append(
                f"Review the {len(grammar_issues)} LanguageTool issue(s) before submission."
            )
        if not dynamic_recs:
            dynamic_recs.append(
                "Verify the cited figures against their original sources before presenting."
            )
        dynamic_recs = dynamic_recs[:5]

        audience_summary = (
            f"Audience references detected ({audience_signal_count} signal(s))."
            if audience_signal_count
            else "The intended audience is not explicit in the text."
        )
        rubric_summary = (
            "Weighted heuristic estimate: content/relevance 25%, seven Cs 35%, "
            "organization 15%, grammar/language 10%, audience/purpose 10%, "
            "evidence/support 5%."
        )
        detailed_feedback = (
            f"DOCUMENT CONTEXT (heuristic): recurring topic terms are {topic_summary}. "
            f"Purpose signal: {purpose_sentence} {audience_summary} "
            f"Key text evidence: {' '.join(argument_sentences) or 'No argument sentence was detected.'}\n\n"
            f"QUALITY ESTIMATE: {overall_score}/100. {rubric_summary} "
            f"Detected {len(present_sections)} organization markers, "
            f"{numeric_evidence_count} numeric details, {source_marker_count} source signals, "
            f"{vague_count} vague-wording signals, and {repeated_term_count} repeated-term signals. "
            f"{grammar_unavailable_message if not grammar_check_succeeded else grammar_quality_summary}\n\n"
            f"LIMITATION: This rule-based estimate is not a verified semantic or factual assessment. "
            "External factual accuracy was not checked; consult the dimension feedback and "
            f"recommendations. Priority actions: {' '.join(dynamic_recs[:3])}"
        )

        fallback_json = {
            "overall_score": overall_score,
            "evaluation_method": "heuristic_fallback",
            "score_rubric": _document_rubric_breakdown(seven_cs_scores, category_scores),
            "document_name": filename,
            "category_scores": category_scores,
            "context_analysis": {
                "context_accuracy_score": None,
                "is_context_accurate": None,
                "topic": topic_summary,
                "purpose": purpose_sentence,
                "intended_audience": audience_summary,
                "major_arguments": argument_sentences,
                "coverage_gaps": coverage_gaps,
                "factual_correctness_summary": factual_summary,
                "inaccuracies_detected": [],
                "context_based_changes": context_changes + [
                    "Verify factual claims and cited figures against reliable sources."
                ],
            },
            "seven_cs_evaluation": seven_cs_eval,
            "seven_cs_scores": seven_cs_scores,
            "strengths": dynamic_strengths,
            "recommendations": dynamic_recs,
            "detailed_feedback": detailed_feedback,
            "improved_text": f"# {os.path.splitext(filename)[0].upper()} (IMPROVED VERSION)\n\n{text}\n\n---\n*Note: Polished for conciseness and style by the AI assistant.*",
            # Grammar enrichment
            "grammar_score": computed_grammar_score if grammar_check_succeeded else None,
            "grammar_check_status": grammar_check_status,
            "grammar_grade": _grammar_grade(computed_grammar_score) if grammar_check_succeeded else None,
            "grammar_summary_short": (
                _grammar_summary_short(computed_grammar_score, grammar_issues)
                if grammar_check_succeeded else grammar_unavailable_message
            ),
            "grammar_issues": grammar_issues,
            "grammar_issues_count": len(grammar_issues),
        }
        
    elif module_type == 'speech':
        fallback_json = {"evaluation_method": "unavailable"}
        
    elif module_type == 'live':
        avg_eye = context_metrics.get("avg_eye", 0)
        avg_posture = context_metrics.get("avg_posture", 0)
        avg_wpm = context_metrics.get("avg_wpm", 0)
        fillers = context_metrics.get("fillers", 0)
        avg_qna = context_metrics.get("avg_qna", 0)
        interruptions = context_metrics.get("interruptions", [])
        has_visual_metrics = context_metrics.get("has_visual_metrics", False)
        has_gaze_metrics = context_metrics.get("has_gaze_metrics", has_visual_metrics)
        has_posture_metrics = context_metrics.get("has_posture_metrics", has_visual_metrics)
        has_voice_metrics = context_metrics.get("has_voice_metrics", False)
        has_qna_scores = context_metrics.get("has_qna_scores", False)
        
        overall_score = context_metrics.get("overall_execution", 0)
        if overall_score == 0 and (has_visual_metrics or has_voice_metrics or has_qna_scores):
            sub_scores = []
            if has_gaze_metrics:
                sub_scores.append(avg_eye)
            if has_posture_metrics:
                sub_scores.append(avg_posture)
            if has_voice_metrics:
                wpm_score = min(100, max(20, 100 - (fillers * 4) - abs(avg_wpm - 140) // 2))
                sub_scores.append(wpm_score)
            if has_qna_scores:
                sub_scores.append(avg_qna)
            if sub_scores:
                overall_score = int(sum(sub_scores) / len(sub_scores))

        insufficient_live_data = not has_visual_metrics and not has_voice_metrics and not has_qna_scores and overall_score == 0
        
        strengths_list = []
        recs_list = []
        
        if has_gaze_metrics and avg_eye >= 80:
            strengths_list.append(f"Camera-facing gaze estimate was high ({avg_eye}%).")
        elif has_gaze_metrics:
            recs_list.append(f"Camera-facing gaze estimate was {avg_eye}%; look toward the camera when appropriate.")

        if has_posture_metrics and avg_posture >= 80:
            strengths_list.append(f"Upper-body posture estimate was high ({avg_posture}%).")
        elif has_posture_metrics:
            recs_list.append(f"Upper-body posture estimate was {avg_posture}%; adjust alignment if comfortable.")
            
        if has_voice_metrics and fillers == 0:
            strengths_list.append("Excellent vocal control with zero verbal filler words used.")
        elif has_voice_metrics and fillers <= 3:
            strengths_list.append(f"Detected minimal filler words ({fillers}).")
        elif has_voice_metrics:
            recs_list.append(f"Reduce filler word frequency (detected {fillers} instances of 'um', 'like', or 'you know').")
            
        if has_voice_metrics and 120 <= avg_wpm <= 160:
            strengths_list.append(f"Speaking pace was within the 120-160 WPM reference range ({avg_wpm} WPM).")
        elif has_voice_metrics and avg_wpm > 160:
            recs_list.append(f"Speaking pace averaged {avg_wpm} WPM, above the 120-160 WPM reference range; consider adding pauses.")
        elif has_voice_metrics:
            recs_list.append(f"Speaking pace averaged {avg_wpm} WPM, below the 120-160 WPM reference range.")
            
        num_interruptions = len(interruptions)
        if num_interruptions == 0:
            qna_feedback = "No panelist interruptions occurred during this session."
        else:
            if not has_qna_scores:
                qna_feedback = "A panelist question was recorded, but no automated Q&A score was available."
            elif avg_qna >= 80:
                strengths_list.append(f"Achieved a high academic Q&A score ({avg_qna}%).")
                qna_feedback = "Handled interruptions reasonably well; answers directly addressed the examiner's queries."
            else:
                recs_list.append("Formulate more structured answers when interrupted by academic panel questions.")
                qna_feedback = f"Academic Q&A score: {avg_qna}%; the response could address the question more directly."
                
        if not num_interruptions:
            complete_feedback = "No panelist interruptions occurred, so topic completeness was not directly assessed."
        elif not has_qna_scores:
            complete_feedback = "A panelist question was recorded, but no automated completeness score was available."
        else:
            complete_feedback = "Panelist responses were included in the Q&A score."

        seven_cs_eval = {
            "Clear": "Clarity was not estimated by the local fallback." if has_voice_metrics else "Not scored — no valid microphone transcript was captured.",
            "Concise": (f"Speaking pace averaged {avg_wpm} WPM with {fillers} filler words detected." if has_voice_metrics else "Not scored — no valid microphone transcript was captured."),
            "Correct": (
                f"LanguageTool grammar score: {computed_grammar_score}/100."
                if grammar_check_succeeded
                else "Grammar check was unavailable; correctness was not scored."
            ),
            "Complete": complete_feedback,
            "Courteous": "Courtesy was not estimated by the local fallback.",
            "Concrete": f"Q&A score was {avg_qna}%." if has_qna_scores else "Not scored — no graded Q&A evidence was available.",
            "Consistent": "Not scored — temporal gaze/posture consistency is not calculated."
        }
        
        if insufficient_live_data:
            fallback_json = {
                "overall_score": 0,
                "category_scores": {
                    "Structure": None, "Clarity": None, "Persuasion": None,
                    "Content_Quality": None, "Call_to_Action": None,
                    "Grammar_and_Syntax": None, "Accuracy": None,
                    "Tone_Appropriateness": None, "Audience_Alignment": None,
                    "Purpose_Fulfillment": None
                },
                "seven_cs_evaluation": {
                    c: "Not scored — no camera or microphone data was captured for this session."
                    for c in ["Clear", "Concise", "Correct", "Complete", "Courteous", "Concrete", "Consistent"]
                },
                "seven_cs_scores": {
                    c: None for c in ["Clear", "Concise", "Correct", "Complete", "Courteous", "Concrete", "Consistent"]
                },
                "strengths": [],
                "recommendations": [],
                "qna_analysis": "No panelist interruptions occurred during this session.",
                "detailed_feedback": "No usable video or audio data was captured during this session, so no delivery score could be generated. Check your camera and microphone permissions and try again.",
                "improved_text": "No speech was captured during this session."
            }
        else:
            # Full structured fallback with verified ratings from captured telemetry
            clear_score = None
            concise_score = max(25, min(95, 100 - (fillers * 6) - abs(avg_wpm - 140) // 2)) if has_voice_metrics else None
            correct_score = computed_grammar_score if grammar_check_succeeded else None
            complete_score = avg_qna if has_qna_scores else None
            courteous_score = None
            concrete_score = avg_qna if has_qna_scores else None
            consistent_score = None

            fallback_json = {
                "overall_score": overall_score,
                "category_scores": {
                    "Structure": None,
                    "Clarity": clear_score,
                    "Persuasion": avg_qna if has_qna_scores else None,
                    "Content_Quality": avg_qna if has_qna_scores else None,
                    "Call_to_Action": None,
                    "Grammar_and_Syntax": computed_grammar_score if grammar_check_succeeded else None,
                    "Accuracy": avg_qna if has_qna_scores else None,
                    "Tone_Appropriateness": courteous_score,
                    "Audience_Alignment": None,
                    "Purpose_Fulfillment": None
                },
                "seven_cs_evaluation": seven_cs_eval,
                "seven_cs_scores": {
                    "Clear": clear_score,
                    "Concise": concise_score,
                    "Correct": correct_score,
                    "Complete": complete_score,
                    "Courteous": courteous_score,
                    "Concrete": concrete_score,
                    "Consistent": consistent_score
                },
                "strengths": strengths_list,
                "recommendations": recs_list,
                "qna_analysis": qna_feedback,
                "detailed_feedback": (
                    f"Live presentation analysis for topic '{context_metrics.get('topic', 'Live Practice')}': "
                    f"Overall score achieved is {overall_score}/100. "
                    f"{'Approximate camera-facing gaze averaged ' + str(avg_eye) + '%. ' if has_gaze_metrics else ''}"
                    f"{'Upper-body posture estimate averaged ' + str(avg_posture) + '%. ' if has_posture_metrics else ''}"
                    f"{'Speaking rate averaged ' + str(avg_wpm) + ' WPM with ' + str(fillers) + ' filler words detected. ' if has_voice_metrics else ''}"
                    f"{'Academic panelist Q&A score was ' + str(avg_qna) + '%. ' if has_qna_scores else ''}"
                    "Unavailable or unmeasured dimensions are not scored."
                ),
                "improved_text": text if text and text != "No content provided." else ""
            }

    # ===== BUILD DYNAMIC PROMPT FOR GEMINI =====
    analysis_prompt = ""
    if module_type == 'document':
        filename = context_metrics.get("filename", "presentation.pdf")
        if grammar_check_succeeded:
            grammar_section = f"""

--- GRAMMAR PRE-ANALYSIS (LanguageTool Cloud) ---
Grammar Quality Score: {computed_grammar_score}/100
Total Grammar/Spelling Issues Detected: {len(grammar_issues)}
{grammar_summary_text if grammar_summary_text else 'No grammar issues detected — text appears grammatically clean.'}
--- END GRAMMAR PRE-ANALYSIS ---
""" if grammar_issues or computed_grammar_score < 100 else ""
            grammar_prompt_guidance = (
                f"A LanguageTool grammar pre-analysis completed with score "
                f"{computed_grammar_score}/100. Use it to calibrate grammar scores."
            )
            grammar_prompt_score = str(computed_grammar_score)
            grammar_prompt_evaluation = (
                f"reference the {len(grammar_issues)} grammar issues found by LanguageTool"
            )
            grammar_prompt_feedback = f"mention the verified grammar score of {computed_grammar_score}/100"
        else:
            grammar_section = f"""

--- GRAMMAR PRE-ANALYSIS (LanguageTool Cloud) ---
Status: {grammar_check_status}. Grammar was not verified; no issue count or score is available.
Do not treat the absence of grammar matches as evidence of error-free text.
--- END GRAMMAR PRE-ANALYSIS ---
"""
            grammar_prompt_guidance = (
                "LanguageTool was unavailable, so grammar was not verified. "
                "Assess grammar directly from the document text without treating missing check results as error-free."
            )
            grammar_prompt_score = "<score 0-100 based on grammar visible in the document>"
            grammar_prompt_evaluation = (
                "assess grammar from the document text and do not claim a LanguageTool check was completed"
            )
            grammar_prompt_feedback = (
                "state that LanguageTool was unavailable and grammar was not verified"
            )

        analysis_prompt = f"""
You are an evidence-grounded document and presentation evaluator. Analyze the provided text only.
Use its actual topic, stated purpose, intended audience, arguments, coverage, examples, and language.
Do not invent document details, citations, audience requirements, or facts. Give each canonical 7C an
independent score; do not copy, average, or apply a shared offset to all seven dimensions.

GRAMMAR: {grammar_prompt_guidance}{grammar_section}
If LanguageTool is unavailable, state that grammar is unverified. Do not describe that as a perfect
grammar result. Evaluate visible language cautiously and distinguish this estimate from a completed
grammar check.

CONTEXT AND FACTUAL LIMITS:
- Infer topic, purpose, intended audience, major arguments, and concrete coverage gaps from the text.
- External factual accuracy has NOT been independently verified. Set context_accuracy_score and
  is_context_accurate to null. Do not state that external claims are true or false.
- You may identify an internal contradiction only when quoting the conflicting document text.
- Category Accuracy means traceability/quality of evidence and support in the document, not a
  verification of external facts.

WEIGHTED OVERALL RUBRIC (the server recomputes the final overall_score):
- Content quality and relevance: 25%, use category_scores.Content_Quality.
- Seven Cs: 35%, equal mean of the seven independent seven_cs_scores.
- Organization and structure: 15%, use category_scores.Structure.
- Grammar and language: 10%, use category_scores.Grammar_and_Syntax.
- Audience and purpose alignment: 10%, mean of Audience_Alignment and Purpose_Fulfillment.
- Evidence and support: 5%, use category_scores.Accuracy as traceability/support only.
Category Clarity, Persuasion, Call_to_Action, and Tone_Appropriateness remain useful diagnostics
for existing charts; do not add them again to the weighted overall score.

For every 7C evaluation, return one string in this exact form:
Evidence: <an exact quotation of at least 12 characters from the source, in double quotes, or
"No direct evidence: <specific absent criterion>"> Feedback: <criterion-based assessment and a
specific actionable improvement when its score is below 65>.
The quoted evidence must be copied verbatim from the document. Never fabricate a quotation.

Return one valid JSON object with this schema and all required keys:
{{
  "overall_score": <finite number 0-100; estimate using the weighted rubric>,
  "document_name": "{filename}",
  "category_scores": {{
    "Structure": <0-100 organization and logical flow>,
    "Clarity": <0-100 message and wording clarity>,
    "Persuasion": <0-100 quality of the argument>,
    "Content_Quality": <0-100 relevance, depth, and coverage>,
    "Call_to_Action": <0-100 clarity of requested outcome or next step>,
    "Grammar_and_Syntax": {grammar_prompt_score},
    "Accuracy": <0-100 evidence/support traceability, not external fact verification>,
    "Tone_Appropriateness": <0-100 professional and respectful tone>,
    "Audience_Alignment": <0-100 fit to the audience actually indicated>,
    "Purpose_Fulfillment": <0-100 fit to the stated or inferable purpose>
  }},
  "context_analysis": {{
    "topic": "<topic inferred from the source, or state it is unclear>",
    "purpose": "<stated/inferred purpose, or state it is not explicit>",
    "intended_audience": "<audience indicated by the source, or state it is not explicit>",
    "major_arguments": ["<source-grounded argument; [] if none>"],
    "coverage_gaps": ["<specific missing coverage relative to the stated purpose; [] if none>"],
    "context_accuracy_score": null,
    "is_context_accurate": null,
    "factual_correctness_summary": "<state that external facts were not independently verified>",
    "inaccuracies_detected": ["<quoted internal contradiction only; [] if none>"],
    "context_based_changes": ["<specific action grounded in a coverage gap; [] if none>"]
  }},
  "seven_cs_evaluation": {{
    "Clear": "<Evidence: ... Feedback: ...>",
    "Concise": "<Evidence: ... Feedback: ...>",
    "Concrete": "<Evidence: ... Feedback: ...>",
    "Correct": "<Evidence: ... Feedback: ...; internal consistency only, not grammar/fact verification>",
    "Coherent": "<Evidence: ... Feedback: ...>",
    "Complete": "<Evidence: ... Feedback: ...>",
    "Courteous": "<Evidence: ... Feedback: ...>"
  }},
  "seven_cs_scores": {{
    "Clear": <0-100 clarity>,
    "Concise": <0-100 conciseness>,
    "Concrete": <0-100 specificity of examples/details>,
    "Correct": <0-100 internal consistency and careful claim framing only>,
    "Coherent": <0-100 connections and progression>,
    "Complete": <0-100 coverage of purpose and required components>,
    "Courteous": <0-100 respectfulness and audience-appropriate wording>
  }},
  "strengths": ["<specific source-backed strength>"],
  "recommendations": [
    "<actionable step grounded in a weak dimension and quoted document evidence>",
    "<second actionable step grounded in evidence>",
    "<third actionable step grounded in evidence>"
  ],
  "detailed_feedback": "<context-aware analysis, score limitations, evidence, and priorities; {grammar_prompt_feedback}>",
  "improved_text": "<professional revision of the document that does not add unsupported facts>"
}}

Original document text:
{text}
"""
        
    elif module_type == 'speech':
        speech_speed_wpm = context_metrics.get("speech_speed_wpm", 130)
        filler_count = context_metrics.get("filler_count", 0)
        filler_percentage = context_metrics.get("filler_percentage", 0.0)
        repetition_count = context_metrics.get("repetition_count", 0)
        duration_seconds = context_metrics.get("duration_seconds", 0)

        if grammar_check_succeeded:
            grammar_section = f"""

--- GRAMMAR PRE-ANALYSIS (LanguageTool Cloud) ---
Grammar Quality Score: {computed_grammar_score}/100
Total Grammar/Spelling Issues Detected: {len(grammar_issues)}
{grammar_summary_text if grammar_summary_text else 'No grammar issues detected.'}
--- END GRAMMAR PRE-ANALYSIS ---
"""
            grammar_prompt_guidance = (
                f"LanguageTool successfully checked the transcript with a score of "
                f"{computed_grammar_score}/100; use its {len(grammar_issues)} issue(s) "
                "to assess Correct."
            )
            grammar_prompt_score = str(computed_grammar_score)
            grammar_prompt_evaluation = (
                f"reference the {len(grammar_issues)} LanguageTool issue(s) and its "
                f"{computed_grammar_score}/100 score"
            )
            grammar_prompt_feedback = (
                f"mention the verified LanguageTool score of {computed_grammar_score}/100"
            )
        else:
            grammar_section = f"""

--- GRAMMAR PRE-ANALYSIS (LanguageTool Cloud) ---
Status: {grammar_check_status}. Grammar was not verified; no issue count or score is available.
--- END GRAMMAR PRE-ANALYSIS ---
"""
            grammar_prompt_guidance = (
                f"LanguageTool status is {grammar_check_status}; grammar was not verified. "
                "Do not treat an unavailable check as a perfect grammar result."
            )
            grammar_prompt_score = (
                "<score 0-100 from visible transcript only; do not claim grammar was verified>"
            )
            grammar_prompt_evaluation = (
                "assess only visible transcript grammar and explicitly state LanguageTool was unavailable"
            )
            grammar_prompt_feedback = (
                "state that grammar is unverified because LanguageTool was unavailable"
            )

        analysis_prompt = f"""
You are an expert presentation coach and public speaking/communication consultant. Your job is to analyze the following speech transcript, identify all errors, grammatical mistakes, verbal pacing issues, and assess how well it adheres to the 7 Cs of Communication (Clear, Concise, Correct, Complete, Courteous, Concrete, Consistent).

IMPORTANT: {grammar_prompt_guidance}{grammar_section}
TRANSCRIPT RELIABILITY: This transcript comes from automatic speech recognition (ASR) and may contain recognition errors (wrong or odd words). Do NOT blame the speaker for words that look like ASR mistakes, and do NOT invent or assume what the speaker meant. Judge only delivery: pacing, fillers, structure, and clarity of ideas. Never introduce technical terms or product details the speaker did not say.

We have also calculated the following real-time speaking metrics from the audio recording for your context:
- Speaking pace: {speech_speed_wpm} WPM (optimal: 120-160 WPM)
- Filler words count: {filler_count} instances (filler percentage: {filler_percentage:.1f}%)
- Word repetition count: {repetition_count} instances
- Speaking duration: {duration_seconds} seconds

Using these metrics and the transcript text, generate a comprehensive report in JSON format.
Your response MUST be a single, valid JSON object matching the schema below. Do not wrap the JSON in markdown code blocks (e.g. ```json).

JSON Schema:
{{
  "overall_score": <integer 0-100 based on the transcript and delivery metrics>,
  "category_scores": {{
    "Structure": <score 0-100 based on structure, organization of thoughts, and presence of introductory/concluding markers>,
    "Clarity": <score 0-100 based on ease of understanding, vocabulary choice, and vocal clarity indicated by pacing>,
    "Persuasion": <score 0-100 based on rhetorical appeal, conviction, and engagement level>,
    "Content_Quality": <score 0-100 based on argument depth, coherence, and logical connection of ideas>,
    "Call_to_Action": <score 0-100 based on presence of a clear concluding message, summary, or next steps>
  }},
  "seven_cs_evaluation": {{
    "Clear": "<1-2 sentence evaluation on the clarity of the speech message>",
    "Concise": "<1-2 sentence evaluation on wordiness, pacing, and filler word usage>",
    "Correct": "<1-2 sentence evaluation on grammatical accuracy — {grammar_prompt_evaluation}>",
    "Complete": "<1-2 sentence evaluation on whether the main point was thoroughly covered in the given duration>",
    "Courteous": "<1-2 sentence evaluation on professional tone, respectfulness, and reader-suitability>",
    "Concrete": "<1-2 sentence evaluation on focus, usage of specific details, data, or illustrative points>",
    "Consistent": "<1-2 sentence evaluation on uniformity of tone, phrasing, and speed consistency>"
  }},
  "seven_cs_scores": {{
    "Clear": <integer 0-100 based on clarity evaluation>,
    "Concise": <integer 0-100 based on conciseness evaluation>,
    "Correct": {grammar_prompt_score},
    "Complete": <integer 0-100 based on completeness evaluation>,
    "Courteous": <integer 0-100 based on courteousness evaluation>,
    "Concrete": <integer 0-100 based on concreteness evaluation>,
    "Consistent": <integer 0-100 based on consistency evaluation>
  }},
  "strengths": [
    "<detailed strength 1 with transcript reference>",
    "<detailed strength 2 with transcript reference>",
    "<detailed strength 3 with transcript reference>"
  ],
  "recommendations": [
    "<actionable recommendations/corrections 1 with specific detail>",
    "<actionable recommendations/corrections 2 with specific detail>",
    "<actionable recommendations/corrections 3 with specific detail>"
  ],
  "detailed_feedback": "<2-3 paragraph comprehensive analysis pointing out transcript-specific vocal flaws, {grammar_prompt_feedback}, pacing problems, and recommendations>",
  "improved_text": "<The ORIGINAL transcript with ONLY these changes: remove filler words (um, uh, like, you know, basically, actually, so) and back-to-back repeated words, and fix punctuation and capitalization. STRICT RULES: do NOT add, replace, or reorder any words. Do NOT add headings, lists, or new sentences. Do NOT improve vocabulary. If something is unclear, keep it exactly as spoken. Output length must be about the same as the original minus the fillers.>"
}}

Original Speech Transcript to Analyze:
{text}
"""
        
    elif module_type == 'live' and not insufficient_live_data:
        topic = context_metrics.get("topic", "General Topic")
        avg_eye = context_metrics.get("avg_eye", 0)
        avg_posture = context_metrics.get("avg_posture", 0)
        avg_wpm = context_metrics.get("avg_wpm", 0)
        fillers = context_metrics.get("fillers", 0)
        avg_qna = context_metrics.get("avg_qna", 0)
        overall_score = context_metrics.get("overall_execution", 0)

        if grammar_check_succeeded:
            grammar_section = f"""

--- GRAMMAR PRE-ANALYSIS (LanguageTool Cloud) ---
Grammar Quality Score: {computed_grammar_score}/100
Total Grammar/Spelling Issues Detected: {len(grammar_issues)}
{grammar_summary_text if grammar_summary_text else 'No grammar issues detected — transcript appears grammatically clean.'}
--- END GRAMMAR PRE-ANALYSIS ---
"""
            correct_score = str(computed_grammar_score)
        else:
            grammar_section = f"""

--- GRAMMAR PRE-ANALYSIS (LanguageTool Cloud) ---
Status: {grammar_check_status}. Grammar was not verified. Do not assign a Correct score.
--- END GRAMMAR PRE-ANALYSIS ---
"""
            correct_score = "null"

        has_gaze_metrics = context_metrics.get("has_gaze_metrics", False)
        has_posture_metrics = context_metrics.get("has_posture_metrics", False)
        gaze_evidence = f"{avg_eye}/100 (approximate camera-facing estimate)" if has_gaze_metrics else "N/A"
        posture_evidence = f"{avg_posture}/100 (upper-body landmark estimate)" if has_posture_metrics else "N/A"
        voice_evidence = f"{avg_wpm} WPM; {fillers} filler words" if context_metrics.get("has_voice_metrics", False) else "N/A"
        qna_evidence = f"{avg_qna}/100" if context_metrics.get("has_qna_scores", False) else "N/A"

        analysis_prompt = f"""
Analyze only the evidence actually available in this live presentation. Return null for dimensions that cannot be supported by the evidence. Do not infer psychological confidence, personality, or emotion from facial appearance or posture. Camera-facing gaze is an approximate estimate, not verified eye contact. Do not treat posture as evidence of courtesy or consistency; temporal consistency is not measured.{grammar_section}
Presentation Context:
- Topic: {topic}
- Full Transcript: {text}
- Approximate camera-facing gaze: {gaze_evidence}
- Upper-body posture estimate: {posture_evidence}
- Voice metrics: {voice_evidence}
- Graded Q&A evidence: {qna_evidence}

Generate a comprehensive report in JSON format matching the schema below. Do not wrap in markdown tags.

JSON Schema:
{{
    "overall_score": {overall_score},
    "category_scores": {{
        "Structure": <score 0-100 based on transcript evidence, or null>,
        "Clarity": <score 0-100 based on transcript evidence, or null>,
        "Persuasion": <score 0-100 based on transcript/Q&A evidence, or null>,
        "Content_Quality": <score 0-100 based on transcript/Q&A evidence, or null>,
        "Call_to_Action": <score 0-100 based on transcript evidence, or null>
    }},
    "seven_cs_evaluation": {{
        "Clear": "<evaluation based on transcript, or state insufficient evidence>",
        "Concise": "<evaluation based on transcript/voice evidence, or state insufficient evidence>",
        "Correct": "<evaluation based on the verified grammar result, or state grammar was not verified>",
        "Complete": "<evaluation based on transcript or graded Q&A evidence, or state insufficient evidence>",
        "Courteous": "<evaluation based only on respectful language in the transcript, or state insufficient evidence>",
        "Concrete": "<evaluation based on specific transcript/Q&A evidence, or state insufficient evidence>",
        "Consistent": "Not scored — temporal gaze/posture consistency is not calculated."
    }},
    "seven_cs_scores": {{
        "Clear": <score 0-100 or null>,
        "Concise": <score 0-100 or null>,
        "Correct": {correct_score},
        "Complete": <score 0-100 or null>,
        "Courteous": <score 0-100 or null>,
        "Concrete": <score 0-100 or null>,
        "Consistent": null
    }},
    "strengths": ["<specific strength supported by available evidence>"],
    "recommendations": ["<specific improvement supported by an observed issue>"],
    "qna_analysis": "<evaluation of graded answers, or state that no graded Q&A evidence was available>",
    "improved_text": "<rewrite only the captured transcript; do not invent speech>"
}}
"""

    # ===== RUN GEMINI INVOCATION =====
    if _gemini_provider.is_available() and analysis_prompt:
        try:
            analysis_json = _gemini_provider.generate_structured(analysis_prompt)
            if not isinstance(analysis_json, dict) or not analysis_json:
                raise ValueError("Gemini response is not a valid JSON dictionary")

            if module_type == 'document':
                for field in ('seven_cs_scores', 'seven_cs_evaluation'):
                    values = analysis_json.get(field)
                    if isinstance(values, dict) and 'Consistent' in values:
                        values.setdefault('Coherent', values['Consistent'])
                        values.pop('Consistent')
                _validate_document_gemini_scores(analysis_json, text)

            # Ensure document_name is set for document analysis
            if module_type == 'document' and 'document_name' not in analysis_json:
                analysis_json['document_name'] = context_metrics.get("filename", "presentation.pdf")

            if module_type == 'speech':
                orig_words = len(text.split())
                new_words = len(str(analysis_json.get('improved_text', '')).split())
                if new_words > orig_words * 1.1 or new_words < orig_words * 0.5:
                    analysis_json['evaluation_method'] = 'unavailable'

            # ===== ATTACH GRAMMAR DATA TO ALL MODULE RESPONSES =====
            # grammar_score and grammar_issues are always attached so the
            # frontend can show a grammar breakdown for every analysis type.
            analysis_json['grammar_score']  = (
                computed_grammar_score
                if module_type not in {'document', 'speech', 'live'} or grammar_check_succeeded
                else None
            )
            analysis_json['grammar_issues'] = grammar_issues
            analysis_json['grammar_issues_count'] = len(grammar_issues)
            if module_type in {'document', 'speech', 'live'}:
                analysis_json['grammar_check_status'] = grammar_check_status
                if not grammar_check_succeeded and module_type in {'document', 'speech'}:
                    feedback = analysis_json.get('detailed_feedback')
                    if not isinstance(feedback, str):
                        feedback = ''
                    if grammar_unavailable_message not in feedback:
                        feedback = f"{feedback.rstrip()}\n\n{grammar_unavailable_message}".strip()
                    analysis_json['detailed_feedback'] = feedback
            # Keep document "Correct" focused on internal consistency; grammar is
            # represented separately in Grammar_and_Syntax to avoid double-counting.
            if module_type not in {'document', 'speech', 'live'} or grammar_check_succeeded:
                if 'seven_cs_scores' in analysis_json:
                    if module_type != 'document':
                        analysis_json['seven_cs_scores']['Correct'] = computed_grammar_score
                if 'category_scores' in analysis_json and 'Grammar_and_Syntax' in analysis_json['category_scores']:
                    analysis_json['category_scores']['Grammar_and_Syntax'] = computed_grammar_score
            if module_type == 'document':
                analysis_json['overall_score'] = _calculate_document_overall_score(
                    analysis_json['seven_cs_scores'],
                    analysis_json['category_scores'],
                )
                analysis_json['evaluation_method'] = 'gemini'
                analysis_json['score_rubric'] = _document_rubric_breakdown(
                    analysis_json['seven_cs_scores'],
                    analysis_json['category_scores'],
                )

            return analysis_json
        except Exception as e:
            logger.warning("Global AI Evaluator: Gemini call failed (%s). Falling back to smart heuristics.", e)
            # Attach grammar data to fallback — all module types
            fallback_json['grammar_score']  = (
                computed_grammar_score
                if module_type not in {'document', 'speech', 'live'} or grammar_check_succeeded
                else None
            )
            fallback_json['grammar_issues'] = grammar_issues
            fallback_json['grammar_issues_count'] = len(grammar_issues)
            if module_type in {'document', 'speech', 'live'}:
                fallback_json['grammar_check_status'] = grammar_check_status
            if module_type not in {'document', 'speech', 'live'} or grammar_check_succeeded:
                if 'seven_cs_scores' in fallback_json:
                    if module_type != 'document':
                        fallback_json['seven_cs_scores']['Correct'] = computed_grammar_score
                if 'category_scores' in fallback_json and 'Grammar_and_Syntax' in fallback_json['category_scores']:
                    fallback_json['category_scores']['Grammar_and_Syntax'] = computed_grammar_score
            if module_type == 'document':
                fallback_json['overall_score'] = _calculate_document_overall_score(
                    fallback_json['seven_cs_scores'],
                    fallback_json['category_scores'],
                )
                fallback_json['score_rubric'] = _document_rubric_breakdown(
                    fallback_json['seven_cs_scores'],
                    fallback_json['category_scores'],
                )
            return fallback_json
    else:
        # Attach grammar data to fallback when Gemini is not available — all module types
        fallback_json['grammar_score']  = (
            computed_grammar_score
            if module_type not in {'document', 'speech', 'live'} or grammar_check_succeeded
            else None
        )
        fallback_json['grammar_issues'] = grammar_issues
        fallback_json['grammar_issues_count'] = len(grammar_issues)
        if module_type in {'document', 'speech', 'live'}:
            fallback_json['grammar_check_status'] = grammar_check_status
        if module_type not in {'document', 'speech', 'live'} or grammar_check_succeeded:
            if 'seven_cs_scores' in fallback_json:
                if module_type != 'document':
                    fallback_json['seven_cs_scores']['Correct'] = computed_grammar_score
            if 'category_scores' in fallback_json and 'Grammar_and_Syntax' in fallback_json['category_scores']:
                fallback_json['category_scores']['Grammar_and_Syntax'] = computed_grammar_score
        if module_type == 'document':
            fallback_json['overall_score'] = _calculate_document_overall_score(
                fallback_json['seven_cs_scores'],
                fallback_json['category_scores'],
            )
            fallback_json['score_rubric'] = _document_rubric_breakdown(
                fallback_json['seven_cs_scores'],
                fallback_json['category_scores'],
            )
        return fallback_json


def compare_documents(v1_text: str, v2_text: str, v1_score: int, v2_score: int, filename: str) -> dict:
    """
    Compares two versions of a presentation document.
    """
    # 1. Fallback JSON calculation
    score_diff = v2_score - v1_score
    improvements = []
    remaining = []
    
    if score_diff > 0:
        improvements.append(f"Overall presentation quality increased by {score_diff} points.")
        improvements.append("Enhanced slide layout and logical section flow.")
        improvements.append("Simplified language, improving clarity metrics.")
        improvements.append("Addressed main recommendations from Version 1 report.")
    elif score_diff == 0:
        improvements.append("Revisions were made, but overall score remained steady.")
        improvements.append("Minor adjustments in phrasing.")
    else:
        improvements.append("Minor edits made, but overall score decreased. Review structure.")
        
    if v2_score < 75:
        remaining.append("Continue working on bullet-point formatting to avoid text clutter.")
        remaining.append("Inject concrete statistics or metrics to make points more concrete.")
        remaining.append("Improve concluding call-to-action slide.")
    else:
        remaining.append("Polish speech delivery and pacing next.")
        
    synthesis = f"You made solid progress in revising this presentation. Your score moved from {v1_score}/100 to {v2_score}/100. The layout shows better organization and structure. Continue practicing your verbal delivery to match these visual improvements!"
    
    fallback_json = {
        "score_difference": score_diff,
        "key_improvements": improvements,
        "remaining_issues": remaining,
        "synthesis_summary": synthesis
    }
    
    # 2. Construct Prompt
    compare_prompt = f"""
Compare these two versions of a presentation and generate a structured JSON progress report.
Identify specific areas where the presenter improved grammar, formatting, clarity, structure, or content in Version 2 compared to Version 1. Also point out any remaining issues that still need attention.

JSON Schema output:
{{
  "score_difference": {score_diff},
  "key_improvements": [
    "<detailed improvement 1, e.g. Fixed spelling error in slide 2>",
    "<detailed improvement 2, e.g. Removed passive fillers and simplified slide 3>",
    "<detailed improvement 3>"
  ],
  "remaining_issues": [
    "<issue 1 still present in Version 2, e.g. slide 4 is still too wordy>",
    "<issue 2>"
  ],
  "synthesis_summary": "<1-2 paragraph description of the user's progress and coaching encouragement>"
}}

Version 1 Text:
{v1_text}

Version 2 Text:
{v2_text}
"""

    if _gemini_provider.is_available():
        try:
            result = _gemini_provider.generate_structured(compare_prompt)
            if isinstance(result, dict) and 'score_difference' in result:
                return result
            return fallback_json
        except Exception as e:
            logger.warning("Global AI Evaluator: Comparison call failed (%s). Using heuristics.", e)
            return fallback_json
    else:
        return fallback_json
