"""
Flask Blueprint for AI Presentation Generator (Enhanced Multi-Stage Gamma Engine)
Exposes endpoints for:
- Outline generation & preview (/outline)
- Generation from customized outline (/generate-from-outline)
- Document seed ingestion (/import-seed)
- 1-step generation (/generate)
- Available themes catalog (/themes)
- PPTX file download (/download/<filename>)
"""

import os
import logging
import tempfile
import re
from flask import Blueprint, request, jsonify, send_from_directory
from flask_jwt_extended import jwt_required
from services.rate_limiter import rate_limit
from services.presentation_generator import (
    generate_presentation_outline,
    build_pptx_from_outline,
    PresentationGenerationError,
    GENERATED_FOLDER,
    THEMES
)
from phase_two import extract_text_from_file
from services.text_extractor import get_extension, is_allowed_for_analysis

logger = logging.getLogger(__name__)


def _safe_int(value, default: int = 6, min_val: int = 3, max_val: int = 20) -> int:
    """Safely parse an integer from user input, returning default on failure.

    AUDIT-05: Bare int() casts on request parameters raise ValueError on non-numeric
    input (empty string, text, null), which propagates as an unhandled HTTP 500.
    This helper avoids that by falling back to a sensible default and clamping
    the result to the accepted range [min_val, max_val].
    """
    try:
        return max(min_val, min(max_val, int(value)))
    except (ValueError, TypeError):
        return default


presentation_generator_bp = Blueprint(
    'presentation_generator',
    __name__,
    url_prefix='/api/presentation-generator'
)

_generation_progress: dict[str, dict] = {}
_PROGRESS_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,80}$')


def _progress_key(value) -> str:
    value = str(value or '').strip()
    return value if _PROGRESS_ID_RE.fullmatch(value) else ''


def _set_progress(job_id: str, percent: int, message: str, error: str = '') -> None:
    if job_id:
        _generation_progress[job_id] = {
            'percent': max(0, min(100, int(percent))),
            'message': message,
            'error': error,
        }


def _progress_callback(job_id: str, start: int = 0, end: int = 100):
    def update(percent: int, message: str):
        mapped = start + round((end - start) * max(0, min(100, int(percent))) / 100)
        _set_progress(job_id, mapped, message)
    return update


def _save_generation_history(outline_data: dict, filename: str, theme: str) -> None:
    try:
        from flask_jwt_extended import get_jwt_identity
        from models import Report
        topic = outline_data.get('topic', 'Presentation')
        Report.create(
            report_json={
                'overall_score': None,
                'topic': topic,
                'presentation_title': outline_data.get('presentation_title', topic),
                'output_filename': filename,
                'slides_count': len(outline_data.get('slides', [])),
                'presentation_type': outline_data.get('presentation_type', 'Auto-detect'),
                'target_audience': outline_data.get('target_audience', ''),
                'tone': outline_data.get('tone', ''),
                'theme': theme,
            },
            report_type='presentation_generated',
            user_id=get_jwt_identity() or 'guest',
        )
    except Exception as exc:
        logger.warning('[presentation_generator_bp] Could not save generation history: %s', exc)


@presentation_generator_bp.route('/progress/<job_id>', methods=['GET'])
@jwt_required(optional=True)
def get_generation_progress(job_id: str):
    job_id = _progress_key(job_id)
    progress = _generation_progress.get(job_id) if job_id else None
    if progress is None:
        return jsonify({'success': False, 'error': 'NotFound', 'message': 'Generation progress is not available.'}), 404
    return jsonify({
        'success': True,
        **progress,
        'done': progress['percent'] >= 100 or bool(progress.get('error')),
    }), 200


