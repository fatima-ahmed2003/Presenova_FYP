"""
Phase Live: Live Presentation Coach & Live Analyzer
Role: Handles real-time video/audio WebSocket streams and compiles the final scorecard with historical comparisons.
"""

import sys
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import os
import json
import base64
import logging
import math
import re
import statistics
import tempfile
import threading
import time
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from services.ai.gemini_provider import GeminiProvider
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models import PresentationSession, HistoricalReport, db, _MEMORY_STORE
import groq
from groq import Groq, RateLimitError, APITimeoutError

# ===== GRACEFUL NATIVE LIBRARY IMPORTS =====
# MediaPipe, OpenCV, Librosa, and Soundfile can have complex native dependencies.
# We import them inside try-except blocks so the server starts correctly on any host.

OPENCV_AVAILABLE = False
OPENCV_IMPORT_ERROR = None
try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    OPENCV_AVAILABLE = True
except Exception as e:
    OPENCV_IMPORT_ERROR = str(e)
    logger.warning("[LIVE WARN] OpenCV or NumPy not available: %s", OPENCV_IMPORT_ERROR)

MEDIAPIPE_AVAILABLE = False
MEDIAPIPE_IMPORTED = False
MEDIAPIPE_VERSION = None
MEDIAPIPE_IMPORT_ERROR = None
MEDIAPIPE_MODE = "tasks"
mp = None
mp_python = None
mp_vision = None
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FACE_TASK_PATH = os.path.join(BASE_DIR, "cascades", "face_landmarker.task")
POSE_TASK_PATH = os.path.join(BASE_DIR, "cascades", "pose_landmarker_lite.task")
YUNET_MODEL_PATH = os.path.join(BASE_DIR, "cascades", "face_detection_yunet.onnx")

try:
    import mediapipe as mp  # type: ignore
    MEDIAPIPE_IMPORTED = True
    MEDIAPIPE_VERSION = getattr(mp, "__version__", None)

    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision
    MEDIAPIPE_AVAILABLE = True
    MEDIAPIPE_IMPORT_ERROR = None if os.path.exists(FACE_TASK_PATH) else "cascades/face_landmarker.task model file not found."
except Exception as e:
    MEDIAPIPE_IMPORT_ERROR = str(e)
    logger.warning("[LIVE WARN] MediaPipe not available: %s", MEDIAPIPE_IMPORT_ERROR)

LIBROSA_AVAILABLE = False
try:
    import librosa  # type: ignore
    import soundfile as sf  # type: ignore
    LIBROSA_AVAILABLE = True
except ImportError:
    logger.warning("[LIVE WARN] Librosa or Soundfile not available. Using mock voice feature extraction.")


# Initialize Gemini Provider
_gemini_provider = GeminiProvider()
gemini_available = _gemini_provider.is_available()

# Initialize Groq client
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
groq_client = None
if GROQ_API_KEY and GROQ_API_KEY != 'your-groq-api-key-here':
    try:
        groq_client = Groq(api_key=GROQ_API_KEY, timeout=4.0)
        logger.info("[LIVE OK] Groq API configured successfully for Live Presentation Coach (timeout=4.0s)")
    except Exception as e:
        logger.warning("[LIVE WARN] Failed to configure Groq client: %s", e)

# Create Blueprint
phase_live_bp = Blueprint('phase_live', __name__, url_prefix='/api/presentation')

# ===== SCORING RELIABILITY THRESHOLDS (FIX) =====
# Minimum required samples to compute stable visual and audio averages
MIN_VALID_VIDEO_SAMPLES = 3     # need at least 3 valid frames to trust visual presence
MIN_VALID_AUDIO_SAMPLES = 1     # need at least 1 audio chunk to measure vocal delivery
MIN_WORDS_PER_CHUNK = 3         # Match Speech Analyzer's minimum reliable transcript length.

# ===== REAL-TIME FEATURE EXTRACTION FUNCTIONS =====

_vision_lock = threading.RLock()
_session_lock = threading.Lock()
_sid_to_session = {}
_face_landmarker = None
_pose_landmarker = None
_yunet_detector = None
_video_timestamp_ms = 0

def create_face_mesh_detector():
    """Create the process-wide MediaPipe Tasks FaceLandmarker once at startup."""
    if not MEDIAPIPE_AVAILABLE or not os.path.isfile(FACE_TASK_PATH):
        return None
    options = mp_vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=FACE_TASK_PATH),
        running_mode=mp_vision.RunningMode.VIDEO,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
        num_faces=1,
        min_face_detection_confidence=0.35,
        min_face_presence_confidence=0.35,
        min_tracking_confidence=0.35,
    )
    return mp_vision.FaceLandmarker.create_from_options(options)

def create_pose_detector():
    """Create the process-wide MediaPipe Tasks PoseLandmarker Lite, if installed."""
    if not MEDIAPIPE_AVAILABLE or not os.path.isfile(POSE_TASK_PATH):
        return None
    options = mp_vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=POSE_TASK_PATH),
        running_mode=mp_vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.4,
        min_pose_presence_confidence=0.4,
        min_tracking_confidence=0.4,
    )
    return mp_vision.PoseLandmarker.create_from_options(options)


try:
    _face_landmarker = create_face_mesh_detector()
    if _face_landmarker is None:
        logger.warning("[LIVE WARN] FaceLandmarker unavailable; expected cascades/face_landmarker.task")
    _pose_landmarker = create_pose_detector()
    if _pose_landmarker is None:
        logger.warning("[LIVE WARN] PoseLandmarker Lite unavailable; expected cascades/pose_landmarker_lite.task")
except Exception as detector_error:
    MEDIAPIPE_IMPORT_ERROR = str(detector_error)
    logger.exception("[LIVE WARN] MediaPipe Tasks initialization failed: %s", detector_error)

if OPENCV_AVAILABLE and os.path.isfile(YUNET_MODEL_PATH):
    try:
        _yunet_detector = cv2.FaceDetectorYN.create(
            YUNET_MODEL_PATH, "", (320, 240), 0.6, 0.3, 5000
        )
    except Exception as detector_error:
        logger.warning("[LIVE WARN] YuNet fallback unavailable: %s", detector_error)

_head_pose_samples = {}
_gaze_calibration = {}
_metric_smoothing = {}
_head_pose_lock = threading.Lock()

def get_session_detector_and_lock(session_id: str):
    return _face_landmarker, _vision_lock

def get_session_pose_detector_and_lock(session_id: str):
    return _pose_landmarker, _vision_lock

def release_session_detector(session_id: str):
    """
    Releases and closes the MediaPipe detector instance for a session to prevent resource leaks.
    """
    if not session_id:
        return
    session_id = str(session_id)
    with _head_pose_lock:
        _head_pose_samples.pop(session_id, None)
        _gaze_calibration.pop(session_id, None)
        _metric_smoothing.pop(session_id, None)


def init_face_mesh():
    """Retained for backward compatibility."""
    pass

def _clip_score(value, low=0, high=100):
    return int(max(low, min(high, value)))


def _calculate_score_stability(values):
    if not values or len(values) < 2:
        return 1.0
    mean_value = sum(values) / len(values)
    if mean_value <= 0:
        return 0.0
    variance = sum((x - mean_value) ** 2 for x in values) / len(values)
    stddev = math.sqrt(variance)
    return max(0.0, 1.0 - min(1.0, stddev / mean_value))


