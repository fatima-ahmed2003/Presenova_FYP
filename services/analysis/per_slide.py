"""
Per-Slide Quality Analyzer

Analyzes each slide individually for:
- Grammar and spelling quality (via LanguageTool Cloud API)
- Readability and tone
- Clarity and conciseness
- 7 Cs of Communication scores
- Strengths and issues per slide

Honesty change in this version:
- When the AI call fails, _default_scores() now carries the REAL reason
  (from provider.last_structured_error, when the provider exposes it) in
  'summary' and 'recommendations' instead of a generic, uninformative
  "Upload with a configured AI provider..." placeholder.

Grounding change in this version:
- _verify_claims() drops any AI-reported issue / recommendation that QUOTES a
  phrase which does not exist in the analysed text (e.g. an invented
  "General Audience Audience"). The number of dropped claims is returned in
  'claims_filtered' so nothing is hidden silently.
"""

import json
import logging
import re
from typing import Optional

from services.ai import get_provider
from services.ai.base_provider import AIProvider
from services.ai.prompts import build_quality_analysis_prompt
from services.language_tool_service import (
    check_grammar,
    grammar_score as lt_grammar_score,
)

logger = logging.getLogger(__name__)

# Human-readable text for each last_structured_error['type'] the provider
# can report. Kept here (not in gemini_provider.py) because the wording is
# analysis-specific — other callers of generate_structured() may want a
# different phrasing for the same error type.
_ERROR_TYPE_MESSAGES = {
    'rate_limited': 'AI analysis is temporarily rate-limited. Please retry in a moment.',
    'model_not_found': 'The configured Gemini model is unavailable (retired/not found). Check GEMINI_MODEL in .env.',
    'timeout': 'The AI analysis request timed out before completing.',
    'provider_unavailable': 'AI analysis is not configured (missing or invalid GEMINI_API_KEY).',
    'provider_error': 'AI analysis failed due to a provider error.',
    'empty_response': 'The AI model returned no response after all retries.',
    'empty_json': 'The AI model response did not contain valid structured data.',
    'bad_json': 'The AI model response could not be parsed as JSON.',
}

# Quoted fragments inside AI-written issues, e.g.  'Vehcal'  "General Audience Audience"
_QUOTE_PATTERNS = (
    re.compile(r"(?<![\w])'([^'\n]{3,80})'(?![\w])"),
    re.compile(r'"([^"\n]{3,80})"'),
    re.compile(r'\u201c([^\u201d\n]{3,80})\u201d'),
    re.compile(r'\u2018([^\u2019\n]{3,80})\u2019'),
)


def _norm_text(value: str) -> str:
    return re.sub(r'\s+', ' ', value or '').strip().casefold()


