"""
Phase Four: Speech Analyzer & Presentation Coach Blueprint (MongoDB & Groq Whisper integration)
Role: Expose endpoints to analyze speech text and WAV audio files using Groq Whisper STT and Gemini.
"""

import os
import tempfile
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from groq import Groq, RateLimitError, APITimeoutError
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from models import Upload, Report

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

def _is_valid_groq_key(key: str | None) -> bool:
    """Groq API keys start with 'gsk_'."""
    return bool(key) and key.startswith('gsk_') and len(key) > 20

# Initialize Groq client if key is configured.
# Timeout raised from 4s to 30s: audio uploads take several seconds, and a 4s timeout
# would now surface as a "transcription failed" error instead of a fake transcript.
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
groq_client = None
if _is_valid_groq_key(GROQ_API_KEY):
    groq_client = Groq(api_key=GROQ_API_KEY, timeout=30.0)
else:
    logger.warning("[PHASE-4] GROQ_API_KEY not configured or invalid. STT unavailable.")

# Whisper drops um/uh unless the prompt shows fillers.
WHISPER_FILLER_PROMPT = "Umm, let me think, like, hmm... Okay, so, uh, basically, you know, I'm, like, thinking about it, actually."

# Gemini API configured globally in ai_evaluator

def run_gemini_speech_analysis(
    transcript: str,
    speech_speed_wpm: float,
    filler_count: int,
    filler_percentage: float,
    repetition_count: int,
    duration_seconds: int
) -> dict:
    """
    Run Gemini to generate a full 7Cs speech scorecard via central evaluator.
    """
    from ai_evaluator import evaluate_7cs
    return evaluate_7cs(
        text=transcript,
        module_type='speech',
        context_metrics={
            "speech_speed_wpm": speech_speed_wpm,
            "filler_count": filler_count,
            "filler_percentage": filler_percentage,
            "repetition_count": repetition_count,
            "duration_seconds": duration_seconds
        }
    )

from services.rate_limiter import rate_limit, enforce_guest_size_limit

# Create blueprint
phase_four_bp = Blueprint('phase_four', __name__, url_prefix='/api')


def count_repetitions(text: str) -> int:
    """
    Count consecutive word repetitions in the text.
    Single-letter repeats are ignored except "I I" (a common spoken stumble).
    """
    words = text.lower().split()
    repetition_count = 0

    for i in range(len(words) - 1):
        current_word = words[i].strip('.,!?;:')
        next_word = words[i + 1].strip('.,!?;:')

        if current_word == next_word and (len(current_word) > 1 or current_word == 'i'):
            repetition_count += 1

    return repetition_count


def unify_clarity(gemini_result: dict, rule_clarity: int) -> int:
    """One final clarity number: average of the rule-based score and the AI score.

    The same number is written back into the AI category score ("Clarity") and the
    7Cs score ("Clear") so the card, category and 7Cs views never disagree.
    """
    cats = gemini_result.get("category_scores") or {}
    ai_clarity = cats.get("Clarity")
    if isinstance(ai_clarity, (int, float)):
        final = int(round((rule_clarity + ai_clarity) / 2))
    else:
        final = rule_clarity
    if cats:
        cats["Clarity"] = final
    scores = gemini_result.get("seven_cs_scores")
    if scores:
        scores["Clear"] = final
    return final


