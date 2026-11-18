import React from 'react';
import './ReportDashboard.css';

interface ReportDashboardProps {
  report: {
    overall_score: number;
    insufficient_data?: boolean;
    speech_status?: string;
    topic: string;
    category_scores: {
      Structure: number | null;
      Clarity: number | null;
      Persuasion: number | null;
      Content_Quality: number | null;
      Call_to_Action: number | null;
    };
    seven_cs_evaluation?: {
      Clear: string;
      Concise: string;
      Correct: string;
      Complete: string;
      Courteous: string;
      Concrete: string;
      Consistent: string;
    };
    seven_cs_scores: {
      Clear: number | null;
      Concise: number | null;
      Correct: number | null;
      Complete: number | null;
      Courteous: number | null;
      Concrete: number | null;
      Consistent: number | null;
    };
    strengths: string[];
    recommendations: string[];
    qna_analysis: string;
    improved_text?: string;
    comparison: {
      improved: boolean;
      difference: number;
      note: string;
    };
    session_metrics: {
      avg_eye_contact: number | null;
      avg_posture: number | null;
      avg_head_movement?: number | null;
      avg_wpm: number | null;
      total_fillers: number | null;
      interruptions_handled: number;
      avg_confidence?: number | null;
      avg_vocal_pitch?: number | null;
      data_quality?: {
        has_audio_metrics: boolean;
        has_video_metrics: boolean;
        has_qna_scores?: boolean;
        has_gaze_metrics?: boolean;
        has_posture_metrics?: boolean;
        has_head_movement_metrics?: boolean;
        has_tracking_proxy?: boolean;
        has_pitch_metrics?: boolean;
        video_frames_received?: number;
        video_frames_analyzed?: number;
        video_frames_with_face?: number;
        video_frames_with_gaze?: number;
        video_frames_with_posture?: number;
        valid_frame_coverage?: number;
        analyzed_frame_coverage?: number;
        gaze_frame_coverage?: number;
        posture_frame_coverage?: number;
        video_frames_dropped?: number;
      };
    };
  };
  onReset: () => void;
  resetButtonLabel?: string;
}

