"""HTTP endpoints for the AI presentation rewriter."""

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape as _xml_escape

from flask import Blueprint, jsonify, request, send_file, url_for
from flask_jwt_extended import jwt_required
from services.rate_limiter import rate_limit
from werkzeug.exceptions import RequestEntityTooLarge

from services.download_service import (
    MAX_UPLOAD_BYTES,
    cleanup_old_files,
    ensure_download_folder,
    get_download_path,
    validate_upload_size,
)
from services.report_generator import load_report_for_pptx
from services.rewrite_engine import run_analysis_pipeline, run_rewrite_pipeline
from services.text_extractor import (
    get_extension,
    is_allowed_for_analysis,
    is_allowed_for_rewrite,
    validate_file_content,
)

logger = logging.getLogger(__name__)

presentation_rewriter_bp = Blueprint(
    'presentation_rewriter', __name__, url_prefix='/api/presentation-rewriter'
)
ensure_download_folder()


def _error(message: str, status: int, error: str = None):
    error_code = error or ("BadRequest" if status == 400 else ("NotFound" if status == 404 else ("PayloadTooLarge" if status == 413 else ("ServiceUnavailable" if status == 502 else "InternalError"))))
    return jsonify({'success': False, 'error': error_code, 'message': message}), status


@presentation_rewriter_bp.errorhandler(RequestEntityTooLarge)
def handle_upload_too_large(_exception):
    return _error(f'File too large. Maximum allowed size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.', 413, 'PayloadTooLarge')


def _save_validated_upload(file_storage, original_filename: str) -> str:
    """Persist an upload to an OS temp file, then validate size and signature."""
    extension = get_extension(original_filename)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=extension or '.upload') as stream:
            temporary_path = stream.name
        file_storage.save(temporary_path)
        validate_upload_size(os.path.getsize(temporary_path))
        validate_file_content(temporary_path, original_filename)
        return temporary_path
    except Exception:
        if temporary_path and os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                logger.warning('[routes/rewriter] Could not remove rejected upload.')
        raise


VALID_MODES = {'quick', 'professional', 'academic'}
VALID_TONES = {'professional', 'academic', 'business', 'technical', 'executive', 'marketing', 'formal', 'simple_english'}


# ─── Analysis availability (detect fake / default scores) ──────────────────

_UNAVAILABLE_MARKERS = (
    'could not be completed',
    'analysis was not available',
    'analysis unavailable',
    'default scores shown',
)

_FAKE_IMPROVEMENT_RE = re.compile(r'score improved|improved from \d+', re.I)

ANALYSIS_NOTICE = (
    'AI quality analysis could not be completed, so the scores shown are default '
    'placeholders and not a real evaluation. The text rewrite itself is unaffected.'
)


def _analysis_available(qs) -> bool:
    """True only if quality_scores came from a real AI analysis (not defaults)."""
    if not isinstance(qs, dict) or not qs:
        return False
    if qs.get('analysis_available') is False:
        return False
    evaluations = qs.get('seven_cs_evaluation') or {}
    blob = ' '.join([
        str(qs.get('summary', '')),
        ' '.join(str(i) for i in (qs.get('issues_found') or [])),
        ' '.join(str(v) for v in evaluations.values()),
    ]).lower()
    return not any(marker in blob for marker in _UNAVAILABLE_MARKERS)


# ─── Submit ────────────────────────────────────────────────────────────────