@presentation_generator_bp.route('/themes', methods=['GET'])
def get_themes():
    """Returns available color themes and their metadata."""
    themes_list = []
    for tid, tval in THEMES.items():
        themes_list.append({
            'id': tid,
            'name': tval.get('name', tid.replace('_', ' ').title()),
            'bg_hex': f"#{tval['bg_color'][0]:02x}{tval['bg_color'][1]:02x}{tval['bg_color'][2]:02x}",
            'title_hex': f"#{tval['title_color'][0]:02x}{tval['title_color'][1]:02x}{tval['title_color'][2]:02x}",
            'accent_hex': f"#{tval['accent_color'][0]:02x}{tval['accent_color'][1]:02x}{tval['accent_color'][2]:02x}",
            'card_hex': f"#{tval['card_color'][0]:02x}{tval['card_color'][1]:02x}{tval['card_color'][2]:02x}"
        })
    return jsonify({'success': True, 'themes': themes_list}), 200


@presentation_generator_bp.route('/outline', methods=['POST'])
@jwt_required(optional=True)
@rate_limit(limit_authenticated=20, limit_guest=3)
def generate_outline_endpoint():
    """
    Generate an editable narrative outline checkpoint before PPTX rendering.
    JSON input:
    {
      "topic": "Autonomous Driving Systems",
      "slide_count": 6,
      "tone": "Academic",
      "target_audience": "Engineering Faculty"
    }
    """
    try:
        data = request.get_json() or {}
        topic = data.get('topic', '').strip()
        if not topic:
            return jsonify({'success': False, 'error': 'ValidationError', 'message': 'Please provide a topic for the presentation.'}), 400

        slide_count = _safe_int(data.get('slide_count'), default=5)
        tone = data.get('tone', 'Professional').strip()
        audience = data.get('target_audience', 'General Audience').strip()
        presentation_type = data.get('presentation_type', 'Auto-detect').strip() or 'Auto-detect'
        progress_id = _progress_key(data.get('progress_id'))
        _set_progress(progress_id, 5, 'Preparing the requested outline')

        outline_data = generate_presentation_outline(
            topic=topic,
            slide_count=slide_count,
            tone=tone,
            audience=audience,
            presentation_type=presentation_type,
            progress_callback=_progress_callback(progress_id, 5, 95),
        )
        _set_progress(progress_id, 100, 'Outline ready for review')

        return jsonify({
            'success': True,
            'message': 'Outline generated successfully!',
            'outline': outline_data
        }), 200

    except PresentationGenerationError as e:
        progress_id = _progress_key((request.get_json(silent=True) or {}).get('progress_id'))
        _set_progress(progress_id, 100, str(e), e.code)
        return jsonify({'success': False, 'error': e.code, 'message': str(e)}), e.status_code
    except Exception as e:
        logger.error("[presentation_generator_bp] Outline generation failed: %s", e, exc_info=True)
        _is_prod = os.getenv('FLASK_ENV', 'development').strip().lower() == 'production'
        msg = 'Failed to generate presentation outline.' if _is_prod else f'Failed to generate outline: {str(e)}'
        return jsonify({'success': False, 'error': 'ProcessingError', 'message': msg}), 500


