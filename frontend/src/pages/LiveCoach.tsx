import React, { useState, useRef, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { useLiveSession } from '../hooks/useLiveSession';
import VideoCapture from '../components/VideoCapture';
import LiveFeedbackOverlay from '../components/LiveFeedbackOverlay';
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

  // Start screen extras (display only)
  const [checks, setChecks] = useState<boolean[]>(CHECKLIST_ITEMS.map(() => false));
  const [countdown, setCountdown] = useState<number | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const timerRef = useRef<any>(null);

  const {
    status,
    sessionId,
    currentQuestion,
    realtimeFeedback,
    hasHistory,
    historySummary,
    finalReport,
    error,
    sttAvailable,
    startSession,
    sendAnswer,
    stopSession,
  } = useLiveSession({ userId, videoRef });

  // Handle timer
  useEffect(() => {
    if (status === 'STREAMING') {
      timerRef.current = setInterval(() => {
        setSeconds((prev) => prev + 1);
      }, 1000);
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
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
    setSeconds(0);
    setCameraError(null);
    setCountdown(3);
  };

  const handleFinish = () => {
    stopSession();
  };

  const handleAnswerSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!answerText.trim()) return;
    sendAnswer(answerText);
    setAnswerText('');
  };

  const handleFullscreen = () => {
    const el = stageRef.current;
    if (!el) return;
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else if (el.requestFullscreen) {
      el.requestFullscreen();
    }
  };

  const toggleCheck = (i: number) => {
    setChecks((prev) => prev.map((v, idx) => (idx === i ? !v : v)));
  };

  // ===== DOWNLOAD REPORT (single-session — comparison disabled) =====
  const handleDownloadReport = () => {
    if (!v1Report) return;

    const categoryScores = [
      { name: 'Structure', v1: v1Report.category_scores?.Structure || 0, v2: v1Report.category_scores?.Structure || 0 },
      { name: 'Clarity', v1: v1Report.category_scores?.Clarity || 0, v2: v1Report.category_scores?.Clarity || 0 },
      { name: 'Persuasion', v1: v1Report.category_scores?.Persuasion || 0, v2: v1Report.category_scores?.Persuasion || 0 },
      { name: 'Content Quality', v1: v1Report.category_scores?.Content_Quality || 0, v2: v1Report.category_scores?.Content_Quality || 0 },
      { name: 'Call to Action', v1: v1Report.category_scores?.Call_to_Action || 0, v2: v1Report.category_scores?.Call_to_Action || 0 },
    ];

    const additionalMetrics = [
      { label: 'Average Eye Contact', v1: `${v1Report.session_metrics?.avg_eye_contact ?? 0}%`, v2: `${v1Report.session_metrics?.avg_eye_contact ?? 0}%`, change: '' },
      { label: 'Body Posture Accuracy', v1: `${v1Report.session_metrics?.avg_posture ?? 0}%`, v2: `${v1Report.session_metrics?.avg_posture ?? 0}%`, change: '' },
      { label: 'Average Confidence', v1: `${v1Report.session_metrics?.avg_confidence ?? 0}%`, v2: `${v1Report.session_metrics?.avg_confidence ?? 0}%`, change: '' },
      { label: 'Vocal Pitch Dynamics', v1: `${v1Report.session_metrics?.avg_vocal_pitch ?? 0}%`, v2: `${v1Report.session_metrics?.avg_vocal_pitch ?? 0}%`, change: '' },
      { label: 'Speaking Pace', v1: `${v1Report.session_metrics?.avg_wpm ?? 0} WPM`, v2: `${v1Report.session_metrics?.avg_wpm ?? 0} WPM`, change: '' },
      { label: 'Filler Words Count', v1: `${v1Report.session_metrics?.total_fillers ?? 0}`, v2: `${v1Report.session_metrics?.total_fillers ?? 0}`, change: '' },
      { label: 'Questions Handled', v1: `${v1Report.session_metrics?.interruptions_handled ?? 0}`, v2: `${v1Report.session_metrics?.interruptions_handled ?? 0}`, change: '' },
    ];

    downloadProgressReportPDF({
      title: 'Live Presentation Report',
      documentName: v1Topic || v1Report.topic || 'Live Practice Session',
      v1Score: v1Report.overall_score,
      v2Score: v1Report.overall_score,
      gain: 0,
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
    setV1SessionId(null);
    setV1Report(null);
    setV1Topic('');
    setChecks(CHECKLIST_ITEMS.map(() => false));
    setCountdown(null);
  };

  const formatTime = (secs: number) => {
    const m = Math.floor(secs / 60).toString().padStart(2, '0');
    const s = (secs % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  };

  const isSessionActive = status === 'STREAMING' || status === 'INTERRUPTED_Q&A' || status === 'REPORT_GENERATING';

  // ===== Display helpers (read-only, no effect on session logic) =====
  const stream = (videoRef.current?.srcObject as MediaStream | null) || null;
  const camOn = !!stream && stream.getVideoTracks().some((t) => t.readyState === 'live');
  const micOn = !!stream && stream.getAudioTracks().some((t) => t.readyState === 'live');

  const eye = Number(realtimeFeedback.eyeContact) || 0;
  const pos = Number(realtimeFeedback.posture) || 0;
  const conf = Number(realtimeFeedback.confidence) || 0;

  const statusNote = (v: number, good: string, ok: string, low: string) =>
    v >= 80 ? good : v >= 60 ? ok : low;

  // Live tip: backend hint first, otherwise a simple rule based on the weakest metric
  let tipTitle = 'You are doing well';
  let tipBody = 'Keep your eyes on the camera, sit upright and speak at a steady pace.';
  if (realtimeFeedback.hint) {
    tipTitle = 'Live Coach says';
    tipBody = realtimeFeedback.hint;
  } else if (eye < 60) {
    tipTitle = 'Look at the camera';
    tipBody = 'Your eye contact is low. Look at the camera lens as if it is your audience.';
  } else if (pos < 60) {
    tipTitle = 'Sit up straight';
    tipBody = 'Your posture needs work. Keep your back straight and shoulders relaxed.';
  } else if (conf < 60) {
    tipTitle = 'Breathe and slow down';
    tipBody = 'Take a calm breath and speak slowly. Confidence follows a steady pace.';
  }

  const frameClass = eye >= 80 ? 'good' : eye >= 60 ? 'mid' : 'low';

  const reminders = ['Maintain eye contact', 'Keep good posture', 'Avoid filler words', 'Speak at a steady pace', 'Stay centered'];

  const allChecked = checks.every(Boolean);
  const checkedCount = checks.filter(Boolean).length;

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
          {status === 'STREAMING' && (
            <div className="lc-arena fadeIn">
              {cameraError && (
                <div className="lc-alert lc-alert-red">
                  <strong>Camera Access Error:</strong> {cameraError} Please grant camera permissions or ensure no other app is using your webcam.
                </div>
              )}
              {error && (
                <div className="lc-alert lc-alert-red">
                  <strong>Session Error:</strong> {error}
                </div>
              )}
              {!sttAvailable && (
                <div className="lc-alert lc-alert-amber">
                  <strong>Voice scoring unavailable:</strong> Speech-to-text isn't configured on the server, so Speaking Pace, Filler Words, and Vocal Pitch Dynamics won't be tracked this session (they'll show 0, not a reflection of your delivery). Eye Contact, Posture, and Confidence are unaffected.
                </div>
              )}

              <div className="lc-main">
                {/* Camera panel */}
                <div className="lc-camera-card">
                  <div className="lc-stage" ref={stageRef}>
                    <VideoCapture
                      videoRef={videoRef}
                      isStreaming={true}
                      onError={(msg) => setCameraError(msg)}
                    />
                    <LiveFeedbackOverlay
                      eyeContact={realtimeFeedback.eyeContact}
                      posture={realtimeFeedback.posture}
                      hint={realtimeFeedback.hint}
                      isStreaming={true}
                      confidence={realtimeFeedback.confidence}
                      emotion={realtimeFeedback.emotion}
                    />
                    <div className="lc-live-badge"><span className="lc-live-dot"></span> Live</div>
                    <div className={`lc-face-frame ${frameClass}`}>
                      <i className="tl"></i><i className="tr"></i><i className="bl"></i><i className="br"></i>
                    </div>
                  </div>

                  <div className="lc-camera-bar">
                    <div className="lc-status-group">
                      <span className="lc-status"><i className={`lc-dot ${camOn ? 'on' : 'off'}`}></i> Camera {camOn ? 'On' : 'Off'}</span>
                      <span className="lc-status"><i className={`lc-dot ${micOn ? 'on' : 'off'}`}></i> Mic {micOn ? 'On' : 'Off'}</span>
                    </div>
                    <div className="lc-timer" title="Session duration (Target: 30 minutes)">
                      {formatTime(seconds)} <span>/ 30:00</span>
                    </div>
                    <div className="lc-bar-actions">
                      <button className="lc-icon-btn" onClick={handleFullscreen} title="Fullscreen">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"></path></svg>
                      </button>
                      <button className="lc-finish-btn" onClick={handleFinish}>Finish Presentation</button>
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
                          <div className="lc-metric-num">{eye}%</div>
                          <div className="lc-metric-name">Eye Contact</div>
                        </div>
                        <Ring value={eye} />
                      </div>
                      <p>{statusNote(eye, 'Great eye contact! Keep it up.', 'Decent. Look at the camera more.', 'Low. Look at the camera lens.')}</p>
                    </div>

                    <div className="lc-metric">
                      <div className="lc-metric-top">
                        <div className="lc-metric-icon">
                          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="5" r="2"></circle><path d="M12 7v7M8 10l4-1 4 1M10 21l2-7 2 7"></path></svg>
                        </div>
                        <div className="lc-metric-text">
                          <div className="lc-metric-num">{pos}%</div>
                          <div className="lc-metric-name">Posture</div>
                        </div>
                        <Ring value={pos} />
                      </div>
                      <p>{statusNote(pos, 'Good posture! Stay relaxed.', 'Okay. Sit a little straighter.', 'Low. Straighten your back.')}</p>
                    </div>

                    <div className="lc-metric">
                      <div className="lc-metric-top">
                        <div className="lc-metric-icon">
                          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>
                        </div>
                        <div className="lc-metric-text">
                          <div className="lc-metric-num">{conf}%</div>
                          <div className="lc-metric-name">Confidence</div>
                        </div>
                        <Ring value={conf} />
                      </div>
                      <p>{statusNote(conf, 'You look confident!', 'Fairly confident. Keep going.', 'Take a breath and slow down.')}</p>
                    </div>

                    <div className="lc-metric">
                      <div className="lc-metric-top">
                        <div className="lc-metric-icon">
                          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"></circle><path d="M8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01"></path></svg>
                        </div>
                        <div className="lc-metric-text">
                          <div className="lc-metric-num lc-emotion">{realtimeFeedback.emotion || '--'}</div>
                          <div className="lc-metric-name">Facial Emotion</div>
                        </div>
                      </div>
                      <p>Your detected expression right now.</p>
                    </div>
                  </div>

                  <div className="lc-tip" key={tipTitle}>
                    <div className="lc-tip-icon">
                      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.2 1 2V17h6v-.3c0-.8.4-1.5 1-2A7 7 0 0 0 12 2Z"></path></svg>
                    </div>
                    <div>
                      <span className="lc-tip-tag">Live Tip</span>
                      <h3>{tipTitle}</h3>
                      <p>{tipBody}</p>
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
              <h2>Compiling Presentation Analytics</h2>
              <p>Processing vocal dynamics, eye-gaze tracking frames, and panel responses...</p>
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
            </>
          ) : (
            <div className="lc-start fadeIn">
              <div className="lc-start-card">
                <span className="coach-badge">LIVE PRACTICE SESSION</span>
                <h1>Interactive Presentation Practice</h1>
                <p className="lc-start-sub">
                  Enter a presentation topic. The coach will track your visual presence, eye contact, posture, and pacing.
                  An academic panel will occasionally interrupt you with real-time questions to test your depth.
                </p>

                <form onSubmit={handleStart} className="lc-start-form">
                  <input
                    type="text"
                    placeholder="e.g., Blockchain Scaling, Machine Learning in Health..."
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
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
                        <input type="checkbox" checked={checks[i]} onChange={() => toggleCheck(i)} />
                        <span className="lc-check-box"></span>
                        <span>{item}</span>
                      </label>
                    ))}
                  </div>

                  <button type="submit" className={`lc-start-btn ${allChecked && topic.trim() ? 'ready' : ''}`}>
                    {allChecked && topic.trim() ? 'You are ready. Start Practice Session' : 'Start Practice Session'}
                  </button>
                </form>
              </div>

              <div className="lc-features">
                <div className="lc-feature">
                  <div className="lc-feature-icon">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                  </div>
                  <h3>Eye Contact</h3>
                  <p>Tracks where you look and tells you when you drift away from the camera.</p>
                </div>
                <div className="lc-feature">
                  <div className="lc-feature-icon">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="5" r="2"></circle><path d="M12 7v7M8 10l4-1 4 1M10 21l2-7 2 7"></path></svg>
                  </div>
                  <h3>Posture and Confidence</h3>
                  <p>Live scores for your body language, updated every second.</p>
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