@presentation_rewriter_bp.route('/submit', methods=['POST'])
@jwt_required(optional=True)
@rate_limit(limit_authenticated=20, limit_guest=3)
def submit_presentation():
    """Accept a PPTX, rewrite supported text, and return a download URL.

    Form/query parameters:
        mode: Processing mode — quick | professional (default) | academic
        tone: Writing tone — professional (default) | academic | business | technical
              | executive | marketing | formal | simple_english
    """
    temporary_path = None
    started = datetime.now(timezone.utc)
    try:
        if request.content_length and request.content_length > MAX_UPLOAD_BYTES:
            return _error(f'File too large. Maximum allowed size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.', 413)
        file_storage = request.files.get('file')
        if file_storage is None:
            return _error('No file uploaded. Send multipart/form-data with field "file".', 400)
        original_filename = file_storage.filename or ''
        if not original_filename:
            return _error('No file selected.', 400)
        if not is_allowed_for_rewrite(original_filename):
            return _error(
                f'File type "{get_extension(original_filename)}" cannot be rewritten. '
                'Only .pptx is supported for text-preserving rewrites. PDFs are analysis-only.', 400
            )

        # Read mode and tone from form/query parameters
        mode = (request.form.get('mode') or request.args.get('mode') or 'professional').lower().strip()
        tone = (request.form.get('tone') or request.args.get('tone') or 'professional').lower().strip()
        if mode not in VALID_MODES:
            return _error(f"Invalid mode '{mode}'. Valid modes: {', '.join(sorted(VALID_MODES))}", 400)
        if tone not in VALID_TONES:
            return _error(f"Invalid tone '{tone}'. Valid tones: {', '.join(sorted(VALID_TONES))}", 400)

        temporary_path = _save_validated_upload(file_storage, original_filename)
        logger.info(
            '[routes/rewriter] Received %r (%s bytes) [mode=%s, tone=%s].',
            original_filename, os.path.getsize(temporary_path), mode, tone,
        )
        result = run_rewrite_pipeline(temporary_path, original_filename, mode=mode, tone=tone)
        try:
            cleanup_old_files()
        except Exception:
            logger.warning('[routes/rewriter] Output cleanup failed.', exc_info=True)

        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        download_url = url_for(
            'presentation_rewriter.download_improved_presentation',
            filename=result['output_filename'],
        )

        quality_scores = result.get('quality_scores', {}) or {}
        analysis_ok = _analysis_available(quality_scores)
        improvements = list(result.get('improvements', []) or [])

        response = {
            'success': True,
            'download_url': download_url,
            'output_filename': result['output_filename'],
            'slides_processed': result['slides_processed'],
            'processing_time': f'{elapsed:.2f} seconds',
            'quality_scores': quality_scores,
            'improvements': improvements,
            'processing_steps': result['processing_steps'],
            'metadata': result.get('metadata', {}),
            'mode': result.get('mode', mode),
            'tone': result.get('tone', tone),
            'analysis_available': analysis_ok,
            'message': 'Presentation successfully improved by AI.',
        }

        # Optional execution metrics (AI vs fallback counts, guard reverts)
        if result.get('execution_metrics'):
            response['execution_metrics'] = result['execution_metrics']

        # Extra platform data derived from scores. Only trust it when the
        # analysis was real; otherwise it would show invented numbers.
        derived_keys = ('final_assessment', 'executive_summary', 'analytics', 'recommendations')
        if analysis_ok:
            for key in derived_keys:
                if result.get(key):
                    response[key] = result[key]
        else:
            logger.warning(
                '[routes/rewriter] AI analysis unavailable for %r; hiding derived score data.',
                original_filename,
            )
            response['analysis_notice'] = ANALYSIS_NOTICE
            response['improvements'] = [i for i in improvements if not _FAKE_IMPROVEMENT_RE.search(str(i))]

        if result.get('statistics'):
            response['statistics'] = result['statistics']

        # Persist report history only for REAL analyses, so the dashboard does
        # not fill up with default placeholder scores.
        if analysis_ok:
            try:
                from flask_jwt_extended import get_jwt_identity
                from models import Report
                user_id = get_jwt_identity() or 'guest'
                report_json = {
                    "overall_score": quality_scores.get('overall_score'),
                    "topic": original_filename,
                    "presentation_title": original_filename,
                    "slides_processed": result.get('slides_processed', 0),
                    "category_scores": quality_scores.get('category_scores', {}),
                    "seven_cs_scores": quality_scores.get('seven_cs_scores', {}),
                    "improvements": response['improvements'],
                    "mode": mode,
                    "tone": tone,
                }
                Report.create(
                    report_json=report_json,
                    report_type='presentation_analysis',
                    user_id=user_id
                )
                logger.info("[routes/rewriter] Saved Report for presentation rewrite (%s) to user %s", original_filename, user_id)
            except Exception as _re:
                logger.warning("[routes/rewriter] Could not save Report history: %s", _re)
        else:
            logger.info('[routes/rewriter] Skipped saving Report history (analysis unavailable).')

        return jsonify(response), 200
    except ValueError as exc:
        logger.warning('[routes/rewriter] Validation error: %s', exc)
        return _error(str(exc), 400)
    except RuntimeError as exc:
        logger.error('[routes/rewriter] Processing error: %s', exc)
        return _error('Presentation processing is temporarily unavailable. Please try again.', 502)
    except Exception:
        logger.exception('[routes/rewriter] Unexpected processing error.')
        return _error('Unexpected presentation processing error.', 500)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                logger.warning('[routes/rewriter] Could not remove temporary upload %r.', temporary_path)