@presentation_generator_bp.route('/import-seed', methods=['POST'])
@jwt_required(optional=True)
@rate_limit(limit_authenticated=20, limit_guest=3)
def import_seed_document():
    """
    Upload a document (.pdf, .docx, .pptx) to seed a new presentation outline.
    """
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'ValidationError', 'message': 'No file uploaded.'}), 400

        uploaded_file = request.files['file']
        if not uploaded_file or not uploaded_file.filename:
            return jsonify({'success': False, 'error': 'ValidationError', 'message': 'Invalid file upload.'}), 400

        ext = get_extension(uploaded_file.filename)
        if not is_allowed_for_analysis(uploaded_file.filename):
            return jsonify({'success': False, 'error': 'UnsupportedFormat', 'message': 'Unsupported file format. Please upload .pptx, .pdf, or .docx.'}), 400

        slide_count = _safe_int(request.form.get('slide_count'), default=6)
        tone = request.form.get('tone', 'Professional')
        audience = request.form.get('target_audience', 'General Audience')
        presentation_type = request.form.get('presentation_type', 'Auto-detect') or 'Auto-detect'
        progress_id = _progress_key(request.form.get('progress_id'))
        _set_progress(progress_id, 5, 'Reading the uploaded source document')

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            uploaded_file.save(tmp.name)
            tmp_path = tmp.name

        try:
            extracted_text = extract_text_from_file(tmp_path, ext)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        if not extracted_text.strip():
            return jsonify({'success': False, 'error': 'ExtractionFailed', 'message': 'Could not extract text content from the uploaded document.'}), 400

        # Infer topic from first lines or filename
        first_line = extracted_text.strip().split('\n')[0][:80]
        topic = first_line if len(first_line) > 5 else uploaded_file.filename.rsplit('.', 1)[0].replace('_', ' ').title()

        outline_data = generate_presentation_outline(
            topic=topic,
            slide_count=slide_count,
            tone=tone,
            audience=audience,
            seed_text=extracted_text[:6000],
            presentation_type=presentation_type,
            progress_callback=_progress_callback(progress_id, 25, 95),
        )
        _set_progress(progress_id, 100, 'Source-based outline ready')

        return jsonify({
            'success': True,
            'message': f'Document parsed! Seeded outline from {uploaded_file.filename}',
            'source_filename': uploaded_file.filename,
            'outline': outline_data
        }), 200

    except PresentationGenerationError as e:
        progress_id = _progress_key(request.form.get('progress_id'))
        _set_progress(progress_id, 100, str(e), e.code)
        return jsonify({'success': False, 'error': e.code, 'message': str(e)}), e.status_code
    except Exception as e:
        logger.error("[presentation_generator_bp] Seed import failed: %s", e, exc_info=True)
        _is_prod = os.getenv('FLASK_ENV', 'development').strip().lower() == 'production'
        msg = 'Failed to parse seed document.' if _is_prod else f'Failed to parse seed document: {str(e)}'
        return jsonify({'success': False, 'error': 'ProcessingError', 'message': msg}), 500


@presentation_generator_bp.route('/generate-from-outline', methods=['POST'])
@jwt_required(optional=True)
@rate_limit(limit_authenticated=20, limit_guest=3)
def generate_from_outline_endpoint():
    """
    Build final PPTX from confirmed outline and custom theme settings.
    JSON input:
    {
      "outline": { ... outline_data ... },
      "theme": "modern_dark",
      "custom_overrides": {
        "show_slide_numbers": true,
        "confidentiality_tag": "Presenova AI"
      }
    }
    """
    try:
        data = request.get_json() or {}
        outline_data = data.get('outline')
        if not outline_data or not isinstance(outline_data, dict):
            return jsonify({'success': False, 'error': 'ValidationError', 'message': 'Valid outline data is required.'}), 400

        theme = data.get('theme', 'modern_dark').strip()
        custom_overrides = data.get('custom_overrides', {})
        progress_id = _progress_key(data.get('progress_id'))
        _set_progress(progress_id, 5, 'Preparing the selected theme')

        filepath = build_pptx_from_outline(
            outline_data=outline_data,
            theme_name=theme,
            custom_overrides=custom_overrides,
            progress_callback=_progress_callback(progress_id, 5, 95),
        )
        filename = os.path.basename(filepath)
        _save_generation_history(outline_data, filename, theme)
        _set_progress(progress_id, 100, 'Presentation ready to download')

        return jsonify({
            'success': True,
            'message': 'Presentation built successfully with smart visual layouts!',
            'output_filename': filename,
            'download_url': f'/api/presentation-generator/download/{filename}',
            'slides_generated': len(outline_data.get('slides', [])),
            'theme': theme
        }), 200

    except PresentationGenerationError as e:
        progress_id = _progress_key((request.get_json(silent=True) or {}).get('progress_id'))
        _set_progress(progress_id, 100, str(e), e.code)
        return jsonify({'success': False, 'error': e.code, 'message': str(e)}), e.status_code
    except Exception as e:
        logger.error("[presentation_generator_bp] PPTX synthesis from outline failed: %s", e, exc_info=True)
        _is_prod = os.getenv('FLASK_ENV', 'development').strip().lower() == 'production'
        msg = 'Failed to build presentation.' if _is_prod else f'Failed to build presentation: {str(e)}'
        return jsonify({'success': False, 'error': 'ProcessingError', 'message': msg}), 500


