"""
Rewrite Executor - Batched Rewrite Execution

Executes rewrite operations with:
- Per-slide AI rewrite with rule-based fallback
- Post-rewrite GUARDS that stop the AI from damaging the deck:
    * footers / slide counters / brand lines / "Audience: ... Tone: ..." lines
      are kept unchanged (only pure spelling fixes are accepted)
    * numbers and percentages must stay identical
    * paragraph count per textbox must match the original
    * leading markers (bullets, emoji, symbols) are restored
    * suspicious growth or truncation of a paragraph is reverted
- Change severity classification
- Honest metrics (how many slides were really rewritten by AI vs fallback)
"""

import difflib
import logging
import re
import time
from typing import Callable, Optional

from services.ai import get_provider
from services.ai.base_provider import AIProvider
from services.ai.fallback import FallbackManager
from services.ai.prompts import build_rewrite_prompt
from services.rewrite.planner import RewritePlanner
from services.rewrite.smart_filter import SmartFilter
from services.rewrite.spacy_rewriter import rewrite_slide_dict

logger = logging.getLogger(__name__)

# Severity thresholds
SEVERITY_HIGH_CHANGE_RATIO = 0.5  # >50% text changed = major rewrite
SEVERITY_MODERATE_CHANGE_RATIO = 0.2  # >20% = moderate

# ── Guard configuration ─────────────────────────────────────────────────────
# Lines matching these are branding / navigation text. The AI may only fix
# spelling in them (very high similarity); any real rewrite is reverted.
_PROTECTED_PATTERNS = [
    re.compile(r'^\s*audience\s*:.*tone\s*:', re.I),          # Audience: X • Tone: Y
    re.compile(r'\d{1,3}\s*/\s*\d{1,3}\s*$'),                 # footer slide counter  02 / 05
    re.compile(r'^\s*presenova\s+ai\b', re.I),                # brand lines
    re.compile(r'^\s*(confidential|draft|\u00a9|copyright)\b', re.I),
]
_PROTECTED_MIN_SIMILARITY = 0.92

# Leading decoration (bullets, symbols, emoji) that belongs to the design.
_MARKER_RE = re.compile(
    r'^[\s\u2022\u25A0\u25AA\u25CF\u25E6\u25B6\u25BA\u2713\u2714\u27A4\u2192'
    r'\*\-\u2013\u2014\u2600-\u27BF\u2B50\uFE0F\U0001F300-\U0001FAFF]+'
)

_NUMBER_RE = re.compile(r'\d+(?:[.,]\d+)?')

# Growth / shrink limits for a single paragraph
_MAX_GROWTH_FACTOR = 1.4
_MAX_GROWTH_EXTRA_WORDS = 2
_MIN_KEEP_FACTOR = 0.4


def _para_text(p) -> str:
    """Return the text of a paragraph that may be a dict or a plain string."""
    if isinstance(p, dict):
        return str(p.get('text', ''))
    return str(p) if p is not None else ''


def _is_protected(text: str) -> bool:
    return any(p.search(text or '') for p in _PROTECTED_PATTERNS)


def _numbers(text: str) -> list:
    return sorted(_NUMBER_RE.findall(text or ''))


def _leading_marker(text: str) -> str:
    match = _MARKER_RE.match(text or '')
    return match.group(0).strip() if match else ''


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _guard_paragraph(original: str, new) -> tuple[str, bool]:
    """Validate one rewritten paragraph against its original.

    Returns (final_text, reverted). `reverted` is True when the AI output was
    rejected and the original text was kept.
    """
    new_text = _para_text(new).strip()

    # Nothing to protect / nothing returned
    if not original.strip():
        return original, False
    if not new_text:
        return original, True
    if new_text == original.strip():
        return original, False

    # Branding / footer / audience lines: only spelling-level fixes allowed
    if _is_protected(original):
        same_len = len(original.split()) == len(new_text.split())
        if same_len and _similarity(original, new_text) >= _PROTECTED_MIN_SIMILARITY \
                and _numbers(original) == _numbers(new_text):
            return new_text, False
        return original, True

    # Numbers / percentages must be identical
    if _numbers(original) != _numbers(new_text):
        return original, True

    # Reject invented growth or dropped content
    orig_words = len(original.split())
    new_words = len(new_text.split())
    if orig_words and new_words > orig_words * _MAX_GROWTH_FACTOR + _MAX_GROWTH_EXTRA_WORDS:
        return original, True
    if orig_words >= 4 and new_words < orig_words * _MIN_KEEP_FACTOR:
        return original, True

    # Restore decorative leading marker (bullet / emoji) if the AI stripped it
    marker = _leading_marker(original)
    if marker and not new_text.lstrip().startswith(marker):
        new_text = f'{marker} {new_text.lstrip()}'

    return new_text, False


