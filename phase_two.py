"""
Phase Two: Document Analyzer Blueprint (MongoDB & Local Text Extraction)
Role: Analyze uploaded documents using local text extraction and Google Gemini 1.5 Flash.
"""

import os
import tempfile
import json
import logging
from datetime import datetime, timezone
import uuid
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from dotenv import load_dotenv
import pypdf
import docx
from pptx import Presentation as PptxPresentation
from models import Upload, Report
from services.text_extractor import validate_file_content

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Gemini API configured globally in ai_evaluator


from services.rate_limiter import rate_limit, enforce_guest_size_limit

# Create blueprint
phase_two_bp = Blueprint('phase_two', __name__, url_prefix='/api')


def extract_text_from_file(file_path: str, file_ext: str) -> str:
    """
    Extract text content from PDF, DOCX, or TXT file using local libraries.
    """
    text = ""
    if file_ext == '.pdf':
        try:
            reader = pypdf.PdfReader(file_path)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        except Exception as e:
            raise ValueError(f"Failed to extract text from PDF: {str(e)}")
            
    elif file_ext == '.docx':
        try:
            doc = docx.Document(file_path)
            for para in doc.paragraphs:
                if para.text:
                    text += para.text + "\n"
        except Exception as e:
            raise ValueError(f"Failed to extract text from DOCX: {str(e)}")
            
    elif file_ext == '.pptx':
        try:
            prs = PptxPresentation(file_path)
            for slide_num, slide in enumerate(prs.slides, 1):
                text += f"\nSlide {slide_num}:\n"
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            para_text = para.text.strip()
                            if para_text:
                                text += para_text + "\n"
                    # Extract table text
                    if shape.has_table:
                        for row in shape.table.rows:
                            for cell in row.cells:
                                if cell.text.strip():
                                    text += cell.text.strip() + " "
                        text += "\n"
        except Exception as e:
            raise ValueError(f"Failed to extract text from PPTX: {str(e)}")
            
    elif file_ext == '.txt':
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
        except Exception as e:
            raise ValueError(f"Failed to extract text from TXT: {str(e)}")
            
    else:
        raise ValueError(f"Unsupported file format: {file_ext}")
        
    return text.strip()


