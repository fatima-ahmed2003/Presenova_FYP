import React, { useState, useCallback, useRef, useEffect } from 'react';
import {
  rewritePresentation,
  analyzePresentation,
  pollProgress,
  fetchSlideReport,
  getReportPdfUrl,
  getDownloadUrl,
  MAX_PRESENTATION_UPLOAD_BYTES,
  PresentationAnalysisResponse,
  PresentationRewriterResponse,
  SlideComparisonReport,
  ReportData,
  ProcessingMode,
  WritingTone,
  PROCESSING_MODES,
  WRITING_TONES,
  ExecutiveSummary,
  AnalyticsData,
  RecommendationsData,
  PresentationStatistics,
} from '../services/presentationRewriterApi';
import './PresentationRewriter.css';

// ─── Step definitions ───────────────────────────────────────────────────────
const STEPS = [
  { id: 1, label: 'Upload',       icon: '📤', desc: 'Validating file'              },
  { id: 2, label: 'Extracting',   icon: '🔍', desc: 'Reading slides & text'        },
  { id: 3, label: 'AI Analysis',  icon: '🧠', desc: 'Scoring quality with AI'      },
  { id: 4, label: 'Rewriting',    icon: '✍️',  desc: 'Improving text & grammar'    },
  { id: 5, label: 'Ready',        icon: '✅', desc: 'Download your improved file'  },
];

type Stage = 'idle' | 'processing' | 'complete' | 'error';

interface QualityScores {
  overall_score: number;
  grade: string;
  summary: string;
  category_scores: Record<string, number>;
  seven_cs_scores: Record<string, number>;
  seven_cs_evaluation: Record<string, string>;
  strengths: string[];
  issues_found: string[];
  recommendations: string[];
}

// ─── Utility ────────────────────────────────────────────────────────────────
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function scoreColor(score: number): string {
  if (score >= 80) return '#22c55e';
  if (score >= 60) return '#f59e0b';
  return '#ef4444';
}

function gradeColor(grade: string): string {
  if (grade.startsWith('A')) return '#22c55e';
  if (grade.startsWith('B')) return '#3b82f6';
  if (grade.startsWith('C')) return '#f59e0b';
  return '#ef4444';
}

// ─── Sub-components ──────────────────────────────────────────────────────────

const ScoreRing: React.FC<{ score: number; label: string; size?: number }> = ({
  score, label, size = 120,
}) => {
  const radius = (size - 16) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (score / 100) * circumference;
  const color = scoreColor(score);

  return (
    <div className="pr-score-ring-wrap" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="8" />
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke={color} strokeWidth="8" strokeDasharray={circumference} strokeDashoffset={offset} strokeLinecap="round" transform={`rotate(-90 ${size / 2} ${size / 2})`} style={{ transition: 'stroke-dashoffset 1.2s cubic-bezier(0.4,0,0.2,1)' }} />
      </svg>
      <div className="pr-score-ring-inner">
        <span className="pr-score-ring-number" style={{ color }}>{score}</span>
        <span className="pr-score-ring-label">{label}</span>
      </div>
    </div>
  );
};

const MiniStat: React.FC<{ label: string; value: string | number }> = ({ label, value }) => (
  <div className="pr-mini-stat">
    <span className="pr-mini-stat-value">{value}</span>
    <span className="pr-mini-stat-label">{label}</span>
  </div>
);

const ScoreBar: React.FC<{ label: string; score: number; delay?: number }> = ({ label, score, delay = 0 }) => {
  const color = scoreColor(score);
  return (
    <div className="pr-score-bar-row">
      <span className="pr-score-bar-label">{label}</span>
      <div className="pr-score-bar-track">
        <div className="pr-score-bar-fill" style={{ width: `${score}%`, background: color, animationDelay: `${delay}ms` }} />
      </div>
      <span className="pr-score-bar-value" style={{ color }}>{score}</span>
    </div>
  );
};