# ─── Analyze only ──────────────────────────────────────────────────────────

@presentation_rewriter_bp.route('/analyze', methods=['POST'])
@jwt_required(optional=True)
@rate_limit(limit_authenticated=20, limit_guest=3)
def analyze_presentation():
    """Analyze a PPTX or PDF without producing an output file."""
    temporary_path = None
    started = datetime.now(timezone.utc)
    try:
        if request.content_length and request.content_length > MAX_UPLOAD_BYTES:
            return _error(f'File too large. Maximum allowed size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.', 413)
        file_storage = request.files.get('file')
        if file_storage is None:
            return _error('No file uploaded. Send multipart/form-data with field "file".', 400)
        original_filename = file_storage.filename or ''
        if not original_filename:
            return _error('No file selected.', 400)
        if not is_allowed_for_analysis(original_filename):
            return _error('Unsupported file type. Please upload a .pptx or .pdf file.', 400)

        temporary_path = _save_validated_upload(file_storage, original_filename)
        result = run_analysis_pipeline(temporary_path, original_filename)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        quality_scores = result.get('quality_scores', {}) or {}
        analysis_ok = _analysis_available(quality_scores)
        payload = {
            'success': True,
            'quality_scores': quality_scores,
            'slides_analysed': result.get('slides_analysed'),
            'processing_steps': result['processing_steps'],
            'processing_time': f'{elapsed:.2f} seconds',
            'analysis_available': analysis_ok,
            'message': 'Quality analysis complete.' if analysis_ok else 'Quality analysis could not be completed.',
        }
        if not analysis_ok:
            payload['analysis_notice'] = ANALYSIS_NOTICE
        return jsonify(payload), 200
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception:
        logger.exception('[routes/rewriter] Analysis error.')
        return _error('Presentation analysis failed.', 500)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                logger.warning('[routes/rewriter] Could not remove temporary analysis upload.')


# ─── Download ──────────────────────────────────────────────────────────────

@presentation_rewriter_bp.route('/download/<filename>', methods=['GET'])
def download_improved_presentation(filename: str):
    """Serve only generated, validated PPTX output files."""
    try:
        path = get_download_path(filename)
        return send_file(
            path,
            as_attachment=True,
            download_name=os.path.basename(path),
            mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation',
            max_age=0,
        )
    except FileNotFoundError:
        return _error('File not found or has expired. Please resubmit the presentation.', 404)
    except Exception:
        logger.exception('[routes/rewriter] Download error.')
        return _error('Download failed.', 500)


# ─── Progress Tracking (poll-based) ────────────────────────────────────────
# In-memory map: filename -> progress dict. Written by the pipeline,
# read by the frontend polling endpoint.
_progress_map: dict[str, dict] = {}


def set_progress(filename: str, step_index: int, total_steps: int, message: str) -> None:
    """Store the current processing progress for a given output file."""
    _progress_map[filename] = {
        'step_index': step_index,
        'total_steps': total_steps,
        'message': message,
        'percent': round((step_index / max(total_steps, 1)) * 100),
    }


def _get_progress_key(filename: str) -> str:
    """Derive a progress lookup key from the original or output filename."""
    key = filename.replace('.pptx.report.json', '').replace('.pptx', '')
    return key


@presentation_rewriter_bp.route('/progress/<filename>', methods=['GET'])
def get_progress(filename: str):
    """Poll endpoint for real-time processing progress."""
    key = _get_progress_key(filename)
    data = _progress_map.get(key)
    if data is None:
        # Check if the output file exists — if it does, progress is done
        try:
            get_download_path(filename)
            return jsonify({
                'success': True,
                'step_index': 100,
                'total_steps': 100,
                'percent': 100,
                'message': 'Complete',
                'done': True,
            }), 200
        except FileNotFoundError:
            return jsonify({
                'success': False,
                'error': 'NotFound',
                'message': 'No progress information available for this file.',
            }), 404
    done = data['percent'] >= 100
    return jsonify({
        'success': True,
        'step_index': data['step_index'],
        'total_steps': data['total_steps'],
        'percent': data['percent'],
        'message': data['message'],
        'done': done,
    }), 200


