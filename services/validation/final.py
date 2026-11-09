"""
Final Validator — Post-Rewrite Scoring and Confidence Computation

After all slides are rewritten and validated, this module:
- Computes the final quality scores for the improved presentation
- Calculates confidence scores for each improvement
- Assigns change severity labels
- Generates the final presentation score (0-100) with letter grade
- Produces overall presentation assessment

Honesty change in this version:
- _heuristic_scores() previously returned HARDCODED before/after numbers
  (e.g. Grammar 75 -> 88, Spelling 80 -> 92) every single time the AI
  scoring call was unavailable or failed — regardless of whether the
  rewrite actually changed anything. This produced a fake, constant
  "improvement" (Grade D -> 69/100 style jumps) even when only a typo
  or two were fixed. The heuristic now derives its delta from the REAL
  per-textbox change ratio computed by _compute_change_metrics (passed
  in as `change_metrics`), so a mostly-unchanged deck shows a small/zero
  delta and a heavily-rewritten deck shows a larger one.
- `compute_final_assessment()` now also records 'scoring_method'
  ('ai' or 'heuristic_fallback') and, when falling back, the provider's
  real failure reason (via last_structured_error, same mechanism as
  gemini_provider.py / per_slide.py) so this is no longer a silent switch.
"""

import logging
import math
from typing import Optional

from services.ai import get_provider
from services.ai.base_provider import AIProvider
from services.ai.prompts import build_scoring_prompt

logger = logging.getLogger(__name__)

# Grade boundaries
GRADE_BOUNDARIES = [
    (95, 'A+'), (90, 'A'), (85, 'A-'),
    (80, 'B+'), (75, 'B'), (70, 'B-'),
    (65, 'C+'), (60, 'C'), (55, 'C-'),
    (50, 'D+'), (45, 'D'), (40, 'D-'),
]

# Base starting point for heuristic scores (used only when AI scoring is
# unavailable/fails). These represent a "typical, unreviewed" deck.
_HEURISTIC_BASE_SCORES = {
    'Grammar': 75, 'Spelling': 80, 'Readability': 72,
    'Tone': 75, 'Clarity': 70, 'Conciseness': 65, 'Structure': 70,
}
_HEURISTIC_BASE_SEVEN_CS = {
    'Clear': 72, 'Concise': 65, 'Correct': 78,
    'Complete': 65, 'Concrete': 60, 'Coherent': 70, 'Courteous': 78,
}
# Max possible delta per category when change_intensity is 100% (a near-total
# rewrite). Scaled down linearly for lower change_intensity.
_HEURISTIC_MAX_DELTA = 15


