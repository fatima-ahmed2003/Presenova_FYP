"""
Phase Four: Speech Analyzer & Presentation Coach Blueprint (MongoDB & Groq Whisper integration)
Role: Expose endpoints to analyze speech text and WAV audio files using Groq Whisper STT and Gemini.
"""

import os
import tempfile
import json
import logging
import math
import re
import shutil
import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from groq import Groq, RateLimitError, APITimeoutError
from dotenv import load_dotenv
import soundfile as sf
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
SPEECH_TRANSCRIPTION_MODEL = "whisper-large-v3"
SPEECH_ANALYZER_VERSION = "phase_four_v5"
logger.info("speech_analyzer_startup version=%s", SPEECH_ANALYZER_VERSION)


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


def _is_valid_speech_score(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 100
    )


def _has_valid_speech_scores(result) -> bool:
    if not isinstance(result, dict) or result.get("evaluation_method") in {
        "heuristic_fallback",
        "unavailable",
    }:
        return False
    if not _is_valid_speech_score(result.get("overall_score")):
        return False

    category_scores = result.get("category_scores")
    if not isinstance(category_scores, dict) or not _is_valid_speech_score(
        category_scores.get("Clarity")
    ):
        return False

    seven_cs_scores = result.get("seven_cs_scores")
    required_scores = {
        "Clear",
        "Concise",
        "Correct",
        "Complete",
        "Courteous",
        "Concrete",
    }
    if not isinstance(seven_cs_scores, dict) or not required_scores.issubset(
        seven_cs_scores
    ):
        return False
    coherence_key = "Coherent" if "Coherent" in seven_cs_scores else "Consistent"
    if coherence_key not in seven_cs_scores:
        return False
    return all(
        _is_valid_speech_score(seven_cs_scores[key])
        for key in required_scores | {coherence_key}
    )


def _speech_scoring_unavailable_response():
    return jsonify({
        "success": False,
        "error": "SpeechScoringUnavailable",
        "message": "Speech scoring is unavailable. Please try again later.",
    }), 503


def _parse_positive_duration(value) -> int | None:
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        return None
    try:
        duration_seconds = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return duration_seconds if duration_seconds > 0 else None