const ReportDashboard: React.FC<ReportDashboardProps> = ({ report, onReset, resetButtonLabel }) => {
  const getScoreColorClass = (score: number) => {
    if (score >= 80) return 'score-green';
    if (score >= 60) return 'score-amber';
    return 'score-red';
  };

  const cat = report?.category_scores || {
    Structure: 0,
    Clarity: 0,
    Persuasion: 0,
    Content_Quality: 0,
    Call_to_Action: 0,
  };

  const metrics = report?.session_metrics || {
    avg_eye_contact: 0,
    avg_posture: 0,
    avg_wpm: 0,
    total_fillers: 0,
    interruptions_handled: 0,
    avg_confidence: 0,
    avg_vocal_pitch: 0,
  };

  const comparison = report?.comparison || {
    improved: false,
    difference: 0,
    note: 'Presentation session analysis completed.',
  };
  const hasVideoMetrics = metrics.data_quality?.has_video_metrics !== false;
  const hasGazeMetrics = metrics.data_quality?.has_gaze_metrics ?? hasVideoMetrics;
  const hasPostureMetrics = metrics.data_quality?.has_posture_metrics ?? hasVideoMetrics;
  const hasHeadMovementMetrics = metrics.data_quality?.has_head_movement_metrics === true;
  const hasTrackingProxy = metrics.data_quality?.has_tracking_proxy ?? hasVideoMetrics;
  const hasAudioMetrics = metrics.data_quality?.has_audio_metrics !== false;
  const hasPitchMetrics = metrics.data_quality?.has_pitch_metrics === true;
  const hasQnaScores = metrics.data_quality?.has_qna_scores ?? metrics.interruptions_handled > 0;
  const hasOverallScore = !report?.insufficient_data;
  const categoryValue = (value: number | null | undefined, available: boolean) =>
    hasOverallScore && available && value !== null && value !== undefined ? `${value}%` : 'N/A';
  const categoryWidth = (value: number | null | undefined, available: boolean) =>
    hasOverallScore && available && value !== null && value !== undefined ? `${value}%` : '0%';

  return (
    <div className="report-dashboard fadeIn">
      {/* Header and Summary Scorecard */}
      <div className="dashboard-header-card">
        <div className="header-text">
          <h1>Presentation Feedback</h1>
          <p className="topic-badge">Topic: {report?.topic || 'Live Practice'}</p>
        </div>
        <div className="score-badge-circle">
          <div className="circle-score-val">{report?.insufficient_data ? 'N/A' : report?.overall_score ?? 0}</div>
          <span className="circle-score-label">{report?.insufficient_data ? 'NOT SCORED' : 'OVERALL'}</span>
        </div>
      </div>

      {/* Progress Comparison Matrix */}
      <div className="comparison-banner-card">
        <div className="comparison-header">
          <h3>Progress Analysis</h3>
        </div>
        <p className="comp-note">{comparison.note}</p>
        {comparison.difference !== 0 && (
          <div className={`comp-diff-badge ${comparison.improved ? 'positive' : 'negative'}`}>
            {comparison.improved ? '+' : ''}{comparison.difference} points vs last session
          </div>
        )}
      </div>

      {/* Grid: Standard 5 Category Scores & Telemetry */}
      <div className="scores-grid">
        {/* Category Scores */}
        <div className="score-panel-card">
          <h2>Performance Dimensions</h2>
          <div className="metric-row">
            <span>Structure (Flow & Organization)</span>
            <div className="progress-bar-container">
              <div className="progress-bar-fill" style={{ width: categoryWidth(cat.Structure, hasAudioMetrics), backgroundColor: '#4f46e5' }}></div>
              <span className="bar-val">{categoryValue(cat.Structure, hasAudioMetrics)}</span>
            </div>
          </div>
          <div className="metric-row">
            <span>Clarity (Vocabulary & Pacing)</span>
            <div className="progress-bar-container">
              <div className="progress-bar-fill" style={{ width: categoryWidth(cat.Clarity, hasAudioMetrics), backgroundColor: '#8b5cf6' }}></div>
              <span className="bar-val">{categoryValue(cat.Clarity, hasAudioMetrics)}</span>
            </div>
          </div>
          <div className="metric-row">
            <span>Persuasion (Argument & Response Evidence)</span>
            <div className="progress-bar-container">
              <div className="progress-bar-fill" style={{ width: categoryWidth(cat.Persuasion, hasAudioMetrics || hasQnaScores), backgroundColor: '#ec4899' }}></div>
              <span className="bar-val">{categoryValue(cat.Persuasion, hasAudioMetrics || hasQnaScores)}</span>
            </div>
          </div>
          <div className="metric-row">
            <span>Content Quality (Depth & Answers)</span>
            <div className="progress-bar-container">
              <div className="progress-bar-fill" style={{ width: categoryWidth(cat.Content_Quality, hasAudioMetrics || hasQnaScores), backgroundColor: '#10b981' }}></div>
              <span className="bar-val">{categoryValue(cat.Content_Quality, hasAudioMetrics || hasQnaScores)}</span>
            </div>
          </div>
          <div className="metric-row">
            <span>Call to Action (Conclusion & Wrap-up)</span>
            <div className="progress-bar-container">
              <div className="progress-bar-fill" style={{ width: categoryWidth(cat.Call_to_Action, hasAudioMetrics), backgroundColor: '#f59e0b' }}></div>
              <span className="bar-val">{categoryValue(cat.Call_to_Action, hasAudioMetrics)}</span>
            </div>
          </div>
        </div>

        {/* Live Session Telemetry Metrics */}
        <div className="telemetry-panel-card">
          <h2>Session Statistics</h2>
          <div className="telemetry-stats">
            <div className="telemetry-item">
              <span className="tel-label">Camera-Facing Gaze (Approx.)</span>
              <span className="tel-val">{hasGazeMetrics && metrics.avg_eye_contact != null ? `${metrics.avg_eye_contact}%` : 'N/A'}</span>
            </div>
            <div className="telemetry-item">
              <span className="tel-label">Upper-Body Posture Estimate</span>
              <span className="tel-val">{hasPostureMetrics && metrics.avg_posture != null ? `${metrics.avg_posture}%` : 'N/A'}</span>
            </div>
            <div className="telemetry-item">
              <span className="tel-label">Head Steadiness</span>
              <span className="tel-val">{hasHeadMovementMetrics && metrics.avg_head_movement != null ? `${metrics.avg_head_movement}%` : 'N/A'}</span>
            </div>
            <div className="telemetry-item">
              <span className="tel-label">Visual Tracking Proxy</span>
              <span className="tel-val">{hasTrackingProxy && metrics.avg_confidence != null ? `${metrics.avg_confidence}%` : 'N/A'}</span>
            </div>
            <div className="telemetry-item">
              <span className="tel-label">Vocal Pitch Dynamics</span>
              <span className="tel-val">
                {hasPitchMetrics ? `${metrics.avg_vocal_pitch ?? 0}%` : 'N/A'}
              </span>
            </div>
            <div className="telemetry-item">
              <span className="tel-label">Avg Speaking Pace</span>
              <span className="tel-val">
                {hasAudioMetrics && metrics.avg_wpm != null ? `${metrics.avg_wpm} WPM` : 'N/A'}
              </span>
            </div>
            <div className="telemetry-item">
              <span className="tel-label">Fillers Detected</span>
              <span className={`tel-val ${(metrics.total_fillers ?? 0) > 5 ? 'text-red' : ''}`}>
                {hasAudioMetrics && metrics.total_fillers != null ? metrics.total_fillers : 'N/A'}
              </span>
            </div>
            {report.speech_status === 'No speech detected' ? (
              <div className="telemetry-item" style={{ gridColumn: '1 / -1' }}>
                <span style={{ fontSize: '0.75rem', color: '#fbbf24' }}>
                  No speech detected. Speech-dependent scores and the overall presentation score are N/A; measured visual metrics are retained.
                </span>
              </div>
            ) : metrics.data_quality && !metrics.data_quality.has_audio_metrics ? (
              <div className="telemetry-item" style={{ gridColumn: '1 / -1' }}>
                <span style={{ fontSize: '0.75rem', color: '#fbbf24' }}>
                  🎤 No valid microphone transcript was captured; audio-dependent scores are unavailable.
                </span>
              </div>
            ) : null}
            {(!hasGazeMetrics || !hasPostureMetrics) && (
              <div className="telemetry-item" style={{ gridColumn: '1 / -1' }}>
                <span style={{ fontSize: '0.75rem', color: '#fbbf24' }}>
                  Camera-facing gaze is approximate. Posture is scored only when visible shoulder and hip landmarks are available.
                </span>
              </div>
            )}
            {metrics.data_quality?.video_frames_received ? (
              <>
                <div className="telemetry-item">
                  <span className="tel-label">Face-detected frame coverage</span>
                  <span className="tel-val">
                    {metrics.data_quality.video_frames_with_face ?? 0}/{metrics.data_quality.video_frames_received}
                    {' '}({metrics.data_quality.valid_frame_coverage ?? 0}%)
                  </span>
                </div>
                <div className="telemetry-item">
                  <span className="tel-label">Frames analyzed / dropped</span>
                  <span className="tel-val">
                    {metrics.data_quality.video_frames_analyzed ?? 0}/{metrics.data_quality.video_frames_dropped ?? 0}
                  </span>
                </div>
                <div className="telemetry-item">
                  <span className="tel-label">Gaze / posture frame coverage</span>
                  <span className="tel-val">
                    {metrics.data_quality.gaze_frame_coverage ?? 0}% / {metrics.data_quality.posture_frame_coverage ?? 0}%
                  </span>
                </div>
              </>
            ) : null}
          </div>
        </div>
      </div>

      {/* 7 Cs Evaluation Table */}
      <div className="seven-cs-card">
        <h2>7 Cs Communication Scorecard</h2>
        <div className="seven-cs-detailed-list">
          {Object.entries(report.seven_cs_scores).map(([key, value]) => {
            const evalKey = key as keyof typeof report.seven_cs_scores;
            const rawEval = report.seven_cs_evaluation?.[evalKey];
            const hasCsMetric = key === 'Consistent'
              ? hasGazeMetrics && hasPostureMetrics
              : key === 'Complete' || key === 'Concrete'
                ? hasAudioMetrics || hasQnaScores
                : key === 'Courteous'
                  ? hasAudioMetrics
                  : hasAudioMetrics;
            const hasMetricForScore = hasOverallScore && hasCsMetric && typeof value === 'number';
            const isObj = typeof rawEval === 'object' && rawEval !== null;
            const status = isObj ? (rawEval as any).status : null;
            const evaluationText = isObj ? (rawEval as any).feedback : (rawEval || 'No evaluation description available for this metric.');
            return (
              <div className="c-detailed-item" key={key}>
                <div className="c-detailed-header">
                  <span className="c-name">{key}</span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    {status && (
                      <span className={`status-badge ${status.toLowerCase().replace(/ /g, '-')}`}>
                        {status}
                      </span>
                    )}
                    <span className={hasMetricForScore ? `c-val-score ${getScoreColorClass(value)}` : 'c-val-score'}>{hasMetricForScore ? `${value}/100` : 'N/A'}</span>
                  </div>
                </div>
                <p className="c-evaluation-text">{evaluationText}</p>
              </div>
            );
          })}
        </div>
      </div>

      {/* Strengths & Recommendations */}
      <div className="feedback-split-grid">
        <div className="feedback-list-card strengths-card">
          <h3>Top Strengths</h3>
          <ul>
            {report.strengths.map((str, idx) => (
              <li key={idx}>{str}</li>
            ))}
          </ul>
        </div>
        <div className="feedback-list-card recommendations-card">
          <h3>Improvement Steps</h3>
          <ul>
            {report.recommendations.map((rec, idx) => (
              <li key={idx}>{rec}</li>
            ))}
          </ul>
        </div>
      </div>

      {/* Interruption Q&A Assessment */}
      <div className="qna-card">
        <h2>Academic Panelist Question Handling</h2>
        <div className="qna-content">
          <p className="qna-summary">{report.qna_analysis}</p>
          <div className="qna-stats-sub">
            <span className="sub-qna-lbl">Interruptions Handled:</span>
            <span className="sub-qna-val">{report.session_metrics.interruptions_handled}</span>
          </div>
        </div>
      </div>

      {/* Professional Rewrite Suggestion */}
      {report.improved_text && report.speech_status !== 'No speech detected' && (
        <div className="rewrite-section-card">
          <h2>AI Cleaned Transcript (Rewrite Suggestion)</h2>
          <p className="rewrite-subtitle">
            Here is a polished, grammatically correct version of your speech transcript with filler words and repetitions removed:
          </p>
          <div className="rewrite-text-display">
            {report.improved_text}
          </div>
        </div>
      )}

      {/* Reset/Start Over Button */}
      <div className="action-footer-row">
        <button className="reset-btn-premium" onClick={onReset}>
          {resetButtonLabel || "Practice New Session"}
        </button>
      </div>
    </div>
  );
};

export default ReportDashboard;