# ─── Slide-by-Slide Report ──────────────────────────────────────────────────

@presentation_rewriter_bp.route('/report/<filename>', methods=['GET'])
def get_slide_report(filename: str):
    """Return the per-slide comparison report for a completed rewrite."""
    try:
        get_download_path(filename)  # verify file exists
    except FileNotFoundError:
        return _error('Presentation file not found. Please rewrite first.', 404)

    download_folder = ensure_download_folder()
    report = load_report_for_pptx(filename, download_folder)
    if not report:
        return _error('Slide-by-slide report is not available for this file.', 404)
    return jsonify({'success': True, 'report': report}), 200


# ─── PDF Report Download ────────────────────────────────────────────────────

@presentation_rewriter_bp.route('/report/<filename>/pdf', methods=['GET'])
def download_report_pdf(filename: str):
    """Generate and return a PDF of the detailed analysis report.

    The PDF includes: overall scores, slide-wise before/after comparison,
    grammar, spelling, readability, tone, and 7 Cs analysis.
    Uses the server-side report.json to render a text-based report.
    """
    try:
        get_download_path(filename)  # verify the PPTX exists
    except FileNotFoundError:
        return _error('Presentation file not found. Please rewrite first.', 404)

    download_folder = ensure_download_folder()
    report = load_report_for_pptx(filename, download_folder)
    if not report:
        return _error('Report data not available for PDF generation.', 404)

    try:
        pdf_bytes = _generate_report_pdf(report, filename)
        from io import BytesIO
        return send_file(
            BytesIO(pdf_bytes),
            as_attachment=True,
            download_name=filename.replace('.pptx', '_report.pdf'),
            mimetype='application/pdf',
            max_age=0,
        )
    except Exception:
        logger.exception('[routes/rewriter] PDF report generation failed.')
        return _error('PDF report generation failed.', 500)


# ─── PDF helpers ───────────────────────────────────────────────────────────

_LATIN_REPLACEMENTS = {
    '\u2014': '-', '\u2013': '-', '\u2018': "'", '\u2019': "'",
    '\u201c': '"', '\u201d': '"', '\u2022': '*', '\u2026': '...',
    '\u2192': '->', '\u25a0': '*', '\u00a0': ' ',
}


def _latin(text) -> str:
    """Make text safe for core PDF fonts (latin-1 only)."""
    text = str(text)
    for old, new in _LATIN_REPLACEMENTS.items():
        text = text.replace(old, new)
    return text.encode('latin-1', 'replace').decode('latin-1')


def _rl(text, limit: int = 600) -> str:
    """Escape text for reportlab Paragraph markup and keep line breaks."""
    return _xml_escape(str(text)[:limit]).replace('\n', '<br/>')


def _report_text_lines(report: dict, pptx_filename: str) -> list:
    """Plain-text version of the report. Lines starting with '## ' are headings."""
    qs = report.get('quality_scores', {}) or {}
    lines = ['## Presentation Rewrite Report', f'File: {pptx_filename}', '']

    if qs:
        if not _analysis_available(qs):
            lines += ['NOTE: ' + ANALYSIS_NOTICE, '']
        lines.append(f"Overall Score: {qs.get('overall_score', 'N/A')}/100 - Grade {qs.get('grade', 'N/A')}")
        lines += ['', '## Category Scores']
        for k, v in (qs.get('category_scores') or {}).items():
            lines.append(f'  {k}: {v}/100')
        lines += ['', '## 7 Cs Scores']
        for k, v in (qs.get('seven_cs_scores') or {}).items():
            lines.append(f'  {k}: {v}/100')
        lines.append('')

    slides = report.get('slides', []) or []
    lines += [f'## Slide-by-Slide Comparison ({len(slides)} slides)', '']
    for slide in slides:
        sn = slide.get('slide_number', '?')
        lines.append(f"Slide {sn}: {slide.get('title', f'Slide {sn}')}")
        for tb in slide.get('textboxes', []) or []:
            orig = '\n'.join(tb.get('original_paragraphs', []))[:600]
            impr = '\n'.join(tb.get('improved_paragraphs', []))[:600]
            if orig != impr:
                lines.append(f'  Original: {orig}')
                lines.append(f'  Improved: {impr}')
        for table in slide.get('tables', []) or []:
            for cell in table.get('cells', []) or []:
                orig = '\n'.join(cell.get('original_paragraphs', []))[:300]
                impr = '\n'.join(cell.get('improved_paragraphs', []))[:300]
                if orig != impr:
                    lines.append(f"  Cell ({cell.get('row_index')},{cell.get('column_index')}): {orig} -> {impr}")
        lines.append('')

    if qs.get('strengths'):
        lines.append('## Strengths')
        lines += [f'  - {s}' for s in qs['strengths']]
        lines.append('')
    if qs.get('recommendations'):
        lines.append('## Recommendations')
        lines += [f'  - {r}' for r in qs['recommendations']]
        lines.append('')
    return lines