def _calculate_confidence(eye_contact_score=None, posture_score=None, head_movement_score=None, visibility_score=1.0):
    # Keep the live proxy tied to the measured modalities. Renormalize only
    # across measurements that actually exist for this frame.
    measurements = (
        (eye_contact_score, 0.4),
        (posture_score, 0.3),
        (head_movement_score, 0.3),
    )
    available = [(score, weight) for score, weight in measurements if score is not None]
    total_weight = sum(weight for _, weight in available)
    if total_weight <= 0:
        return None
    return _clip_score(sum(score * weight for score, weight in available) / total_weight, 0, 100)


def _smooth_metric(session, metric, value, confidence):
    if value is None or session is None:
        return value, confidence
    now = time.monotonic()
    session_id = str(getattr(session, "id", ""))
    with _head_pose_lock:
        metrics = _metric_smoothing.setdefault(session_id, {})
        samples = metrics.setdefault(metric, deque(maxlen=7))
        samples.append((now, float(value)))
        recent = [score for timestamp, score in samples if now - timestamp <= 3.0]
        median_value = statistics.median(recent) if recent else float(value)
        key = f"ema_{metric}"
        ema = 0.4 * median_value + 0.6 * metrics.get(key, median_value)
        metrics[key] = ema
    return _clip_score(ema), confidence


def _smooth_visual_scores(eye_contact_score, posture_score, session):
    return (
        _smooth_metric(session, "gaze", eye_contact_score, None)[0],
        _smooth_metric(session, "posture", posture_score, None)[0],
    )


_DISPLAYED_BLENDSHAPES = (
    "mouthSmileLeft", "mouthSmileRight", "eyeBlinkLeft", "eyeBlinkRight",
    "browDownLeft", "browDownRight", "jawOpen", "mouthPressLeft",
    "mouthPressRight",
)


def _measure_facial_blendshapes(blendshapes, session=None):
    """Return smoothed, measured blendshape coefficients without emotion labels."""
    values = _blendshape_map(blendshapes)
    measured = {}
    for name in _DISPLAYED_BLENDSHAPES:
        if name not in values or not math.isfinite(values[name]):
            continue
        coefficient = max(0.0, min(1.0, values[name]))
        smoothed, _ = _smooth_metric(session, f"blendshape_{name}", coefficient * 100, None)
        measured[name] = round(smoothed / 100, 3)
    return measured or None


def _visual_result(
    eye_contact_score,
    posture_score,
    hint,
    session=None,
    face_detected=True,
    visibility_score=1.0,
    gaze_method=None,
    posture_method=None,
    head_movement_score=None,
    gaze_calibration_status="unavailable",
    expression="N/A",
    metric_confidence=None,
    facial_blendshapes=None,
):
    eye_contact_score, posture_score = _smooth_visual_scores(
        _clip_score(eye_contact_score) if eye_contact_score is not None else None,
        _clip_score(posture_score) if posture_score is not None else None,
        session
    )
    head_movement_score, head_confidence = _smooth_metric(session, "head", head_movement_score, (metric_confidence or {}).get("head_movement"))
    if metric_confidence is None:
        metric_confidence = {}
    metric_confidence.update({
        "eye_contact": metric_confidence.get("eye_contact") if eye_contact_score is not None else None,
        "posture": metric_confidence.get("posture") if posture_score is not None else None,
        "head_movement": head_confidence if head_movement_score is not None else None,
        "expression": metric_confidence.get("expression") if expression != "N/A" else None,
    })
    confidence_score = _calculate_confidence(
        eye_contact_score=eye_contact_score,
        posture_score=posture_score,
        head_movement_score=head_movement_score,
    )

    return {
        "face_detected": face_detected,
        "eye_contact": eye_contact_score,
        "eye_contact_score": eye_contact_score,
        "posture": posture_score,
        "posture_score": posture_score,
        "hint": hint,
        "confidence": confidence_score,
        "confidence_score": confidence_score,
        "emotion": expression,
        "expression": expression,
        "expression_confidence": None,
        "facial_blendshapes": facial_blendshapes,
        "metric_confidence": metric_confidence,
        "gaze_method": gaze_method,
        "posture_method": posture_method,
        "head_movement": head_movement_score,
        "head_movement_score": head_movement_score,
        "gaze_calibration_status": gaze_calibration_status,
        "valid": face_detected
    }


def _unmeasured_visual_result(hint, gaze_calibration_status="unavailable"):
    return {
        "face_detected": False,
        "eye_contact": None,
        "eye_contact_score": None,
        "posture": None,
        "posture_score": None,
        "confidence": None,
        "confidence_score": None,
        "metric_confidence": {"eye_contact": None, "posture": None, "head_movement": None, "expression": None},
        "expression": "N/A",
        "facial_blendshapes": None,
        "face_detection_confidence": None,
        "expression_confidence": None,
        "hint": hint,  # FIX: was missing, so the reason never reached the caller/frontend
        "emotion": "N/A",
        "gaze_method": None,
        "posture_method": None,
        "head_movement": None,
        "head_movement_score": None,
        "gaze_calibration_status": gaze_calibration_status,
        "valid": False
    }


def _estimate_posture_score(pose_landmarks, frame_width, frame_height):
    """Score shoulder tilt and head-to-shoulder centering from Pose Tasks landmarks."""
    try:
        points = pose_landmarks
        left_shoulder, right_shoulder = points[11], points[12]
        left_hip, right_hip = points[23], points[24]
        nose = points[0]
        required = (left_shoulder, right_shoulder, left_hip, right_hip, nose)
        if any(min(getattr(p, "visibility", 0.0), getattr(p, "presence", 0.0)) < 0.45 for p in required):
            return None, None
        shoulder_span = math.hypot((right_shoulder.x-left_shoulder.x)*frame_width,
                                   (right_shoulder.y-left_shoulder.y)*frame_height)
        if shoulder_span < 1:
            return None, None
        shoulder_center_x = (left_shoulder.x + right_shoulder.x) / 2
        shoulder_center_y = (left_shoulder.y + right_shoulder.y) / 2
        hip_center_y = (left_hip.y + right_hip.y) / 2
        torso_height = abs(hip_center_y - shoulder_center_y) * frame_height
        if torso_height < 1:
            return None, None
        tilt = abs((right_shoulder.y-left_shoulder.y)*frame_height) / shoulder_span
        head_offset = abs(nose.x - shoulder_center_x) * frame_width / shoulder_span
        penalty = min(45, tilt * 100) + min(55, head_offset * 100)
        confidence = _clip_score(sum(min(getattr(p, "visibility", 0.0), getattr(p, "presence", 0.0)) for p in required) / len(required) * 100)
        return _clip_score(100 - penalty), confidence
    except (AttributeError, IndexError, TypeError, ValueError):
        return None, None


def _estimate_head_pose(transformation_matrix):
    """Convert MediaPipe's facial transformation matrix to yaw and pitch degrees."""
    try:
        matrix = np.asarray(transformation_matrix, dtype=np.float64).reshape(4, 4)[:3, :3]
        sy = math.sqrt(matrix[0, 0] ** 2 + matrix[1, 0] ** 2)
        if sy < 1e-6:
            pitch = math.degrees(math.atan2(-matrix[1, 2], matrix[1, 1]))
            yaw = math.degrees(math.atan2(-matrix[2, 0], sy))
        else:
            pitch = math.degrees(math.atan2(matrix[2, 1], matrix[2, 2]))
            yaw = math.degrees(math.atan2(-matrix[2, 0], sy))
        return (yaw, pitch) if math.isfinite(yaw) and math.isfinite(pitch) else None
    except (TypeError, ValueError, IndexError):
        return None