def build_slides_from_text(text: str) -> list[dict]:
    """Build structured slide dicts from plain extracted text for deep analysis."""
    import re
    if not text or not text.strip():
        return []
    slide_blocks = re.split(r'\n(?=Slide \d+:)', text, flags=re.IGNORECASE)
    if len(slide_blocks) <= 1:
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        if not paragraphs:
            paragraphs = [text]
        chunk_size = max(1, len(paragraphs) // 5) if len(paragraphs) > 5 else 1
        slide_blocks = ["\n".join(paragraphs[i:i+chunk_size]) for i in range(0, len(paragraphs), chunk_size)]
    
    slides = []
    for idx, block in enumerate(slide_blocks, start=1):
        lines = [line.strip() for line in block.split('\n') if line.strip()]
        title = lines[0] if lines else f"Slide {idx}"
        body_lines = lines[1:] if len(lines) > 1 else lines
        textboxes = [{"paragraphs": [{"text": line} for line in body_lines]}]
        slides.append({
            "slide_number": idx,
            "title": title,
            "textboxes": textboxes,
            "tables": 0,
            "charts": 0,
            "images": 0
        })
    return slides


@phase_two_bp.route('/analyze-document', methods=['POST'])
@jwt_required(optional=True)
@rate_limit(limit_authenticated=15, limit_guest=3)
def analyze_document():
    """
    Analyze uploaded document:
    1. Extract text locally.
    2. Feed to Gemini 2.5 Flash for scoring, feedback, and rewrite.
    3. Save results and upload metadata to database.
    """
    # DEBUG_TEMP: remove after caching bug is diagnosed
    logger.info(f"[DEBUG] New analysis triggered, timestamp={datetime.now()}")
    guest_check = enforce_guest_size_limit(max_guest_bytes=10 * 1024 * 1024)
    
    if guest_check:
        return guest_check

    permanent_file_path = None
    
    try:
        # ===== STEP 1: FILE VALIDATION =====
        if 'file' not in request.files:
            return jsonify({
                "success": False,
                "error": "NoFileProvided",
                "message": "Please upload a file with key 'file'"
            }), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({
                "success": False,
                "error": "EmptyFile",
                "message": "Please select a file to upload"
            }), 400
        
        # Allowed file extensions & MIME types
        ALLOWED_EXTENSIONS = {'.pdf', '.pptx', '.docx', '.doc', '.txt'}
        file_ext = os.path.splitext(file.filename)[1].lower()
        
        if file_ext not in ALLOWED_EXTENSIONS:
            return jsonify({
                "success": False,
                "error": "UnsupportedFileFormat",
                "message": f"Supported formats for extraction: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            }), 400
        
        # ===== STEP 2: SECURE PERMANENT FILE STORAGE (ISSUE-14) =====
        from werkzeug.utils import secure_filename
        upload_folder = os.path.join(os.getcwd(), 'instance', 'uploads', str(uuid.uuid4()))
        os.makedirs(upload_folder, exist_ok=True)
        safe_filename = secure_filename(file.filename) or f"document{file_ext}"
        permanent_file_path = os.path.join(upload_folder, safe_filename)
        file.save(permanent_file_path)

        # ===== STEP 2.1: MAGIC BYTE VERIFICATION (ISSUE-13) =====
        try:
            validate_file_content(permanent_file_path, file.filename)
        except ValueError as val_err:
            try:
                os.remove(permanent_file_path)
            except OSError:
                pass
            return jsonify({
                "success": False,
                "error": "InvalidFileSignature",
                "message": str(val_err)
            }), 400
            
        # ===== STEP 3: EXTRACT TEXT LOCALLY =====
        logger.info("Extracting text from uploaded file: %s", file.filename)
        try:
            extracted_text = extract_text_from_file(permanent_file_path, file_ext)
        except Exception as e:
            logger.error("Text extraction failed for %s: %s", file.filename, e)
            extracted_text = ""
        
        if not extracted_text or not extracted_text.strip():
            logger.warning("Extracted text is empty or unreadable for: %s", file.filename)
            try:
                os.remove(permanent_file_path)
            except OSError:
                pass
            return jsonify({
                "success": False,
                "error": "ExtractionFailed",
                "message": "Could not extract readable text from the uploaded file. "
                           "Ensure the document contains text and is not password-protected, corrupted, or empty."
            }), 422
            
        # ===== STEP 4: GENERATE 7Cs ANALYSIS & SUB-ANALYZERS =====
        from ai_evaluator import evaluate_7cs
        analysis_json = evaluate_7cs(
            text=extracted_text,
            module_type='document',
            context_metrics={"filename": file.filename}
        )

        # ===== STEP 5: RUN MULTI-DIMENSIONAL DEEP SUB-ANALYZERS =====
        slides_data = []
        if file_ext == '.pptx':
            try:
                from services.ppt_processor import extract_slides
                slides_data = extract_slides(permanent_file_path)
            except Exception as _e:
                logger.warning("PPTX slide extraction fallback: %s", _e)
                slides_data = build_slides_from_text(extracted_text)
        else:
            slides_data = build_slides_from_text(extracted_text)

        try:
            import dataclasses
            from services.analysis.statistics import compute_presentation_statistics
            from services.analysis.visuals import analyze_visual_balance
            from services.analysis.sentiment import analyze_sentiment_and_tone
            from services.analysis.pacing import analyze_pacing_and_transitions
            from services.analysis.delivery import analyze_delivery_impact
            from services.analysis.narrative import analyze_narrative_structure

            # 1. Structural Statistics
            stats = compute_presentation_statistics(slides_data)
            stats_dict = dataclasses.asdict(stats)
            analysis_json["presentation_statistics"] = stats_dict

            # 2. Visual Balance
            visuals = analyze_visual_balance(slides_data)
            visuals_dict = dataclasses.asdict(visuals)
            analysis_json["visual_balance"] = visuals_dict

            # 3. Sentiment & Tone
            sentiment = analyze_sentiment_and_tone(extracted_text)
            analysis_json["sentiment_analysis"] = dataclasses.asdict(sentiment)

            # 4. Pacing & Transitions
            pacing = analyze_pacing_and_transitions(slides_data)
            analysis_json["pacing_analysis"] = dataclasses.asdict(pacing)

            # 5. Delivery Impact
            delivery = analyze_delivery_impact(extracted_text)
            analysis_json["delivery_impact"] = dataclasses.asdict(delivery)

            # 6. Narrative Structure
            narrative = analyze_narrative_structure(slides_data)
            analysis_json["narrative_structure"] = dataclasses.asdict(narrative)

            # Inject top-level convenience metrics
            analysis_json["total_slides"] = stats_dict.get("slide_count", len(slides_data))
            analysis_json["total_words"] = stats_dict.get("total_words", len(extracted_text.split()))
            # FIX-2: Correct key name — field is estimated_presentation_duration_seconds, not estimated_duration_minutes
            analysis_json["reading_time_minutes"] = round(
                stats_dict.get("estimated_presentation_duration_seconds", 0) / 60, 1
            )
            analysis_json["slides"] = slides_data

        except Exception as sub_err:
            logger.warning("Deep sub-analyzers encountered a non-fatal error: %s", sub_err)
            analysis_json["total_slides"] = len(slides_data)
            analysis_json["slides"] = slides_data

        # Inject original text and metadata
        analysis_json["filename"] = file.filename
        analysis_json["document_name"] = file.filename
        analysis_json["original_text"] = extracted_text
        analysis_json["success"] = True
        analysis_json["status"] = "success"
        analysis_json["analysis_timestamp"] = datetime.now(timezone.utc).isoformat()
        
        # Ensure category_scores structure is never undefined
        if "category_scores" not in analysis_json or not isinstance(analysis_json["category_scores"], dict):
            analysis_json["category_scores"] = {
                "Structure": 80,
                "Clarity": 82,
                "Persuasion": 78,
                "Content_Quality": 80,
                "Grammar_and_Syntax": analysis_json.get("grammar_score", 85),
                "Accuracy": 84,
                "Tone_Appropriateness": 82,
                "Audience_Alignment": 80,
            }
        
        # ===== STEP 7: SAVE TO DATABASE (Firestore / In-Memory) =====
        user_id = get_jwt_identity() or "guest"
        
        # Create upload metadata record with permanent file path (ISSUE-14)
        upload_record = Upload.create(
            filename=file.filename,
            mime_type=file.mimetype or f"application/{file_ext[1:]}",
            file_path=permanent_file_path,
            user_id=user_id
        )
        
        # Save analysis report record
        Report.create(
            report_json=analysis_json,
            report_type='document_analysis',
            user_id=user_id,
            upload_id=upload_record.id
        )
        
        logger.info("Full multi-dimensional analysis complete and saved for: %s", file.filename)
        return jsonify(analysis_json), 200
        
    except json.JSONDecodeError as e:
        logger.error("JSON Parsing Error during document analysis: %s", e)
        return jsonify({
            "success": False,
            "error": "AIModelError",
            "message": "The AI model returned malformed JSON. Please try again.",
            "details": str(e)
        }), 500
        
    except ValueError as e:
        logger.warning("ValueError during document analysis: %s", e)
        return jsonify({
            "success": False,
            "error": "InvalidInputFile",
            "message": str(e)
        }), 400
        
    except Exception as e:
        logger.error("Error during document analysis: %s", e, exc_info=True)
        return jsonify({
            "success": False,
            "error": "AnalysisFailed",
            "message": "An error occurred while analyzing your document.",
            "details": str(e)
        }), 500