def _generate_report_pdf(report: dict, pptx_filename: str) -> bytes:
    """Build the report PDF: reportlab -> FPDF -> minimal fallback."""
    try:
        return _reportlab_pdf(report, pptx_filename)
    except Exception:
        logger.warning('[routes/rewriter] reportlab PDF failed; trying FPDF.', exc_info=True)
    try:
        return _simple_pdf(report, pptx_filename)
    except Exception:
        logger.warning('[routes/rewriter] FPDF PDF failed; using minimal PDF.', exc_info=True)
    return _minimal_pdf(report, pptx_filename)


def _reportlab_pdf(report: dict, pptx_filename: str) -> bytes:
    """Generate PDF using reportlab (rich formatting)."""
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Presentation Rewrite Report", styles['Title']))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(f"File: {_rl(pptx_filename, 200)}", styles['Normal']))
    story.append(Spacer(1, 4 * mm))

    qs = report.get('quality_scores', {}) or {}
    if qs:
        if not _analysis_available(qs):
            story.append(Paragraph(f"<b>Note:</b> {_rl(ANALYSIS_NOTICE, 400)}", styles['Normal']))
            story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(
            f"<b>Overall Score:</b> {_rl(qs.get('overall_score', 'N/A'), 20)}/100 - Grade {_rl(qs.get('grade', 'N/A'), 5)}",
            styles['Normal']))
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph("<b>Category Scores</b>", styles['Heading2']))
        cat_data = [['Category', 'Score']]
        for k, v in (qs.get('category_scores') or {}).items():
            cat_data.append([str(k), str(v)])
        t = Table(cat_data, colWidths=[120 * mm, 40 * mm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6366f1')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f8fafc'), colors.white]),
        ]))
        story.append(t)
        story.append(Spacer(1, 4 * mm))

    slides = report.get('slides', []) or []
    story.append(Paragraph(f"<b>Slide-by-Slide Comparison ({len(slides)} slides)</b>", styles['Heading2']))
    story.append(Spacer(1, 3 * mm))

    for slide in slides:
        sn = slide.get('slide_number', '?')
        title = slide.get('title', f'Slide {sn}')
        story.append(Paragraph(f"<b>Slide {_rl(sn, 10)}: {_rl(title, 150)}</b>", styles['Heading3']))
        story.append(Spacer(1, 2 * mm))

        for tb in slide.get('textboxes', []) or []:
            orig_text = '\n'.join(tb.get('original_paragraphs', []))
            impr_text = '\n'.join(tb.get('improved_paragraphs', []))
            if orig_text == impr_text:
                continue
            story.append(Paragraph(f"<i>Textbox (shape {_rl(tb.get('shape_index', '?'), 10)}):</i>", styles['Normal']))
            story.append(Paragraph(f"<font color='#dc2626'>Original:</font> {_rl(orig_text)}", styles['Normal']))
            story.append(Paragraph(f"<font color='#16a34a'>Improved:</font> {_rl(impr_text)}", styles['Normal']))
            story.append(Spacer(1, 2 * mm))

        for table in slide.get('tables', []) or []:
            story.append(Paragraph(f"<i>Table (shape {_rl(table.get('shape_index', '?'), 10)}):</i>", styles['Normal']))
            for cell in table.get('cells', []) or []:
                orig = '\n'.join(cell.get('original_paragraphs', []))
                impr = '\n'.join(cell.get('improved_paragraphs', []))
                if orig != impr:
                    story.append(Paragraph(
                        f"  Cell ({_rl(cell.get('row_index'), 5)},{_rl(cell.get('column_index'), 5)}): "
                        f"{_rl(orig, 200)} -&gt; {_rl(impr, 200)}", styles['Normal']))
            story.append(Spacer(1, 2 * mm))

        story.append(Spacer(1, 4 * mm))

    if qs.get('seven_cs_scores'):
        story.append(Paragraph("<b>7 Cs of Communication</b>", styles['Heading2']))
        cs_data = [['Criterion', 'Score', 'Evaluation']]
        evaluations = qs.get('seven_cs_evaluation') or {}
        small = styles['Normal'].clone('cs_small', fontSize=8)
        for k, v in qs.get('seven_cs_scores', {}).items():
            cs_data.append([str(k), str(v), Paragraph(_rl(evaluations.get(k, ''), 300), small)])
        ct = Table(cs_data, colWidths=[40 * mm, 20 * mm, 110 * mm])
        ct.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6366f1')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(ct)

    if qs.get('strengths'):
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("<b>Strengths</b>", styles['Heading2']))
        for s in qs['strengths']:
            story.append(Paragraph(f"&bull; {_rl(s, 400)}", styles['Normal']))
    if qs.get('recommendations'):
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph("<b>Recommendations</b>", styles['Heading2']))
        for r in qs['recommendations']:
            story.append(Paragraph(f"&bull; {_rl(r, 400)}", styles['Normal']))

    doc.build(story)
    return buf.getvalue()