def _measure_head_movement(session, pose):
    if session is None or pose is None:
        return None, None
    session_id = str(getattr(session, "id", ""))
    now = time.monotonic()
    with _head_pose_lock:
        samples = _head_pose_samples.setdefault(session_id, deque())
        samples.append((now, pose[0], pose[1]))
        while samples and now - samples[0][0] > 3.0:
            samples.popleft()
        if len(samples) < 4 or samples[-1][0] - samples[0][0] < 0.6:
            return None, None
        variation = math.hypot(statistics.pstdev(p[1] for p in samples), statistics.pstdev(p[2] for p in samples))
        return _clip_score(100 - min(100, variation * 3.0)), 95


def _calibrate_gaze(session, horizontal_ratio, vertical_ratio, blendshape_away=0.0):
    if session is None or horizontal_ratio is None or vertical_ratio is None:
        return None, "unavailable", None
    session_id = str(getattr(session, "id", ""))
    now = time.monotonic()
    with _head_pose_lock:
        state = _gaze_calibration.setdefault(session_id, {"started": now, "samples": deque(maxlen=48), "baseline": None})
        if state["baseline"] is None:
            state["samples"].append((horizontal_ratio, vertical_ratio))
            elapsed = now - state["started"]
            if elapsed >= 3.0 and len(state["samples"]) >= 5:
                state["baseline"] = (statistics.median(p[0] for p in state["samples"]), statistics.median(p[1] for p in state["samples"]))
            else:
                return None, "calibrating", 70
        baseline_h, baseline_v = state["baseline"]
    iris_score = 100 - min(100, abs(horizontal_ratio-baseline_h)*240 + abs(vertical_ratio-baseline_v)*200)
    score = 0.7 * iris_score + 0.3 * (100 - 100 * max(0.0, min(1.0, blendshape_away)))
    return _clip_score(score), "calibrated", 85

def _decode_frame(base64_image_data):
    if not OPENCV_AVAILABLE:
        return None
    if "," in base64_image_data:
        base64_image_data = base64_image_data.split(",", 1)[1]
    img_bytes = base64.b64decode(base64_image_data)
    np_arr = np.frombuffer(img_bytes, np.uint8)
    image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if image is None:
        return None
    height, width = image.shape[:2]
    if width > 480:
        target_height = max(1, round(height * 480 / width))
        image = cv2.resize(image, (480, target_height), interpolation=cv2.INTER_AREA)
    return image

def _blendshape_map(blendshapes):
    return {item.category_name: float(item.score) for item in (blendshapes or [])}


def _gaze_ratios(landmarks):
    def p(index):
        return landmarks[index].x, landmarks[index].y
    left_outer, left_inner = p(33), p(133)
    right_inner, right_outer = p(362), p(263)
    left_top, left_bottom = p(159), p(145)
    right_top, right_bottom = p(386), p(374)
    left_iris, right_iris = [p(i) for i in range(468, 473)], [p(i) for i in range(473, 478)]
    left_c = (sum(v[0] for v in left_iris) / 5, sum(v[1] for v in left_iris) / 5)
    right_c = (sum(v[0] for v in right_iris) / 5, sum(v[1] for v in right_iris) / 5)
    left_width = abs(left_inner[0] - left_outer[0])
    right_width = abs(right_outer[0] - right_inner[0])
    left_height = abs(left_bottom[1] - left_top[1])
    right_height = abs(right_bottom[1] - right_top[1])
    if min(left_width, right_width, left_height, right_height) < 1e-4:
        return None, None
    ratios = (
        (left_c[0] - min(left_outer[0], left_inner[0])) / left_width,
        (right_c[0] - min(right_inner[0], right_outer[0])) / right_width,
        (left_c[1] - min(left_top[1], left_bottom[1])) / left_height,
        (right_c[1] - min(right_top[1], right_bottom[1])) / right_height,
    )
    if not all(math.isfinite(value) and -0.15 <= value <= 1.15 for value in ratios):
        return None, None
    return (ratios[0] + ratios[1]) / 2, (ratios[2] + ratios[3]) / 2


def _next_video_timestamp_ms():
    global _video_timestamp_ms
    with _vision_lock:
        _video_timestamp_ms = max(_video_timestamp_ms + 1, int(time.monotonic() * 1000))
        return _video_timestamp_ms


def _analyze_frame_with_tasks(img, session=None):
    if not OPENCV_AVAILABLE or _face_landmarker is None:
        return None
    h, w = img.shape[:2]
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    timestamp = _next_video_timestamp_ms()
    try:
        with _vision_lock:
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            face_result = _face_landmarker.detect_for_video(mp_image, timestamp)
            pose_result = _pose_landmarker.detect_for_video(mp_image, timestamp) if _pose_landmarker is not None else None
    except Exception as exc:
        logger.warning('[LIVE WARN] MediaPipe Tasks frame processing failed: %s', exc)
        return None
    pose_landmarks = pose_result.pose_landmarks[0] if pose_result and pose_result.pose_landmarks else None
    posture, posture_confidence = _estimate_posture_score(pose_landmarks, w, h)

    if not face_result.face_landmarks:
        return _visual_result(
            None, posture,
            'Face not detected; posture is measured only when shoulder and hip landmarks are visible.' if posture is not None else 'Face and posture landmarks are unavailable in this frame.',
            session=session, face_detected=False,
            posture_method='mediapipe_tasks_pose' if posture is not None else None,
            metric_confidence={'posture': posture_confidence if posture is not None else None},
        )

    landmarks = face_result.face_landmarks[0]
    if len(landmarks) < 478:
        return _visual_result(
            None, posture, 'Face landmarks are incomplete; available posture measurement is retained.',
            session=session, face_detected=False,
            posture_method='mediapipe_tasks_pose' if posture is not None else None,
            metric_confidence={'posture': posture_confidence if posture is not None else None},
        )

    blendshapes = face_result.face_blendshapes[0] if face_result.face_blendshapes else []
    matrices = face_result.facial_transformation_matrixes
    transform = matrices[0] if matrices is not None and len(matrices) else None
    face_height = max(p.y for p in landmarks) - min(p.y for p in landmarks)
    face_visibility = max(0.0, min(1.0, (face_height - 0.08) / 0.35))
    horizontal, vertical = _gaze_ratios(landmarks)
    categories = _blendshape_map(blendshapes)
    gaze_away = sum(categories.get(name, 0.0) for name in (
        'eyeLookInLeft', 'eyeLookOutLeft', 'eyeLookUpLeft', 'eyeLookDownLeft',
        'eyeLookInRight', 'eyeLookOutRight', 'eyeLookUpRight', 'eyeLookDownRight'
    )) / 2
    gaze, calibration_status, gaze_confidence = _calibrate_gaze(session, horizontal, vertical, gaze_away)

    head_pose = _estimate_head_pose(transform) if transform is not None else None
    head_score, head_confidence = _measure_head_movement(session, head_pose)
    if head_score is not None:
        head_confidence = _clip_score(90 * face_visibility)

    facial_blendshapes = _measure_facial_blendshapes(blendshapes, session) if blendshapes else None
    if calibration_status == 'calibrating':
        hint = 'Look toward the camera for gaze calibration to finish.'
    elif posture is None:
        hint = 'Face detected; posture needs visible shoulders and hips.'
    elif gaze is None:
        hint = 'Face detected; gaze calibration needs a few seconds of visible eyes.'
    else:
        hint = 'Face, gaze, head pose and upper-body landmarks are being tracked.'
    return _visual_result(
        gaze, posture, hint, session=session, face_detected=True, visibility_score=face_visibility,
        gaze_method='mediapipe_tasks_iris_blendshapes' if gaze is not None else None,
        posture_method='mediapipe_tasks_pose' if posture is not None else None,
        head_movement_score=head_score, gaze_calibration_status=calibration_status,
        expression='N/A',
        facial_blendshapes=facial_blendshapes,
        metric_confidence={
            'eye_contact': _clip_score(gaze_confidence * face_visibility) if gaze_confidence is not None else None,
            'posture': posture_confidence if posture is not None else None,
            'head_movement': head_confidence if head_score is not None else None,
            'expression': None,
        },
    )