def analyze_speech_quality(
    word_count: int,
    wpm: float,
    filler_count: int,
    filler_percentage: float,
    repetition_count: int
) -> dict:
    """
    Generate actionable feedback based on speech metrics.
    """
    feedback = []
    clarity_score = 100

    # WPM Pacing Analysis (optimal: 120-160 WPM)
    if wpm > 160:
        feedback.append(f"⚠️ Speaking too fast ({wpm} WPM). Aim for 120-160 WPM for better clarity.")
        clarity_score -= 15
    elif wpm < 80:
        feedback.append(f"⚠️ Speaking too slowly ({wpm} WPM). Try to maintain 120-160 WPM.")
        clarity_score -= 10
    elif wpm >= 120 and wpm <= 160:
        feedback.append(f"✅ Excellent speaking pace ({wpm} WPM).")

    # Filler Words Analysis
    if filler_count > 10:
        feedback.append(f"⚠️ High filler word usage ({filler_count} instances, {filler_percentage:.1f}%). Try to eliminate them for professional delivery.")
        clarity_score -= 20
    elif filler_count > 5:
        feedback.append(f"⚠️ Moderate filler word usage ({filler_count} instances). Reduce for improvement.")
        clarity_score -= 10
    elif filler_count == 0:
        feedback.append("✅ No filler words detected. Excellent!")
    else:
        feedback.append(f"✅ Good filler word control ({filler_count} instances).")

    # Repetition Analysis
    if repetition_count > 5:
        feedback.append(f"⚠️ Multiple word repetitions detected ({repetition_count}). Vary your vocabulary.")
        clarity_score -= 10
    elif repetition_count > 0:
        feedback.append(f"⚠️ {repetition_count} consecutive word repetition(s) detected. Try to vary expressions.")
        clarity_score -= 5
    else:
        feedback.append("✅ Good word variety and no repetitions.")

    # Content Length
    if word_count < 30:
        feedback.append("⚠️ Short speech recording. Speak longer for more comprehensive feedback.")
        clarity_score -= 5

    clarity_score = max(0, min(100, clarity_score))

    return {
        "feedback": feedback,
        "clarity_score": clarity_score
    }


@phase_four_bp.route('/analyze-speech', methods=['POST'])
@jwt_required(optional=True)
@rate_limit(limit_authenticated=15, limit_guest=3)
def analyze_speech():
    """
    Endpoint to analyze raw speech text (backward compatibility).
    """
    try:
        data = request.get_json()
        if data is None:
            return jsonify({
                "success": False,
                "error": "InvalidJson",
                "message": "Request body must be valid JSON"
            }), 400

        if 'text' not in data or not data['text'].strip():
            return jsonify({
                "success": False,
                "error": "MissingTextField",
                "message": "Please provide a 'text' field with transcribed speech"
            }), 400

        speech_text = data['text'].strip()
        duration_seconds = int(data.get('duration_seconds', 10))  # Default to 10 seconds if missing

        if duration_seconds <= 0:
            duration_seconds = 1

        # Calculate metrics
        words = speech_text.split()
        word_count = len([w for w in words if w.strip()])
        speech_speed_wpm = round((word_count / duration_seconds) * 60, 2)

        filler_words = ['um', 'uh', 'erm', 'err', 'like', 'you know', 'basically', 'actually', 'kind of']
        text_lower = speech_text.lower()
        filler_count = 0
        for filler in filler_words:
            pattern = r'\b' + re.escape(filler) + r'\b'
            filler_count += len(re.findall(pattern, text_lower))

        filler_percentage = (filler_count / max(word_count, 1)) * 100
        repetition_count = count_repetitions(speech_text)

        quality_analysis = analyze_speech_quality(
            word_count=word_count,
            wpm=speech_speed_wpm,
            filler_count=filler_count,
            filler_percentage=filler_percentage,
            repetition_count=repetition_count
        )

        gemini_result = run_gemini_speech_analysis(
            transcript=speech_text,
            speech_speed_wpm=speech_speed_wpm,
            filler_count=filler_count,
            filler_percentage=filler_percentage,
            repetition_count=repetition_count,
            duration_seconds=duration_seconds
        )
        final_clarity = unify_clarity(gemini_result, quality_analysis["clarity_score"])

        analysis_result = {
            "success": True,        # AUDIT-08: Unified API contract — all success responses carry success:True
            "status": "success",
            "word_count": word_count,
            "speech_speed_wpm": speech_speed_wpm,
            "filler_words_count": filler_count,
            "filler_words_percentage": round(filler_percentage, 2),
            "repetition_count": repetition_count,
            "duration_seconds": duration_seconds,
            "actionable_feedback": quality_analysis["feedback"],
            "clarity_score": final_clarity,
            "transcript": speech_text,
            "overall_score": gemini_result.get("overall_score", quality_analysis["clarity_score"]),
            "category_scores": gemini_result.get("category_scores"),
            "seven_cs_evaluation": gemini_result.get("seven_cs_evaluation"),
            "seven_cs_scores": gemini_result.get("seven_cs_scores"),
            "grammar_score": gemini_result.get("grammar_score", 100),
            "grammar_issues": gemini_result.get("grammar_issues", []),
            "grammar_issues_count": gemini_result.get("grammar_issues_count", 0),
            "strengths": gemini_result.get("strengths"),
            "recommendations": gemini_result.get("recommendations"),
            "detailed_feedback": gemini_result.get("detailed_feedback"),
            "improved_text": gemini_result.get("improved_text"),
            "analysis_timestamp": datetime.now(timezone.utc).isoformat()
        }

        # Save to database if requested
        user_id = get_jwt_identity() or "guest"
        Report.create(
            report_json=analysis_result,
            report_type='speech_analysis',
            user_id=user_id
        )

        return jsonify(analysis_result), 200

    except Exception as e:
        logger.error("Error during speech analysis: %s", e, exc_info=True)
        return jsonify({
            "success": False,
            "error": "SpeechAnalysisFailed",
            "message": str(e)
        }), 500


