/**
 * SpeechAnalyzer Page (Presentation Coach)
 * Records speech with MediaRecorder and sends it to the backend for analysis.
 * Redesigned layout (mockup style). Logic is unchanged.
 */

import React, { useState, useEffect, useRef } from 'react';
import Button from '../components/Button';
import { analyzeAudio, analyzeSpeech } from '../services/api';
import { SpeechMetrics } from '../types';
import { downloadProgressReportPDF } from '../services/pdfGenerator';
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts';
import './SpeechAnalyzerMockup.css';
import SpeechIdle from './SpeechIdle';
// Words that get highlighted in the transcript (display only)
const FILLER_REGEX = /\b(um+|uh+|uhm|er|ah|like|you know|basically|actually|kind of|sort of)\b/gi;

const renderHighlighted = (text: string): React.ReactNode[] => {
  const parts = text.split(FILLER_REGEX);
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <mark key={i} className="sa-filler">{part}</mark>
    ) : (
      <React.Fragment key={i}>{part}</React.Fragment>
    )
  );
};

// Sub-component: 7Cs Pie Chart
const SevenCsPieChart: React.FC<{ scores?: any; overallScore?: number }> = ({ scores, overallScore = 70 }) => {
  const defaultScores = {
    Clear: Math.max(25, overallScore - 5),
    Concise: Math.max(25, overallScore - 12),
    Correct: Math.max(25, overallScore - 10),
    Complete: Math.max(25, overallScore - 15),
    Courteous: Math.min(100, overallScore + 5),
    Concrete: Math.max(25, overallScore - 8),
    Consistent: Math.max(25, overallScore - 14)
  };
  const activeScores = scores || defaultScores;

  const normalizedScores: Record<string, number> = {};
  Object.entries(activeScores).forEach(([key, val]) => {
    const normalizedKey = key.charAt(0).toUpperCase() + key.slice(1);
    normalizedScores[normalizedKey] = typeof val === 'number' ? val : 70;
  });

  const data = Object.entries(normalizedScores).map(([name, value]) => ({ name, value }));

  const COLORS = ['#4f46e5', '#10b981', '#f43f5e', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4'];

  return (
    <div className="sa-pie-wrap">
      <ResponsiveContainer width="100%" height={240}>
        <PieChart>
          <Pie data={data} cx="50%" cy="50%" innerRadius={60} outerRadius={85} paddingAngle={3} dataKey="value">
            {data.map((_, index) => (
              <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
            ))}
          </Pie>
          <Tooltip
            formatter={(value: any) => [`${value}/100`, 'Score']}
            contentStyle={{ borderRadius: '8px', border: '1px solid rgba(255,255,255,0.2)', backgroundColor: 'rgba(15, 23, 42, 0.95)', color: '#fff' }}
          />
        </PieChart>
      </ResponsiveContainer>
      <div className="sa-pie-legend">
        {data.map((entry, index) => (
          <div key={entry.name} className="sa-legend-item">
            <span className="sa-legend-dot" style={{ backgroundColor: COLORS[index % COLORS.length] }}></span>
            <span className="sa-legend-label">{entry.name}: <strong>{entry.value}</strong></span>
          </div>
        ))}
      </div>
    </div>
  );
};

const SpeechAnalyzer: React.FC = () => {

  // ===== STATE =====
  const [v1Transcript, setV1Transcript] = useState('');
  const [v1Duration, setV1Duration] = useState(0);
  const [v1Analysis, setV1Analysis] = useState<SpeechMetrics | null>(null);
  const [analysisDate, setAnalysisDate] = useState<Date | null>(null);

  // Common UI / Recording State
  const [isRecording, setIsRecording] = useState(false);
  const [duration, setDuration] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasRecordedAudio, setHasRecordedAudio] = useState(false);

  // ===== REFS FOR AUDIO RECORDING =====
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const timerIntervalRef = useRef<any | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  // ===== CLEANUP ON COMPONENT UNMOUNT =====
  useEffect(() => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setError('Web Audio Recording is not supported in this browser. Please use Chrome, Firefox, or Edge.');
    }
    return () => {
      if (timerIntervalRef.current) {
        clearInterval(timerIntervalRef.current);
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
      }
    };
  }, []);

  // ===== TIMER EFFECT =====
  useEffect(() => {
    if (isRecording) {
      timerIntervalRef.current = setInterval(() => {
        setDuration((prev) => prev + 1);
      }, 1000);
    } else {
      if (timerIntervalRef.current) {
        clearInterval(timerIntervalRef.current);
      }
    }
    return () => {
      if (timerIntervalRef.current) {
        clearInterval(timerIntervalRef.current);
      }
    };
  }, [isRecording]);

  // ===== START/STOP RECORDING =====
  const handleToggleRecording = async () => {
    setError(null);
    if (isRecording) {
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop();
      }
      setIsRecording(false);
      if (timerIntervalRef.current) {
        clearInterval(timerIntervalRef.current);
      }
    } else {
      setError(null);
      setDuration(0);
      audioChunksRef.current = [];
      setHasRecordedAudio(false);
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        streamRef.current = stream;
        const mediaRecorder = new MediaRecorder(stream);
        mediaRecorderRef.current = mediaRecorder;
        mediaRecorder.ondataavailable = (event) => {
          if (event.data.size > 0) {
            audioChunksRef.current.push(event.data);
          }
          setHasRecordedAudio(audioChunksRef.current.length > 0);
        };
        mediaRecorder.onstop = () => {
          if (streamRef.current) {
            streamRef.current.getTracks().forEach((track) => track.stop());
            streamRef.current = null;
          }
        };
        mediaRecorder.start();
        setIsRecording(true);
      } catch (err) {
        console.error('Microphone error:', err);
        setError('Microphone access denied or unavailable. Please connect a microphone and check permissions.');
      }
    }
  };

  // ===== ANALYZE SPEECH =====
  const handleAnalyze = async () => {
    if (audioChunksRef.current.length === 0) {
      setError('Please record some speech audio first');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const mimeType = mediaRecorderRef.current?.mimeType || 'audio/webm';
      const fileExt = mimeType.includes('webm') ? 'webm'
      : mimeType.includes('ogg') ? 'ogg'
      : mimeType.includes('mp4') ? 'm4a'
      : 'wav';
      const audioBlob = new Blob(audioChunksRef.current, { type: mimeType });
      const audioFile = new File([audioBlob], `speech.${fileExt}`, { type: mimeType });
      const result = await analyzeAudio(audioFile, duration);
      setV1Analysis(result);
      setV1Transcript(result.transcript || '');
      setV1Duration(duration);
      setAnalysisDate(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to analyze speech audio');
    } finally {
      setLoading(false);
    }
  };

  // ===== CLEAR =====
  const handleClear = () => {
    if (isRecording && mediaRecorderRef.current) {
      mediaRecorderRef.current.stop();
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    setDuration(0);
    setError(null);
    setIsRecording(false);
    setHasRecordedAudio(false);
    audioChunksRef.current = [];
    setV1Transcript('');
    setV1Duration(0);
    setV1Analysis(null);
    setAnalysisDate(null);
  };

  // ===== RESET ALL STATE =====
  const handleReset = () => {
    handleClear();
  };

  const handleUseDemoSpeech = async () => {
    setLoading(true);
    setError(null);
    try {
      const demoV1Text = "Hello everyone, today I want to talk about, you know, blockchain technology. Basically, blockchain is like a distributed ledger, and actually, it is very secure. Um, we can use it for smart contracts, and kind of, you know, supply chain tracking. But the main problem is scalability. Um, we need to find, you know, better consensus mechanisms. So, basically, that is my presentation. Any questions?";
      const durationSecs = 30;

      const result = await analyzeSpeech(demoV1Text, durationSecs);
      setV1Analysis(result);
      setV1Transcript(result.transcript || demoV1Text);
      setV1Duration(durationSecs);
      setAnalysisDate(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to analyze demo speech');
    } finally {
      setLoading(false);
    }
  };

  const handleDownloadReport = () => {
    if (!v1Analysis) return;
    const v1Score = v1Analysis.overall_score !== undefined ? v1Analysis.overall_score : v1Analysis.clarity_score;

    const categoryScores = [
      { name: 'Structure', v1: v1Analysis.category_scores?.Structure || 0, v2: v1Analysis.category_scores?.Structure || 0 },
      { name: 'Clarity', v1: v1Analysis.category_scores?.Clarity || 0, v2: v1Analysis.category_scores?.Clarity || 0 },
      { name: 'Persuasion', v1: v1Analysis.category_scores?.Persuasion || 0, v2: v1Analysis.category_scores?.Persuasion || 0 },
      { name: 'Content Quality', v1: v1Analysis.category_scores?.Content_Quality || 0, v2: v1Analysis.category_scores?.Content_Quality || 0 },
      { name: 'Call to Action', v1: v1Analysis.category_scores?.Call_to_Action || 0, v2: v1Analysis.category_scores?.Call_to_Action || 0 },
    ];

    downloadProgressReportPDF({
      title: 'Speech Analysis Report',
      documentName: 'Speech Practice Session',
      v1Score,
      v2Score: v1Score,
      gain: 0,
      categoryScores,
      synthesis: v1Analysis.detailed_feedback || 'Your speech analysis is complete.',
      improvements: v1Analysis.recommendations || [],
      remaining: v1Analysis.actionable_feedback || [],
    }, 'Speech_Analysis_Report.pdf');
  };

  // ===== FORMAT DURATION TO MM:SS =====
  const formatDuration = (seconds: number): string => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  // ===== RENDER REPORT =====
  const renderSingleReport = (report: SpeechMetrics, elapsedSecs: number) => {
    const wpm = report.speech_speed_wpm;
    const paceText = wpm > 160 ? 'A little fast. Slow down for clarity.'
      : wpm < 100 ? 'A little slow. Try to pick up the pace.'
      : 'Good pace! You are speaking at a natural speed.';
    const fillerCount = report.filler_words_count || 0;
    const fillerText = fillerCount > 5 ? 'Try to reduce filler words for a smoother flow.' : 'Nice, very few filler words.';
    const clarity = report.clarity_score;
    const clarityText = clarity >= 80 ? 'Your message is clear and easy to follow.' : 'Work on making your message clearer.';
    const overall = report.overall_score !== undefined ? report.overall_score : report.clarity_score;
    const transcriptText = report.transcript || v1Transcript || '';
    const wordCount = report.word_count || (transcriptText ? transcriptText.split(/\s+/).length : 0);

    const tips: string[] =
      report.recommendations && report.recommendations.length > 0
        ? report.recommendations
        : report.actionable_feedback && report.actionable_feedback.length > 0
          ? report.actionable_feedback
          : [];

    return (
      <div className="sa-report">
        {/* Header card */}
        <div className="sa-header-card">
          <div className="sa-header-icon">
            <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path>
              <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
              <line x1="12" y1="19" x2="12" y2="22"></line>
            </svg>
          </div>
          <div className="sa-header-text">
            <h2>Speech Analysis Results</h2>
            <p>Here is how your speech performed. Keep going, you are doing well!</p>
          </div>
          <div className="sa-header-date">
            <span>Analysis Date</span>
            <strong>
              {(analysisDate || new Date()).toLocaleString([], { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' })}
            </strong>
          </div>
        </div>

        {/* 4 stat cards */}
        <div className="sa-stats">
          <div className="sa-stat">
            <div className="sa-stat-top">
              <div className="sa-stat-ring c-violet">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
              </div>
              <div>
                <div className="sa-stat-num">{wpm}</div>
                <div className="sa-stat-unit">words per minute</div>
              </div>
            </div>
            <p className="sa-stat-note">{paceText}</p>
          </div>

          <div className="sa-stat">
            <div className="sa-stat-top">
              <div className="sa-stat-ring c-cyan">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
              </div>
              <div>
                <div className="sa-stat-num">{fillerCount}</div>
                <div className="sa-stat-unit">filler words</div>
              </div>
            </div>
            <p className="sa-stat-note">{fillerText}</p>
          </div>

          <div className="sa-stat">
            <div className="sa-stat-top">
              <div className="sa-stat-ring c-violet">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="17 1 21 5 17 9"></polyline><path d="M3 11V9a4 4 0 0 1 4-4h14"></path><polyline points="7 23 3 19 7 15"></polyline><path d="M21 13v2a4 4 0 0 1-4 4H3"></path></svg>
              </div>
              <div>
                <div className="sa-stat-num">{report.repetition_count || 0}</div>
                <div className="sa-stat-unit">word repetitions</div>
              </div>
            </div>
            <p className="sa-stat-note">Try not to repeat the same word back to back.</p>
          </div>

          <div className="sa-stat">
            <div className="sa-stat-top">
              <div className="sa-stat-ring c-teal">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>
              </div>
              <div>
                <div className="sa-stat-num">{clarity}</div>
                <div className="sa-stat-unit">clarity score</div>
              </div>
            </div>
            <p className="sa-stat-note">{clarityText}</p>
          </div>
        </div>

        {/* Transcript + Suggestions */}
        <div className="sa-two-col">
          <section className="sa-card">
            <div className="sa-card-head">
              <div className="sa-card-icon">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>
              </div>
              <div>
                <h3>Transcript</h3>
                <p>Your spoken words with filler words highlighted.</p>
              </div>
              <span className="sa-card-meta">{wordCount} words · {report.duration_seconds || elapsedSecs}s</span>
            </div>
            <div className="sa-transcript">
              {transcriptText ? renderHighlighted(transcriptText) : 'No transcript text available.'}
            </div>
          </section>

          <section className="sa-card">
            <div className="sa-card-head">
              <div className="sa-card-icon">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.2 1 2V17h6v-.3c0-.8.4-1.5 1-2A7 7 0 0 0 12 2Z"></path></svg>
              </div>
              <div>
                <h3>Coaching Suggestions</h3>
                <p>Personalized tips to improve your next speech.</p>
              </div>
            </div>
            {tips.length > 0 ? (
              <ol className="sa-tips">
                {tips.map((tip, i) => (
                  <li key={i}>
                    <span className="sa-tip-num">{i + 1}</span>
                    <span className="sa-tip-text">{tip}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="sa-muted">Excellent delivery! No pacing or filler word improvements recommended.</p>
            )}
          </section>
        </div>

        {/* Overall score + summary */}
        <div className="sa-summary-row">
          <div className="sa-card sa-score-card">
            <h3>Overall Score</h3>
            <div className="sa-score-circle">{overall}</div>
          </div>
          <div className="sa-card sa-summary-card">
            <h3>Speech Coach Summary</h3>
            <p>
              {report.detailed_feedback ||
               `Your speech analysis is complete. You spoke ${report.word_count} words over ${report.duration_seconds || elapsedSecs} seconds at an average pace of ${report.speech_speed_wpm} WPM. Clarity was evaluated at ${report.clarity_score}/100 based on filler word density and repetition rates.`}
            </p>
          </div>
        </div>

        {/* Category scores */}
        {report.category_scores && (
          <section className="sa-card">
            <h3 className="sa-section-title">Pacing & Quality Categories</h3>
            <div className="sa-cat-grid">
              {Object.entries(report.category_scores).map(([cat, score]) => (
                <div key={cat} className="sa-cat">
                  <div className="sa-cat-head">
                    <span>{cat.replace(/_/g, ' ')}</span>
                    <strong>{score as number}/100</strong>
                  </div>
                  <div className="sa-bar"><div className="sa-bar-fill" style={{ width: `${score}%` }}></div></div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* 7Cs */}
        {report.seven_cs_evaluation && (
          <section className="sa-card">
            <h3 className="sa-section-title">7Cs Speech Evaluation</h3>
            <div className="sa-sevencs">
              <div className="sa-checklist">
                {Object.entries(report.seven_cs_evaluation).map(([cs, val]) => {
                  const isObj = typeof val === 'object' && val !== null;
                  const status = isObj ? (val as any).status : null;
                  const feedback = isObj ? (val as any).feedback : String(val);
                  return (
                    <div key={cs} className="sa-c-item">
                      <strong>{cs}:</strong>{' '}
                      {status && <span className={`sa-status ${String(status).toLowerCase().replace(/ /g, '-')}`}>{status}</span>}{' '}
                      {feedback}
                    </div>
                  );
                })}
              </div>
              <SevenCsPieChart scores={report.seven_cs_scores} overallScore={report.overall_score || report.clarity_score} />
            </div>
          </section>
        )}

        {/* Strengths */}
        {report.strengths && report.strengths.length > 0 && (
          <section className="sa-card sa-strengths">
            <h3 className="sa-section-title">Key Strengths Identified</h3>
            <ul>
              {report.strengths.map((str, idx) => <li key={idx}>{str}</li>)}
            </ul>
          </section>
        )}

        {/* Cleaned transcript */}
        {report.improved_text && (
          <section className="sa-card">
            <h3 className="sa-section-title">Cleaned Transcript</h3>
            <p className="sa-muted">A polished version of your speech with filler words and repetitions removed:</p>
            <div className="sa-rewrite">{report.improved_text}</div>
          </section>
        )}
      </div>
    );
  };

  return (
    <div className="sa-page">
      <div className="sa-title-row">
        <div>
          <h1>Presentation Coach</h1>
          <p className="sa-subtitle">
            Record your speech to get instant feedback with 7 C's scoring, delivery metrics, and improvement suggestions.
          </p>
        </div>
        {v1Analysis && (
          <Button label="Download Report" onClick={handleDownloadReport} variant="primary" />
        )}
      </div>

      {error && (
        <div className="sa-error">
          <strong>Error:</strong> {error}
        </div>
      )}

      {/* ===== RECORD & ANALYZE ===== */}
      <section className="sa-card sa-record">
        <h2>Record Your Speech</h2>
        <p className="sa-muted">Click Start Recording to begin. We will analyze your pacing, clarity, and 7 C's score.</p>

        <div className="sa-record-row">
          <div className={`sa-rec-status ${isRecording ? 'active' : ''}`}>
            <span className="sa-rec-dot"></span>
            <span>{isRecording ? 'Recording...' : 'Ready to Record'}</span>
          </div>
          <div className="sa-timer">
            <span>Duration</span>
            <strong>{formatDuration(isRecording ? duration : v1Duration)}</strong>
          </div>
        </div>

        <div className="sa-actions">
          <Button
            label={isRecording ? 'Stop Recording' : 'Start Recording'}
            onClick={handleToggleRecording}
            disabled={loading}
          />
          {hasRecordedAudio && !isRecording && (
            <>
              <Button label="Analyze Speech" onClick={handleAnalyze} loading={loading} />
              <Button label="Clear" onClick={handleClear} variant="secondary" disabled={loading} />
            </>
          )}
          {!isRecording && !hasRecordedAudio && !v1Analysis && (
            <Button label="Load Demo Speech" onClick={handleUseDemoSpeech} variant="secondary" disabled={loading} />
          )}
        </div>
      </section>

      {v1Transcript && !v1Analysis && (
        <section className="sa-card">
          <h3 className="sa-section-title">Current Speech Transcript</h3>
          <div className="sa-transcript">{v1Transcript}</div>
        </section>
      )}
         {!v1Analysis && !v1Transcript && <SpeechIdle />}

      {v1Analysis && (
        <>
          {renderSingleReport(v1Analysis, v1Duration)}
          <div className="sa-center">
            <Button label="Analyze New Speech" onClick={handleReset} variant="secondary" />
          </div>
        </>
      )}
    </div>
  );
};

export default SpeechAnalyzer;