def _yunet_fallback(img):
    if _yunet_detector is None:
        return None
    h, w = img.shape[:2]
    try:
        with _vision_lock:
            _yunet_detector.setInputSize((w, h))
            _, faces = _yunet_detector.detect(img)
        if faces is None or not len(faces):
            return None
        face_confidence = _clip_score(float(max(faces, key=lambda row: row[2] * row[3])[14]) * 100)
        result = _unmeasured_visual_result('YuNet detected a face; detailed face and pose landmarks are unavailable.')
        result.update({'face_detected': True, 'valid': True, 'face_detection_confidence': face_confidence})
        return result
    except Exception as exc:
        logger.warning('[LIVE WARN] YuNet fallback failed: %s', exc)
        return None

def analyze_webcam_frame(base64_image_data: str, session=None) -> dict:
    """
    Analyzes a base64 encoded video frame for eye contact and posture alignment.
    Uses MediaPipe Tasks and YuNet face detection fallback only.
    """
    if not base64_image_data:
        return _unmeasured_visual_result("No video signal was received.")

    try:
        img = _decode_frame(base64_image_data)
        if img is None:
            reason = OPENCV_IMPORT_ERROR or "OpenCV could not decode this frame."
            return _unmeasured_visual_result(f"Camera frame could not be processed: {reason}")

        tasks_result = _analyze_frame_with_tasks(img, session)
        if tasks_result is not None:
            return tasks_result

        yunet_result = _yunet_fallback(img)
        if yunet_result is not None:
            return yunet_result

        return _unmeasured_visual_result("Face not detected. Ensure adequate lighting and look directly at the camera.", "calibrating")
    except Exception as e:
        logger.error("[LIVE ERROR] Frame processing failed: %s", e)
        return _unmeasured_visual_result("Camera analysis failed for this frame.")


def _detect_live_speech_activity(rms_samples, sample_period_ms=50):
    """Apply the Speech Analyzer RMS gate to browser-sampled microphone levels."""
    if not isinstance(rms_samples, (list, tuple)) or len(rms_samples) < 5:
        return 'unsupported'
    try:
        levels = [float(value) for value in rms_samples]
        if any(not math.isfinite(level) or level < 0 or level > 1 for level in levels):
            return 'unsupported'
        period = float(sample_period_ms) / 1000.0
        if not math.isfinite(period) or period <= 0:
            return 'unsupported'
    except (TypeError, ValueError, OverflowError):
        return 'unsupported'

    noise_floor = sorted(levels)[int((len(levels) - 1) * 0.2)]
    threshold = max(0.003, noise_floor * 2.5)
    active_duration = sum(period for level in levels if level >= threshold)
    total_duration = len(levels) * period
    active_ratio = active_duration / total_duration if total_duration else 0
    return 'silence' if active_duration < 0.25 or active_ratio < 0.02 else 'speech'


def analyze_audio_chunk(
    base64_audio_data: str,
    session_id: str = 'live',
    transcript_hint: str = '',
    rms_samples=None,
    rms_sample_period_ms=50,
) -> dict:
    """Validate real microphone activity and Whisper output before scoring a chunk."""
    unavailable = {
        'wpm': None, 'filler_word_detected': False, 'transcript': '',
        'filler_count': 0, 'pitch_score': None,
        'vocal_sentiment': 'Unavailable', 'valid': False,
        'no_speech_detected': False, 'transcription_unavailable': True,
    }
    if not base64_audio_data:
        return unavailable

    try:
        audio_payload = base64_audio_data.split(',', 1)[-1]
        audio_bytes = base64.b64decode(audio_payload, validate=True)
        if not audio_bytes:
            return {**unavailable, 'no_speech_detected': True, 'transcription_unavailable': False}
        signal_status = _detect_live_speech_activity(rms_samples, rms_sample_period_ms)
        if signal_status == 'silence':
            return {**unavailable, 'no_speech_detected': True, 'transcription_unavailable': False}
        if signal_status != 'speech' or not groq_client:
            return unavailable

        transcript = ''
        no_speech_probabilities = []
        temp_audio_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.webm') as temp_audio:
                temp_audio.write(audio_bytes)
                temp_audio_path = temp_audio.name
            with open(temp_audio_path, 'rb') as audio_file:
                result = groq_client.audio.transcriptions.create(
                    file=(f'chunk_{session_id}.webm', audio_file.read()),
                    model='whisper-large-v3',
                    language='en',
                    response_format='verbose_json',
                    temperature=0.0,
                )
            raw_text = result.get('text') if isinstance(result, dict) else getattr(result, 'text', None)
            if not isinstance(raw_text, str):
                return unavailable
            transcript = raw_text.strip()
            raw_segments = result.get('segments') if isinstance(result, dict) else getattr(result, 'segments', None)
            for segment in raw_segments if isinstance(raw_segments, (list, tuple)) else []:
                probability = segment.get('no_speech_prob') if isinstance(segment, dict) else getattr(segment, 'no_speech_prob', None)
                if isinstance(probability, (int, float)) and not isinstance(probability, bool) and math.isfinite(probability) and 0 <= probability <= 1:
                    no_speech_probabilities.append(float(probability))
        finally:
            if temp_audio_path and os.path.exists(temp_audio_path):
                try:
                    os.remove(temp_audio_path)
                except OSError:
                    logger.warning('[LIVE WARN] Could not remove temporary audio chunk.')

        if not no_speech_probabilities or sum(no_speech_probabilities) / len(no_speech_probabilities) >= 0.6 or len(transcript.split()) < MIN_WORDS_PER_CHUNK:
            return {**unavailable, 'no_speech_detected': True, 'transcription_unavailable': False}

        words = transcript.split()
        wpm = len(words) * 20  # Each validated MediaRecorder chunk spans three seconds.
        filler_words = ('um', 'uh', 'erm', 'err', 'like', 'you know', 'basically', 'actually', 'kind of')
        filler_count = sum(len(re.findall(r'\b' + re.escape(filler) + r'\b', transcript.lower())) for filler in filler_words)
        return {
            'wpm': wpm, 'filler_word_detected': filler_count > 0,
            'transcript': transcript, 'filler_count': filler_count,
            'pitch_score': None, 'vocal_sentiment': 'Unavailable',
            'valid': True, 'no_speech_detected': False, 'transcription_unavailable': False,
        }
    except Exception as exc:
        logger.warning('[LIVE STT WARN] Audio chunk validation/transcription failed: %s', exc)
        return unavailable

@phase_live_bp.route('/vision-status', methods=['GET'])
def get_vision_status():
    """
    Reports native CV dependency status for debugging live webcam analysis.
    """
    return jsonify({
        "opencv_available": OPENCV_AVAILABLE,
        "opencv_version": cv2.__version__ if OPENCV_AVAILABLE else None,
        "opencv_error": OPENCV_IMPORT_ERROR,
        "mediapipe_imported": MEDIAPIPE_IMPORTED,
        "mediapipe_available": MEDIAPIPE_AVAILABLE,
        "mediapipe_mode": MEDIAPIPE_MODE,
        "mediapipe_version": MEDIAPIPE_VERSION,
        "mediapipe_error": MEDIAPIPE_IMPORT_ERROR,
        "face_task_model_ready": os.path.isfile(FACE_TASK_PATH),
        "pose_task_model_ready": os.path.isfile(POSE_TASK_PATH),
        "yunet_fallback_ready": _yunet_detector is not None,
        "primary_analyzer": "mediapipe_tasks" if _face_landmarker is not None else ("opencv_yunet" if _yunet_detector is not None else None),
    }), 200


