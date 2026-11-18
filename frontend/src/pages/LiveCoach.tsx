import React, { useState, useRef, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { useLiveSession } from '../hooks/useLiveSession';
import VideoCapture from '../components/VideoCapture';
import ReportDashboard from '../components/ReportDashboard';
import { downloadProgressReportPDF } from '../services/pdfGenerator';
import './LiveCoach.css';
import './LiveCoachMockup.css';

/**
 * LiveCoach Page
 * Real-time presentation practice with webcam/mic tracking (eye contact, posture, pacing, filler words)
 * and academic-panel Q&A interruptions, scored via Gemini 7Cs evaluation.
 * Redesigned layout (mockup style) with topic chips, checklist and countdown. Session logic is unchanged.
 */

// Small circular progress ring used on the metric cards
const Ring: React.FC<{ value: number }> = ({ value }) => {
  const v = Math.max(0, Math.min(100, Number(value) || 0));
  const color = v >= 80 ? '#10b981' : v >= 60 ? '#fbbf24' : '#ef4444';
  const r = 24;
  const c = 2 * Math.PI * r;
  return (
    <svg width="64" height="64" viewBox="0 0 60 60" className="lc-ring">
      <circle cx="30" cy="30" r={r} fill="none" stroke="rgba(129,140,248,0.2)" strokeWidth="5" />
      <circle
        cx="30"
        cy="30"
        r={r}
        fill="none"
        stroke={color}
        strokeWidth="5"
        strokeLinecap="round"
        strokeDasharray={c}
        strokeDashoffset={c * (1 - v / 100)}
        transform="rotate(-90 30 30)"
        style={{ transition: 'stroke-dashoffset 0.6s ease' }}
      />
    </svg>
  );
};

const TOPIC_SUGGESTIONS = [
  'Blockchain Scaling',
  'Machine Learning in Healthcare',
  'Climate Change Solutions',
  'Cyber Security Basics',
  'Startup Pitch',
  'Final Year Project Defense',
];

const CHECKLIST_ITEMS = [
  'Camera is allowed and facing me',
  'Microphone is allowed and working',
  'My face is well lit',
  'I am in a quiet place',
];

const BLENDSHAPE_LABELS: Record<string, string> = {
  mouthSmileLeft: 'Mouth corner (L)', mouthSmileRight: 'Mouth corner (R)',
  eyeBlinkLeft: 'Eye blink (L)', eyeBlinkRight: 'Eye blink (R)',
  browDownLeft: 'Brow movement (L)', browDownRight: 'Brow movement (R)',
  jawOpen: 'Jaw opening', mouthPressLeft: 'Mouth press (L)', mouthPressRight: 'Mouth press (R)',
};

const formatBlendshapeReport = (values?: Record<string, number> | null) => {
  if (!values) return 'N/A';
  const entries = Object.entries(values)
    .filter(([, value]) => Number.isFinite(value))
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3);
  return entries.length
    ? entries.map(([name, value]) => `${BLENDSHAPE_LABELS[name] || name}: ${Math.round(value)}%`).join(' · ')
    : 'N/A';
};