@phase_four_bp.route('/analyze-audio', methods=['POST'])
@jwt_required(optional=True)
@rate_limit(limit_authenticated=15, limit_guest=3)
def analyze_audio():
    """
    Analyze uploaded WAV/MP3 speech audio:
    1. Transcribe audio to text using Groq Whisper.
    2. Analyze pacing, repetitions, and filler words.
    3. Evaluate text transcript under the 7Cs parameters using Gemini.
    4. Save upload and report to database.
    """
    guest_check = enforce_guest_size_limit(max_guest_bytes=10 * 1024 * 1024)
    if guest_check:
        return guest_check

    permanent_audio_path = None

    try:
        # ===== STEP 1: VALIDATE REQUEST DATA =====
        if 'file' not in request.files:
            return jsonify({
                "success": False,
                "error": "NoFileProvided",
                "message": "Please upload an audio file with key 'file'"
            }), 400

        file = request.files['file']
        duration_seconds = int(request.form.get('duration_seconds', 0))

        if file.filename == '':
            return jsonify({
                "success": False,
                "error": "EmptyFile",
                "message": "Please select a file to upload"
            }), 400

        ALLOWED_AUDIO_EXTENSIONS = {'.wav', '.mp3', '.m4a', '.ogg', '.webm', '.flac'}
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in ALLOWED_AUDIO_EXTENSIONS:
            return jsonify({
                "success": False,
                "error": "UnsupportedAudioFormat",
                "message": f"Supported audio formats: {', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}"
            }), 400

        if duration_seconds <= 0:
            return jsonify({
                "success": False,
                "error": "InvalidDuration",
                "message": "Please provide a valid speaking duration in seconds"
            }), 400

        # Secure permanent file storage (ISSUE-14)
        upload_folder = os.path.join(os.getcwd(), 'instance', 'uploads', str(uuid.uuid4()))
        os.makedirs(upload_folder, exist_ok=True)
        safe_filename = secure_filename(file.filename) or f"audio{file_ext}"
        permanent_audio_path = os.path.join(upload_folder, safe_filename)
        file.save(permanent_audio_path)
        logger.info("Saved audio upload: %s (%d bytes)", permanent_audio_path, os.path.getsize(permanent_audio_path))

        # ===== STEP 2: TRANSCRIBE AUDIO (Groq Whisper API) =====
        transcript = ""
        stt_fallback = False  # FIX-6: track whether Groq STT succeeded or fell back

        if groq_client:
            logger.info("Transcribing audio using Groq Whisper API: %s", file.filename)
            try:
                with open(permanent_audio_path, "rb") as audio_file:
                    transcription = groq_client.audio.transcriptions.create(
                        file=(file.filename, audio_file.read()),
                        model="whisper-large-v3",
                        language="en",
                        prompt=WHISPER_FILLER_PROMPT,
                        temperature=0.0
                    )
                    transcript = transcription.text
            except (RateLimitError, APITimeoutError) as rate_err:
                logger.warning("Groq Whisper transcription rate-limited or timed out: %s", rate_err)
                stt_fallback = True
            except Exception as e:
                logger.warning("Groq Whisper transcription failed: %s", e)
                stt_fallback = True
        else:
            logger.warning("GROQ_API_KEY not configured. Transcription unavailable.")
            stt_fallback = True

        # If transcription failed, return a clear error instead of analysing a FAKE transcript.
        if stt_fallback:
            if permanent_audio_path and os.path.exists(permanent_audio_path):
                try:
                    os.remove(permanent_audio_path)
                except OSError:
                    pass
            return jsonify({
                "success": False,
                "error": "TranscriptionUnavailable",
                "message": "Speech transcription failed. Please try again."
            }), 503

        if not transcript.strip():
            if permanent_audio_path and os.path.exists(permanent_audio_path):
                try:
                    os.remove(permanent_audio_path)
                except OSError:
                    pass
            return jsonify({
                "success": False,
                "error": "NoSpeechDetected",
                "message": "Whisper STT could not transcribe any speech. Please make sure the audio contains clear speaking."
            }), 400

        # ===== STEP 3: RUN METRICS CALCULATIONS =====
        words = transcript.split()
        word_count = len([w for w in words if w.strip()])
        speech_speed_wpm = round((word_count / duration_seconds) * 60, 2)

        filler_words = ['um', 'uh', 'erm', 'err', 'like', 'you know', 'basically', 'actually', 'kind of']
        text_lower = transcript.lower()
        filler_count = 0
        for filler in filler_words:
            pattern = r'\b' + re.escape(filler) + r'\b'
            filler_count += len(re.findall(pattern, text_lower))

        filler_percentage = (filler_count / max(word_count, 1)) * 100
        repetition_count = count_repetitions(transcript)

        quality_analysis = analyze_speech_quality(
            word_count=word_count,
            wpm=speech_speed_wpm,
            filler_count=filler_count,
            filler_percentage=filler_percentage,
            repetition_count=repetition_count
        )

        # ===== STEP 4: 7Cs SPEECH EVALUATION (Gemini API) =====
        gemini_result = run_gemini_speech_analysis(
            transcript=transcript,
            speech_speed_wpm=speech_speed_wpm,
            filler_count=filler_count,
            filler_percentage=filler_percentage,
            repetition_count=repetition_count,
            duration_seconds=duration_seconds
        )
        final_clarity = unify_clarity(gemini_result, quality_analysis["clarity_score"])

        # ===== STEP 5: COMPILE REPORT RESULTS =====
        analysis_result = {
            "success": True,
            "status": "success",
            "stt_fallback": stt_fallback,   # always False here (failures return 503 above); kept for API compatibility
            "word_count": word_count,
            "speech_speed_wpm": speech_speed_wpm,
            "filler_words_count": filler_count,
            "filler_words_percentage": round(filler_percentage, 2),
            "repetition_count": repetition_count,
            "duration_seconds": duration_seconds,
            "actionable_feedback": quality_analysis["feedback"],
            "clarity_score": final_clarity,
            "transcript": transcript,
            "overall_score": gemini_result.get("overall_score", quality_analysis["clarity_score"]),
            "category_scores": gemini_result.get("category_scores"),
            "seven_cs_evaluation": gemini_result.get("seven_cs_evaluation"),
            "seven_cs_scores": gemini_result.get("seven_cs_scores"),
            "strengths": gemini_result.get("strengths"),
            "recommendations": gemini_result.get("recommendations"),
            "detailed_feedback": gemini_result.get("detailed_feedback"),
            "improved_text": gemini_result.get("improved_text"),
            "analysis_timestamp": datetime.now(timezone.utc).isoformat()
        }

        # ===== STEP 6: SAVE TO DATABASE =====
        user_id = get_jwt_identity() or "guest"

        upload_record = Upload.create(
            filename=file.filename,
            mime_type=file.mimetype or f"audio/{file_ext[1:]}",
            file_path=permanent_audio_path,
            user_id=user_id
        )

        Report.create(
            report_json=analysis_result,
            report_type='speech_analysis',
            user_id=user_id,
            upload_id=upload_record.id
        )

        logger.info("Speech audio analysis completed and saved for: %s", file.filename)
        return jsonify(analysis_result), 200

    except Exception as e:
        logger.error("Audio analysis failed: %s", e, exc_info=True)
        if permanent_audio_path and os.path.exists(permanent_audio_path):
            try:
                os.remove(permanent_audio_path)
            except OSError:
                pass
        return jsonify({
            "success": False,
            "error": "AudioAnalysisFailed",
            "message": "An error occurred while transcribing or analyzing your audio",
            "details": str(e)
        }), 500