class RewriteExecutor:
    """Executes slide rewriting with batching, fallback, guards and metrics."""

    def __init__(
        self,
        provider: Optional[AIProvider] = None,
        planner: Optional[RewritePlanner] = None,
        smart_filter: Optional[SmartFilter] = None,
    ):
        self.provider = provider or get_provider()
        self.planner = planner or RewritePlanner()
        self.smart_filter = smart_filter or SmartFilter()
        self.metrics: dict = {
            'start_time': None,
            'end_time': None,
            'total_duration': 0,
            'model_calls': 0,
            'retry_count': 0,
            'fallback_count': 0,
            'ai_slides_succeeded': 0,
            'rule_based_slides': 0,
            'guard_reverted_paragraphs': 0,
            'guard_reverted_textboxes': 0,
            'slides_rewritten': 0,
            'slides_skipped': 0,
            'batches_processed': 0,
            'total_batches': 0,
        }

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def execute_rewrite(
        self,
        slides: list[dict],
        grammar_issues_summary: str = "",
        presentation_context: Optional[dict] = None,
        focus_items: Optional[list[str]] = None,
    ) -> tuple[list[dict], dict]:
        """Execute the full rewrite pipeline.

        Args:
            slides: List of slide dicts to rewrite.
            grammar_issues_summary: Pre-computed grammar issues.
            presentation_context: Optional holistic context (reference only).
            focus_items: Pre-analysis recommendations the rewrite must address.

        Returns:
            Tuple of (rewritten_slides, execution_metrics).
        """
        self.metrics['start_time'] = time.time()

        # Step 1: Smart filter to identify what needs rewriting
        to_rewrite, to_skip = self.smart_filter.filter_slides(slides)
        self.metrics['slides_rewritten'] = len(to_rewrite)
        self.metrics['slides_skipped'] = len(to_skip)

        if not to_rewrite:
            logger.info("[executor] No slides need rewriting; returning originals.")
            self._finalize_metrics()
            return slides, self.metrics

        # Step 2: Plan batches
        batches = self.planner.plan_batches(to_rewrite)
        self.metrics['total_batches'] = len(batches)

        # Step 3: Execute batches
        all_rewritten = []
        for batch in batches:
            rewritten = self._execute_batch(
                batch['slides'],
                grammar_issues_summary,
                presentation_context,
                batch['batch_index'],
                batch['context_summary'],
                focus_items,
            )
            all_rewritten.extend(rewritten)
            self.metrics['batches_processed'] += 1

        self._finalize_metrics()
        logger.info(
            "[executor] Rewrote %d slides in %d batches (%.1fs) | AI ok=%d, rule-based=%d, "
            "guard reverts: %d paragraphs / %d textboxes",
            len(to_rewrite), len(batches), self.metrics['total_duration'],
            self.metrics['ai_slides_succeeded'], self.metrics['rule_based_slides'],
            self.metrics['guard_reverted_paragraphs'], self.metrics['guard_reverted_textboxes'],
        )

        return all_rewritten, self.metrics

    # ------------------------------------------------------------------ #
    # Batch / slide execution
    # ------------------------------------------------------------------ #
    def _execute_batch(
        self,
        batch_slides: list[dict],
        grammar_issues_summary: str,
        presentation_context: Optional[dict],
        batch_index: int,
        context_summary: Optional[str] = None,
        focus_items: Optional[list[str]] = None,
    ) -> list[dict]:
        """Execute rewrite for a single batch of slides (per-slide recovery)."""
        rewritten = []

        for slide in batch_slides:
            slide_num = slide.get('slide_number')
            try:
                result = self._rewrite_single_slide(
                    slide, grammar_issues_summary, presentation_context, focus_items
                )
                if result:
                    result = self._apply_guards(slide, result)
                    result['_change_severity'] = self._classify_severity(slide, result)
                    self.metrics['ai_slides_succeeded'] += 1
                    rewritten.append(result)
                else:
                    rewritten.append(self._fallback_slide(slide))
            except Exception as exc:
                logger.error("[executor] Slide %s rewrite failed: %s", slide_num, exc)
                rewritten.append(self._fallback_slide(slide, error=str(exc)))

        return rewritten

    def _rewrite_single_slide(
        self,
        slide: dict,
        grammar_issues_summary: str,
        presentation_context: Optional[dict],
        focus_items: Optional[list[str]] = None,
    ) -> Optional[dict]:
        """Rewrite a single slide using the AI provider.

        Returns the rewritten slide dict, or None for fallback.
        """
        prompt = build_rewrite_prompt(
            [slide],
            grammar_issues_summary,
            presentation_context,
            mode=self.planner.mode,
            tone=self.planner.tone,
            focus_items=focus_items,
        )

        try:
            result = self.provider.generate_structured(
                prompt=prompt,
                temperature=0.2,
                max_output_tokens=4096,
            )
            self.metrics['model_calls'] += 1

            if isinstance(result, dict) and 'slides' in result:
                slides_result = result.get('slides', [])
                if slides_result and isinstance(slides_result[0], dict):
                    return slides_result[0]
            logger.warning(
                "[executor] Slide %s: AI returned no usable 'slides' payload.",
                slide.get('slide_number'),
            )
        except Exception as exc:
            logger.warning(
                "[executor] Slide %s: AI rewrite failed: %s", slide.get('slide_number'), exc
            )
            self.metrics['retry_count'] += 1

        return None

    # ------------------------------------------------------------------ #
    # Guards
    # ------------------------------------------------------------------ #
    def _apply_guards(self, slide: dict, result: dict) -> dict:
        """Validate AI/rule output against the original slide.

        - Drops textboxes the AI invented, restores ones it forgot.
        - Reverts a textbox whose paragraph count changed.
        - Validates every paragraph with `_guard_paragraph`.
        """
        result = dict(result)
        result_by_shape = {}
        for tb in result.get('textboxes', []) or []:
            if isinstance(tb, dict):
                result_by_shape[tb.get('shape_index')] = tb

        guarded_textboxes = []
        for orig_tb in slide.get('textboxes', []) or []:
            idx = orig_tb.get('shape_index')
            orig_paras = [_para_text(p) for p in orig_tb.get('paragraphs', [])]
            new_tb = result_by_shape.get(idx)

            if new_tb is None:
                # AI skipped this textbox -> keep the original
                paragraphs = orig_paras
            else:
                new_paras = new_tb.get('paragraphs') or []
                if len(new_paras) != len(orig_paras):
                    paragraphs = orig_paras
                    self.metrics['guard_reverted_textboxes'] += 1
                else:
                    paragraphs = []
                    for o, n in zip(orig_paras, new_paras):
                        final_text, reverted = _guard_paragraph(o, n)
                        if reverted:
                            self.metrics['guard_reverted_paragraphs'] += 1
                        paragraphs.append(final_text)

            guarded_textboxes.append({'shape_index': idx, 'paragraphs': paragraphs})

        result['textboxes'] = guarded_textboxes
        result.setdefault('slide_number', slide.get('slide_number'))
        result.setdefault('tables', [])
        result.setdefault('charts', [])
        return result

    # ------------------------------------------------------------------ #
    # Fallbacks
    # ------------------------------------------------------------------ #
    def _unchanged_slide(self, slide: dict) -> dict:
        """Return the slide's original text in the rewrite output shape."""
        return {
            'slide_number': slide.get('slide_number'),
            'textboxes': [
                {
                    'shape_index': tb.get('shape_index'),
                    'paragraphs': [_para_text(p) for p in tb.get('paragraphs', [])],
                }
                for tb in slide.get('textboxes', [])
            ],
            'tables': [],
            'charts': [],
        }

    def _fallback_slide(self, slide: dict, error: Optional[str] = None) -> dict:
        """Rule-based rewrite (guarded). Falls back to unchanged text on error."""
        slide_num = slide.get('slide_number')
        try:
            result = rewrite_slide_dict(
                slide,
                mode=self.planner.mode,
                tone=self.planner.tone,
            )
            result = self._apply_guards(slide, result)
            result['_rule_based'] = True
            self.metrics['rule_based_slides'] += 1
        except Exception as rule_err:
            logger.warning(
                "[executor] Rule-based fallback error for slide %s: %s", slide_num, rule_err
            )
            result = self._unchanged_slide(slide)

        result['_fallback'] = True
        if error:
            result['_error'] = error
        result['_change_severity'] = self._classify_severity(slide, result)
        self.metrics['fallback_count'] += 1
        return result

    # ------------------------------------------------------------------ #
    # Metrics / classification
    # ------------------------------------------------------------------ #
    def _classify_severity(
        self,
        original_slide: dict,
        rewritten_slide: dict,
    ) -> str:
        """Classify the severity of changes made to a slide.

        Returns 'none', 'minor', 'moderate', or 'major'.
        """
        changes = 0
        total = 0

        for orig_tb in original_slide.get('textboxes', []):
            orig_texts = [_para_text(p) for p in orig_tb.get('paragraphs', [])]
            for rew_tb in rewritten_slide.get('textboxes', []):
                if rew_tb.get('shape_index') == orig_tb.get('shape_index'):
                    rew_texts = [_para_text(p) for p in rew_tb.get('paragraphs', [])]
                    for o, r in zip(orig_texts, rew_texts):
                        total += 1
                        if o.strip() != r.strip():
                            changes += 1
                    break

        if total == 0:
            return 'none'

        change_ratio = changes / total
        if change_ratio >= SEVERITY_HIGH_CHANGE_RATIO:
            return 'major'
        elif change_ratio >= SEVERITY_MODERATE_CHANGE_RATIO:
            return 'moderate'
        return 'minor'

    def _finalize_metrics(self) -> None:
        """Finalize execution metrics."""
        self.metrics['end_time'] = time.time()
        self.metrics['total_duration'] = round(
            self.metrics['end_time'] - self.metrics['start_time'], 2
        )
        # Remove internal timestamps for cleaner output
        clean = {k: v for k, v in self.metrics.items()
                 if k not in ('start_time', 'end_time')}
        self.metrics = clean