def _simple_pdf(report: dict, pptx_filename: str) -> bytes:
    """Plain-text PDF using FPDF (no reportlab needed)."""
    from fpdf import FPDF  # ImportError is handled by the caller

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    for line in _report_text_lines(report, pptx_filename):
        if line.startswith('## '):
            pdf.set_font('Helvetica', 'B', 11)
            text = _latin(line[3:])
        else:
            pdf.set_font('Helvetica', size=9)
            text = _latin(line)
        if not text.strip():
            pdf.ln(3)
            continue
        pdf.multi_cell(0, 5, text, new_x='LMARGIN', new_y='NEXT')

    return bytes(pdf.output())


def _minimal_pdf(report: dict, pptx_filename: str) -> bytes:
    """Last-resort valid multi-page text PDF built by hand (correct xref offsets)."""
    import textwrap

    wrapped = []
    for line in _report_text_lines(report, pptx_filename):
        text = _latin(line[3:] if line.startswith('## ') else line)
        wrapped.extend(textwrap.wrap(text, 95) or [''])

    per_page = 52
    pages = [wrapped[i:i + per_page] for i in range(0, len(wrapped), per_page)] or [[]]

    def esc(s: str) -> str:
        return s.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')

    kids = [4 + 2 * i for i in range(len(pages))]
    objects = {
        1: b'<< /Type /Catalog /Pages 2 0 R >>',
        2: ('<< /Type /Pages /Kids [%s] /Count %d >>' % (
            ' '.join(f'{k} 0 R' for k in kids), len(pages))).encode(),
        3: b'<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>',
    }
    for i, page_lines in enumerate(pages):
        page_obj, content_obj = 4 + 2 * i, 5 + 2 * i
        stream = b'BT\n/F1 9 Tf\n50 750 Td\n12 TL\n'
        for line in page_lines:
            stream += f'({esc(line)}) Tj T*\n'.encode('latin-1', 'replace')
        stream += b'ET'
        objects[page_obj] = (
            f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_obj} 0 R '
            f'/Resources << /Font << /F1 3 0 R >> >> >>'
        ).encode()
        objects[content_obj] = b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream'

    out = bytearray(b'%PDF-1.4\n')
    offsets = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f'{num} 0 obj\n'.encode() + objects[num] + b'\nendobj\n'

    max_obj = max(objects)
    xref_pos = len(out)
    out += f'xref\n0 {max_obj + 1}\n0000000000 65535 f \n'.encode()
    for num in range(1, max_obj + 1):
        out += f'{offsets[num]:010d} 00000 n \n'.encode()
    out += f'trailer\n<< /Size {max_obj + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n'.encode()
    return bytes(out)