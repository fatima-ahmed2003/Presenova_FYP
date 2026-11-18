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

REFRESH mode (RewriteExecutor(refresh=True)):
- Replaces generic filler with accurate topic content, design unchanged.
- ~1 plan call + 1 call per 5 slides (instead of 1 call per slide) so the
  Gemini free-tier quota (429) is not exhausted.
- Uses the refresh guard from services/rewrite/refresh.py (numbers may be
  removed but never invented; per-paragraph length budget; footers locked).
"""

import difflib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from services.ai import get_provider
from services.ai.base_provider import AIProvider
from services.ai.fallback import FallbackManager
from services.ai.prompts import build_rewrite_prompt
from services.rewrite.planner import RewritePlanner
from services.rewrite.smart_filter import SmartFilter
from services.rewrite.spacy_rewriter import rewrite_slide_dict
from services.rewrite.refresh import (
    build_plan_prompt,
    build_refresh_prompt,
    chunk_slides,
    deck_outline,
    detect_topic,
    guard_refresh_paragraph,
    index_plan,
    index_result,
    is_locked,  # GAP_DETECTOR
)

logger = logging.getLogger(__name__)

# Severity thresholds
SEVERITY_HIGH_CHANGE_RATIO = 0.5  # >50% text changed = major rewrite
SEVERITY_MODERATE_CHANGE_RATIO = 0.2  # >20% = moderate

# Pause between Gemini calls in refresh mode (seconds) to stay under rate limits
_REFRESH_CALL_GAP = 1.5

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
        refresh: bool = False,
    ):
        self.provider = provider or get_provider()
        self.planner = planner or RewritePlanner()
        self.smart_filter = smart_filter or SmartFilter()
        self.refresh = bool(refresh)
        self._consec_fail = 0  # GAP_DETECTOR
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
            'refresh_mode': self.refresh,
            # GAP_DETECTOR
            'slide_status': {},
            'last_ai_error': '',
            'ai_failed': False,
            'no_gaps_found': False,
            'slide_gaps': {},
            'deck_gaps': [],
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

        # Step 1: Smart filter and refresh-only gap detection.  # GAP_DETECTOR
        to_rewrite, to_skip = self.smart_filter.filter_slides(slides)
        gap_skipped = []
        if self.refresh:  # GAP_DETECTOR
            to_rewrite, to_skip, gap_skipped = self._gap_filter(to_rewrite, to_skip)
        self.metrics['slides_rewritten'] = len(to_rewrite)
        self.metrics['slides_skipped'] = len(to_skip)

        if not to_rewrite:
            self.metrics['no_gaps_found'] = bool(gap_skipped)  # GAP_DETECTOR
            logger.info("[executor] No slides need rewriting; returning originals.")
            self._finalize_metrics()
            return slides, self.metrics

        # REFRESH mode: topic-content refresh with few, batched AI calls
        if self.refresh:
            all_rewritten = self._execute_refresh(slides, to_rewrite, presentation_context)
            # GAP_DETECTOR: retain skipped slides for downstream structural validation.
            all_rewritten += [self._unchanged_slide(slide) for slide in gap_skipped]
            all_rewritten.sort(key=lambda result: result.get('slide_number') or 0)
            self._finalize_metrics()
            logger.info(
                "[executor] REFRESH rewrote %d slides (%.1fs) | AI ok=%d, rule-based=%d, "
                "model calls=%d, guard reverts: %d paragraphs / %d textboxes",
                len(to_rewrite), self.metrics['total_duration'],
                self.metrics['ai_slides_succeeded'], self.metrics['rule_based_slides'],
                self.metrics['model_calls'],
                self.metrics['guard_reverted_paragraphs'], self.metrics['guard_reverted_textboxes'],
            )
            return all_rewritten, self.metrics

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
    # REFRESH mode
    # ------------------------------------------------------------------ #
    def _generate(self, prompt: str, temperature: float, max_tokens: int):
        """One provider call; never raises. Circuit breaker after 3 consecutive failures."""
        if self._consec_fail >= 3:  # GAP_DETECTOR
            return {}
        try:
            result = self.provider.generate_structured(
                prompt=prompt, temperature=temperature, max_output_tokens=max_tokens,
            )
            self.metrics['model_calls'] += 1
            self._consec_fail = 0  # GAP_DETECTOR
            return result
        except Exception as exc:
            logger.warning("[executor] refresh call failed: %s", exc)
            self.metrics['retry_count'] += 1
            self.metrics['last_ai_error'] = str(exc)[:200]  # GAP_DETECTOR
            self._consec_fail += 1  # GAP_DETECTOR
            return {}

    # GAP_DETECTOR
    def _gap_filter(self, to_rewrite: list[dict], to_skip: list[dict]):
        """Keep only slides with content gaps. Never make refresh fatal."""
        try:
            from services.rewrite.gap_detector import analyze_deck

            payload = []
            for slide in to_rewrite:
                title = str(slide.get('title') or '').strip()
                paragraphs = []
                for textbox in slide.get('textboxes', []) or []:
                    for paragraph in textbox.get('paragraphs', []) or []:
                        text = _para_text(paragraph).strip()
                        if text and text != title and not _is_protected(text) and not is_locked(text):
                            paragraphs.append(text)
                payload.append({
                    'index': slide.get('slide_number'),
                    'title': title,
                    'paragraphs': paragraphs,
                    'notes': slide.get('notes', '') or '',
                })

            result = analyze_deck(payload, mode='refresh' if self.refresh else 'professional')
            rewrite_indices = set(result['rewrite_indices'])
            self.metrics['slide_gaps'] = {
                str(index): value['gaps'] for index, value in result['slides'].items()
            }
            self.metrics['deck_gaps'] = result['deck']
            kept = [slide for slide in to_rewrite if slide.get('slide_number') in rewrite_indices]
            dropped = [slide for slide in to_rewrite if slide.get('slide_number') not in rewrite_indices]
            return kept, list(to_skip) + dropped, dropped
        except Exception as exc:
            logger.warning("[executor] gap filter skipped (non-fatal): %s", exc)
            return to_rewrite, to_skip, []

    def _execute_refresh(
        self,
        all_slides: list[dict],
        to_rewrite: list[dict],
        presentation_context: Optional[dict],
    ) -> list[dict]:
        tone = f"{self.planner.tone}: {self.planner.tone_instruction}"
        topic = detect_topic(all_slides, presentation_context)
        outline = deck_outline(all_slides)
        logger.info("[executor] REFRESH topic=%r tone=%r slides=%d", topic, tone, len(to_rewrite))

        # 1 small call: a deck plan so batches stay coherent with each other
        plan = index_plan(self._generate(build_plan_prompt(topic, tone, all_slides), 0.4, 3072))
        self.metrics['plan_ok'] = bool(plan)
        if not plan:
            logger.warning("[executor] REFRESH plan unavailable; continuing from the deck outline.")

        chunks = chunk_slides(to_rewrite)
        self.metrics['total_batches'] = len(chunks)
        results: list[dict] = []

        for chunk in chunks:
            time.sleep(_REFRESH_CALL_GAP)
            by_num = index_result(self._generate(
                build_refresh_prompt(chunk, topic, tone, plan, outline, len(all_slides)),
                0.4, 8192,
            ))
            for slide in chunk:
                num = slide.get('slide_number')
                ai_result = by_num.get(num)
                if ai_result is None:
                    # one retry for just this slide
                    time.sleep(_REFRESH_CALL_GAP)
                    single = index_result(self._generate(
                        build_refresh_prompt([slide], topic, tone, plan, outline, len(all_slides)),
                        0.4, 4096,
                    ))
                    ai_result = single.get(num)
                if ai_result is not None:
                    try:
                        guarded = self._apply_guards(slide, ai_result, use_refresh=True)
                        guarded['_change_severity'] = self._classify_severity(slide, guarded)
                        self.metrics['ai_slides_succeeded'] += 1
                        self.metrics['slide_status'][num] = 'ai_ok'  # GAP_DETECTOR
                        results.append(guarded)
                        continue
                    except Exception as exc:
                        logger.error("[executor] REFRESH slide %s guard failed: %s", num, exc)
                self.metrics['slide_status'][num] = 'ai_failed'  # GAP_DETECTOR
                results.append(self._fallback_slide(slide, error='refresh call failed'))
            self.metrics['batches_processed'] += 1

        results.sort(key=lambda r: r.get('slide_number') or 0)
        return results

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
        """Rewrite small slide groups per provider call, with per-slide recovery."""
        groups = [batch_slides[start:start + 5] for start in range(0, len(batch_slides), 5)]

        def request_group(slide_group):
            prompt = build_rewrite_prompt(
                slide_group, grammar_issues_summary, presentation_context,
                mode=self.planner.mode, tone=self.planner.tone, focus_items=focus_items,
            )
            error = None
            for attempt in range(2):  # one bounded retry for a failed provider request
                try:
                    response = self.provider.generate_structured(
                        prompt=prompt, temperature=0.2, max_output_tokens=8192,
                    )
                    by_number = {}
                    if isinstance(response, dict):
                        for item in response.get('slides', []):
                            if isinstance(item, dict):
                                try:
                                    by_number[int(item.get('slide_number'))] = item
                                except (TypeError, ValueError):
                                    continue
                    return by_number, None, attempt
                except Exception as exc:
                    error = str(exc)
                    if attempt == 0:
                        time.sleep(0.25)
            return {}, error, 1

        rewritten = []
        # Two concurrent batches reduce wall time without creating an
        # unbounded burst against the configured provider.
        with ThreadPoolExecutor(max_workers=min(2, max(1, len(groups)))) as pool:
            pending = {pool.submit(request_group, group): group for group in groups}
            for future in as_completed(pending):
                slide_group = pending[future]
                try:
                    by_number, call_error, retries = future.result()
                except Exception as exc:
                    by_number, call_error, retries = {}, str(exc), 0
                self.metrics['model_calls'] += 1 + retries
                self.metrics['retry_count'] += retries
                for slide in slide_group:
                    slide_num = slide.get('slide_number')
                    result = by_number.get(slide_num)
                    try:
                        if result:
                            result = self._apply_guards(slide, result)
                            result['_change_severity'] = self._classify_severity(slide, result)
                            self.metrics['ai_slides_succeeded'] += 1
                            rewritten.append(result)
                        else:
                            rewritten.append(self._fallback_slide(
                                slide, error=call_error or 'AI response omitted slide'
                            ))
                    except Exception as exc:
                        logger.error("[executor] Slide %s rewrite failed: %s", slide_num, exc)
                        rewritten.append(self._fallback_slide(slide, error=str(exc)))

        return sorted(rewritten, key=lambda result: result.get('slide_number') or 0)

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
    def _apply_guards(self, slide: dict, result: dict, use_refresh: Optional[bool] = None) -> dict:
        """Validate AI/rule output against the original slide.

        - Drops textboxes the AI invented, restores ones it forgot.
        - Reverts a textbox whose paragraph count changed.
        - Validates every paragraph with the polish guard, or with the
          refresh guard when `use_refresh` (default: self.refresh) is True.
        """
        if use_refresh is None:
            use_refresh = self.refresh
        guard = guard_refresh_paragraph if use_refresh else _guard_paragraph

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
                        final_text, reverted = guard(o, n)
                        if reverted:
                            self.metrics['guard_reverted_paragraphs'] += 1
                        paragraphs.append(final_text)

            guarded_textboxes.append({'shape_index': idx, 'paragraphs': paragraphs})

        # Tables and editable chart titles are also supported rewrite targets.
        # Validate them against the source structure so omitted/extra cells do
        # not silently become unchanged-but-successful output.
        def guarded_paragraphs(original, candidate):
            original = [_para_text(value) for value in (original or [])]
            candidate = candidate or []
            if len(candidate) != len(original):
                self.metrics['guard_reverted_textboxes'] += 1
                return original
            output = []
            for before, after in zip(original, candidate):
                text, reverted = guard(before, after)
                if reverted:
                    self.metrics['guard_reverted_paragraphs'] += 1
                output.append(text)
            return output

        rewritten_tables = {
            table.get('shape_index'): table
            for table in (result.get('tables') or []) if isinstance(table, dict)
        }
        guarded_tables = []
        for source_table in slide.get('tables_data') or slide.get('tables') or []:
            candidate = rewritten_tables.get(source_table.get('shape_index'), {})
            candidate_cells = {
                (cell.get('row_index'), cell.get('column_index')): cell
                for cell in (candidate.get('cells') or []) if isinstance(cell, dict)
            }
            cells = []
            for source_cell in source_table.get('cells') or []:
                key = (source_cell.get('row_index'), source_cell.get('column_index'))
                candidate_cell = candidate_cells.get(key, {})
                cells.append({
                    'row_index': key[0], 'column_index': key[1],
                    'paragraphs': guarded_paragraphs(
                        source_cell.get('paragraphs'), candidate_cell.get('paragraphs')
                    ) if candidate_cell else [_para_text(p) for p in source_cell.get('paragraphs', [])],
                })
            guarded_tables.append({'shape_index': source_table.get('shape_index'), 'cells': cells})

        rewritten_charts = {
            chart.get('shape_index'): chart
            for chart in (result.get('charts') or []) if isinstance(chart, dict)
        }
        guarded_charts = []
        for source_chart in slide.get('charts_data') or slide.get('charts') or []:
            candidate = rewritten_charts.get(source_chart.get('shape_index'), {})
            source_title = source_chart.get('title_paragraphs') or []
            guarded_charts.append({
                'shape_index': source_chart.get('shape_index'),
                'title_paragraphs': guarded_paragraphs(source_title, candidate.get('title_paragraphs'))
                if candidate else [_para_text(p) for p in source_title],
            })

        result['textboxes'] = guarded_textboxes
        result['tables'] = guarded_tables
        result['charts'] = guarded_charts
        result.setdefault('slide_number', slide.get('slide_number'))
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
            # Rule-based output is a light polish, so it always uses the strict guard.
            result = self._apply_guards(slide, result, use_refresh=False)
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