const SevenCsBadge: React.FC<{ label: string; score: number; eval: string }> = ({ label, score, eval: evalText }) => {
  const [open, setOpen] = useState(false);
  const color = scoreColor(score);
  return (
    <div className={`pr-7cs-badge ${open ? 'open' : ''}`} onClick={() => setOpen(o => !o)} title="Click to expand">
      <div className="pr-7cs-badge-header">
        <span className="pr-7cs-badge-name">{label}</span>
        <span className="pr-7cs-badge-score" style={{ color }}>{score}</span>
      </div>
      {open && <p className="pr-7cs-badge-eval">{evalText}</p>}
    </div>
  );
};

const PriorityBadge: React.FC<{ priority: string }> = ({ priority }) => {
  const color = priority === 'high' ? '#ef4444' : priority === 'medium' ? '#f59e0b' : '#64748b';
  return <span className="pr-priority-badge" style={{ background: color + '22', color, border: `1px solid ${color}44` }}>{priority}</span>;
};

// ─── Slide Comparison View ───────────────────────────────────────────────────
const SlideComparisonView: React.FC<{
  slides: SlideComparisonReport[];
  qualityScores: QualityScores;
  onClose: () => void;
}> = ({ slides, qualityScores, onClose }) => {
  const [expandedSlide, setExpandedSlide] = useState<number | null>(null);
  return (
    <div className="pr-comparison-overlay">
      <div className="pr-comparison-header">
        <h2>📋 Slide-by-Slide Comparison</h2>
        <div className="pr-comparison-meta">
          <span>{slides.length} slides</span>
          <span className="pr-comparison-score-badge" style={{ background: gradeColor(qualityScores.grade) + '22', color: gradeColor(qualityScores.grade) }}>Grade {qualityScores.grade} · {qualityScores.overall_score}/100</span>
        </div>
        <button className="pr-btn pr-btn-ghost" onClick={onClose}>✕ Close</button>
      </div>
      <div className="pr-comparison-slides">
        {slides.map((slide) => {
          const hasChanges = slide.textboxes.some(tb => JSON.stringify(tb.original_paragraphs) !== JSON.stringify(tb.improved_paragraphs)) || slide.tables.some(t => t.cells.some(c => JSON.stringify(c.original_paragraphs) !== JSON.stringify(c.improved_paragraphs)));
          const isExpanded = expandedSlide === slide.slide_number;
          return (
            <div key={slide.slide_number} className={`pr-compare-slide-card ${hasChanges ? 'has-changes' : ''}`}>
              <div className="pr-compare-slide-header" onClick={() => setExpandedSlide(isExpanded ? null : slide.slide_number)}>
                <div className="pr-compare-slide-title">
                  <span className="pr-compare-slide-num">Slide {slide.slide_number}</span>
                  <span className="pr-compare-slide-name">{slide.title}</span>
                </div>
                <span className={`pr-compare-badge ${hasChanges ? 'changed' : 'unchanged'}`}>{hasChanges ? '✏️ Modified' : '✅ No changes'}</span>
              </div>
              {isExpanded && (
                <div className="pr-compare-slide-body">
                  {slide.textboxes.map((tb, i) => {
                    const origText = tb.original_paragraphs.join('\n').trim();
                    const imprText = tb.improved_paragraphs.join('\n').trim();
                    if (origText === '' && imprText === '') return null;
                    const changed = origText !== imprText;
                    return (
                      <div key={`tb-${i}`} className={`pr-compare-block ${changed ? 'diff' : ''}`}>
                        <div className="pr-compare-block-label">{tb.shape_name || `Textbox #${tb.shape_index}`}{changed && <span className="pr-diff-badge">Changed</span>}</div>
                        <div className="pr-compare-columns">
                          <div className="pr-compare-col original"><h4>Original</h4><pre>{origText || '(empty)'}</pre></div>
                          <div className="pr-compare-col improved"><h4>Improved</h4><pre>{imprText || '(empty)'}</pre></div>
                        </div>
                      </div>
                    );
                  })}
                  {slide.tables.map((t, ti) => (
                    <div key={`tbl-${ti}`} className="pr-compare-block diff">
                      <div className="pr-compare-block-label">Table #{t.shape_index}</div>
                      {t.cells.map((cell, ci) => {
                        const orig = cell.original_paragraphs.join('\n').trim();
                        const impr = cell.improved_paragraphs.join('\n').trim();
                        if (orig === '' && impr === '') return null;
                        const changed = orig !== impr;
                        return (
                          <div key={`cell-${ci}`} className="pr-compare-cell-row">
                            <span className="pr-compare-cell-coord">Cell ({cell.row_index},{cell.column_index})</span>
                            {changed ? (
                              <div className="pr-compare-cell-diff">
                                <span className="pr-diff-original">{orig || '(empty)'}</span>
                                <span className="pr-diff-arrow">→</span>
                                <span className="pr-diff-improved">{impr || '(empty)'}</span>
                              </div>
                            ) : <span className="pr-diff-unchanged">{orig || '(empty)'}</span>}
                          </div>
                        );
                      })}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

// ─── Recommendations Panel ───────────────────────────────────────────────────
const RecommendationsPanel: React.FC<{ data: RecommendationsData }> = ({ data }) => {
  const categories = Object.keys(data.categorized || {});
  return (
    <div className="pr-card pr-rec-card">
      <h3 className="pr-card-title">💡 AI Recommendations</h3>
      <p className="pr-card-subtitle">{data.total_recommendations} recommendations · {data.high_priority_count} high priority</p>
      {categories.map(cat => (
        <div key={cat} className="pr-rec-category">
          <h4 className="pr-rec-category-title">{cat}</h4>
          {(data.categorized[cat] || []).map((rec, i) => (
            <div key={i} className="pr-rec-item">
              <div className="pr-rec-header">
                <PriorityBadge priority={rec.priority} />
                <span className="pr-rec-text">{rec.recommendation}</span>
              </div>
              <p className="pr-rec-reason">{rec.reason}</p>
              <p className="pr-rec-impact">🎯 {rec.expected_impact}</p>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
};

// ─── Executive Summary Panel ─────────────────────────────────────────────────
const ExecutiveSummaryPanel: React.FC<{ summary: ExecutiveSummary }> = ({ summary }) => (
  <div className="pr-card pr-exec-card">
    <h3 className="pr-card-title">📊 Executive Summary</h3>
    <div className="pr-exec-grade-row">
      <div className="pr-exec-grade-badge" style={{ background: gradeColor(summary.grade) + '22', color: gradeColor(summary.grade) }}>
        {summary.grade}
      </div>
      <div className="pr-exec-grade-info">
        <span className="pr-exec-grade-label">{summary.quality_label}</span>
        <span className="pr-exec-grade-desc">{summary.quality_description}</span>
      </div>
      <div className="pr-exec-improvement">
        <span className="pr-exec-imp-value">+{summary.improvement_percentage}%</span>
        <span className="pr-exec-imp-label">improvement</span>
      </div>
    </div>
    <p className="pr-exec-assessment">{summary.overall_assessment}</p>
    <div className="pr-exec-stats-row">
      <MiniStat label="Slides" value={summary.slide_count} />
      <MiniStat label="Words" value={summary.word_count} />
      <MiniStat label="Score" value={`${summary.overall_score}/100`} />
    </div>
    {summary.top_improvements.length > 0 && (
      <>
        <h4 className="pr-exec-section-title">Top Improvements</h4>
        <div className="pr-exec-improvements">
          {summary.top_improvements.slice(0, 5).map((imp, i) => (
            <div key={i} className="pr-exec-imp-item">
              <span className="pr-exec-imp-name">{imp.category}</span>
              <span className="pr-exec-imp-delta" style={{ color: '#22c55e' }}>+{imp.improvement}</span>
            </div>
          ))}
        </div>
      </>
    )}
  </div>
);

// ─── Analytics Panel ────────────────────────────────────────────────────────
const AnalyticsPanel: React.FC<{ data: AnalyticsData }> = ({ data }) => (
  <div className="pr-card pr-analytics-card">
    <h3 className="pr-card-title">📈 Before vs After</h3>
    <div className="pr-analytics-overall">
      <div className="pr-analytics-score-box">
        <span className="pr-analytics-score-label">Original</span>
        <span className="pr-analytics-score-value">{data.overall.original_score}</span>
        <span className="pr-analytics-grade">{data.overall.original_grade}</span>
      </div>
      <div className="pr-analytics-arrow">→</div>
      <div className="pr-analytics-score-box improved">
        <span className="pr-analytics-score-label">Improved</span>
        <span className="pr-analytics-score-value">{data.overall.improved_score}</span>
        <span className="pr-analytics-grade">{data.overall.improved_grade}</span>
      </div>
      <div className="pr-analytics-delta">
        <span className="pr-analytics-delta-value" style={{ color: '#22c55e' }}>+{data.overall.delta}</span>
        <span className="pr-analytics-delta-label">points</span>
      </div>
    </div>
    <div className="pr-analytics-categories">
      {data.categories.map((cat, i) => (
        <div key={i} className="pr-analytics-cat-row">
          <span className="pr-analytics-cat-name">{cat.category}</span>
          <div className="pr-analytics-cat-bar-track">
            <div className="pr-analytics-cat-bar-orig" style={{ width: `${cat.original_score}%` }} />
            <div className="pr-analytics-cat-bar-impr" style={{ width: `${cat.improved_score}%`, marginTop: 2 }} />
          </div>
          <span className="pr-analytics-cat-delta" style={{ color: cat.delta > 0 ? '#22c55e' : cat.delta < 0 ? '#ef4444' : '#94a3b8' }}>
            {cat.delta > 0 ? '+' : ''}{cat.delta}
          </span>
        </div>
      ))}
    </div>
  </div>
);

// ─── Statistics Card ────────────────────────────────────────────────────────
const StatisticsPanel: React.FC<{ stats: PresentationStatistics }> = ({ stats }) => (
  <div className="pr-card pr-stats-card">
    <h3 className="pr-card-title">📊 Presentation Statistics</h3>
    <div className="pr-stats-grid">
      <MiniStat label="Slides" value={stats.slide_count} />
      <MiniStat label="Total Words" value={stats.total_words} />
      <MiniStat label="Avg Words/Slide" value={stats.average_words_per_slide} />
      <MiniStat label="Reading Time" value={stats.reading_time} />
      <MiniStat label="Speaking Time" value={stats.speaking_time} />
      <MiniStat label="Est. Duration" value={stats.estimated_duration} />
    </div>
  </div>
);

// ─── Main Component ──────────────────────────────────────────────────────────
const PresentationRewriter: React.FC = () => {
  const [stage, setStage]       = useState<Stage>('idle');
  const [currentStep, setStep]  = useState(0);
  const [selectedFile, setFile] = useState<File | null>(null);
  const [isDragging, setDragging] = useState(false);
  const [result, setResult]     = useState<PresentationRewriterResponse | null>(null);
  const [analysisResult, setAnalysisResult] = useState<PresentationAnalysisResponse | null>(null);
  const [error, setError]       = useState<string | null>(null);
  const [activeTab, setTab]     = useState<'category' | 'seven_cs'>('category');
  const [slideReport, setSlideReport] = useState<ReportData | null>(null);
  const [showComparison, setShowComparison] = useState(false);
  const [loadingReport, setLoadingReport] = useState(false);
  const [processingMode, setProcessingMode] = useState<ProcessingMode>('professional');
  const [writingTone, setWritingTone] = useState<WritingTone>('professional');
  const [activeInfoTab, setActiveInfoTab] = useState<'analysis' | 'summary' | 'analytics' | 'recommendations' | 'stats'>('analysis');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const progressIntervalRef = useRef<number | null>(null);

  useEffect(() => {
    return () => { if (progressIntervalRef.current) window.clearInterval(progressIntervalRef.current); };
  }, []);

  const onDragOver = useCallback((e: React.DragEvent) => { e.preventDefault(); setDragging(true); }, []);
  const onDragLeave = useCallback(() => setDragging(false), []);
  const onDrop = useCallback((e: React.DragEvent) => { e.preventDefault(); setDragging(false); const f = e.dataTransfer.files[0]; if (f) acceptFile(f); }, []);

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => { const f = e.target.files?.[0]; if (f) acceptFile(f); };

  const acceptFile = (f: File) => {
    const ext = f.name.split('.').pop()?.toLowerCase();
    // PDF_DISABLED: if (ext !== 'pptx' && ext !== 'pdf') { setError('Supported formats are .pptx for rewriting and .pdf/.pptx for analysis.'); return; }
    if (ext !== 'pptx') { setError('Only .pptx files are supported.'); return; }
    if (f.size > MAX_PRESENTATION_UPLOAD_BYTES) { setError('File too large. Maximum allowed size is 50 MB.'); return; }
    setFile(f); setResult(null); setAnalysisResult(null); setSlideReport(null); setShowComparison(false); setError(null); setStage('idle'); setStep(0);
  };

  const reset = () => {
    setFile(null); setResult(null); setAnalysisResult(null); setSlideReport(null); setShowComparison(false); setError(null); setStage('idle'); setStep(0);
    if (progressIntervalRef.current) { window.clearInterval(progressIntervalRef.current); progressIntervalRef.current = null; }
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const startProgressPolling = (filename: string) => {
    if (progressIntervalRef.current) window.clearInterval(progressIntervalRef.current);
    progressIntervalRef.current = window.setInterval(async () => {
      try {
        const p = await pollProgress(filename);
        setStep(Math.max(1, Math.min(5, Math.ceil((p.percent / 100) * 5))));
        if (p.done && progressIntervalRef.current) { window.clearInterval(progressIntervalRef.current); progressIntervalRef.current = null; }
      } catch { /* ignore */ }
    }, 800);
  };

  const handleSubmit = async () => {
    if (!selectedFile || !selectedFile.name.toLowerCase().endsWith('.pptx')) { setError('Select a .pptx file to rewrite.'); return; }
    setStage('processing'); setStep(1); setError(null); setResult(null); setSlideReport(null); setShowComparison(false);
    try {
      const response = await rewritePresentation(selectedFile, processingMode, writingTone);
      setResult(response); setStep(5); setStage('complete');
      startProgressPolling(response.output_filename);
    } catch (err: any) { setError(err.message || 'An error occurred.'); setStage('error'); setStep(0); }
  };

  const handleAnalyze = async () => {
    if (!selectedFile) return;
    setStage('processing'); setStep(1); setError(null); setResult(null);
    try {
      const response = await analyzePresentation(selectedFile);
      setAnalysisResult(response); setStage('complete'); setStep(5);
    } catch (err: any) { setError(err.message || 'Analysis failed.'); setStage('error'); setStep(0); }
  };

  const handleLoadSlideReport = async () => {
    if (!result) return;
    setLoadingReport(true);
    try {
      const data = await fetchSlideReport(result.output_filename);
      if (data.success && data.report) { setSlideReport(data.report); setShowComparison(true); }
      else setError('Slide comparison report is not available.');
    } catch (err: any) { setError(err.message || 'Failed to load slide report.'); }
    finally { setLoadingReport(false); }
  };

  const qs: QualityScores | undefined = result?.quality_scores as QualityScores | undefined;
  const execSummary = result?.executive_summary;
  const analyticsData = result?.analytics;
  const recsData = result?.recommendations;
  const stats = result?.statistics;

  return (
    <div className="pr-page">
      {showComparison && slideReport && (
        <SlideComparisonView slides={slideReport.slides} qualityScores={slideReport.quality_scores as QualityScores} onClose={() => setShowComparison(false)} />
      )}

      <div className="pr-hero">
        <div className="pr-hero-badge">✦ AI-Powered</div>
        <h1 className="pr-hero-title">Presentation Enhancement Platform</h1>
        <p className="pr-hero-subtitle">
          Upload your PowerPoint and the AI platform will analyze, enhance, score, and report
          on every slide — improving grammar, tone, clarity, and the 7 Cs — while keeping design 100% intact.
        </p>
      </div>

      <div className="pr-layout">
        {/* ═══ LEFT PANEL ═══ */}
        <div className="pr-left">
          <div className="pr-card pr-upload-card">
            <div id="pr-dropzone" className={`pr-dropzone ${isDragging ? 'dragging' : ''} ${selectedFile ? 'has-file' : ''}`} onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop} onClick={() => fileInputRef.current?.click()}>
              <input ref={fileInputRef} type="file" accept=".pptx" style={{ display: 'none' }} onChange={onFileChange} id="pr-file-input" />
              {selectedFile ? (
                <div className="pr-file-info">
                  <span className="pr-file-icon">📊</span>
                  <div className="pr-file-details">
                    <span className="pr-file-name">{selectedFile.name}</span>
                    <span className="pr-file-size">{formatBytes(selectedFile.size)}</span>
                  </div>
                  <button className="pr-file-clear" onClick={e => { e.stopPropagation(); reset(); }} title="Remove file">✕</button>
                </div>
              ) : (
                <div className="pr-dropzone-content">
                  <div className="pr-dropzone-icon">📁</div>
                  <p className="pr-dropzone-primary">Drop your .pptx file here</p>
                  <p className="pr-dropzone-secondary">Text is rewritten, design stays intact · Max 50 MB</p>
                  <div className="pr-format-pills"><span className="pr-pill active">PPTX</span>{/* PDF_DISABLED: <span className="pr-pill muted">PDF*</span> */}</div>
                </div>
              )}
            </div>

            {/* ═══ Mode & Tone Selectors ═══ */}
            <div className="pr-config-row">
              <div className="pr-config-group">
                <label className="pr-config-label">Mode</label>
                <div className="pr-config-options">
                  {PROCESSING_MODES.map(m => (
                    <button key={m} className={`pr-config-btn ${processingMode === m ? 'active' : ''}`} onClick={() => setProcessingMode(m)} disabled={stage === 'processing'} title={m === 'refresh' ? 'Replaces generic filler text with accurate topic content (design unchanged)' : undefined}>
                      {m === 'quick' ? '⚡ Quick' : m === 'professional' ? '🎯 Pro' : m === 'refresh' ? '✨ Refresh' : '🎓 Academic'}
                    </button>
                  ))}
                </div>
              </div>
              <div className="pr-config-group">
                <label className="pr-config-label">Tone</label>
                <select className="pr-config-select" value={writingTone} onChange={e => setWritingTone(e.target.value as WritingTone)} disabled={stage === 'processing'}>
                  {WRITING_TONES.map(t => <option key={t} value={t}>{t.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase())}</option>)}
                </select>
              </div>
            </div>

            <div className="pr-btn-row">
              <button id="pr-rewrite-btn" className="pr-btn pr-btn-primary" disabled={!selectedFile || !selectedFile.name.toLowerCase().endsWith('.pptx') || stage === 'processing'} onClick={handleSubmit}>
                {stage === 'processing' ? <><span className="pr-spinner" /> Rewriting…</> : <><span>✨</span> Rewrite with AI</>}
              </button>
              {selectedFile?.name.toLowerCase().endsWith('.pdf') && (
                <button id="pr-analyze-btn" className="pr-btn pr-btn-ghost" disabled={stage === 'processing'} onClick={handleAnalyze}>Analyze PDF</button>
              )}
            </div>

            {error && <div className="pr-error-banner" role="alert"><span>⚠️</span><span>{error}</span></div>}
          </div>

          {analysisResult && !result && (
            <div className="pr-card pr-analysis-card" role="status">
              <h3 className="pr-card-title">Analysis Complete</h3>
              <p className="pr-card-subtitle">{analysisResult.message} · {analysisResult.processing_time}</p>
              <div className="pr-overall-row">
                <ScoreRing score={analysisResult.quality_scores.overall_score} label="Overall" size={110} />
                <p className="pr-summary-text">{analysisResult.quality_scores.summary}</p>
              </div>
            </div>
          )}

          {stage !== 'idle' && (
            <div className="pr-card pr-stepper-card">
              <h3 className="pr-card-title">Progress</h3>
              <div className="pr-stepper">
                {STEPS.map((step, idx) => {
                  const done = currentStep > step.id;
                  const active = currentStep === step.id;
                  return (
                    <React.Fragment key={step.id}>
                      <div className={`pr-step ${done ? 'done' : active ? 'active' : 'pending'}`}>
                        <div className="pr-step-circle">{done ? '✓' : active ? <span className="pr-step-spinner" /> : step.id}</div>
                        <div className="pr-step-info">
                          <span className="pr-step-label">{step.icon} {step.label}</span>
                          {active && <span className="pr-step-desc">{step.desc}…</span>}
                        </div>
                      </div>
                      {idx < STEPS.length - 1 && <div className={`pr-step-connector ${done ? 'done' : ''}`} />}
                    </React.Fragment>
                  );
                })}
              </div>
            </div>
          )}

          {stage === 'complete' && result && (
            <div className="pr-card pr-download-card">
              <div className="pr-download-header">
                <span className="pr-download-icon">🎉</span>
                <div>
                  <h3 className="pr-download-title">Your Improved Presentation is Ready!</h3>
                  <p className="pr-download-meta">{result.slides_processed} slides · {result.processing_time}{result.mode ? ` · Mode: ${result.mode}` : ''}</p>
                </div>
              </div>
              {result.improvements && result.improvements.length > 0 && (
                <ul className="pr-improvement-list">
                  {result.improvements.map((item, i) => <li key={i} className="pr-improvement-item"><span className="pr-improvement-icon">✔</span><span>{item}</span></li>)}
                </ul>
              )}
              <div className="pr-download-actions">
                <a id="pr-download-btn" className="pr-btn pr-btn-download" href={getDownloadUrl(result.download_url)} target="_blank" rel="noreferrer" download><span>⬇️</span> Download Improved PPTX</a>
                <button id="pr-compare-btn" className="pr-btn pr-btn-secondary" onClick={handleLoadSlideReport} disabled={loadingReport}>
                  {loadingReport ? <><span className="pr-spinner-sm" /> Loading…</> : <><span>📋</span> Compare Changes</>}
                </button>
                <a id="pr-report-pdf-btn" className="pr-btn pr-btn-ghost" href={getReportPdfUrl(result.output_filename)} target="_blank" rel="noreferrer"><span>📄</span> Download Report (PDF)</a>
              </div>
              <button className="pr-btn pr-btn-ghost pr-reset-btn" onClick={reset}>↩ Rewrite Another</button>
            </div>
          )}
        </div>

        {/* ═══ RIGHT PANEL ═══ */}
        <div className="pr-right">
          {qs && stage === 'complete' ? (
            <>
              {/* ═══ Info Tabs ═══ */}
              <div className="pr-tab-row pr-info-tabs">
                {[{ id: 'analysis', label: 'Analysis' }, { id: 'summary', label: 'Summary' }, { id: 'analytics', label: 'Analytics' }, { id: 'recommendations', label: 'Recs' }, { id: 'stats', label: 'Stats' }].map(tab => (
                  <button key={tab.id} className={`pr-tab ${activeInfoTab === tab.id ? 'active' : ''}`} onClick={() => setActiveInfoTab(tab.id as any)}>{tab.label}</button>
                ))}
              </div>

              {/* Analysis Tab */}
              {activeInfoTab === 'analysis' && (
                <div className="pr-card pr-analysis-card">
                  <h3 className="pr-card-title">Quality Analysis</h3>
                  <p className="pr-card-subtitle">AI scored your presentation before rewriting</p>
                  <div className="pr-overall-row">
                    <ScoreRing score={qs.overall_score} label="Overall" size={130} />
                    <div className="pr-overall-meta">
                      <div className="pr-grade-badge" style={{ background: gradeColor(qs.grade) + '22', color: gradeColor(qs.grade) }}>Grade {qs.grade}</div>
                      <p className="pr-summary-text">{qs.summary}</p>
                    </div>
                  </div>
                  <div className="pr-tab-row">
                    <button id="pr-tab-category" className={`pr-tab ${activeTab === 'category' ? 'active' : ''}`} onClick={() => setTab('category')}>Category Scores</button>
                    <button id="pr-tab-7cs" className={`pr-tab ${activeTab === 'seven_cs' ? 'active' : ''}`} onClick={() => setTab('seven_cs')}>7 Cs Analysis</button>
                  </div>
                  {activeTab === 'category' && (
                    <div className="pr-bars-section">{Object.entries(qs.category_scores).map(([label, score], i) => <ScoreBar key={label} label={label} score={score} delay={i * 80} />)}</div>
                  )}
                  {activeTab === 'seven_cs' && (
                    <div className="pr-7cs-grid">{Object.entries(qs.seven_cs_scores).map(([label, score]) => <SevenCsBadge key={label} label={label} score={score} eval={qs.seven_cs_evaluation?.[label] || ''} />)}</div>
                  )}
                  <div className="pr-insights-row">
                    {qs.strengths?.length > 0 && <div className="pr-insight-box pr-strengths"><h4>💪 Strengths</h4><ul>{qs.strengths.map((s, i) => <li key={i}>{s}</li>)}</ul></div>}
                    {qs.issues_found?.length > 0 && <div className="pr-insight-box pr-issues"><h4>⚠️ Issues Found</h4><ul>{qs.issues_found.map((s, i) => <li key={i}>{s}</li>)}</ul></div>}
                  </div>
                  {qs.recommendations?.length > 0 && <div className="pr-recommendations"><h4>💡 AI Recommendations</h4><ol>{qs.recommendations.map((r, i) => <li key={i}>{r}</li>)}</ol></div>}
                </div>
              )}

              {/* Executive Summary Tab */}
              {activeInfoTab === 'summary' && execSummary && <ExecutiveSummaryPanel summary={execSummary} />}
              {activeInfoTab === 'summary' && !execSummary && (
                <div className="pr-card pr-placeholder-card"><p>Executive summary will appear here after processing.</p></div>
              )}

              {/* Analytics Tab */}
              {activeInfoTab === 'analytics' && analyticsData && <AnalyticsPanel data={analyticsData} />}
              {activeInfoTab === 'analytics' && !analyticsData && (
                <div className="pr-card pr-placeholder-card"><p>Analytics data will appear here after processing.</p></div>
              )}

              {/* Recommendations Tab */}
              {activeInfoTab === 'recommendations' && recsData && <RecommendationsPanel data={recsData} />}
              {activeInfoTab === 'recommendations' && !recsData && (
                <div className="pr-card pr-placeholder-card"><p>AI recommendations will appear here after processing.</p></div>
              )}

              {/* Statistics Tab */}
              {activeInfoTab === 'stats' && stats && <StatisticsPanel stats={stats} />}
              {activeInfoTab === 'stats' && !stats && (
                <div className="pr-card pr-placeholder-card"><p>Statistics will appear here after processing.</p></div>
              )}
            </>
          ) : (
            <div className="pr-card pr-placeholder-card">
              <div className="pr-placeholder-icon">🧠</div>
              <h3>AI Presentation Enhancement Platform</h3>
              <p>After rewriting, you'll see detailed analysis, executive summary, before/after analytics, personalized recommendations, and presentation statistics.</p>
              <div className="pr-placeholder-features">
                {['Quality Analysis', 'Executive Summary', 'Before/After Analytics', 'AI Recommendations', 'Presentation Statistics', 'Slide Comparison'].map(f => (
                  <div key={f} className="pr-placeholder-feature"><span className="pr-placeholder-dot" /><span>{f}</span></div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default PresentationRewriter;