# ===== WEBSOCKET SOCKET.IO EVENT HANDLERS =====

_active_frame_sessions = set()
_active_frame_lock = threading.Lock()

def init_socketio_events(socketio):
    """
    Binds WebSocket events to the Flask-SocketIO instance.
    """
    
    @socketio.on('connect', namespace='/ws/live-session')
    def on_connect():
        logger.info("[CONN] Live presentation socket connected (sid: %s...)", str(request.sid)[:8])

    @socketio.on('disconnect', namespace='/ws/live-session')
    def on_disconnect():
        logger.info("[CONN] Live presentation socket disconnected (sid: %s...)", str(request.sid)[:8])
        sess_id = _sid_to_session.pop(request.sid, None)
        if sess_id:
            s_str = str(sess_id)
            with _active_frame_lock:
                _active_frame_sessions.discard(s_str)
            release_session_detector(s_str)

    @socketio.on('stop_session', namespace='/ws/live-session')
    def on_stop_session(data):
        sess_id = data.get('session_id') if data else _sid_to_session.get(request.sid)
        _sid_to_session.pop(request.sid, None)
        if sess_id:
            s_str = str(sess_id)
            with _active_frame_lock:
                _active_frame_sessions.discard(s_str)
            release_session_detector(s_str)

    @socketio.on('start_session', namespace='/ws/live-session')
    def on_start_session(data):
        """
        Initializes a presentation practice session, pulls memory, and sends config.
        """
        user_id = data.get('user_id', 'guest')
        topic = data.get('topic', 'General Presentation').strip()
        
        logger.info("[INFO] Creating live presentation session. Topic: %s", topic[:50])
        
        # Create DB session
        session = PresentationSession.create(user_id=user_id, topic=topic)
        _sid_to_session[request.sid] = session.id
        
        # Context Memory Matrix: Fetch past reports for this topic
        historical_records = HistoricalReport.get_by_user_and_topic(user_id, topic)
        has_history = len(historical_records) > 0
        
        history_summary = {}
        if has_history:
            last_rep = historical_records[0].report_json
            history_summary = {
                "previous_score": last_rep.get("overall_score", 0),
                "top_strengths": last_rep.get("strengths", [])[:2],
                "top_recommendations": last_rep.get("recommendations", [])[:2]
            }

        session_data = {
            "status": "success",
            "session_id": session.id,
            "has_history": has_history,
            "history_summary": history_summary,
            "stt_available": groq_client is not None
        }
        socketio.emit('session_started', session_data, room=request.sid, namespace='/ws/live-session')
        return session_data

    @socketio.on('video_frame', namespace='/ws/live-session')
    def on_video_frame(data):
        """
        Processes a real-time frame, updates metrics, and returns feedback.
        """
        session_id = data.get('session_id')
        frame_data = data.get('frame')
        frame_id = data.get('frame_id')
        
        if not session_id or not frame_data:
            return {'frame_id': frame_id, 'dropped': True}
            
        session = PresentationSession.get_by_id(session_id)
        if not session or session.status not in ('STREAMING', 'INTERRUPTED_Q&A'):
            return {'frame_id': frame_id, 'dropped': True}

        session.increment_metric("video_frames_received")

        # Frame backpressure guard: If a previous frame is currently being processed
        # for this session, drop this frame immediately to avoid thread buildup and latency lag.
        session_id_str = str(session_id)
        with _active_frame_lock:
            if session_id_str in _active_frame_sessions:
                session.increment_metric("video_frames_dropped")
                return {'frame_id': frame_id, 'dropped': True}
            _active_frame_sessions.add(session_id_str)

        try:
            metrics = analyze_webcam_frame(frame_data, session)
            session.increment_metric("video_frames_analyzed")
            face_detected = metrics.get("face_detected", False)
            if face_detected:
                session.increment_metric("video_frames_with_face")

            eye_score = metrics.get("eye_contact")
            posture_score = metrics.get("posture")
            head_movement_score = metrics.get("head_movement")
            confidence_score = metrics.get("confidence")
            facial_blendshapes = metrics.get("facial_blendshapes")
            if eye_score is not None:
                session.update_metrics("eye_contact_scores", eye_score)
                session.increment_metric("video_frames_with_gaze")
            if posture_score is not None:
                session.update_metrics("posture_scores", posture_score)
                session.increment_metric("video_frames_with_posture")
            if head_movement_score is not None:
                session.update_metrics("head_movement_scores", head_movement_score)
                session.increment_metric("video_frames_with_head_movement")
            if confidence_score is not None:
                session.update_metrics("confidence_scores", confidence_score)
            if isinstance(facial_blendshapes, dict) and facial_blendshapes:
                session.metrics.setdefault("facial_blendshape_samples", [])
                session.update_metrics("facial_blendshape_samples", facial_blendshapes)
            
            socketio.emit('realtime_feedback', {
                "frame_id": frame_id,
                "face_detected": face_detected,
                "eye_contact": eye_score,
                "posture": posture_score,
                "head_movement": head_movement_score,
                "gaze_calibration_status": metrics.get("gaze_calibration_status", "unavailable"),
                "hint": metrics.get("hint", "No visual metrics were available for this frame."),
                "confidence": confidence_score,
                "emotion": "N/A",
                "expression": "N/A",
                "facial_blendshapes": facial_blendshapes,
                "metric_confidence": metrics.get("metric_confidence", {}),
            }, room=request.sid, namespace='/ws/live-session')
        except Exception as exc:
            logger.warning('[LIVE WARN] Frame event failed: %s', exc)
            session.increment_metric("video_frames_dropped")
            return {'frame_id': frame_id, 'dropped': True}
        finally:
            with _active_frame_lock:
                _active_frame_sessions.discard(session_id_str)
        return {'frame_id': frame_id, 'dropped': False}

    @socketio.on('audio_chunk', namespace='/ws/live-session')
    def on_audio_chunk(data):
        """
        Processes real-time audio chunk, checks filler density, triggers interruptions.
        """
        session_id = data.get('session_id')
        audio_data = data.get('audio')
        current_transcript = data.get('transcript_snippet', '').strip()
        
        if not session_id or not audio_data:
            return
            
        session = PresentationSession.get_by_id(session_id)
        if not session or session.status != 'STREAMING':
            return
            
        # Voice dynamics
        voice_metrics = analyze_audio_chunk(
            audio_data,
            session_id,
            current_transcript,
            data.get('rms_samples'),
            data.get('rms_sample_period_ms', 50),
        )
        if voice_metrics.get("valid", False):
            session.update_metrics("wpm_history", voice_metrics["wpm"])
            session.update_metrics("transcripts", voice_metrics["transcript"])
            if voice_metrics.get("pitch_score") is not None:
                session.update_metrics("vocal_sentiment_scores", voice_metrics["pitch_score"])
            
        if voice_metrics.get("filler_count", 0) > 0:
            session.increment_metric("fillers_detected", voice_metrics["filler_count"])
            
        # Check Interruption Trigger
        # Condition: Trigger if filler count is high, or periodically every 45-60 seconds.
        # We also check if we are already in Q&A to avoid double triggers.
        total_fillers = session.metrics.get("fillers_detected", 0)
        interruptions = session.metrics.get("interruptions", [])
        
        # Frequency Limiter: At most 2 interruptions, and wait at least 45 seconds.
        can_interrupt = len(interruptions) < 2
        if can_interrupt and len(interruptions) > 0:
            last_int_time = datetime.fromisoformat(interruptions[-1]["timestamp"])
            if last_int_time.tzinfo is None:
                last_int_time = last_int_time.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last_int_time).total_seconds()
            if elapsed < 45:
                can_interrupt = False
                
        # Trigger interruption on weak points (e.g. filler count reaches 5, or transcript shows poor pacing)
        trigger_by_filler = (total_fillers > 0 and total_fillers % 5 == 0)
        
        wpm_count = len(session.metrics.get("wpm_history", []))
        trigger_by_interval = wpm_count > 0 and wpm_count % 25 == 0
        if can_interrupt and (trigger_by_filler or trigger_by_interval):
            # Extract speaker's recent transcript context
            recent_transcripts = session.metrics.get("transcripts", [])[-4:]
            recent_speech = " ".join(recent_transcripts).strip() if recent_transcripts else session.topic

            # Generate high-level academic professor cross-question
            topic = session.topic
            question = f"Regarding {topic}, how do you validate your methodology against potential edge cases?"

            if gemini_available:
                try:
                    prompt = f"""You are Professor Eleanor Vance, a Senior Academic Evaluator and University Defense Chair presiding over a presentation.

PRESENTATION TOPIC: '{topic}'
SPEAKER'S RECENT WORDS: '{recent_speech}'

Formulate 1 sharp, highly educated, probing cross-examination question directly challenging or probing the speaker's claim, methodology, assumptions, or real-world applicability.
Your question MUST sound like a tough, inquisitive university professor testing their deep conceptual understanding. Keep it under 25 words."""
                    generated_q = _gemini_provider.generate(prompt=prompt)
                    if generated_q and generated_q.strip():
                        question = generated_q.strip()
                except Exception as e:
                    logger.warning("[LIVE WARN] Gemini cross-question generation failed: %s", e)

            # Transition state in DB
            session.update_status("INTERRUPTED_Q&A")

            # Log interruption
            int_log = {
                "question": question,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "answer": None,
                "score": None
            }
            session.update_metrics("interruptions", int_log)

            # Emit interruption to frontend
            socketio.emit('interruption_trigger', {
                "question": question,
                "evaluator_name": "Prof. Eleanor Vance",
                "evaluator_role": "Senior Academic Defense Examiner"
            }, room=request.sid, namespace='/ws/live-session')

    @socketio.on('submit_answer', namespace='/ws/live-session')
    def on_submit_answer(data):
        """
        Receives user answer to panelist cross-question and grades it like a Senior Professor.
        """
        session_id = data.get('session_id')
        answer = data.get('answer', '').strip()

        if not session_id or not answer:
            return

        session = PresentationSession.get_by_id(session_id)
        if not session or session.status != 'INTERRUPTED_Q&A':
            return

        # Grade the answer like a senior academic examiner
        grade_score = None
        feedback = "Answer recorded, but automated grading was unavailable."

        if gemini_available:
            try:
                last_interruption = session.metrics["interruptions"][-1]
                question = last_interruption["question"]

                grading_prompt = f"""You are Professor Eleanor Vance, Senior Academic Examiner. Evaluate this student's response to your cross-examination question.

QUESTION: '{question}'
STUDENT ANSWER: '{answer}'

Evaluate the answer on:
1. Conceptual accuracy & analytical depth
2. Use of evidence/examples
3. Composure & defensive clarity

Return ONLY a single valid JSON object:
{{
    "score": <integer 0-100>,
    "feedback": "<2-sentence articulate critique explaining the score and how to make the answer bulletproof>"
}}"""
                res_data = _gemini_provider.generate_structured(prompt=grading_prompt)
                if res_data and isinstance(res_data, dict):
                    score = int(res_data["score"])
                    if not 0 <= score <= 100:
                        raise ValueError("Grader returned a score outside the valid range.")
                    grade_score = score
                    generated_feedback = res_data.get("feedback")
                    if isinstance(generated_feedback, str) and generated_feedback.strip():
                        feedback = generated_feedback.strip()
            except Exception as e:
                logger.warning("[LIVE WARN] Gemini grading failed: %s", e)
        
        # Update session logs — Firestore-compatible approach:
        # Read the current interruptions list, update the last unanswered entry
        # in-memory, then write the whole list back.
        try:
            ref = db.collection("presentation_sessions").document(session_id)
            doc = ref.get()
            if doc.exists:
                current_interruptions = doc.to_dict().get("metrics", {}).get("interruptions", [])
                # Find and update the last interruption that has no answer yet
                for i in range(len(current_interruptions) - 1, -1, -1):
                    if current_interruptions[i].get("answer") is None:
                        current_interruptions[i]["answer"] = answer
                        current_interruptions[i]["score"] = grade_score
                        current_interruptions[i]["feedback"] = feedback
                        break
                ref.update({"metrics.interruptions": current_interruptions})
                # Keep the in-memory session object in sync
                if session.metrics:
                    session.metrics["interruptions"] = current_interruptions
        except Exception as e:
            logger.warning("[LIVE WARN] Failed to update interruption answer in Firestore: %s", e)
        
        # Resume streaming status
        session.update_status("STREAMING")
        
        # Emit resolution
        socketio.emit('interruption_resolved', {
            "status": "success",
            "score": grade_score,
            "feedback": feedback
        }, room=request.sid, namespace='/ws/live-session')


