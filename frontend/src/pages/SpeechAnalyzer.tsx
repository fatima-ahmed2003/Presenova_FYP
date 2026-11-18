import './speech-polish';
/**
 * SpeechAnalyzer Page (Presentation Coach)
 * Records speech with MediaRecorder and sends it to the backend for analysis.
 * Redesigned layout (mockup style). Logic is unchanged.
 */

import React, { useState, useEffect, useRef } from 'react';
import Button from '../components/Button';
import { analyzeAudio } from '../services/api';
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

const convertToMonoWav = async (audioBlob: Blob): Promise<Blob> => {
  const audioContext = new AudioContext();
  try {
    const decoded = await audioContext.decodeAudioData(await audioBlob.arrayBuffer());
    const sampleRate = 16000;
    const frameCount = Math.ceil(decoded.duration * sampleRate);
    if (frameCount <= 0) {
      throw new Error('The recording contains no audio samples.');
    }

    const offlineContext = new OfflineAudioContext(1, frameCount, sampleRate);
    const source = offlineContext.createBufferSource();
    source.buffer = decoded;
    source.connect(offlineContext.destination);
    source.start();
    const rendered = await offlineContext.startRendering();
    const samples = rendered.getChannelData(0);
    const dataSize = samples.length * 2;
    const wav = new ArrayBuffer(44 + dataSize);
    const view = new DataView(wav);
    const writeText = (offset: number, value: string) => {
      for (let index = 0; index < value.length; index += 1) {
        view.setUint8(offset + index, value.charCodeAt(index));
      }
    };

    writeText(0, 'RIFF');
    view.setUint32(4, 36 + dataSize, true);
    writeText(8, 'WAVE');
    writeText(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeText(36, 'data');
    view.setUint32(40, dataSize, true);

    for (let index = 0; index < samples.length; index += 1) {
      const sample = Math.max(-1, Math.min(1, samples[index]));
      view.setInt16(
        44 + index * 2,
        sample < 0 ? sample * 0x8000 : sample * 0x7fff,
        true
      );
    }
    return new Blob([wav], { type: 'audio/wav' });
  } finally {
    await audioContext.close();
  }
};

interface WavDiagnostics {
  durationSeconds: number;
  byteSize: number;
  sampleRate: number;
  channels: number;
  peak: number;
  rms: number;
}

const inspectWavFile = async (file: File): Promise<WavDiagnostics> => {
  const buffer = await file.arrayBuffer();
  const view = new DataView(buffer);
  const readText = (offset: number, length: number) =>
    Array.from({ length }, (_, index) =>
      String.fromCharCode(view.getUint8(offset + index))
    ).join('');

  if (
    buffer.byteLength < 44 ||
    readText(0, 4) !== 'RIFF' ||
    readText(8, 4) !== 'WAVE' ||
    view.getUint16(20, true) !== 1 ||
    view.getUint16(34, true) !== 16
  ) {
    throw new Error('Generated WAV is not PCM16 audio.');
  }

  const channels = view.getUint16(22, true);
  const sampleRate = view.getUint32(24, true);
  const byteRate = view.getUint32(28, true);
  const blockAlign = view.getUint16(32, true);
  const dataSize = view.getUint32(40, true);
  const dataStart = 44;
  const dataEnd = dataStart + dataSize;
  if (
    channels < 1 ||
    sampleRate < 1 ||
    byteRate < 1 ||
    blockAlign !== channels * 2 ||
    dataSize % blockAlign !== 0 ||
    dataEnd > buffer.byteLength
  ) {
    throw new Error('Generated WAV has invalid PCM data.');
  }

  let sumSquares = 0;
  let peak = 0;
  let sampleCount = 0;
  for (let offset = dataStart; offset < dataEnd; offset += 2) {
    const sample = view.getInt16(offset, true) / 32768;
    const magnitude = Math.abs(sample);
    sumSquares += sample * sample;
    peak = Math.max(peak, magnitude);
    sampleCount += 1;
  }

  return {
    durationSeconds: dataSize / byteRate,
    byteSize: file.size,
    sampleRate,
    channels,
    peak,
    rms: sampleCount ? Math.sqrt(sumSquares / sampleCount) : 0,
  };
};

// Sub-component: 7Cs Pie Chart
const SevenCsPieChart: React.FC<{ scores?: SpeechMetrics['seven_cs_scores'] }> = ({ scores }) => {
  if (!scores) return null;
  const data = Object.entries(scores)
    .filter((entry): entry is [string, number] =>
      typeof entry[1] === 'number' && Number.isFinite(entry[1])
    )
    .map(([name, value]) => ({ name, value }));
  if (data.length === 0) return null;

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
  const [v1Duration, setV1Duration] = useState(0);
  const [v1Analysis, setV1Analysis] = useState<SpeechMetrics | null>(null);
  const [analysisDate, setAnalysisDate] = useState<Date | null>(null);

  // Common UI / Recording State
  const [isRecording, setIsRecording] = useState(false);
  const [duration, setDuration] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasRecordedAudio, setHasRecordedAudio] = useState(false);
  const [diagnosticAudioUrl, setDiagnosticAudioUrl] = useState<string | null>(null);
  const [audioDiagnostics, setAudioDiagnostics] = useState<WavDiagnostics | null>(null);

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

  useEffect(() => {
    return () => {
      if (diagnosticAudioUrl) {
        URL.revokeObjectURL(diagnosticAudioUrl);
      }
    };
  }, [diagnosticAudioUrl]);

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
      setDiagnosticAudioUrl(null);
      setAudioDiagnostics(null);
      let acquiredStream: MediaStream | null = null;
      try {
        acquiredStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        streamRef.current = acquiredStream;
        const mediaRecorder = new MediaRecorder(acquiredStream);
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
        acquiredStream?.getTracks().forEach((track) => track.stop());
        if (streamRef.current === acquiredStream) {
          streamRef.current = null;
        }
        mediaRecorderRef.current = null;
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
    setV1Analysis(null);
    setV1Duration(0);
    setAnalysisDate(null);
    setDiagnosticAudioUrl(null);
    setAudioDiagnostics(null);
    setLoading(true);
    setError(null);
    try {
      const mimeType = mediaRecorderRef.current?.mimeType || 'audio/webm';
      const audioBlob = new Blob(audioChunksRef.current, { type: mimeType });
      const wavBlob = await convertToMonoWav(audioBlob);
      const audioFile = new File([wavBlob], 'speech.wav', { type: 'audio/wav' });
      setDiagnosticAudioUrl(URL.createObjectURL(audioFile));
      try {
        setAudioDiagnostics(await inspectWavFile(audioFile));
      } catch {
        console.warn('Speech Analyzer WAV diagnostics could not be calculated.');
      }
      const result = await analyzeAudio(audioFile, duration);
      setV1Analysis(result);
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
    setV1Duration(0);
    setV1Analysis(null);
    setAnalysisDate(null);
    setDiagnosticAudioUrl(null);
    setAudioDiagnostics(null);
  };

  // ===== RESET ALL STATE =====
  const handleReset = () => {
    handleClear();
  };

  const handleDownloadReport = () => {
    if (!v1Analysis) return;
    const v1Score = v1Analysis.overall_score;
    const categoryRows = [
      { name: 'Structure', score: v1Analysis.category_scores?.Structure },
      { name: 'Clarity', score: v1Analysis.category_scores?.Clarity },
      { name: 'Persuasion', score: v1Analysis.category_scores?.Persuasion },
      { name: 'Content Quality', score: v1Analysis.category_scores?.Content_Quality },
      { name: 'Call to Action', score: v1Analysis.category_scores?.Call_to_Action },
    ];
    const categoryScores = categoryRows.map(({ name, score }) =>
      typeof score === 'number' && Number.isFinite(score)
        ? { name, v1: score, v2: score }
        : null
    );
    if (
      typeof v1Score !== 'number' ||
      !Number.isFinite(v1Score) ||
      categoryScores.some((score) => score === null)
    ) {
      setError('Complete score data is unavailable. No report was generated.');
      return;
    }
    const completeCategoryScores = categoryScores.filter(
      (score): score is NonNullable<typeof score> => score !== null
    );

    downloadProgressReportPDF({
      title: 'Speech Analysis Report',
      documentName: 'Speech Practice Session',
      v1Score,
      v2Score: v1Score,
      gain: 0,
      categoryScores: completeCategoryScores,
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
  const renderSingleReport = (report: SpeechMetrics) => {
    const wpm = report.speech_speed_wpm;
    const paceText = wpm > 160 ? 'A little fast. Slow down for clarity.'
      : wpm < 100 ? 'A little slow. Try to pick up the pace.'
      : 'Good pace! You are speaking at a natural speed.';
    const fillerCount = report.filler_words_count;
    const fillerText = fillerCount > 5 ? 'Try to reduce filler words for a smoother flow.' : 'Nice, very few filler words.';
    const clarity = report.clarity_score;
    const clarityText = clarity >= 80 ? 'Your message is clear and easy to follow.' : 'Work on making your message clearer.';
    const overall = report.overall_score;
    const transcriptText = report.transcript || '';
    const wordCount = report.word_count;

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
              <span className="sa-card-meta">{wordCount} words Â· {report.duration_seconds}s</span>
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
            <div className="sa-score-circle">
              {typeof overall === 'number' && Number.isFinite(overall) ? overall : 'â€”'}
            </div>
          </div>
          <div className="sa-card sa-summary-card">
            <h3>Speech Coach Summary</h3>
            <p>
              {report.detailed_feedback ||
               `Your speech analysis is complete. You spoke ${report.word_count} words over ${report.duration_seconds} seconds at an average pace of ${report.speech_speed_wpm} WPM. Clarity was evaluated at ${report.clarity_score}/100 based on filler word density and repetition rates.`}
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
              <SevenCsPieChart scores={report.seven_cs_scores} />
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
        </div>
        {diagnosticAudioUrl && (
          <div className="sa-audio-diagnostics">
            <a href={diagnosticAudioUrl} download="speech-analyzer-diagnostic.wav">
              Download exact WAV sent to backend
            </a>
            {audioDiagnostics && (
              <p className="sa-muted">
                Duration: {audioDiagnostics.durationSeconds.toFixed(2)}s Â·
                {' '}Size: {audioDiagnostics.byteSize} bytes Â·
                {' '}Sample rate: {audioDiagnostics.sampleRate} Hz Â·
                {' '}Channels: {audioDiagnostics.channels} Â·
                {' '}Peak: {audioDiagnostics.peak.toFixed(6)} Â·
                {' '}RMS: {audioDiagnostics.rms.toFixed(6)}
              </p>
            )}
          </div>
        )}
      </section>

      {!v1Analysis && <SpeechIdle />}

      {v1Analysis && (
        <>
          {renderSingleReport(v1Analysis)}
          <div className="sa-center">
            <Button label="Analyze New Speech" onClick={handleReset} variant="secondary" />
          </div>
        </>
      )}
    </div>
  );
};

export default SpeechAnalyzer;