const LiveCoach: React.FC = () => {
  const { user } = useAuth();
  const userId = user?.id || 'guest';

  // ===== WIZARD STATE (kept for potential re-enable, unused while comparison is disabled) =====
  const [_step, _setStep] = useState(1);

  // Version 1 (single-session) states
  const [_v1SessionId, setV1SessionId] = useState<string | null>(null);
  const [v1Report, setV1Report] = useState<any | null>(null);
  const [v1Topic, setV1Topic] = useState('');

  // Live session interactive state
  const [topic, setTopic] = useState('');
  const [seconds, setSeconds] = useState(0);
  const [answerText, setAnswerText] = useState('');
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [cameraEnabled, setCameraEnabled] = useState(true);
  const [cameraRetryKey, setCameraRetryKey] = useState(0);
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [fullscreenError, setFullscreenError] = useState<string | null>(null);
  const [liveTip, setLiveTip] = useState({ key: 'ready', title: 'Live Coach', body: 'Look toward the camera when the session begins.' });
  const [criticalWarning, setCriticalWarning] = useState<string | null>(null);

  // Start screen extras (display only)
  const [checks, setChecks] = useState<boolean[]>(CHECKLIST_ITEMS.map(() => false));
  const [countdown, setCountdown] = useState<number | null>(null);
  const [readinessError, setReadinessError] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const timerRef = useRef<any>(null);
  const sessionStartedAtRef = useRef<number | null>(null);
  const finishRef = useRef<() => void>(() => undefined);
  const previousTipKeyRef = useRef('');
  const warningCandidateRef = useRef<{ key: string; message: string; timer: number } | null>(null);

  const {
    status,
    sessionId,
    currentQuestion,
    realtimeFeedback,
    micActive,
    micLoading,
    micError,
    hasHistory,
    historySummary,
    finalReport,
    error,
    sttAvailable,
    clearVisualFeedback,
    startSession,
    sendAnswer,
    stopSession,
    toggleMicrophone,
    retryMicrophone,
  } = useLiveSession({ userId, videoRef, videoOnly: !!videoFile });
  const feedbackRef = useRef(realtimeFeedback);
  feedbackRef.current = realtimeFeedback;

  // Keep elapsed time across Q&A interruptions and finish exactly at 30 minutes.
  useEffect(() => {
    const sessionActive = status === 'STREAMING' || status === 'INTERRUPTED_Q&A';
    if (!sessionActive) {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      return;
    }
    sessionStartedAtRef.current ??= Date.now();
    const updateElapsed = () => {
      const elapsed = Math.min(1800, Math.floor((Date.now() - sessionStartedAtRef.current!) / 1000));
      setSeconds(elapsed);
      if (elapsed >= 1800) finishRef.current();
    };
    updateElapsed();
    timerRef.current = window.setInterval(updateElapsed, 250);
    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [status]);

  // Save session report (always V1 — comparison disabled)
  useEffect(() => {
    if (status === 'FINISHED' && finalReport && sessionId) {
      if (!v1Report) {
        setV1Report(finalReport);
        setV1SessionId(sessionId);
        setV1Topic(topic);
      }
    }
  }, [status, finalReport, sessionId, topic, v1Report]);

  // 3-2-1 countdown, then the real session starts
  useEffect(() => {
    if (countdown === null) return;
    if (countdown === 0) {
      setCountdown(null);
      sessionStartedAtRef.current = null;
      startSession(topic);
      return;
    }
    const t = setTimeout(() => {
      setCountdown((c) => (c === null ? null : c - 1));
    }, 1000);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [countdown]);

  const handleStart = (e: React.FormEvent) => {
    e.preventDefault();
    if (!topic.trim() || countdown !== null) return;
    if (!checks.every(Boolean)) {
      setReadinessError('Please complete all readiness checks');
      return;
    }
    setReadinessError(null);
    setSeconds(0);
    sessionStartedAtRef.current = null;
    setCameraError(null);
    setCountdown(3);
  };

  const handleFinish = () => stopSession();
  finishRef.current = handleFinish;

  const handleVideoUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] || null;
    event.target.value = '';
    if (!file) return;
    if (!file.size) {
      setCameraError('The selected video is empty. Choose a video with recorded frames.');
      return;
    }
    if (!file.type.startsWith('video/') && !/\.(mp4|webm|mov|m4v|ogg)$/i.test(file.name)) {
      setCameraError('Choose a supported video file.');
      return;
    }
    setCameraError(null);
    clearVisualFeedback('Waiting for a readable video frame.');
    setCameraEnabled(true);
    setVideoFile(file);
  };

  const handleAnswerSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!answerText.trim()) return;
    sendAnswer(answerText);
    setAnswerText('');
  };

  const handleFullscreen = async () => {
    const el = stageRef.current;
    if (!el) return;
    setFullscreenError(null);
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (el.requestFullscreen) await el.requestFullscreen();
      else setFullscreenError('Fullscreen is unavailable in this browser.');
    } catch (err: any) {
      setFullscreenError(err?.message || 'Could not change fullscreen mode.');
    }
  };

  useEffect(() => {
    const onFullscreenChange = () => setIsFullscreen(document.fullscreenElement === stageRef.current);
    document.addEventListener('fullscreenchange', onFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', onFullscreenChange);
  }, []);

  const toggleCheck = (i: number) => {
    setChecks((prev) => prev.map((v, idx) => (idx === i ? !v : v)));
    setReadinessError(null);
  };

  // ===== DOWNLOAD REPORT (single-session — comparison disabled) =====
  const handleDownloadReport = () => {
    if (!v1Report) return;

    const dataQuality = v1Report.session_metrics?.data_quality;
    const hasAudioMetrics = dataQuality?.has_audio_metrics !== false;
    const hasGazeMetrics = dataQuality?.has_gaze_metrics ?? (dataQuality?.has_video_metrics !== false);
    const hasPostureMetrics = dataQuality?.has_posture_metrics ?? (dataQuality?.has_video_metrics !== false);
    const hasTrackingProxy = dataQuality?.has_tracking_proxy ?? (dataQuality?.has_video_metrics !== false);
    const hasQnaScores = dataQuality?.has_qna_scores ?? (v1Report.session_metrics?.interruptions_handled ?? 0) > 0;
    const hasOverallScore = !v1Report.insufficient_data;
    const categoryScore = (score: number | null | undefined, available: boolean): number | string =>
      hasOverallScore && available && score !== null && score !== undefined ? score : 'N/A';

    const categoryScores = [
      { name: 'Structure', v1: categoryScore(v1Report.category_scores?.Structure, hasAudioMetrics), v2: categoryScore(v1Report.category_scores?.Structure, hasAudioMetrics) },
      { name: 'Clarity', v1: categoryScore(v1Report.category_scores?.Clarity, hasAudioMetrics), v2: categoryScore(v1Report.category_scores?.Clarity, hasAudioMetrics) },
      { name: 'Persuasion', v1: categoryScore(v1Report.category_scores?.Persuasion, hasAudioMetrics || hasQnaScores), v2: categoryScore(v1Report.category_scores?.Persuasion, hasAudioMetrics || hasQnaScores) },
      { name: 'Content Quality', v1: categoryScore(v1Report.category_scores?.Content_Quality, hasAudioMetrics || hasQnaScores), v2: categoryScore(v1Report.category_scores?.Content_Quality, hasAudioMetrics || hasQnaScores) },
      { name: 'Call to Action', v1: categoryScore(v1Report.category_scores?.Call_to_Action, hasAudioMetrics), v2: categoryScore(v1Report.category_scores?.Call_to_Action, hasAudioMetrics) },
    ];

    const additionalMetrics = [
      { label: 'Camera-Facing Gaze (Approx.)', v1: hasGazeMetrics && v1Report.session_metrics?.avg_eye_contact != null ? `${v1Report.session_metrics.avg_eye_contact}%` : 'N/A', v2: hasGazeMetrics && v1Report.session_metrics?.avg_eye_contact != null ? `${v1Report.session_metrics.avg_eye_contact}%` : 'N/A', change: '' },
      { label: 'Upper-Body Posture Estimate', v1: hasPostureMetrics && v1Report.session_metrics?.avg_posture != null ? `${v1Report.session_metrics.avg_posture}%` : 'N/A', v2: hasPostureMetrics && v1Report.session_metrics?.avg_posture != null ? `${v1Report.session_metrics.avg_posture}%` : 'N/A', change: '' },
      { label: 'Visual Tracking Proxy', v1: hasTrackingProxy && v1Report.session_metrics?.avg_confidence != null ? `${v1Report.session_metrics.avg_confidence}%` : 'N/A', v2: hasTrackingProxy && v1Report.session_metrics?.avg_confidence != null ? `${v1Report.session_metrics.avg_confidence}%` : 'N/A', change: '' },
      { label: 'Facial Blendshape Coefficients', v1: formatBlendshapeReport(v1Report.session_metrics?.avg_facial_blendshapes), v2: formatBlendshapeReport(v1Report.session_metrics?.avg_facial_blendshapes), change: '' },
      { label: 'Vocal Pitch Dynamics', v1: 'N/A', v2: 'N/A', change: '' },
      { label: 'Speaking Pace', v1: hasAudioMetrics && v1Report.session_metrics?.avg_wpm != null ? `${v1Report.session_metrics.avg_wpm} WPM` : 'N/A', v2: hasAudioMetrics && v1Report.session_metrics?.avg_wpm != null ? `${v1Report.session_metrics.avg_wpm} WPM` : 'N/A', change: '' },
      { label: 'Filler Words Count', v1: hasAudioMetrics && v1Report.session_metrics?.total_fillers != null ? `${v1Report.session_metrics.total_fillers}` : 'N/A', v2: hasAudioMetrics && v1Report.session_metrics?.total_fillers != null ? `${v1Report.session_metrics.total_fillers}` : 'N/A', change: '' },
      { label: 'Face-Frame Coverage', v1: dataQuality?.video_frames_received ? `${dataQuality.video_frames_with_face ?? 0}/${dataQuality.video_frames_received} (${dataQuality.valid_frame_coverage ?? 0}%)` : 'N/A', v2: dataQuality?.video_frames_received ? `${dataQuality.video_frames_with_face ?? 0}/${dataQuality.video_frames_received} (${dataQuality.valid_frame_coverage ?? 0}%)` : 'N/A', change: '' },
      { label: 'Questions Handled', v1: `${v1Report.session_metrics?.interruptions_handled ?? 0}`, v2: `${v1Report.session_metrics?.interruptions_handled ?? 0}`, change: '' },
    ];

    downloadProgressReportPDF({
      title: 'Live Presentation Report',
      documentName: v1Topic || v1Report.topic || 'Live Practice Session',
      v1Score: hasOverallScore ? v1Report.overall_score : 'N/A',
      v2Score: hasOverallScore ? v1Report.overall_score : 'N/A',
      gain: hasOverallScore ? 0 : 'N/A',
      categoryScores,
      synthesis: v1Report.detailed_feedback || 'Your live presentation session has been analyzed.',
      improvements: v1Report.recommendations || [],
      remaining: [],
      additionalMetrics,
    }, 'Live_Coaching_Report.pdf');
  };

  const handleReset = () => {
    setTopic('');
    setSeconds(0);
    setAnswerText('');
    setCameraError(null);
    setCameraEnabled(true);
    setVideoFile(null);
    setV1SessionId(null);
    setV1Report(null);
    setV1Topic('');
    setChecks(CHECKLIST_ITEMS.map(() => false));
    setCountdown(null);
    sessionStartedAtRef.current = null;
  };

  const formatTime = (secs: number) => {
    const m = Math.floor(secs / 60).toString().padStart(2, '0');
    const s = (secs % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  };

  const isSessionActive = status === 'CONNECTING' || status === 'STREAMING' || status === 'INTERRUPTED_Q&A' || status === 'REPORT_GENERATING';

  // ===== Display helpers (read-only, no effect on session logic) =====
  const stream = (videoRef.current?.srcObject as MediaStream | null) || null;
  const camOn = cameraEnabled && (videoFile
    ? !!videoRef.current && videoRef.current.readyState >= 2 && !videoRef.current.paused && !videoRef.current.ended
    : !!stream && stream.getVideoTracks().some((t) => t.readyState === 'live'));
  const micOn = micActive;

  const eye = realtimeFeedback.eyeContact;
  const pos = realtimeFeedback.posture;
  const conf = realtimeFeedback.confidence;
  const headMovement = realtimeFeedback.headMovement;
  const facialBlendshapes = realtimeFeedback.facialBlendshapes;
  const faceDetected = realtimeFeedback.faceDetected;
  const gazeAvailable = faceDetected && realtimeFeedback.eyeContact !== null;
  const postureAvailable = realtimeFeedback.posture !== null;
  const headMovementAvailable = headMovement !== null;
  const trackingProxyAvailable = realtimeFeedback.confidence !== null;
  const facialBlendshapeEntries = Object.entries(facialBlendshapes || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3);
  const facialBlendshapeReadout = facialBlendshapeEntries
    .map(([name, value]) => `${BLENDSHAPE_LABELS[name] || name}: ${Math.round(value * 100)}%`)
    .join(' · ');

  const reminders = ['Maintain eye contact', 'Keep good posture', 'Avoid filler words', 'Speak at a steady pace', 'Stay centered'];

  const allChecked = checks.every(Boolean);
  const checkedCount = checks.filter(Boolean).length;

  useEffect(() => {
    const existingKey = warningCandidateRef.current?.key;
    const measurements = [
      { key: 'gaze', value: eye, confidence: realtimeFeedback.metricConfidence.eyeContact, calibrated: realtimeFeedback.gazeCalibrationStatus === 'calibrated', message: 'Camera-facing gaze estimate remains low; look toward the lens when appropriate.' },
      { key: 'posture', value: pos, confidence: realtimeFeedback.metricConfidence.posture, calibrated: true, message: 'Measured upper-body alignment remains low; adjust your shoulder and torso position.' },
      { key: 'head', value: headMovement, confidence: realtimeFeedback.metricConfidence.headMovement, calibrated: true, message: 'Measured head steadiness remains low; reduce repeated head turns.' },
    ];
    const candidate = ['STREAMING', 'INTERRUPTED_Q&A'].includes(status)
      ? measurements.find((metric) => metric.calibrated && metric.value !== null
        && metric.confidence !== null && metric.confidence >= 70
        && metric.value < (metric.key === existingKey ? 45 : 35))
      : undefined;

    if (!candidate) {
      if (warningCandidateRef.current) window.clearTimeout(warningCandidateRef.current.timer);
      warningCandidateRef.current = null;
      setCriticalWarning(null);
      return;
    }
    if (warningCandidateRef.current?.key === candidate.key) return;

    if (warningCandidateRef.current) window.clearTimeout(warningCandidateRef.current.timer);
    setCriticalWarning(null);
    const warning = { key: candidate.key, message: candidate.message, timer: 0 };
    warning.timer = window.setTimeout(() => setCriticalWarning(warning.message), 1200);
    warningCandidateRef.current = warning;
  }, [status, eye, pos, headMovement, realtimeFeedback.metricConfidence, realtimeFeedback.gazeCalibrationStatus]);

  useEffect(() => () => {
    if (warningCandidateRef.current) window.clearTimeout(warningCandidateRef.current.timer);
  }, []);

  useEffect(() => {
    if (status !== 'STREAMING' && status !== 'INTERRUPTED_Q&A') return;
    const refreshTip = () => {
      const metrics = feedbackRef.current;
      const candidates: { key: string; title: string; body: string }[] = [];
      if (metrics.gazeCalibrationStatus === 'calibrating') {
        candidates.push(
          { key: 'calibration_a', title: 'Gaze calibration', body: 'Look toward the camera lens while calibration completes.' },
          { key: 'calibration_b', title: 'Hold your gaze', body: 'Keep looking toward the camera for a few seconds.' },
        );
      } else if (!metrics.faceDetected) {
        candidates.push(
          { key: 'face_a', title: 'Face not detected', body: 'Move into the frame and improve the light on your face.' },
          { key: 'face_b', title: 'Adjust the camera', body: 'Center your face in view and keep the lens unobstructed.' },
        );
      } else {
        if (metrics.eyeContact !== null) candidates.push(...(metrics.eyeContact < 65
          ? [
            { key: 'gaze_low_a', title: 'Camera-facing gaze', body: 'Bring your gaze back toward the camera when addressing your audience.' },
            { key: 'gaze_low_b', title: 'Address the lens', body: 'Look toward the camera while making your main points.' },
          ]
          : [
            { key: 'gaze_good_a', title: 'Camera-facing gaze', body: 'Your recent gaze estimate is steady; keep addressing the camera.' },
            { key: 'gaze_good_b', title: 'Gaze consistency', body: 'Your measured gaze is holding near its calibrated position.' },
          ]));
        if (metrics.posture !== null) candidates.push(...(metrics.posture < 65
          ? [
            { key: 'posture_low_a', title: 'Upper-body posture', body: 'Level your shoulders and keep your torso upright.' },
            { key: 'posture_low_b', title: 'Shoulder alignment', body: 'Adjust your shoulders to sit level in the frame.' },
          ]
          : [
            { key: 'posture_good_a', title: 'Upper-body posture', body: 'Your measured upper-body alignment is holding steady.' },
            { key: 'posture_good_b', title: 'Posture consistency', body: 'Your shoulders and torso remain well aligned in recent frames.' },
          ]));
        if (metrics.headMovement !== null) candidates.push(...(metrics.headMovement < 65
          ? [
            { key: 'head_low_a', title: 'Head steadiness', body: 'Try reducing repeated head turns while speaking to the camera.' },
            { key: 'head_low_b', title: 'Head pose variation', body: 'Keep your head orientation steadier for the next few seconds.' },
          ]
          : [
            { key: 'head_good_a', title: 'Head steadiness', body: 'Your head pose has stayed steady across recent frames.' },
            { key: 'head_good_b', title: 'Head pose control', body: 'Your measured head movement is low over the rolling window.' },
          ]));
        if (candidates.length === 0) candidates.push(
          { key: 'metrics_wait_a', title: 'Keep in frame', body: 'Stay centered while visual measurements become available.' },
          { key: 'metrics_wait_b', title: 'Improve visibility', body: 'Keep your face and upper body clearly visible to the camera.' },
        );
      }
      const lastKey = previousTipKeyRef.current;
      let options = candidates.filter((candidate) => candidate.key !== lastKey);
      if (!options.length) {
        const previousTitle = liveTip.title;
        options = candidates.filter((candidate) => candidate.title !== previousTitle);
      }
      const tip = options[Math.floor(Math.random() * options.length)] || candidates[0];
      previousTipKeyRef.current = tip.key;
      setLiveTip(tip);
    };
    refreshTip();
    const interval = window.setInterval(refreshTip, 5000);
    return () => window.clearInterval(interval);
  }, [status]);

  return (
    <div className="live-coach-page container lc-page">

      {/* COUNTDOWN OVERLAY */}
      {countdown !== null && countdown > 0 && (
        <div className="lc-countdown">
          <p className="lc-countdown-label">Get ready. Your session starts in</p>
          <div className="lc-countdown-num" key={countdown}>{countdown}</div>
          <p className="lc-countdown-topic">Topic: {topic}</p>
          <button className="lc-countdown-cancel" onClick={() => setCountdown(null)}>Cancel</button>
        </div>
      )}

      {/* ACTIVE PRESENTATION HUD */}
      {isSessionActive && (
        <>
          {(status === 'STREAMING' || status === 'INTERRUPTED_Q&A') && (
            <div className="lc-arena fadeIn">
              {cameraError && (
                <div className="lc-alert lc-alert-red">
                  <strong>Video Source Error:</strong> {cameraError} Check camera permissions or choose a readable video file.
                </div>
              )}
              {error && (
                <div className="lc-alert lc-alert-red">
                  <strong>Session Error:</strong> {error}
                </div>
              )}
              {(!micOn || !sttAvailable) && (
                <div className="lc-alert lc-alert-amber">
                  <strong>{!micOn ? 'Microphone is off:' : 'Voice scoring may be unavailable:'}</strong>{' '}
                  {videoFile
                    ? 'This upload is analyzed for visible facial and posture cues only; speech metrics are unavailable.'
                    : !micOn
                    ? 'WPM, filler words, grammar, and pitch cannot be scored without captured audio.'
                    : 'Server speech-to-text is unavailable; audio-derived scores require a valid captured transcript.'}
                </div>
              )}
              {micError && <div className="lc-alert lc-alert-amber"><strong>Microphone:</strong> {micError}</div>}
              {fullscreenError && <div className="lc-alert lc-alert-amber"><strong>Fullscreen:</strong> {fullscreenError}</div>}

              <div className="lc-main">
                {/* Camera panel */}
                <div className="lc-camera-card">
                  <div className="lc-stage" ref={stageRef}>
                    <VideoCapture
                      videoRef={videoRef}
                      isStreaming={cameraEnabled}
                      videoFile={videoFile}
                      retryKey={cameraRetryKey}
                      onError={(msg) => { setCameraError(msg); clearVisualFeedback(msg); }}
                    />
                    <div className={`lc-live-badge ${!camOn ? 'off' : ''}`}><span className="lc-live-dot"></span> {camOn ? (videoFile ? 'VIDEO' : 'LIVE') : 'CAMERA OFF'}</div>
                    {criticalWarning && <div className="lc-critical-warning" role="status">{criticalWarning}</div>}

                  </div>

                  <div className="lc-camera-bar">
                    <div className="lc-status-group">
                      <span className="lc-status"><i className={`lc-dot ${camOn ? 'on' : 'off'}`}></i> Camera {camOn ? 'On' : 'Off'}</span>
                      <span className="lc-status"><i className={`lc-dot ${micOn ? 'on' : 'off'}`}></i> Mic {micLoading ? 'Starting…' : micOn ? 'On' : 'Off'}</span>
                    </div>
                    <div className="lc-timer" title="Session duration (Target: 30 minutes)">
                      {formatTime(seconds)} <span>/ 30:00</span>
                    </div>
                    <div className="lc-bar-actions">
                      <button className="lc-icon-btn lc-text-btn" onClick={() => {
                        setCameraError(null);
                        clearVisualFeedback('Waiting for a camera frame.');
                        if (videoFile) {
                          if (camOn) videoRef.current?.pause();
                          else setCameraRetryKey((key) => key + 1);
                        } else if (camOn) setCameraEnabled(false);
                        else {
                          setCameraEnabled(true);
                          setCameraRetryKey((key) => key + 1);
                        }
                      }} title={videoFile ? (camOn ? 'Pause video' : 'Play video from the beginning') : camOn ? 'Turn camera off' : 'Start or retry camera'}>
                        {videoFile ? (camOn ? 'Pause video' : 'Play video') : camOn ? 'Camera off' : 'Camera on'}
                      </button>
                      <button className="lc-icon-btn lc-text-btn" onClick={() => micOn || micLoading ? toggleMicrophone() : retryMicrophone()} disabled={!!videoFile} title={videoFile ? 'Microphone is not used while analyzing a video file' : micOn || micLoading ? 'Turn microphone off' : 'Turn microphone on or retry access'}>
                        {micOn || micLoading ? 'Mic off' : 'Mic on'}
                      </button>
                      <label className="lc-icon-btn lc-text-btn lc-upload-btn" title="Analyze a video file">
                        {videoFile ? 'Change video' : 'Upload video'}
                        <input type="file" accept="video/*" onChange={handleVideoUpload} style={{ display: 'none' }} />
                      </label>
                      {videoFile && <button className="lc-icon-btn lc-text-btn" onClick={() => { clearVisualFeedback('Waiting for a readable camera frame.'); setVideoFile(null); setCameraEnabled(true); }} title="Return to webcam">Use camera</button>}
                      <button className="lc-icon-btn" onClick={handleFullscreen} title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'} aria-label={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"></path></svg>
                      </button>
                      <button className="lc-finish-btn" onClick={handleFinish} disabled={status === 'REPORT_GENERATING'}>Finish Presentation</button>
                    </div>
                  </div>
                </div>

                {/* Metrics column */}
                <div className="lc-side">
                  <div className="lc-metrics">
                    <div className="lc-metric">
                      <div className="lc-metric-top">
                        <div className="lc-metric-icon">
                          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                        </div>
                        <div className="lc-metric-text">
                          <div className="lc-metric-num">{gazeAvailable && eye !== null ? `${eye}%` : 'N/A'}</div>
                          <div className="lc-metric-name">Camera-Facing Gaze (Approx.)</div>
                        </div>
                        {gazeAvailable && eye !== null && <Ring value={eye} />}
                      </div>
                      <p>{gazeAvailable ? 'Estimated from iris position relative to eye landmarks after lens-facing calibration.' : realtimeFeedback.gazeCalibrationStatus === 'calibrating' ? 'For three seconds, look toward the camera lens to calibrate gaze.' : realtimeFeedback.hint}</p>
                    </div>

                    <div className="lc-metric">
                      <div className="lc-metric-top">
                        <div className="lc-metric-icon">
                          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="5" r="2"></circle><path d="M12 7v7M8 10l4-1 4 1M10 21l2-7 2 7"></path></svg>
                        </div>
                        <div className="lc-metric-text">
                          <div className="lc-metric-num">{postureAvailable && pos !== null ? `${pos}%` : 'N/A'}</div>
                          <div className="lc-metric-name">Upper-Body Posture</div>
                        </div>
                        {postureAvailable && pos !== null && <Ring value={pos} />}
                      </div>
                      <p>{postureAvailable ? 'Measured from shoulder tilt and head-to-shoulder alignment using visible shoulder and hip landmarks.' : realtimeFeedback.hint}</p>
                    </div>

                    <div className="lc-metric">
                      <div className="lc-metric-top">
                        <div className="lc-metric-icon">
                          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3v3M5.6 5.6l2.1 2.1M3 12h3M5.6 18.4l2.1-2.1M12 18v3M16.3 16.3l2.1 2.1M18 12h3M16.3 7.7l2.1-2.1"/><circle cx="12" cy="12" r="4"/></svg>
                        </div>
                        <div className="lc-metric-text">
                          <div className="lc-metric-num">{headMovementAvailable && headMovement !== null ? `${headMovement}%` : 'N/A'}</div>
                          <div className="lc-metric-name">Head Steadiness</div>
                        </div>
                        {headMovementAvailable && headMovement !== null && <Ring value={headMovement} />}
                      </div>
                      <p>{headMovementAvailable ? 'Calculated from yaw and pitch variation in the rolling head-pose window.' : realtimeFeedback.hint}</p>
                    </div>

                    <div className="lc-metric">
                      <div className="lc-metric-top">
                        <div className="lc-metric-icon">
                          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>
                        </div>
                        <div className="lc-metric-text">
                          <div className="lc-metric-num">{trackingProxyAvailable ? `${conf}%` : 'N/A'}</div>
                          <div className="lc-metric-name">Visual Tracking Proxy</div>
                        </div>
                        {trackingProxyAvailable && conf !== null && <Ring value={conf} />}
                      </div>
                      <p>{trackingProxyAvailable ? 'Weighted composite of available gaze, posture and head steadiness measurements.' : realtimeFeedback.hint}</p>
                    </div>

                    <div className="lc-metric">
                      <div className="lc-metric-top">
                        <div className="lc-metric-icon">
                          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"></circle><path d="M8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01"></path></svg>
                        </div>
                        <div className="lc-metric-text">
                          <div className="lc-metric-num lc-emotion">{facialBlendshapes ? 'Measured' : 'N/A'}</div>
                          <div className="lc-metric-name">Facial Blendshapes</div>
                        </div>
                      </div>
                      <p>{facialBlendshapes ? facialBlendshapeReadout || 'Measured coefficients are below 5%.' : realtimeFeedback.hint}</p>
                    </div>
                  </div>

                  <div className="lc-tip" key={liveTip.key}>
                    <div className="lc-tip-icon">
                      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.2 1 2V17h6v-.3c0-.8.4-1.5 1-2A7 7 0 0 0 12 2Z"></path></svg>
                    </div>
                    <div>
                      <span className="lc-tip-tag">Live Tip</span>
                      <h3>{liveTip.title}</h3>
                      <p>{liveTip.body}</p>
                    </div>
                  </div>

                  {hasHistory && historySummary && (
                    <div className="lc-memory">
                      <h4>Topic Target</h4>
                      <p>In your last session for this topic, your score was {historySummary.previous_score}. Keep an eye on:</p>
                      <ul>
                        {historySummary.top_recommendations.map((rec: string, i: number) => (
                          <li key={i}>{rec}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              </div>

              {/* Quick reminders */}
              <div className="lc-reminders">
                <div className="lc-reminders-title">
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="6"></circle><circle cx="12" cy="12" r="2"></circle></svg>
                  Quick Reminders
                </div>
                <div className="lc-chips">
                  {reminders.map((r) => (
                    <span key={r} className="lc-chip">{r}</span>
                  ))}
                </div>
              </div>
            </div>
          )}

          {status === 'CONNECTING' && (
            <div className="generating-card fadeIn" role="status" aria-live="polite">
              <div className="spinner-hud"></div>
              <h2>Connecting to Live Coach</h2>
              <p>Starting the session and preparing camera and microphone controls…</p>
            </div>
          )}

          {status === 'INTERRUPTED_Q&A' && (
            <div className="interruption-modal fadeIn">
              <div className="interruption-card">
                <div className="panelist-indicator">
                  <span className="panelist-avatar">P</span>
                  <div>
                    <h3>Academic Examiner</h3>
                    <span className="question-status-badge">QUESTION DETECTED</span>
                  </div>
                </div>

                <p className="interruption-question">"{currentQuestion}"</p>

                <form onSubmit={handleAnswerSubmit} className="answer-form">
                  <textarea
                    placeholder="Formulate and type your answer here..."
                    value={answerText}
                    onChange={(e) => setAnswerText(e.target.value)}
                    required
                    className="answer-textarea"
                    rows={4}
                  />
                  <button type="submit" className="submit-answer-btn">
                    Submit Response
                  </button>
                </form>
              </div>
            </div>
          )}

          {status === 'REPORT_GENERATING' && (
            <div className="generating-card fadeIn">
              <div className="spinner-hud"></div>
              <h2>{error ? 'Report generation needs another try' : 'Compiling Presentation Analytics'}</h2>
              <p>{error || 'Processing measured camera, microphone, and panel data…'}</p>
              {error && <button className="lc-finish-btn" onClick={handleFinish}>Retry report generation</button>}
            </div>
          )}
        </>
      )}

      {/* STATIC SCREENS (When session is NOT active) */}
      {!isSessionActive && (
        <>
          {v1Report ? (
            <>
              <div style={{ display: 'flex', justifyContent: 'flex-end', margin: '1rem 0' }}>
                <button
                  onClick={handleDownloadReport}
                  className="start-btn-premium"
                  style={{ margin: 0, padding: '0.6rem 2rem', fontSize: '0.95rem' }}
                >
                  Download Report
                </button>
              </div>
              <ReportDashboard
                report={v1Report}
                onReset={handleReset}
                resetButtonLabel="Practice New Topic"
              />
              {v1Report.session_metrics?.data_quality?.has_facial_blendshape_metrics && (
                <div className="lc-blendshape-report">
                  <h3>Measured Facial Blendshape Coefficients</h3>
                  <p>{formatBlendshapeReport(v1Report.session_metrics?.avg_facial_blendshapes)}</p>
                  <small>Model coefficients describe facial landmark movement; no emotion is inferred.</small>
                </div>
              )}
            </>
          ) : (
            <div className="lc-start fadeIn">
              <div className="lc-start-card">
                <span className="coach-badge">LIVE PRACTICE SESSION</span>
                <h1>Interactive Presentation Practice</h1>
                <p className="lc-start-sub">
                  Enter a presentation topic. The coach estimates camera-facing gaze, scores posture only when upper-body landmarks are visible, and tracks pacing when valid speech is transcribed.
                  An academic panel will occasionally interrupt you with real-time questions to test your depth.
                </p>

                <form onSubmit={handleStart} className="lc-start-form">
                  <input
                    type="text"
                    placeholder="e.g., Blockchain Scaling, Machine Learning in Health..."
                    value={topic}
                    onChange={(e) => { setTopic(e.target.value); setReadinessError(null); }}
                    required
                    className="lc-topic-input"
                  />

                  <div className="lc-suggest">
                    <span className="lc-suggest-label">Try a topic:</span>
                    <div className="lc-suggest-chips">
                      {TOPIC_SUGGESTIONS.map((t) => (
                        <button
                          type="button"
                          key={t}
                          className={`lc-suggest-chip ${topic === t ? 'active' : ''}`}
                          onClick={() => setTopic(t)}
                        >
                          {t}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="lc-checklist">
                    <div className="lc-checklist-head">
                      <strong>Before you start</strong>
                      <span>{checkedCount}/{CHECKLIST_ITEMS.length} ready</span>
                    </div>
                    {CHECKLIST_ITEMS.map((item, i) => (
                      <label key={item} className={`lc-check ${checks[i] ? 'done' : ''}`}>
                        <input type="checkbox" checked={checks[i]} required onChange={() => toggleCheck(i)} />
                        <span className="lc-check-box"></span>
                        <span>{item}</span>
                      </label>
                    ))}
                  </div>

                  {(!allChecked || readinessError) && (
                    <p role="alert" style={{ color: '#fbbf24', margin: 0 }}>
                      {readinessError || 'Please complete all readiness checks'}
                    </p>
                  )}
                  <button type="submit" disabled={!topic.trim() || !allChecked || countdown !== null} className={`lc-start-btn ${allChecked && topic.trim() ? 'ready' : ''}`}>
                    {allChecked && topic.trim() ? 'You are ready. Start Practice Session' : 'Start Practice Session'}
                  </button>
                </form>
              </div>

              <div className="lc-features">
                <div className="lc-feature">
                  <div className="lc-feature-icon">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                  </div>
                  <h3>Camera-Facing Gaze (Approx.)</h3>
                  <p>Face landmarks estimate camera-facing gaze; this does not verify true eye contact.</p>
                </div>
                <div className="lc-feature">
                  <div className="lc-feature-icon">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="5" r="2"></circle><path d="M12 7v7M8 10l4-1 4 1M10 21l2-7 2 7"></path></svg>
                  </div>
                  <h3>Upper-Body Posture</h3>
                  <p>Posture is scored only when visible shoulder and hip landmarks are available. Visual tracking is not psychological confidence.</p>
                </div>
                <div className="lc-feature">
                  <div className="lc-feature-icon">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
                  </div>
                  <h3>Panel Questions</h3>
                  <p>An academic examiner interrupts with real questions to test your depth.</p>
                </div>
              </div>
            </div>
          )}
        </>
      )}

      {error && !isSessionActive && (
        <div className="error-alert">
          <span>Error:</span> {error}
        </div>
      )}
    </div>
  );
};

export default LiveCoach;
