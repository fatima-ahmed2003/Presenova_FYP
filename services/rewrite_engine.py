"""
End-to-end orchestration for the AI Presentation Enhancement Platform.

Integrates all modular components:
- Provider abstraction (services/ai/)
- Analysis modules (services/analysis/)
- Rewrite modules (services/rewrite/)
- Validation modules (services/validation/)
- Report modules (services/report/)
- Existing services (ppt_processor, download_service, etc.)

Maintains backward compatibility with the existing API endpoints.

Honesty changes in this version:
- `quality_scores['analysis_available']` tells whether the AI analysis was real.
- Pre-analysis recommendations are only fed to the rewrite when the analysis was real.
- The "improvements" list is built from REAL change counts, not score thresholds.
- Rewrite execution metrics (AI vs rule-based, guard reverts) are returned.
- Grammar/Spelling/Conciseness "addressed" lines were REMOVED from
  _build_improvements_summary: they used to fire from a category SCORE
  THRESHOLD regardless of whether that category's text was actually
  touched by the rewrite — fabricated ticks (e.g. "Spelling corrected
  throughout" even when nothing changed).

Consistency change in this version:
- After the rewrite, a cross-slide consistency pass (services/consistency.py)
  makes sure the SAME source typo is corrected the SAME way on every slide
  (e.g. "Vehcal" -> always "Vehicle", never a mix of "Vehicle"/"Vehicles").
  It runs without any AI call and is wrapped in try/except, so it can never
  break the pipeline.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional
from dotenv import dotenv_values

from services.ai import get_provider
from services.ai.base_provider import AIProvider
from services.ai.fallback import FallbackManager
from services.ai.gemini_provider import GeminiProvider
from services.ai.prompts import build_quality_analysis_prompt, build_rewrite_prompt
from services.analysis import (
    compute_presentation_statistics,
    HolisticAnalyzer,
    PerSlideAnalyzer,
    StorytellingAnalyzer,
    DesignAnalyzer,
    ConsistencyAnalyzer,
    DuplicateDetector,
    AccessibilityAnalyzer,
    SpeakerAnalyzer,
)
from services.rewrite import SmartFilter, RewritePlanner, RewriteExecutor
from services.validation import SemanticValidator, StructuralValidator, FinalValidator
from services.report import ExecutiveSummaryGenerator, AnalyticsGenerator, RecommendationsGenerator

from services.consistency import harmonize_slides, harmonize_footers
from services.download_service import (
    ensure_download_folder,
    generate_output_filename,
    validate_upload_size,
)
from services.language_tool_service import summarise_grammar_issues
from services.ppt_processor import (
    extract_slides,
    get_presentation_metadata,
    update_presentation_text,
)
from services.report_generator import build_slide_report, save_report_json
from services.text_extractor import (
    get_all_text_for_analysis,
    get_extension,
    is_allowed_for_analysis,
    is_allowed_for_rewrite,
    validate_file_content,
)
from tools.compare_pptx import compare_pptx_files

logger = logging.getLogger(__name__)


def _get_ppt_rewrite_provider():
    """Prefer an explicitly configured project .env locally; use runtime env on Render."""
    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / '.env'
    env_values = dotenv_values(env_path) if env_path.is_file() else {}
    is_render = bool(os.getenv('RENDER_SERVICE_ID') or os.getenv('RENDER', '').lower() == 'true')

    if is_render:
        api_key = os.getenv('GEMINI_API_KEY', '').strip()
        fallback_setting = os.getenv('GEMINI_FALLBACK_MODELS')
    else:
        # dotenv_values bypasses inherited/stale GEMINI_API_KEY values when the
        # project file contains the updated credential. Runtime env remains the
        # compatibility path when no key is configured in .env.
        api_key = (env_values.get('GEMINI_API_KEY') or os.getenv('GEMINI_API_KEY', '')).strip()
        fallback_setting = env_values.get('GEMINI_FALLBACK_MODELS')
        if fallback_setting is None:
            fallback_setting = os.getenv('GEMINI_FALLBACK_MODELS')

    if not api_key:
        return get_provider(max_retries=0)

    fallback_models = [
        model.strip()
        for model in (fallback_setting or '').split(',')
        if model.strip() and model.strip() != 'gemini-flash-lite-latest'
    ]
    return GeminiProvider(
        api_key=api_key,
        model='gemini-flash-lite-latest',
        fallback_models=fallback_models,
        max_retries=0,
    )

# Phrases the analyzer uses when it fell back to default placeholder scores.
_UNAVAILABLE_MARKERS = (
    'could not be completed',
    'analysis was not available',
    'analysis unavailable',
    'default scores shown',
    'upload with a configured ai provider',
)


def _analysis_is_real(quality_scores) -> bool:
    """True only when quality_scores came from a real AI analysis."""
    if not isinstance(quality_scores, dict) or not quality_scores:
        return False
    if quality_scores.get('analysis_available') is False:
        return False
    evaluations = quality_scores.get('seven_cs_evaluation') or {}
    blob = ' '.join([
        str(quality_scores.get('summary', '')),
        ' '.join(str(i) for i in (quality_scores.get('issues_found') or [])),
        ' '.join(str(r) for r in (quality_scores.get('recommendations') or [])),
        ' '.join(str(v) for v in evaluations.values()),
    ]).lower()
    return not any(marker in blob for marker in _UNAVAILABLE_MARKERS)


def _extract_paragraph_text(value) -> str:
    """Extract plain display text from a paragraph value.

    RewriteExecutor / validators sometimes pass richer paragraph objects
    like {'para_index': 0, 'text': '...', 'runs': [...]} for run-level
    diffing. The PPT writer only ever wants the plain 'text' — never the
    raw dict repr, which would render as literal text on the slide.
    """
    if isinstance(value, dict):
        return str(value.get('text', ''))
    return str(value)


def _count_text_changes(original_slides: list, rewritten_slides: list) -> dict:
    """Count and describe validated paragraph changes across editable content."""
    rewritten_map = {slide.get('slide_number'): slide for slide in rewritten_slides or []}
    total = changed = 0
    slides_changed = set()
    examples = []
    all_changes = []

    def compare(slide_num, location, before, after):
        nonlocal total, changed
        before = _extract_paragraph_text(before).strip()
        after = _extract_paragraph_text(after).strip()
        if not before:
            return
        total += 1
        if before != after:
            changed += 1
            slides_changed.add(slide_num)
            entry = {
                'slide_number': slide_num, 'location': location,
                'before': before, 'after': after,
            }
            all_changes.append(entry)
            if len(examples) < 12:
                examples.append({**entry, 'before': before[:180], 'after': after[:180]})

    for source in original_slides or []:
        slide_num = source.get('slide_number')
        result = rewritten_map.get(slide_num, {})
        boxes = {v.get('shape_index'): v for v in result.get('textboxes', []) or []}
        for box in source.get('textboxes', []) or []:
            out = boxes.get(box.get('shape_index'), {})
            for i, para in enumerate(box.get('paragraphs', []) or []):
                candidates = out.get('paragraphs') or []
                compare(slide_num, f"textbox {box.get('shape_index')} paragraph {i + 1}", para,
                        candidates[i] if i < len(candidates) else para)

        tables = {v.get('shape_index'): v for v in result.get('tables', []) or []}
        for table in source.get('tables_data') or source.get('tables') or []:
            out_table = tables.get(table.get('shape_index'), {})
            out_cells = {(v.get('row_index'), v.get('column_index')): v
                         for v in out_table.get('cells', []) or []}
            for cell in table.get('cells', []) or []:
                key = (cell.get('row_index'), cell.get('column_index'))
                candidates = (out_cells.get(key) or {}).get('paragraphs') or []
                for i, para in enumerate(cell.get('paragraphs', []) or []):
                    compare(slide_num, f"table {table.get('shape_index')} cell {key} paragraph {i + 1}",
                            para, candidates[i] if i < len(candidates) else para)

        charts = {v.get('shape_index'): v for v in result.get('charts', []) or []}
        for chart in source.get('charts_data') or source.get('charts') or []:
            candidates = (charts.get(chart.get('shape_index')) or {}).get('title_paragraphs') or []
            for i, para in enumerate(chart.get('title_paragraphs', []) or []):
                compare(slide_num, f"chart {chart.get('shape_index')} title paragraph {i + 1}",
                        para, candidates[i] if i < len(candidates) else para)
    return {
        'total': total, 'changed': changed, 'slides_changed': len(slides_changed),
        'changed_slide_numbers': sorted(slides_changed), 'examples': examples,
        'changes': all_changes,
    }


class NoRewriteChangesError(ValueError):
    """Raised when validation finds no actual edits to include in an output deck."""


def run_rewrite_pipeline(
    temp_file_path: str,
    original_filename: str,
    mode: str = "professional",
    tone: str = "professional",
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """Rewrite a PPTX using the complete AI enhancement pipeline.

    Args:
        temp_file_path: Path to the uploaded temporary file.
        original_filename: Original filename for extension detection.
        mode: Processing mode (quick, professional, academic).
        tone: Writing tone (professional, academic, business, etc.).

    Returns:
        Dict with output_filename, slides_processed, quality_scores,
        improvements, processing_steps, metadata, and full report data.
    """
    extension = get_extension(original_filename)
    def report_progress(index: int, message: str) -> None:
        if progress_callback:
            try:
                progress_callback(index, 6, message)
            except Exception:
                logger.debug('[rewrite_engine] Progress update skipped.', exc_info=True)

    report_progress(0, 'Validating presentation')
    if not is_allowed_for_rewrite(original_filename):
        raise ValueError(
            f"Unsupported file type '{extension}' for rewriting. Only .pptx files supported."
        )

    file_size = os.path.getsize(temp_file_path)
    validate_upload_size(file_size)
    validate_file_content(temp_file_path, original_filename)
    steps = ['Validating file integrity and format']

    # ── Step 1: Extract slides and metadata ────────────────────────────
    slides = extract_slides(temp_file_path)
    metadata = get_presentation_metadata(temp_file_path)
    slide_count = len(slides)
    steps.append(f'Extracting text from {slide_count} slides & reading metadata')
    report_progress(1, 'Reading slide content')

    # ── Step 2: Extract text for analysis ──────────────────────────────
    extracted_text = get_all_text_for_analysis(temp_file_path, original_filename)
    steps.append('Text extraction complete — preparing for analysis')

    # ── Step 4: Initialize providers and analyzers ─────────────────────
    # Prefer the updated project .env credentials and fast model for PPT work;
    # fallbacks are the existing Gemini models configured in that same file.
    provider = _get_ppt_rewrite_provider()
    ai_available = provider.is_available()
    if not ai_available:
        logger.warning('[rewrite_engine] AI provider is NOT available; rule-based fallback will be used.')
        steps.append('AI provider unavailable — using rule-based fallback')
    fallback = FallbackManager(primary_provider=provider)
    refresh_mode = (mode == 'refresh')  # REFRESH_MODE
    planner_mode = 'professional' if refresh_mode else mode
    planner = RewritePlanner(mode=planner_mode, tone=tone)
    smart_filter = SmartFilter()
    executor = RewriteExecutor(provider=provider, planner=planner, smart_filter=smart_filter, refresh=refresh_mode)

    holistic_analyzer = HolisticAnalyzer(provider=provider)
    per_slide_analyzer = PerSlideAnalyzer(provider=provider)
    storytelling_analyzer = StorytellingAnalyzer(provider=provider)
    design_analyzer = DesignAnalyzer()
    consistency_analyzer = ConsistencyAnalyzer()
    duplicate_detector = DuplicateDetector()
    accessibility_analyzer = AccessibilityAnalyzer()
    speaker_analyzer = SpeakerAnalyzer()

    semantic_validator = SemanticValidator(provider=provider)
    structural_validator = StructuralValidator()
    final_validator = FinalValidator(provider=provider)

    exec_summary_gen = ExecutiveSummaryGenerator()
    analytics_gen = AnalyticsGenerator()
    recommendations_gen = RecommendationsGenerator()

    # ── Step 5: Holistic presentation analysis ─────────────────────────
    presentation_context = holistic_analyzer.analyze(slides)
    steps.append('Running holistic presentation analysis — evaluating structure, narrative, and topic coherence')

    # ── Step 6: Quality analysis ───────────────────────────────────────
    quality_scores = per_slide_analyzer.analyze_presentation(
        extracted_text, original_filename, planner_mode
    )
    if not isinstance(quality_scores, dict):
        quality_scores = {}
    grammar_matches = quality_scores.get('grammar_issues') or []
    grammar_summary = summarise_grammar_issues(grammar_matches)
    steps.append(
        f'Grammar & spelling pre-check: {len(grammar_matches)} issue(s) detected — will be addressed in rewrite'
        if grammar_matches else 'Grammar & spelling pre-check passed — no issues found'
    )
    analysis_ok = _analysis_is_real(quality_scores)
    quality_scores['analysis_available'] = analysis_ok
    if analysis_ok:
        steps.append('Running 7Cs semantic quality analysis — evaluating clarity, conciseness, correctness, completeness')
    else:
        logger.warning('[rewrite_engine] Quality analysis returned default/placeholder scores for %r.', original_filename)
        steps.append('AI quality analysis unavailable — default placeholder scores (not a real evaluation)')
    report_progress(2, 'Analyzing presentation context')

    # ── Step 7: Additional analyses (design, storytelling, etc.) ───────
    stats = compute_presentation_statistics(slides)
    steps.append('Computing readability indices & slide-by-slide statistics')

    design_analysis = design_analyzer.analyze(slides)
    storytelling_analysis = storytelling_analyzer.analyze(slides)
    consistency_analysis = consistency_analyzer.analyze(slides)
    duplicate_analysis = duplicate_detector.analyze(slides)
    accessibility_analysis = accessibility_analyzer.analyze(slides)
    speaker_analysis = speaker_analyzer.analyze(slides)
    steps.append('Checking design consistency, storytelling arc, accessibility compliance, and speaker notes quality')

    # ── Step 8: Token savings estimate (informational only) ────────────
    # NOTE: SmartFilter currently sends every slide that has text to the AI,
    # so this estimate is NOT reported to the user as a real saving.
    token_savings = smart_filter.estimate_token_savings(slides)

    # ── Step 9: Execute rewrite ────────────────────────────────────────
    # Only feed pre-analysis recommendations to the rewrite when they are real.
    focus_items = quality_scores.get('recommendations', []) if analysis_ok else None
    report_progress(3, 'Rewriting slide content')
    rewritten_slides, rewrite_metrics = executor.execute_rewrite(
        slides, grammar_summary, presentation_context,
        focus_items=focus_items,
    )
    # GAP_DETECTOR
    rewrite_metrics['ai_failed'] = (
        rewrite_metrics.get('slides_rewritten', 0) > 0
        and rewrite_metrics.get('ai_slides_succeeded', 0) == 0
    )

    # ── Step 9b: Cross-slide consistency pass (no AI call, never fatal) ─
    # Same source typo must be corrected the same way on every slide.
    consistency_fixes = 0
    try:
        rewritten_slides, consistency_log = (harmonize_slides(slides, rewritten_slides) if not refresh_mode else (rewritten_slides, []))
        consistency_fixes = len(consistency_log)
        if consistency_fixes:
            logger.info(
                '[rewrite_engine] Consistency pass normalized %d inconsistent correction(s): %s',
                consistency_fixes, consistency_log,
            )
    except Exception as exc:
        logger.warning('[rewrite_engine] Consistency pass skipped (non-fatal): %s', exc)
    # Footers / running text must read identically on every slide.
    try:
        rewritten_slides, footer_log = (harmonize_footers(rewritten_slides) if not refresh_mode else (rewritten_slides, []))
        if footer_log:
            consistency_fixes += len(footer_log)
            logger.info(
                '[rewrite_engine] Footer pass unified %d running-text line(s): %s',
                len(footer_log), footer_log,
            )
    except Exception as exc:
        logger.warning('[rewrite_engine] Footer pass skipped (non-fatal): %s', exc)
    rewrite_metrics['consistency_fixes'] = consistency_fixes

    change_stats = _count_text_changes(slides, rewritten_slides)
    if change_stats['changed'] == 0:
        raise NoRewriteChangesError(
            'No validated slide text changes were produced, so no rewritten PPTX was generated. '
            'Try a different tone or retry when an AI provider is available.'
        )
    steps.append(
        f"Rewrote {len(rewritten_slides)} slides: "
        f"{rewrite_metrics.get('ai_slides_succeeded', 0)} by AI, "
        f"{rewrite_metrics.get('rule_based_slides', 0)} by rule-based fallback; "
        f"{change_stats['changed']} of {change_stats['total']} text elements changed"
    )
    if consistency_fixes:
        steps.append(f'Unified {consistency_fixes} inconsistent spelling correction(s) across slides')
    report_progress(4, 'Checking rewritten text and slide structure')

    # ── Step 10: Validate rewritten content ────────────────────────────
    semantic_result = semantic_validator.validate(slides, rewritten_slides)
    structural_result = structural_validator.validate_slides(slides, rewritten_slides)
    steps.append('Running semantic coherence & structural preservation validation')

    # ── Step 11: Compute final assessment ──────────────────────────────
    final_assessment = final_validator.compute_final_assessment(
        original_slides=slides,
        rewritten_slides=rewritten_slides,
        analysis_results=quality_scores,
        semantic_results=semantic_result,
        structural_results=structural_result,
        statistics={
            'slide_count': stats.slide_count,
            'total_words': stats.total_words,
            'topic': presentation_context.get('overall_topic', ''),
        },
    )
    steps.append('Computing final quality assessment & improvement delta scores')

    # ── Step 12: Generate reports ─────────────────────────────────────
    executive_summary = exec_summary_gen.generate(final_assessment, {
        'slide_count': stats.slide_count,
        'total_words': stats.total_words,
        'topic': presentation_context.get('overall_topic', ''),
    }, quality_scores)
    analytics = analytics_gen.generate(final_assessment)
    recommendations = recommendations_gen.generate(
        final_assessment, quality_scores,
        design_analysis, storytelling_analysis,
        consistency_analysis, accessibility_analysis, speaker_analysis,
    )
    steps.append('Building executive summary, analytics dashboard, and prioritized recommendations')

    # ── Step 13: Save PPTX output ─────────────────────────────────────
    output_folder = ensure_download_folder()
    output_filename = generate_output_filename(original_filename)
    output_path = os.path.join(output_folder, output_filename)

    update_presentation_text(
        input_path=temp_file_path,
        rewritten_slides=_convert_gemini_to_ppt_format(rewritten_slides),
        original_slides=slides,
        output_path=output_path,
    )
    report_progress(5, 'Saving rewritten PowerPoint')

    # ── Step 14: Post-save validation (soft-fail) ───────────────────────
    preservation_ok = True
    comparison = {}
    try:
        comparison = compare_pptx_files(temp_file_path, output_path)
        preservation_ok = comparison.get('visually_identical_structure', False)
        if not preservation_ok:
            logger.warning(
                '[rewrite_engine] Preservation check flagged differences for %r: %s',
                original_filename, comparison.get('structural_mismatches', comparison),
            )
    except Exception as exc:
        logger.warning('[rewrite_engine] Preservation check itself failed: %s', exc)
        comparison = {}
    steps.append('Improved presentation generated')
    steps.append('Preservation verified' if preservation_ok else 'Preservation check flagged differences (file kept — please review)')

    # ── Step 15: Save per-slide comparison report ─────────────────────
    try:
        slide_report = build_slide_report(slides, rewritten_slides)
        report_payload = {
            'output_filename': output_filename,
            'original_filename': original_filename,
            'slide_count': slide_count,
            'quality_scores': quality_scores,
            'slides': slide_report,
            'generated_at': None,
            'final_assessment': final_assessment,
            'executive_summary': executive_summary,
            'analytics': analytics,
            'recommendations': recommendations,
            'statistics': {
                'slide_count': stats.slide_count,
                'total_words': stats.total_words,
                'total_characters': stats.total_characters,
                'total_textboxes': stats.total_textboxes,
                'total_tables': stats.total_tables,
                'total_charts': stats.total_charts,
                'total_images': stats.total_images,
                'total_bullets': stats.total_bullets,
                'average_words_per_slide': stats.average_words_per_slide,
                'longest_slide': stats.longest_slide,
                'shortest_slide': stats.shortest_slide,
                'reading_time_formatted': stats.reading_time_formatted,
                'speaking_time_formatted': stats.speaking_time_formatted,
                'estimated_duration_formatted': stats.estimated_presentation_duration_formatted,
            },
            'design_analysis': design_analysis,
            'storytelling_analysis': storytelling_analysis,
            'consistency_analysis': consistency_analysis,
            'duplicate_analysis': duplicate_analysis,
            'accessibility_analysis': accessibility_analysis,
            'speaker_analysis': speaker_analysis,
            'semantic_validation': semantic_result,
            'structural_validation': structural_result,
            'rewrite_metrics': rewrite_metrics,
            'change_stats': change_stats,
            'analysis_available': analysis_ok,
            'processing_metadata': {
                'mode': mode,
                'tone': tone,
                'model': provider.get_model_info() if ai_available else 'local',
                'provider': 'gemini' if ai_available else 'local',
                'retry_count': rewrite_metrics.get('retry_count', 0),
                'fallback_used': rewrite_metrics.get('fallback_count', 0) > 0,
                'total_ai_calls': rewrite_metrics.get('model_calls', 0),
            },
        }
        save_report_json(report_payload, output_path)
        steps.append('Comprehensive report saved')
    except Exception as exc:
        logger.warning('[rewrite_engine] Could not save report: %s', exc)
        steps.append('Report saving failed')

    # ── Build improvements list (from REAL changes) ───────────────────
    improvements = _build_improvements_summary(
        quality_scores, final_assessment,
        change_stats=change_stats,
        rewrite_metrics=rewrite_metrics,
        analysis_ok=analysis_ok,
    )

    if refresh_mode:  # REFRESH_MODE
        improvements.append('Refresh mode: generic filler was replaced with topic-specific content; design was not changed.')
        pics = sum(int(s.get('images', 0) or 0) for s in slides)
        if pics:
            improvements.append(f'{pics} picture(s) / chart image(s) were kept unchanged - replace any chart image that still shows placeholder figures.')

    report_progress(6, 'Rewrite complete')

    return {
        'output_filename': output_filename,
        'slides_processed': slide_count,
        'quality_scores': quality_scores,
        'improvements': improvements,
        'processing_steps': steps,
        'metadata': {
            **metadata,
            'preservation_check': _comparison_summary(comparison) if comparison else {},
        },
        'mode': mode,
        'tone': tone,
        'rewrite_history': {
            'changed_slide_numbers': change_stats['changed_slide_numbers'],
            'total_changes': change_stats['changed'],
            'changes': change_stats['changes'],
        },
        'analysis_available': analysis_ok,
        'execution_metrics': {**rewrite_metrics, **{
            'text_elements_total': change_stats['total'],
            'text_elements_changed': change_stats['changed'],
        }},
        'final_assessment': final_assessment,
        'executive_summary': executive_summary,
        'analytics': analytics,
        'recommendations': recommendations,
        'statistics': {
            'slide_count': stats.slide_count,
            'total_words': stats.total_words,
            'reading_time': stats.reading_time_formatted,
            'speaking_time': stats.speaking_time_formatted,
            'estimated_duration': stats.estimated_presentation_duration_formatted,
            'average_words_per_slide': stats.average_words_per_slide,
        },
    }


def run_analysis_pipeline(
    temp_file_path: str,
    original_filename: str,
) -> dict:
    """Analyze PPTX/PDF text without creating a rewritten file."""
    extension = get_extension(original_filename)
    if not is_allowed_for_analysis(original_filename):
        raise ValueError(f"Unsupported file type '{extension}'. Supported: .pptx, .pdf")
    file_size = os.path.getsize(temp_file_path)
    validate_upload_size(file_size)
    validate_file_content(temp_file_path, original_filename)
    steps = ['File validated']

    extracted_text = get_all_text_for_analysis(temp_file_path, original_filename)
    steps.append('Text extracted')

    slide_count = None
    slides = []
    if extension == '.pptx':
        slides = extract_slides(temp_file_path)
        slide_count = len(slides)
        steps.append(f'{slide_count} slides extracted')

    provider = get_provider()
    analyzer = PerSlideAnalyzer(provider=provider)
    quality_scores = analyzer.analyze_presentation(extracted_text, original_filename)
    if not isinstance(quality_scores, dict):
        quality_scores = {}
    analysis_ok = _analysis_is_real(quality_scores)
    quality_scores['analysis_available'] = analysis_ok
    steps.append('AI quality analysis complete' if analysis_ok
                 else 'AI quality analysis unavailable — default placeholder scores')

    extra_analyses = {}
    if slides:
        stats = compute_presentation_statistics(slides)
        extra_analyses['statistics'] = {
            'slide_count': stats.slide_count,
            'total_words': stats.total_words,
            'total_characters': stats.total_characters,
            'reading_time': stats.reading_time_formatted,
            'speaking_time': stats.speaking_time_formatted,
            'average_words_per_slide': stats.average_words_per_slide,
            'total_bullets': stats.total_bullets,
            'total_images': stats.total_images,
            'total_tables': stats.total_tables,
            'total_charts': stats.total_charts,
        }

        design_analyzer = DesignAnalyzer()
        extra_analyses['design'] = design_analyzer.analyze(slides)

        consistency_analyzer = ConsistencyAnalyzer()
        extra_analyses['consistency'] = consistency_analyzer.analyze(slides)

        steps.append('Advanced analyses complete')

    return {
        'quality_scores': quality_scores,
        'slides_analysed': slide_count,
        'processing_steps': steps,
        'analyses': extra_analyses,
    }


def _convert_gemini_to_ppt_format(gemini_slides: list[dict]) -> list[dict]:
    """Normalise already-validated Gemini output for the PPT writer."""
    result = []
    for slide in gemini_slides:
        textboxes = []
        for textbox in slide.get('textboxes', []):
            textboxes.append({
                'shape_index': textbox.get('shape_index'),
                'paragraphs': [_extract_paragraph_text(v) for v in textbox.get('paragraphs', [])],
            })
        tables = []
        tables_raw = slide.get('tables', [])
        if isinstance(tables_raw, list):
            for table in tables_raw:
                tables.append({
                    'shape_index': table.get('shape_index'),
                    'cells': [
                        {
                            'row_index': cell.get('row_index'),
                            'column_index': cell.get('column_index'),
                            'paragraphs': [_extract_paragraph_text(v) for v in cell.get('paragraphs', [])],
                        }
                        for cell in table.get('cells', [])
                    ],
                })
        charts = []
        charts_raw = slide.get('charts', [])
        if isinstance(charts_raw, list):
            charts = [
                {
                    'shape_index': chart.get('shape_index'),
                    'title_paragraphs': [_extract_paragraph_text(v) for v in chart.get('title_paragraphs', [])],
                }
                for chart in charts_raw
            ]
        result.append({
            'slide_number': slide.get('slide_number'),
            'textboxes': textboxes,
            'tables': tables,
            'charts': charts,
        })
    return result


def _comparison_summary(comparison: dict) -> dict:
    return {
        'slide_count_match': comparison.get('slide_count_match', False),
        'dimensions_match': comparison.get('dimensions_match', False),
        'visually_identical_structure': comparison.get('visually_identical_structure', False),
        'structural_mismatches': comparison.get('structural_mismatches', []),
        'text_changes_detected': len(comparison.get('text_changes_detected', [])),
        'package_mismatches': comparison.get('package_mismatches', []),
    }


def _build_improvements_summary(
    quality_scores: dict,
    final_assessment: Optional[dict] = None,
    change_stats: Optional[dict] = None,
    rewrite_metrics: Optional[dict] = None,
    analysis_ok: bool = False,
) -> list[str]:
    """Build improvement descriptions from what REALLY happened.

    Generic claims ("spelling corrected throughout") are only added when the
    AI analysis was real AND the rewrite actually changed text.
    """
    improvements = []
    change_stats = change_stats or {'total': 0, 'changed': 0}
    rewrite_metrics = rewrite_metrics or {}

    changed = change_stats.get('changed', 0)
    total = change_stats.get('total', 0)

    if total == 0:
        return ['No editable text was found in this presentation.']
    changes = change_stats.get('examples') or []
    actual_changes = [
        f"Slide {item['slide_number']}, {item['location']}: “{item['before']}” → “{item['after']}”"
        for item in changes[:5]
    ]
    # GAP_DETECTOR
    if rewrite_metrics.get('ai_failed'):
        return actual_changes + [
            'The AI could not rewrite this deck (quota or timeout). '
            'The file contains only minor rule-based edits. Please retry in a few minutes.'
        ]
    if rewrite_metrics.get('no_gaps_found'):
        return actual_changes + ['No content gaps were found, so no slides were rewritten.']
    if changed == 0:
        return ['No text changes were needed — the existing wording was kept.']

    improvements.append(f'{changed} of {total} text elements were edited across {change_stats.get("slides_changed", 0)} slides')
    improvements.extend(actual_changes)

    reverted = rewrite_metrics.get('guard_reverted_paragraphs', 0) + rewrite_metrics.get('guard_reverted_textboxes', 0)
    if reverted:
        improvements.append(
            f'{reverted} risky AI edit(s) were blocked to protect your numbers, footers and original wording'
        )

    if rewrite_metrics.get('fallback_count', 0) > 0:
        improvements.append(
            f"{rewrite_metrics['fallback_count']} slide(s) used the rule-based fallback because the AI was unavailable"
        )

    if rewrite_metrics.get('consistency_fixes', 0) > 0:
        improvements.append(
            f"{rewrite_metrics['consistency_fixes']} inconsistent spelling correction(s) were unified across slides"
        )

    if analysis_ok and final_assessment:
        overall = final_assessment.get('overall_score', 0)
        original = final_assessment.get('original_score', 0)
        if overall > original:
            improvements.append(f'Overall score improved from {original}/100 to {overall}/100')
        # Grammar/Spelling/Conciseness "addressed" lines were removed here:
        # they used to fire from a category SCORE THRESHOLD (e.g. Spelling
        # < 90) regardless of whether that category's text was actually
        # touched by the rewrite — a fabricated tick. Restore only once
        # final_validator exposes a real per-category before/after delta
        # to check against (e.g. final_assessment['category_deltas']).

    return improvements