# ===== COMPILATION REST ENDPOINT =====

@phase_live_bp.route('/submit', methods=['POST'])
@jwt_required(optional=True)
def submit_presentation():
    """
    Submits and finalizes the live presentation session:
    1. Compiles metrics from DB.
    2. Runs Gemini 1.5 Flash to structure a final 7Cs scorecard.
    3. Compares metrics with historical presentations on the same topic.
    4. Saves final report in MongoDB.
    """
    try:
        user_id = get_jwt_identity() or "guest"
        data = request.get_json()
        
        if not data or 'session_id' not in data:
            return jsonify({
                "success": False,
                "error": "MissingSessionId",
                "message": "Please provide a valid session_id"
            }), 400
            
        session_id = data['session_id']
        session = PresentationSession.get_by_id(session_id)
        
        if not session:
            return jsonify({
                "success": False,
                "error": "SessionNotFound",
                "message": "The requested session does not exist"
            }), 404
            
        # ===== STEP 1: COMPILE SESSION METRICS =====
        eye_scores = session.metrics.get("eye_contact_scores", [])
        posture_scores = session.metrics.get("posture_scores", [])
        head_movement_scores = session.metrics.get("head_movement_scores", [])
        facial_blendshape_samples = session.metrics.get("facial_blendshape_samples", [])
        wpm_history = session.metrics.get("wpm_history", [])
        fillers = session.metrics.get("fillers_detected", 0)
        interruptions = session.metrics.get("interruptions", [])
        transcripts = session.metrics.get("transcripts", [])
        
        confidence_scores = session.metrics.get("confidence_scores", [])
        vocal_sentiment_scores = session.metrics.get("vocal_sentiment_scores", [])

        def avg_or_zero(values):
            return int(sum(values) / len(values)) if values else 0

        avg_eye = avg_or_zero(eye_scores)
        avg_posture = avg_or_zero(posture_scores)
        avg_head_movement = avg_or_zero(head_movement_scores)
        avg_wpm = avg_or_zero(wpm_history) if wpm_history else None
        avg_confidence = avg_or_zero(confidence_scores)
        has_enough_blendshapes = len(facial_blendshape_samples) >= MIN_VALID_VIDEO_SAMPLES
        avg_facial_blendshapes = None
        if has_enough_blendshapes:
            blendshape_totals = {}
            blendshape_counts = {}
            for sample in facial_blendshape_samples:
                if not isinstance(sample, dict):
                    continue
                for name, coefficient in sample.items():
                    if isinstance(coefficient, (int, float)) and math.isfinite(coefficient):
                        blendshape_totals[name] = blendshape_totals.get(name, 0.0) + coefficient
                        blendshape_counts[name] = blendshape_counts.get(name, 0) + 1
            avg_facial_blendshapes = {
                name: round(blendshape_totals[name] * 100 / count, 1)
                for name, count in blendshape_counts.items() if count
            } or None

        qna_scores = [i["score"] for i in interruptions if i.get("score") is not None]
        avg_qna = avg_or_zero(qna_scores)

        has_enough_gaze = len(eye_scores) >= MIN_VALID_VIDEO_SAMPLES
        has_enough_posture = len(posture_scores) >= MIN_VALID_VIDEO_SAMPLES
        has_enough_head_movement = len(head_movement_scores) >= MIN_VALID_VIDEO_SAMPLES
        has_enough_video = has_enough_gaze or has_enough_posture or has_enough_head_movement
        has_valid_speech = bool(wpm_history and transcripts)
        has_enough_audio = has_valid_speech and len(wpm_history) >= MIN_VALID_AUDIO_SAMPLES
        if not has_enough_audio:
            avg_wpm = None
        has_tracking_proxy = len(confidence_scores) >= MIN_VALID_VIDEO_SAMPLES

        # The final report uses the mean of the same per-frame weighted
        # Visual Tracking values displayed live.
        visual_presence = avg_confidence if has_tracking_proxy else None
        vocal_delivery = None
        if has_enough_audio:
            vocal_delivery = min(100, max(20, 100 - (fillers * 4) - abs(avg_wpm - 140) // 2))
        content_quality = avg_qna if qna_scores else None
        video_frames_received = session.metrics.get("video_frames_received", 0)
        video_frames_analyzed = session.metrics.get("video_frames_analyzed", 0)
        video_frames_dropped = session.metrics.get("video_frames_dropped", 0)
        video_frames_with_face = session.metrics.get("video_frames_with_face", 0)
        video_frames_with_gaze = session.metrics.get("video_frames_with_gaze", 0)
        video_frames_with_posture = session.metrics.get("video_frames_with_posture", 0)
        valid_frame_coverage = (
            round(video_frames_with_face * 100 / video_frames_received, 1)
            if video_frames_received else 0
        )
        gaze_frame_coverage = (
            round(video_frames_with_gaze * 100 / video_frames_received, 1)
            if video_frames_received else 0
        )
        posture_frame_coverage = (
            round(video_frames_with_posture * 100 / video_frames_received, 1)
            if video_frames_received else 0
        )
        analyzed_frame_coverage = (
            round(video_frames_analyzed * 100 / video_frames_received, 1)
            if video_frames_received else 0
        )

        # ===== SCORING FORMULA WITH WEIGHT RENORMALIZATION =====
        # Base weights: Visual Presence (0.35), Vocal Delivery (0.35), Content / Q&A Quality (0.30).
        # If any component is unavailable (e.g. no Q&A interruptions occurred or camera was off),
        # its weight is redistributed proportionally among remaining active components:
        #   w_i_normalized = w_i / sum(w_active)
        # Full 3-component case: 0.35/1.0 = 35%, 0.35/1.0 = 35%, 0.30/1.0 = 30%
        # 2-component visual+vocal case: 0.35/0.70 = 50%, 0.35/0.70 = 50%
        weighted_scores = []
        if visual_presence is not None:
            weighted_scores.append((visual_presence, 0.35))
        if vocal_delivery is not None:
            weighted_scores.append((vocal_delivery, 0.35))
        if content_quality is not None:
            weighted_scores.append((content_quality, 0.30))

        total_weight = sum(weight for _, weight in weighted_scores)
        if has_valid_speech and total_weight > 0:
            # Proportional weight redistribution: sum( score * (w / total_weight) )
            normalized_scores = [(score, weight / total_weight) for score, weight in weighted_scores]
            overall_execution = int(round(sum(score * norm_w for score, norm_w in normalized_scores)))
        else:
            overall_execution = None

        # ── FIX: explicit flag for "nothing measurable happened this session"
        # so the frontend can show "Insufficient data" instead of a bare 0/40
        # that looks like a real (bad) score.
        has_any_data = bool(weighted_scores)
        
        # Build full transcript for evaluation
        transcript_full = " ".join(transcripts)

        # ===== STEP 2: GENERATE 7Cs ANALYSIS VIA CENTRAL EVALUATOR =====
        if has_valid_speech:
            from ai_evaluator import evaluate_7cs
            report_json = evaluate_7cs(
                text=transcript_full,
                module_type='live',
                context_metrics={
                    "topic": session.topic,
                    "avg_eye": avg_eye,
                    "avg_posture": avg_posture,
                    "avg_head_movement": avg_head_movement if has_enough_head_movement else None,
                    "avg_wpm": avg_wpm,
                    "fillers": fillers,
                    "avg_qna": avg_qna,
                    "interruptions": interruptions,
                    "overall_execution": overall_execution,
                    "has_visual_metrics": has_enough_video,
                    "has_gaze_metrics": has_enough_gaze,
                    "has_posture_metrics": has_enough_posture,
                    "has_voice_metrics": has_enough_audio,
                    "has_qna_scores": bool(qna_scores)
                }
            )
        else:
            dimensions = ("Clear", "Concise", "Correct", "Complete", "Courteous", "Concrete", "Consistent")
            categories = ("Structure", "Clarity", "Persuasion", "Content_Quality", "Call_to_Action", "Grammar_and_Syntax", "Accuracy", "Tone_Appropriateness", "Audience_Alignment", "Purpose_Fulfillment")
            report_json = {
                "overall_score": None,
                "category_scores": {name: None for name in categories},
                "seven_cs_scores": {name: None for name in dimensions},
                "seven_cs_evaluation": {name: "No speech detected — not scored." for name in dimensions},
                "strengths": [],
                "recommendations": [],
                "detailed_feedback": "No speech detected. Speech-dependent scores and the overall presentation score were not calculated. Measured visual metrics are retained below.",
                "improved_text": "No speech detected.",
                "grammar_score": None,
                "grammar_issues": [],
                "grammar_issues_count": 0,
                "grammar_check_status": "unavailable",
                "qna_analysis": "No speech detected; Q&A was not included in the presentation score.",
            }
        seven_cs_scores = report_json.get("seven_cs_scores")
        if not isinstance(seven_cs_scores, dict):
            seven_cs_scores = {}
            report_json["seven_cs_scores"] = seven_cs_scores
        seven_cs_evaluation = report_json.get("seven_cs_evaluation")
        if not isinstance(seven_cs_evaluation, dict):
            seven_cs_evaluation = {}
            report_json["seven_cs_evaluation"] = seven_cs_evaluation

        def mark_unscored(dimension, reason):
            seven_cs_scores[dimension] = None
            seven_cs_evaluation[dimension] = reason

        if not has_enough_audio:
            for dimension in ("Clear", "Concise", "Correct", "Courteous"):
                mark_unscored(dimension, "Not scored — no valid microphone transcript was captured.")
            report_json["grammar_score"] = None
            report_json["grammar_issues"] = []
            report_json["grammar_issues_count"] = 0
            report_json["grammar_check_status"] = "unavailable"
        elif report_json.get("grammar_check_status") != "success":
            mark_unscored("Correct", "Not scored — the transcript grammar check was unavailable.")
            report_json["grammar_score"] = None
            category_scores = report_json.get("category_scores")
            if isinstance(category_scores, dict):
                category_scores["Grammar_and_Syntax"] = None
        if not has_enough_audio and not qna_scores:
            for dimension in ("Complete", "Concrete"):
                mark_unscored(dimension, "Not scored — no transcript or graded Q&A evidence was available.")
        mark_unscored("Consistent", "Not scored — temporal gaze/posture consistency is not calculated.")

        category_scores = report_json.get("category_scores")
        if isinstance(category_scores, dict):
            if not has_enough_audio:
                for category in ("Structure", "Clarity", "Call_to_Action"):
                    category_scores[category] = None
            if not has_enough_audio and not qna_scores:
                category_scores["Content_Quality"] = None
                category_scores["Persuasion"] = None

        report_json["overall_score"] = overall_execution
        report_json["insufficient_data"] = (not has_any_data) or (not has_valid_speech)
        report_json["speech_status"] = "Speech detected" if has_valid_speech else "No speech detected"

        video_feedback = []
        if has_enough_gaze:
            video_feedback.append(
                "Your measured camera-facing gaze averaged "
                f"{avg_eye}%; try returning your gaze to the camera more often."
                if avg_eye < 65 else
                f"Your measured camera-facing gaze averaged {avg_eye}%."
            )
        if has_enough_posture:
            video_feedback.append(
                "Your measured upper-body alignment averaged "
                f"{avg_posture}%; keep your shoulders level and torso upright."
                if avg_posture < 65 else
                f"Your measured upper-body alignment averaged {avg_posture}%."
            )
        if has_enough_head_movement:
            video_feedback.append(
                "Your head steadiness averaged "
                f"{avg_head_movement}%; reduce frequent head shifts while addressing the camera."
                if avg_head_movement < 65 else
                f"Your measured head steadiness averaged {avg_head_movement}%."
            )
        report_json["video_feedback"] = video_feedback
        if video_feedback:
            recommendations = report_json.get("recommendations")
            if not isinstance(recommendations, list):
                recommendations = []
            report_json["recommendations"] = recommendations + video_feedback

        # Context Memory Matrix: Fetch past reports for this topic to check progress
        past_reports = HistoricalReport.get_by_user_and_topic(user_id, session.topic)
        
        comparison = {
            "improved": False,
            "difference": 0,
            "note": "No prior comparable session is available for this topic."
        }
        
        if past_reports:
            last_report = past_reports[0].report_json
            if not last_report.get("insufficient_data", False):
                last_overall = last_report.get("overall_score")
                if isinstance(last_overall, (int, float)) and report_json["overall_score"] is not None:
                    diff = report_json["overall_score"] - last_overall
                    comparison = {
                        "improved": diff > 0,
                        "difference": diff,
                        "note": f"Your score shifted from {last_overall} to {report_json['overall_score']} using the measurements available in each session."
                    }

        # FIX: don't present a misleading "progress" comparison when this
        # session (or the prior one) had no real measured data.
        if not has_valid_speech:
            comparison = {
                "improved": False,
                "difference": 0,
                "note": "No speech detected. The overall presentation score is unavailable; measured visual metrics are retained."
            }
        elif not has_any_data:
            comparison = {
                "improved": False,
                "difference": 0,
                "note": "No measurable camera, microphone, or graded Q&A data was captured this session."
            }

        report_json["comparison"] = comparison
        report_json["topic"] = session.topic
        report_json["session_metrics"] = {
            "avg_eye_contact": avg_eye if has_enough_gaze else None,
            "avg_posture": avg_posture if has_enough_posture else None,
            "avg_head_movement": avg_head_movement if has_enough_head_movement else None,
            "avg_facial_blendshapes": avg_facial_blendshapes,
            "avg_wpm": avg_wpm if has_enough_audio else None,
            "total_fillers": fillers if has_enough_audio else None,
            "interruptions_handled": len(interruptions),
            "avg_confidence": avg_confidence if has_tracking_proxy else None,
            "avg_vocal_pitch": None,
            "data_quality": {
                "video_samples": len(eye_scores),
                "video_frames_received": video_frames_received,
                "video_frames_analyzed": video_frames_analyzed,
                "video_frames_dropped": video_frames_dropped,
                "video_frames_with_face": video_frames_with_face,
                "video_frames_with_gaze": video_frames_with_gaze,
                "video_frames_with_posture": video_frames_with_posture,
                "head_movement_samples": len(head_movement_scores),
                "facial_blendshape_samples": len(facial_blendshape_samples),
                "has_facial_blendshape_metrics": has_enough_blendshapes,
                "valid_frame_coverage": valid_frame_coverage,
                "gaze_frame_coverage": gaze_frame_coverage,
                "posture_frame_coverage": posture_frame_coverage,
                "analyzed_frame_coverage": analyzed_frame_coverage,
                "gaze_samples": len(eye_scores),
                "posture_samples": len(posture_scores),
                "tracking_proxy_samples": len(confidence_scores),
                "audio_samples": len(wpm_history),
                "pitch_samples": 0,
                "transcript_segments": len(transcripts),
                "qna_scores": len(qna_scores),
                "has_video_metrics": has_enough_video,
                "has_gaze_metrics": has_enough_gaze,
                "has_posture_metrics": has_enough_posture,
                "has_head_movement_metrics": has_enough_head_movement,
                "has_tracking_proxy": has_tracking_proxy,
                "has_audio_metrics": has_enough_audio,
                "has_pitch_metrics": False,
                "has_qna_scores": bool(qna_scores),
                "min_video_samples_required": MIN_VALID_VIDEO_SAMPLES,   # FIX
                "min_audio_samples_required": MIN_VALID_AUDIO_SAMPLES,   # FIX
            }
        }
        
        # Save historical report
        HistoricalReport.create(
            session_id=session_id,
            user_id=user_id,
            topic=session.topic,
            report_json=report_json
        )
                # Also store in the main reports collection so the dashboard history shows Live Coach sessions
        try:
            from models import Report as _DashboardReport
            _dashboard_json = dict(report_json)
            _dashboard_json.setdefault("topic", session.topic)
            _DashboardReport.create(
                report_json=_dashboard_json,
                report_type="live_coaching",
                user_id=user_id,
            )
        except Exception as _dash_err:
            import logging
            logging.getLogger(__name__).warning("Dashboard history save skipped: %s", _dash_err)
        # Update session status
        session.update_status("FINISHED")
        
        # Release MediaPipe detector to prevent resource leaks (ISSUE-01)
        release_session_detector(session_id)
        
        return jsonify({
            "status": "success",
            "report": report_json
        }), 200
        
    except Exception as e:
        logger.error("[LIVE ERROR] Final submit error: %s", e)
        return jsonify({
            "success": False,
            "error": "SubmissionFailed",
            "message": str(e)
        }), 500


@phase_live_bp.route('/history', methods=['GET'])
@jwt_required()
def get_topic_history():
    """
    Fetches historical live session reports for comparison dashboard.
    """
    try:
        user_id = get_jwt_identity()
        topic = request.args.get('topic', '').strip()
        
        if not topic:
            return jsonify({
                "success": False,
                "error": "MissingTopic",
                "message": "Please provide a 'topic' query parameter"
            }), 400
            
        reports = HistoricalReport.get_by_user_and_topic(user_id, topic)
        return jsonify({
            "status": "success",
            "reports": [r.to_dict() for r in reports]
        }), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "error": "HistoryRetrievalFailed",
            "message": str(e)
        }), 500

# touch reload