class FinalValidator:
    """Final validation, scoring, and confidence computation."""

    def __init__(self, provider: Optional[AIProvider] = None):
        self.provider = provider or get_provider()

    def compute_final_assessment(
        self,
        original_slides: list[dict],
        rewritten_slides: list[dict],
        analysis_results: Optional[dict] = None,
        semantic_results: Optional[dict] = None,
        structural_results: Optional[dict] = None,
        statistics: Optional[dict] = None,
    ) -> dict:
        """Compute the final assessment of the rewritten presentation.

        Args:
            original_slides: Original slide data.
            rewritten_slides: Rewritten slide data.
            analysis_results: Pre-rewrite analysis results.
            semantic_results: Semantic validation results.
            structural_results: Structural validation results.
            statistics: Presentation statistics.

        Returns:
            Dict with overall scores, grades, confidence, and detailed metrics.
        """
        # 1. Compute change metrics FIRST — the heuristic scorer needs the
        #    real change ratio to avoid returning fabricated deltas.
        changes = self._compute_change_metrics(
            original_slides, rewritten_slides
        )

        # 2. Compute before/after scores
        scores, scoring_method, scoring_error = self._compute_scores(
            original_slides, rewritten_slides, analysis_results, changes
        )

        # 3. Calculate confidence scores
        confidence = self._compute_confidence(
            scores, semantic_results, structural_results
        )

        # 4. Determine final grade
        overall_improved = scores.get('overall_improved', 75)
        overall_original = scores.get('overall_original', 70)

        # Safeguard: if overall_improved is 0 or invalid, compute from category scores
        category_scores_dict = scores.get('category_scores', {})
        if (not overall_improved or overall_improved < 30) and category_scores_dict:
            cat_vals = [v for v in category_scores_dict.values() if isinstance(v, (int, float))]
            if cat_vals:
                overall_improved = round(sum(cat_vals) / len(cat_vals))
            else:
                overall_improved = max(overall_original, 75)
        if not overall_original or overall_original < 30:
            orig_cat_dict = scores.get('original_category_scores', {})
            orig_vals = [v for v in orig_cat_dict.values() if isinstance(v, (int, float))]
            if orig_vals:
                overall_original = round(sum(orig_vals) / len(orig_vals))
            else:
                overall_original = min(overall_improved, 70)

        grade = self._score_to_grade(overall_improved)
        original_grade = self._score_to_grade(overall_original)

        # 5. Build final assessment
        assessment = {
            'overall_score': overall_improved,
            'original_score': overall_original,
            'grade': grade,
            'original_grade': original_grade,
            'improvement': round(overall_improved - overall_original, 1),
            'improvement_percentage': round(
                ((overall_improved - overall_original) / max(overall_original, 1)) * 100, 1
            ),
            'confidence_score': confidence.get('overall_confidence', 80),
            'category_scores': scores.get('category_scores', {}),
            'original_category_scores': scores.get('original_category_scores', {}),
            'delta_scores': scores.get('delta_scores', {}),
            'seven_cs_scores': scores.get('seven_cs_scores', {}),
            'original_seven_cs_scores': scores.get('original_seven_cs_scores', {}),
            'seven_cs_delta': scores.get('seven_cs_delta', {}),
            'change_metrics': changes,
            'confidence_breakdown': confidence,
            'semantic_valid': semantic_results.get('valid', True) if semantic_results else True,
            'structural_valid': structural_results.get('valid', True) if structural_results else True,
            'semantic_score': semantic_results.get('preservation_score', 100) if semantic_results else 100,
            'structural_score': structural_results.get('structural_score', 100) if structural_results else 100,
            'statistics': statistics or {},
            'scoring_method': scoring_method,
        }
        if scoring_error:
            assessment['scoring_error'] = scoring_error

        return assessment

    def _compute_scores(
        self,
        original_slides: list[dict],
        rewritten_slides: list[dict],
        analysis_results: Optional[dict] = None,
        change_metrics: Optional[dict] = None,
    ) -> tuple[dict, str, Optional[dict]]:
        """Compute scores for original and improved content.

        Uses AI-powered scoring when available, heuristic otherwise.

        Returns:
            (scores, scoring_method, scoring_error) where scoring_method is
            'ai' or 'heuristic_fallback', and scoring_error is the real
            failure reason (from the provider) when the fallback was used
            because of a failure rather than because no provider is
            configured at all.
        """
        if self.provider and self.provider.is_available():
            try:
                prompt = build_scoring_prompt(
                    original_slides, rewritten_slides, analysis_results
                )
                result = self.provider.generate_structured(
                    prompt=prompt,
                    temperature=0.2,
                    max_output_tokens=4096,
                )
                if isinstance(result, dict) and 'overall_original' in result:
                    return self._normalize_scores(result, change_metrics), 'ai', None

                # Provider returned something, but not usable scoring data.
                error_info = getattr(self.provider, 'last_structured_error', None)
                if not isinstance(error_info, dict):
                    error_info = {'type': 'empty_json', 'detail': 'AI scoring result missing overall_original.'}
                logger.warning(
                    "[final] AI scoring result unusable (%s): %s — using heuristic fallback.",
                    error_info.get('type'), error_info.get('detail'),
                )
                return self._heuristic_scores(original_slides, rewritten_slides, change_metrics), 'heuristic_fallback', error_info
            except Exception as exc:
                logger.warning("[final] AI scoring failed: %s", exc)
                return (
                    self._heuristic_scores(original_slides, rewritten_slides, change_metrics),
                    'heuristic_fallback',
                    {'type': 'provider_error', 'detail': str(exc)[:300]},
                )

        return (
            self._heuristic_scores(original_slides, rewritten_slides, change_metrics),
            'heuristic_fallback',
            {'type': 'provider_unavailable', 'detail': 'Gemini not configured (no API key, or FORCE_OFFLINE_MODE).'},
        )

    def _heuristic_scores(
        self,
        original_slides: list[dict],
        rewritten_slides: list[dict],
        change_metrics: Optional[dict] = None,
    ) -> dict:
        """Compute heuristic scores when AI is unavailable.

        The delta applied to every category is now scaled by the REAL
        change_intensity from _compute_change_metrics (0-100), instead of a
        fixed +10 to +15 that used to appear on every single run regardless
        of what actually changed.
        """
        # Compare text lengths as a secondary signal for conciseness.
        orig_total = 0
        rew_total = 0
        for orig, rew in zip(original_slides, rewritten_slides):
            orig_text = self._get_all_text(orig)
            rew_text = self._get_all_text(rew)
            orig_total += len(orig_text)
            rew_total += len(rew_text)

        conciseness_bonus = 0
        if orig_total > 0:
            ratio = rew_total / orig_total
            if 0.7 <= ratio <= 0.9:
                conciseness_bonus = 3
            elif 0.5 <= ratio < 0.7:
                conciseness_bonus = 5
            elif ratio > 1.05:
                conciseness_bonus = -3

        # Real change intensity (0-100) from actual per-textbox diffing.
        # No change_metrics available (e.g. called with empty slide lists
        # for default-filling) -> treat as zero change.
        change_intensity = 0.0
        if change_metrics:
            change_intensity = change_metrics.get('change_intensity', 0.0)

        # Scale the max delta linearly by how much REALLY changed.
        scale = max(0.0, min(1.0, change_intensity / 100.0))
        base_delta = round(_HEURISTIC_MAX_DELTA * scale)

        original_scores = dict(_HEURISTIC_BASE_SCORES)
        improved_scores = {}
        for k, base in original_scores.items():
            delta = base_delta
            if k == 'Conciseness':
                delta += conciseness_bonus
            improved_scores[k] = max(0, min(100, base + delta))

        deltas = {
            k: improved_scores[k] - original_scores[k]
            for k in improved_scores
        }

        overall_original = round(sum(original_scores.values()) / len(original_scores))
        overall_improved = round(sum(improved_scores.values()) / len(improved_scores))

        original_seven_cs = dict(_HEURISTIC_BASE_SEVEN_CS)
        improved_seven_cs = {k: max(0, min(100, v + base_delta)) for k, v in original_seven_cs.items()}
        seven_cs_delta = {k: improved_seven_cs[k] - original_seven_cs[k] for k in improved_seven_cs}

        return {
            'overall_original': overall_original,
            'overall_improved': overall_improved,
            'original_category_scores': original_scores,
            'category_scores': improved_scores,
            'delta_scores': deltas,
            'seven_cs_scores': improved_seven_cs,
            'original_seven_cs_scores': original_seven_cs,
            'seven_cs_delta': seven_cs_delta,
        }

    def _compute_confidence(
        self,
        scores: dict,
        semantic_results: Optional[dict] = None,
        structural_results: Optional[dict] = None,
    ) -> dict:
        """Compute confidence scores for the rewrite results."""
        confidences = {}

        # Category-level confidence
        for category in scores.get('category_scores', {}):
            delta = abs(scores.get('delta_scores', {}).get(category, 0))
            if delta >= 10:
                confidences[category] = 85  # High confidence for big improvements
            elif delta >= 5:
                confidences[category] = 75
            else:
                confidences[category] = 90  # Very confident about small changes

        # Overall confidence
        structural_confidence = 100
        if structural_results:
            structural_confidence = structural_results.get('structural_score', 100)

        semantic_confidence = 100
        if semantic_results:
            semantic_confidence = semantic_results.get('preservation_score', 100)

        overall = round(
            (sum(confidences.values()) / max(len(confidences), 1) * 0.5) +
            (structural_confidence * 0.25) +
            (semantic_confidence * 0.25)
        )

        return {
            'overall_confidence': overall,
            'per_category': confidences,
            'structural_confidence': structural_confidence,
            'semantic_confidence': semantic_confidence,
        }

    def _compute_change_metrics(
        self,
        original_slides: list[dict],
        rewritten_slides: list[dict],
    ) -> dict:
        """Compute metrics about the changes made."""
        minor = 0
        moderate = 0
        major = 0
        unchanged = 0

        for orig, rew in zip(original_slides, rewritten_slides):
            for orig_tb in orig.get('textboxes', []):
                orig_texts = [
                    p.get('text', '') if isinstance(p, dict) else str(p)
                    for p in orig_tb.get('paragraphs', [])
                ]
                for rew_tb in rew.get('textboxes', []):
                    if rew_tb.get('shape_index') == orig_tb.get('shape_index'):
                        rew_texts = [str(p) for p in rew_tb.get('paragraphs', [])]

                        changes = sum(
                            1 for o, r in zip(orig_texts, rew_texts)
                            if o.strip() != r.strip()
                        )
                        total = max(len(orig_texts), 1)
                        ratio = changes / total

                        if ratio == 0:
                            unchanged += 1
                        elif ratio < 0.2:
                            minor += 1
                        elif ratio < 0.5:
                            moderate += 1
                        else:
                            major += 1
                        break

        total = unchanged + minor + moderate + major
        return {
            'total_textboxes_checked': total,
            'unchanged': unchanged,
            'minor_improvements': minor,
            'moderate_improvements': moderate,
            'major_rewrites': major,
            'change_intensity': round(
                (minor * 0.2 + moderate * 0.5 + major * 1.0) / max(total, 1) * 100, 1
            ),
        }

    def _get_all_text(self, slide: dict) -> str:
        """Get all text from a slide as a single string."""
        parts = []
        for tb in slide.get('textboxes', []):
            for para in tb.get('paragraphs', []):
                text = para.get('text', '') if isinstance(para, dict) else str(para)
                parts.append(text)
        return ' '.join(parts)

    def _normalize_scores(self, result: dict, change_metrics: Optional[dict] = None) -> dict:
        """Normalize AI scoring result with defaults."""
        defaults = self._heuristic_scores([], [], change_metrics)
        defaults.update(result)
        return defaults

    def _score_to_grade(self, score: int) -> str:
        """Convert a numerical score to a letter grade."""
        for threshold, grade in GRADE_BOUNDARIES:
            if score >= threshold:
                return grade
        return 'F'