@presentation_generator_bp.route('/generate', methods=['POST'])
@jwt_required(optional=True)
@rate_limit(limit_authenticated=20, limit_guest=3)
def generate_presentation():
    """1-Click generation endpoint preserved for direct invocation."""
    try:
        data = request.get_json() or {}
        topic = data.get('topic', '').strip()
        if not topic:
            return jsonify({'success': False, 'error': 'ValidationError', 'message': 'Please provide a topic for the presentation.'}), 400

        slide_count = _safe_int(data.get('slide_count'), default=5)
        tone = data.get('tone', 'Professional').strip()
        theme = data.get('theme', 'modern_dark').strip()
        audience = data.get('target_audience', 'General Audience').strip()
        presentation_type = data.get('presentation_type', 'Auto-detect').strip() or 'Auto-detect'
        progress_id = _progress_key(data.get('progress_id'))
        _set_progress(progress_id, 5, 'Preparing your presentation request')

        outline_data = generate_presentation_outline(
            topic=topic,
            slide_count=slide_count,
            tone=tone,
            audience=audience,
            presentation_type=presentation_type,
            progress_callback=_progress_callback(progress_id, 5, 45),
        )

        filepath = build_pptx_from_outline(
            outline_data, theme_name=theme,
            progress_callback=_progress_callback(progress_id, 45, 95),
        )
        filename = os.path.basename(filepath)
        _save_generation_history(outline_data, filename, theme)
        _set_progress(progress_id, 100, 'Presentation ready to download')

        return jsonify({
            'success': True,
            'message': 'Presentation generated successfully!',
            'output_filename': filename,
            'download_url': f'/api/presentation-generator/download/{filename}',
            'slides_generated': len(outline_data.get('slides', [])),
            'outline': outline_data,
            'theme': theme
        }), 200

    except PresentationGenerationError as e:
        progress_id = _progress_key((request.get_json(silent=True) or {}).get('progress_id'))
        _set_progress(progress_id, 100, str(e), e.code)
        return jsonify({'success': False, 'error': e.code, 'message': str(e)}), e.status_code
    except Exception as e:
        logger.error("[presentation_generator_bp] Generation failed: %s", e, exc_info=True)
        _is_prod = os.getenv('FLASK_ENV', 'development').strip().lower() == 'production'
        msg = 'Failed to generate presentation.' if _is_prod else f'Failed to generate presentation: {str(e)}'
        return jsonify({'success': False, 'error': 'ProcessingError', 'message': msg}), 500


@presentation_generator_bp.route('/download/<filename>', methods=['GET'])
@jwt_required(optional=True)
def download_presentation(filename: str):
    """Serve generated .pptx file for download."""
    try:
        safe_filename = os.path.basename(filename)
        filepath = os.path.join(GENERATED_FOLDER, safe_filename)
        if not os.path.exists(filepath):
            return jsonify({'success': False, 'error': 'NotFound', 'message': 'Requested presentation file was not found.'}), 404

        return send_from_directory(
            GENERATED_FOLDER,
            safe_filename,
            as_attachment=True,
            download_name=f"Presentation_{safe_filename}"
        )
    except Exception as e:
        logger.error("[presentation_generator_bp] Download failed: %s", e)
        return jsonify({'success': False, 'error': 'InternalError', 'message': 'Failed to download file.'}), 500