class PerSlideAnalyzer:
    """Analyzes quality of individual slides and the overall presentation."""

    def __init__(self, provider: Optional[AIProvider] = None):
        self.provider = provider or get_provider()

    def analyze_presentation(
        self,
        text: str,
        filename: str = "",
        mode: str = "professional",
    ) -> dict:
        """Analyze the full presentation text for quality.

        Runs a LanguageTool Cloud grammar pre-pass first so that Grammar,
        Spelling, and the 'Correct' 7Cs dimension are scored with real data.

        Args:
            text: Full presentation text.
            filename: Original filename for context.
            mode: Processing mode (quick, professional, academic).

        Returns:
            Dict with quality scores, 7Cs, strengths, issues, recommendations.
        """
        if not text or len(text.strip()) < 10:
            return self._default_scores()

        # ── Grammar pre-pass (LanguageTool Cloud) ─────────────────────────
        grammar_issues: list[dict] = []
        computed_grammar_score: int = 100
        try:
            grammar_issues = check_grammar(text)
            computed_grammar_score = lt_grammar_score(grammar_issues, len(text.split()))
            logger.info(
                "[per_slide] LanguageTool: %d issues, grammar score=%d",
                len(grammar_issues), computed_grammar_score,
            )
        except Exception as exc:
            logger.warning("[per_slide] Grammar pre-pass failed: %s", exc)

        if self.provider and self.provider.is_available():
            try:
                prompt = build_quality_analysis_prompt(
                    text, filename, mode,
                    grammar_issues=grammar_issues,
                    grammar_score=computed_grammar_score,
                )
                result = self.provider.generate_structured(
                    prompt=prompt,
                    temperature=0.2,
                    max_output_tokens=4096,
                )
                if isinstance(result, dict) and 'overall_score' in result:
                    normalized = self._normalize(result)
                    # Drop AI claims that quote text which is not in the deck.
                    self._verify_claims(normalized, text)
                    # Override grammar-related scores with LanguageTool's
                    # authoritative values so they cannot be hallucinated.
                    normalized.setdefault('category_scores', {})['Grammar'] = computed_grammar_score
                    normalized['category_scores']['Spelling'] = computed_grammar_score
                    normalized.setdefault('seven_cs_scores', {})['Correct'] = computed_grammar_score
                    normalized['grammar_issues'] = grammar_issues
                    normalized['grammar_issues_count'] = len(grammar_issues)
                    normalized['grammar_score'] = computed_grammar_score
                    return normalized

                # Result came back but wasn't usable (empty dict, or missing
                # 'overall_score' — e.g. the model returned a differently
                # shaped JSON object). Surface WHY instead of silently
                # falling through to a generic placeholder.
                error_info = self._get_last_error_info(default_type='empty_json')
                if isinstance(result, dict) and result:
                    # Helps diagnose fallback models that answer with another schema.
                    error_info = dict(error_info)
                    error_info['detail'] = (
                        f"{error_info.get('detail', '')} | returned keys: {sorted(result.keys())[:12]}"
                    )[:300]
                logger.warning(
                    "[per_slide] AI analysis result unusable (%s): %s",
                    error_info['type'], error_info['detail'],
                )
                return self._default_scores(
                    error_info=error_info,
                    grammar_issues=grammar_issues,
                    computed_grammar_score=computed_grammar_score,
                )
            except Exception as exc:
                logger.warning("[per_slide] AI analysis failed: %s", exc)
                return self._default_scores(
                    error_info={'type': 'provider_error', 'detail': str(exc)[:300]},
                    grammar_issues=grammar_issues,
                    computed_grammar_score=computed_grammar_score,
                )

        # No provider configured / provider.is_available() is False.
        error_info = {
            'type': 'provider_unavailable',
            'detail': 'Gemini not configured (no API key, or FORCE_OFFLINE_MODE).',
        }
        return self._default_scores(
            error_info=error_info,
            grammar_issues=grammar_issues,
            computed_grammar_score=computed_grammar_score,
        )

    def _verify_claims(self, result: dict, source_text: str) -> None:
        """Remove AI issues/recommendations that quote phrases absent from the text.

        The model sometimes invents problems ("redundant 'General Audience Audience'")
        that are not in the deck. If a claim quotes something, that quote must exist
        in the analysed text; otherwise the claim is dropped. Mutates `result`.
        """
        haystack = _norm_text(source_text)
        dropped = 0
        for key in ('issues_found', 'recommendations'):
            items = result.get(key)
            if not isinstance(items, list):
                continue
            kept = []
            for item in items:
                fragments = []
                for pattern in _QUOTE_PATTERNS:
                    fragments.extend(pattern.findall(str(item)))
                if any(_norm_text(f) not in haystack for f in fragments):
                    dropped += 1
                    logger.info("[per_slide] Dropped unverifiable AI claim: %s", str(item)[:160])
                    continue
                kept.append(item)
            result[key] = kept
        result['claims_filtered'] = dropped

    def _get_last_error_info(self, default_type: str) -> dict:
        """Pull a structured error reason off the provider, if it exposes one.

        gemini_provider.GeminiProvider sets `last_structured_error` after any
        generate_structured() failure. Other AIProvider implementations may
        not have this attribute — fall back to a generic type in that case.
        """
        info = getattr(self.provider, 'last_structured_error', None)
        if isinstance(info, dict) and info.get('type'):
            return info
        return {'type': default_type, 'detail': 'No further detail available from the provider.'}

    def analyze_slide(self, slide: dict) -> dict:
        """Analyze a single slide's text content.

        Args:
            slide: Single slide dict.

        Returns:
            Dict with per-slide quality metrics.
        """
        text = self._get_slide_text(slide)
        word_count = len(text.split())
        char_count = len(text)
        tb_count = len(slide.get('textboxes', []))

        return {
            'slide_number': slide.get('slide_number'),
            'title': slide.get('title', ''),
            'word_count': word_count,
            'character_count': char_count,
            'textbox_count': tb_count,
            'has_content': word_count > 0,
            'readability_score': self._estimate_readability(text),
            'has_grammar_issues': self._has_grammar_issues(text),
            'text_density': round(char_count / max(tb_count, 1), 1),
        }

    def _get_slide_text(self, slide: dict) -> str:
        """Extract all text from a slide."""
        parts = []
        for tb in slide.get('textboxes', []):
            for para in tb.get('paragraphs', []):
                text = para.get('text', '') if isinstance(para, dict) else str(para)
                parts.append(text)
        return ' '.join(parts)

    def _estimate_readability(self, text: str) -> float:
        """Estimate readability score (0-100) using Flesch-like heuristics.

        Higher scores mean easier to read.
        """
        if not text.strip():
            return 100.0

        words = text.split()
        word_count = len(words)
        if word_count < 3:
            return 90.0

        # Count syllables (heuristic)
        syllable_count = sum(
            sum(1 for c in word.lower() if c in 'aeiou')
            for word in words[:100]
        )

        # Count sentences
        sentence_count = max(1, text.count('.') + text.count('!') + text.count('?'))

        # Flesch Reading Ease: 206.835 - 1.015*(words/sentences) - 84.6*(syllables/words)
        avg_words_per_sentence = word_count / sentence_count
        avg_syllables_per_word = syllable_count / max(word_count, 1)

        score = 206.835 - 1.015 * avg_words_per_sentence - 84.6 * avg_syllables_per_word
        return max(0, min(100, round(score, 1)))

    def _has_grammar_issues(self, text: str) -> bool:
        """Check for grammar issues using LanguageTool Cloud API.

        Falls back to simple heuristics if the API is unavailable.
        """
        try:
            issues = check_grammar(text)
            return len(issues) > 0
        except Exception:
            pass

        # Heuristic fallback
        issues = 0
        if '  ' in text:
            issues += 1
        for sentence in text.split('.'):
            s = sentence.strip()
            if s and s[0].islower():
                issues += 1
        for sentence in text.split('.'):
            if len(sentence.split()) > 40:
                issues += 1
        return issues > 0

    def _normalize(self, result: dict) -> dict:
        """Ensure all expected keys are present in the result."""
        defaults = self._default_scores()
        defaults.update(result)
        return defaults

    def _default_scores(
        self,
        error_info: Optional[dict] = None,
        grammar_issues: Optional[list] = None,
        computed_grammar_score: Optional[int] = None,
    ) -> dict:
        """Return default/neutral scores when analysis is unavailable.

        Args:
            error_info: {'type': ..., 'detail': ...} describing WHY the real
                AI analysis wasn't used (see _ERROR_TYPE_MESSAGES). When
                omitted, the text content itself was too short to analyze
                (not a failure), so a neutral message is used.
            grammar_issues / computed_grammar_score: real LanguageTool data
                to inject even when the AI call failed, so Grammar/Spelling
                aren't ALSO faked.
        """
        if error_info:
            error_type = error_info.get('type', 'provider_error')
            reason = _ERROR_TYPE_MESSAGES.get(error_type, _ERROR_TYPE_MESSAGES['provider_error'])
            summary = f'AI quality analysis unavailable: {reason}'
            recommendations = [f'AI analysis unavailable ({error_type}) — scores below are placeholders, not a real evaluation.']
            issues_found = [f'Detailed AI analysis could not be completed: {reason}']
            analysis_available = False
        else:
            summary = 'Content too short for AI quality analysis.'
            recommendations = ['Add more text content to enable a detailed AI evaluation.']
            issues_found = ['Not enough text was extracted to run an AI evaluation.']
            analysis_available = False

        grammar_val = computed_grammar_score if computed_grammar_score is not None else 75
        spelling_val = computed_grammar_score if computed_grammar_score is not None else 80

        return {
            'overall_score': 75,
            'grade': 'B',
            'summary': summary,
            'analysis_available': analysis_available,
            'error_type': error_info.get('type') if error_info else None,
            'category_scores': {
                'Grammar': grammar_val,
                'Spelling': spelling_val,
                'Readability': 75,
                'Tone': 75,
                'Clarity': 70,
                'Conciseness': 70,
                'Structure': 72,
            },
            'seven_cs_scores': {
                'Clear': 75, 'Concise': 70, 'Correct': grammar_val,
                'Complete': 70, 'Concrete': 65, 'Coherent': 72, 'Courteous': 78,
            },
            'seven_cs_evaluation': {
                'Clear': 'Analysis unavailable.',
                'Concise': 'Analysis unavailable.',
                'Correct': 'Analysis unavailable.',
                'Complete': 'Analysis unavailable.',
                'Concrete': 'Analysis unavailable.',
                'Coherent': 'Analysis unavailable.',
                'Courteous': 'Analysis unavailable.',
            },
            'strengths': ['Presentation has been processed'],
            'issues_found': issues_found,
            'recommendations': recommendations,
            'grammar_issues': grammar_issues or [],
            'grammar_issues_count': len(grammar_issues) if grammar_issues else 0,
            'grammar_score': grammar_val,
        }