def _detect_speech_activity(
    audio_path: str, diagnostics: dict | None = None
) -> str:
    frame_levels: list[tuple[float, float]] = []
    sample_rate = None
    channels = None
    total_squared = 0.0
    sample_count = 0
    try:
        with sf.SoundFile(audio_path) as audio:
            sample_rate = audio.samplerate
            channels = audio.channels
            if audio.samplerate < 8000 or audio.channels < 1:
                if diagnostics is not None:
                    diagnostics.update(
                        sample_rate=sample_rate,
                        channels=channels,
                        rms_gate_result="unsupported",
                    )
                return "unsupported"
            frame_size = max(1, round(audio.samplerate * 0.02))
            while True:
                samples = audio.read(frame_size, dtype="float32", always_2d=True)
                if samples.size == 0:
                    break
                mean_square = float((samples * samples).mean())
                if not math.isfinite(mean_square) or mean_square < 0:
                    if diagnostics is not None:
                        diagnostics.update(
                            sample_rate=sample_rate,
                            channels=channels,
                            rms_gate_result="unsupported",
                        )
                    return "unsupported"
                frame_levels.append(
                    (math.sqrt(mean_square), len(samples) / audio.samplerate)
                )
                total_squared += mean_square * samples.size
                sample_count += samples.size
    except (sf.LibsndfileError, OSError, RuntimeError, ValueError):
        if diagnostics is not None:
            diagnostics.update(rms_gate_result="unsupported")
        return "unsupported"

    total_duration = sum(duration for _level, duration in frame_levels)
    if diagnostics is not None:
        diagnostics.update(
            sample_rate=sample_rate,
            channels=channels,
            duration_seconds=total_duration,
            rms_overall=(
                math.sqrt(total_squared / sample_count) if sample_count else 0.0
            ),
        )

    if not frame_levels:
        if diagnostics is not None:
            diagnostics.update(rms_gate_result="silence")
        return "silence"

    levels = sorted(level for level, _duration in frame_levels)
    noise_floor = levels[int((len(levels) - 1) * 0.2)]
    threshold = max(0.003, noise_floor * 2.5)
    active_duration = sum(
        duration for level, duration in frame_levels if level >= threshold
    )
    active_ratio = active_duration / total_duration if total_duration else 0

    if active_duration < 0.25 or active_ratio < 0.02:
        if diagnostics is not None:
            diagnostics.update(
                rms_threshold=threshold,
                rms_active_duration=active_duration,
                rms_active_ratio=active_ratio,
                rms_gate_result="silence",
            )
        return "silence"
    if diagnostics is not None:
        diagnostics.update(
            rms_threshold=threshold,
            rms_active_duration=active_duration,
            rms_active_ratio=active_ratio,
            rms_gate_result="speech",
        )
    return "speech"


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
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({
                "success": False,
                "error": "InvalidJson",
                "message": "Request body must be valid JSON"
            }), 400

        speech_text = data.get("text")
        if not isinstance(speech_text, str) or not speech_text.strip():
            return jsonify({
                "success": False,
                "error": "MissingTextField",
                "message": "Please provide a 'text' field with transcribed speech"
            }), 400

        speech_text = speech_text.strip()
        duration_seconds = _parse_positive_duration(
            data.get("duration_seconds", 10)
        )
        if duration_seconds is None:
            return jsonify({
                "success": False,
                "error": "InvalidDuration",
                "message": "Please provide a positive duration in whole seconds"
            }), 400

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
        if not _has_valid_speech_scores(gemini_result):
            return _speech_scoring_unavailable_response()
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
            "overall_score": gemini_result["overall_score"],
            "category_scores": gemini_result.get("category_scores"),
            "seven_cs_evaluation": gemini_result.get("seven_cs_evaluation"),
            "seven_cs_scores": gemini_result.get("seven_cs_scores"),
            "grammar_score": gemini_result.get("grammar_score"),
            "grammar_issues": gemini_result.get("grammar_issues", []),
            "grammar_issues_count": gemini_result.get("grammar_issues_count", 0),
            "strengths": gemini_result.get("strengths"),
            "recommendations": gemini_result.get("recommendations"),
            "detailed_feedback": gemini_result.get("detailed_feedback"),
            "improved_text": gemini_result.get("improved_text"),
            "evaluation_method": gemini_result.get("evaluation_method"),
            "grammar_check_status": gemini_result.get("grammar_check_status"),
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
    audio_byte_size = None
    file_content_type = "unavailable"
    audio_diagnostics = {}
    whisper_segment_diagnostics = []
    transcript = ""
    avg_no_speech_prob = None
    debug_log_state = {"logged": False}

    def log_speech_debug(rejection_check: str, status: str):
        if debug_log_state["logged"]:
            return
        logger.info(
            "speech_analyzer_debug status=%s content_type=%s audio_bytes=%s "
            "sample_rate=%s channels=%s audio_duration_seconds=%s "
            "request_duration_seconds=%s rms_overall=%s rms_gate=%s "
            "rms_threshold=%s whisper_segments=%r avg_no_speech_prob=%s "
            "transcript=%r word_count=%d rejection_check=%s",
            status,
            file_content_type,
            audio_byte_size if audio_byte_size is not None else "unavailable",
            audio_diagnostics.get("sample_rate", "unavailable"),
            audio_diagnostics.get("channels", "unavailable"),
            audio_diagnostics.get("duration_seconds", "unavailable"),
            duration_seconds if duration_seconds is not None else "unavailable",
            audio_diagnostics.get("rms_overall", "unavailable"),
            audio_diagnostics.get("rms_gate_result", "not_checked"),
            audio_diagnostics.get("rms_threshold", "unavailable"),
            whisper_segment_diagnostics,
            (
                f"{avg_no_speech_prob:.6f}"
                if avg_no_speech_prob is not None
                else "unavailable"
            ),
            transcript,
            len(transcript.split()),
            rejection_check,
        )
        debug_log_state["logged"] = True

    try:
        # ===== STEP 1: VALIDATE REQUEST DATA =====
        if 'file' not in request.files:
            return jsonify({
                "success": False,
                "error": "NoFileProvided",
                "message": "Please upload an audio file with key 'file'"
            }), 400

        file = request.files['file']
        file_content_type = file.mimetype or "unknown"
        duration_seconds = _parse_positive_duration(
            request.form.get("duration_seconds", "0")
        )

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

        if duration_seconds is None:
            return jsonify({
                "success": False,
                "error": "InvalidDuration",
                "message": "Please provide a positive duration in whole seconds"
            }), 400

        # Secure permanent file storage (ISSUE-14)
        upload_folder = os.path.join(os.getcwd(), 'instance', 'uploads', str(uuid.uuid4()))
        os.makedirs(upload_folder, exist_ok=True)
        safe_filename = secure_filename(file.filename) or f"audio{file_ext}"
        permanent_audio_path = os.path.join(upload_folder, safe_filename)
        file.save(permanent_audio_path)
        audio_byte_size = os.path.getsize(permanent_audio_path)
        # TEMP DEBUG: retain only the latest local Speech Analyzer recording.
        debug_audio_folder = os.path.join(os.getcwd(), 'instance', 'debug')
        os.makedirs(debug_audio_folder, exist_ok=True)
        shutil.copyfile(
            permanent_audio_path,
            os.path.join(debug_audio_folder, 'last_speech.wav'),
        )
        logger.info(
            "speech_audio_diagnostics=%s status=received "
            "audio_bytes=%d duration_seconds=%d",
            SPEECH_ANALYZER_VERSION,
            audio_byte_size,
            duration_seconds,
        )
        if audio_byte_size == 0:
            log_speech_debug("none", "empty_audio")
            os.remove(permanent_audio_path)
            return jsonify({
                "success": False,
                "error": "EmptyFile",
                "message": "The uploaded audio file is empty"
            }), 400

        signal_status = _detect_speech_activity(
            permanent_audio_path, diagnostics=audio_diagnostics
        )
        logger.info(
            "speech_signal_diagnostics=%s status=%s "
            "audio_bytes=%d duration_seconds=%d",
            SPEECH_ANALYZER_VERSION,
            signal_status,
            audio_byte_size,
            duration_seconds,
        )
        if signal_status == "unsupported":
            log_speech_debug("none", "audio_decode_unsupported")
            if permanent_audio_path and os.path.exists(permanent_audio_path):
                try:
                    os.remove(permanent_audio_path)
                except OSError:
                    pass
            return jsonify({
                "success": False,
                "error": "AudioSignalValidationUnavailable",
                "message": (
                    "This recording format could not be checked for silence. "
                    "Please record again using WAV, FLAC, OGG, or MP3."
                )
            }), 415

        if signal_status != "speech":
            log_speech_debug("RMS gate", "no_speech")
            if permanent_audio_path and os.path.exists(permanent_audio_path):
                try:
                    os.remove(permanent_audio_path)
                except OSError:
                    pass
            return jsonify({
                "success": False,
                "error": "NoSpeechDetected",
                "message": "No speech detected. Please record again."
            }), 400

        # ===== STEP 2: TRANSCRIBE AUDIO (Groq Whisper API) =====
        transcript = ""
        stt_fallback = False  # FIX-6: track whether Groq STT succeeded or fell back
        transcription_status = "provider_unavailable"
        segment_count = 0
        no_speech_probabilities = []

        if groq_client:
            transcription_status = "pending"
            try:
                with open(permanent_audio_path, "rb") as audio_file:
                    transcription = groq_client.audio.transcriptions.create(
                        file=(file.filename, audio_file.read()),
                        model=SPEECH_TRANSCRIPTION_MODEL,
                        language="en",
                        response_format="verbose_json",
                        temperature=0.0
                    )
                    raw_segments = (
                        transcription.get("segments")
                        if isinstance(transcription, dict)
                        else getattr(transcription, "segments", None)
                    )
                    segments = (
                        raw_segments
                        if isinstance(raw_segments, (list, tuple))
                        else []
                    )
                    segment_count = len(segments)
                    for segment in segments:
                        probability = (
                            segment.get("no_speech_prob")
                            if isinstance(segment, dict)
                            else getattr(segment, "no_speech_prob", None)
                        )
                        avg_logprob = (
                            segment.get("avg_logprob")
                            if isinstance(segment, dict)
                            else getattr(segment, "avg_logprob", None)
                        )
                        whisper_segment_diagnostics.append(
                            {
                                "no_speech_prob": probability,
                                "avg_logprob": avg_logprob,
                            }
                        )
                        if (
                            isinstance(probability, (int, float))
                            and not isinstance(probability, bool)
                            and math.isfinite(probability)
                            and 0 <= probability <= 1
                        ):
                            no_speech_probabilities.append(float(probability))
                    if no_speech_probabilities:
                        avg_no_speech_prob = sum(no_speech_probabilities) / len(
                            no_speech_probabilities
                        )

                    transcribed_text = (
                        transcription.get("text")
                        if isinstance(transcription, dict)
                        else getattr(transcription, "text", None)
                    )
                    if not isinstance(transcribed_text, str):
                        transcription_status = "invalid_response"
                        stt_fallback = True
                    else:
                        transcript = transcribed_text.strip()
                        if (
                            avg_no_speech_prob is not None
                            and avg_no_speech_prob >= 0.6
                        ):
                            transcription_status = "no_speech"
                        elif (
                            not no_speech_probabilities
                            or len(transcript.split()) < 3
                        ):
                            transcription_status = "unreliable_transcript"
                        else:
                            transcription_status = "success"
            except (RateLimitError, APITimeoutError) as rate_err:
                transcription_status = "provider_error"
                logger.warning(
                    "Groq Whisper transcription failed (type=%s)",
                    type(rate_err).__name__,
                )
                stt_fallback = True
            except Exception as e:
                transcription_status = "provider_error"
                logger.warning(
                    "Groq Whisper transcription failed (type=%s)",
                    type(e).__name__,
                )
                stt_fallback = True
        else:
            logger.warning("Groq Whisper transcription unavailable.")
            stt_fallback = True

        logger.info(
            "speech_transcription_diagnostics=%s status=%s "
            "audio_bytes=%d duration_seconds=%d model=%s segment_count=%d "
            "avg_no_speech_prob=%s transcript_words=%d",
            SPEECH_ANALYZER_VERSION,
            transcription_status,
            audio_byte_size,
            duration_seconds,
            SPEECH_TRANSCRIPTION_MODEL,
            segment_count,
            (
                f"{avg_no_speech_prob:.3f}"
                if avg_no_speech_prob is not None
                else "unavailable"
            ),
            len(transcript.split()),
        )

        rejection_check = "none"
        if transcription_status == "no_speech":
            rejection_check = "no_speech average"
        elif transcription_status == "unreliable_transcript":
            rejection_reasons = []
            if not no_speech_probabilities:
                rejection_reasons.append("missing no_speech_prob")
            if len(transcript.split()) < 3:
                rejection_reasons.append("short transcript")
            rejection_check = " + ".join(rejection_reasons)
        log_speech_debug(rejection_check, transcription_status)

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

        if transcription_status in {"no_speech", "unreliable_transcript"}:
            if permanent_audio_path and os.path.exists(permanent_audio_path):
                try:
                    os.remove(permanent_audio_path)
                except OSError:
                    pass
            return jsonify({
                "success": False,
                "error": "NoSpeechDetected",
                "message": "No speech detected. Please record again."
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
        if not _has_valid_speech_scores(gemini_result):
            if permanent_audio_path and os.path.exists(permanent_audio_path):
                try:
                    os.remove(permanent_audio_path)
                except OSError:
                    pass
            return _speech_scoring_unavailable_response()
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
            "overall_score": gemini_result["overall_score"],
            "category_scores": gemini_result.get("category_scores"),
            "seven_cs_evaluation": gemini_result.get("seven_cs_evaluation"),
            "seven_cs_scores": gemini_result.get("seven_cs_scores"),
            "strengths": gemini_result.get("strengths"),
            "recommendations": gemini_result.get("recommendations"),
            "detailed_feedback": gemini_result.get("detailed_feedback"),
            "improved_text": gemini_result.get("improved_text"),
            "evaluation_method": gemini_result.get("evaluation_method"),
            "grammar_score": gemini_result.get("grammar_score"),
            "grammar_check_status": gemini_result.get("grammar_check_status"),
            "grammar_issues": gemini_result.get("grammar_issues", []),
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

        logger.info("Speech audio analysis completed.")
        return jsonify(analysis_result), 200

    except Exception as e:
        if audio_byte_size is not None:
            log_speech_debug("none", "error")
        logger.error("Audio analysis failed (type=%s)", type(e).__name__)
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
