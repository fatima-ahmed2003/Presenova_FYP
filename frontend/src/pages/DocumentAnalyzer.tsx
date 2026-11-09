/**
 * DocumentAnalyzer Page (REFACTORED - Phase 2, UI V4)
 * Single-session analysis flow (no V1 vs V2 wizard)
 * Reports automatically saved to history
 *
 * UI V4 changes:
 *  - Upload tab: step guide, file card (type chip, size, remove), analysis progress, dark "what we analyze" cards
 *  - Report: hero header + quick stats + inner tabs (Overview / Coach Feedback / 7Cs / Accuracy / Improved Text)
 *  - FIX: duplicate numbering, broken feedback titles, negative notes no longer shown as "Highlights"
 *  - FIX: improved text is cut at a line boundary (no more "g...")
 *  - FIX: "Analyze Document" button shows whenever a file is selected
 *  - Old UI blocks kept (commented / ReportViewV1_DISABLED). Nothing deleted.
 */

import React, { useState, useEffect } from 'react';
import FileUploader from '../components/FileUploader';
import Button from '../components/Button';
import { useNavigate } from 'react-router-dom';
import { analyzeDocument } from '../services/api';
import { AnalysisReport } from '../types';
import { downloadProgressReportPDF } from '../services/pdfGenerator';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';
import './DocumentAnalyzer.css';
import './DocumentAnalyzerV2.css';

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

  const data = Object.entries(normalizedScores).map(([name, value]) => ({
    name,
    value
  }));

  const COLORS = [
    '#4f46e5', '#10b981', '#f43f5e', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4',
  ];

  return (
    <div className="seven-cs-chart-wrapper">
      <ResponsiveContainer width="100%" height={240}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={60}
            outerRadius={85}
            paddingAngle={3}
            dataKey="value"
          >
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
      <div className="seven-cs-legend">
        {data.map((entry, index) => (
          <div key={entry.name} className="legend-item">
            <span className="legend-color-dot" style={{ backgroundColor: COLORS[index] }}></span>
            <span className="legend-label">{entry.name}: <strong>{entry.value}</strong></span>
          </div>
        ))}
      </div>
    </div>
  );
};

// Circular score ring
const ScoreRing: React.FC<{ score: number }> = ({ score }) => {
  const radius = 70;
  const circumference = 2 * Math.PI * radius;
  const safe = Math.max(0, Math.min(100, Number(score) || 0));
  const offset = circumference * (1 - safe / 100);
  return (
    <div className="da2-ring">
      <svg viewBox="0 0 180 180" width="180" height="180">
        <defs>
          <linearGradient id="da2RingGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#7c3aed" />
            <stop offset="100%" stopColor="#22d3ee" />
          </linearGradient>
        </defs>
        <circle cx="90" cy="90" r={radius} fill="none" stroke="rgba(148,163,184,0.18)" strokeWidth="14" />
        <circle
          cx="90" cy="90" r={radius} fill="none"
          stroke="url(#da2RingGrad)" strokeWidth="14" strokeLinecap="round"
          strokeDasharray={circumference} strokeDashoffset={offset}
          transform="rotate(-90 90 90)"
        />
      </svg>
      <div className="da2-ring-label">
        <span className="da2-ring-score">{safe}</span>
        <span className="da2-ring-max">/100</span>
      </div>
    </div>
  );
};

const scoreHeadline = (s: number) => {
  if (s >= 80) return 'Your document is well written and meets most of the key criteria.';
  if (s >= 60) return 'Decent foundation. A few targeted fixes will lift it noticeably.';
  return 'Needs work. Follow the recommendations to improve structure and language.';
};

const gradeInfo = (s: number) => {
  if (s >= 80) return { label: 'Excellent', cls: 'great' };
  if (s >= 65) return { label: 'Good', cls: 'good' };
  if (s >= 50) return { label: 'Needs work', cls: 'warn' };
  return { label: 'Weak', cls: 'bad' };
};

const toTitleCase = (s: string) => s.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());

// Split "STRUCTURE ASSESSMENT: ... LANGUAGE QUALITY: ... TOP 3 PRIORITY ACTIONS: (1) ... (2) ..." into sections
type FeedbackSection = { title: string; body: string; items?: string[] };
const parseCoachFeedback = (text: string): FeedbackSection[] => {
  if (!text) return [];
  const parts = text.split(/(?=\b[A-Z][A-Z &0-9]{4,}:\s)/g).map(s => s.trim()).filter(Boolean);
  return parts.map(p => {
    const m = p.match(/^([A-Z][A-Z &0-9]{4,}):\s*([\s\S]*)$/);
    if (!m) return { title: '', body: p };
    const title = toTitleCase(m[1]);
    const body = m[2].trim();
    if (/^\(\d+\)/.test(body)) {
      const items = body.split(/\(\d+\)\s*/).map(s => s.trim()).filter(Boolean);
      return { title, body: '', items };
    }
    return { title, body };
  });
};

// Repair text where every word landed on its own line (PDF extraction issue)
const formatImprovedText = (raw: string): string => {
  if (!raw) return '';
  const lines = raw.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  const shortLines = lines.filter(l => l.split(/\s+/).length <= 2).length;
  if (lines.length > 8 && shortLines / lines.length > 0.6) {
    let text = lines.join(' ');
    text = text.replace(/\s(?=#\s)/g, '\n');
    const cmdMatches = text.match(/\s(git|npm|docker|cd|pip|python|mkdir)\s/g) || [];
    if (cmdMatches.length >= 3) {
      text = text.replace(/\s(?=(git|npm|docker|cd|pip|python|mkdir)\s)/g, '\n');
    }
    return text;
  }
  return raw;
};

const isNegativeNote = (s: string) => /below average|room for improvement|needs? (work|attention|improvement)|\bweak\b|\bpoor\b/i.test(s);

const formatSize = (bytes: number) => (bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(2)} MB` : `${(bytes / 1024).toFixed(2)} KB`);

const LOAD_STEPS = [
  'Uploading and extracting text...',
  'Checking grammar and spelling...',
  'Scoring structure, clarity and the 7Cs...',
  'Building your report...',
];

type ReportView = 'overview' | 'feedback' | 'sevencs' | 'accuracy' | 'improved';

const DocumentAnalyzer: React.FC = () => {
  const navigate = useNavigate();
  
  // Upload & Analysis states
  const [file, setFile] = useState<File | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisReport | null>(null);
  const [analysisHistory, setAnalysisHistory] = useState<AnalysisReport[]>([]);
  
  // UI states
  const [loading, setLoading] = useState(false);
  const [loadStep, setLoadStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'upload' | 'report' | 'history'>('upload');
  const [reportView, setReportView] = useState<ReportView>('overview');
  const [showFullImproved, setShowFullImproved] = useState(false);
  const [copied, setCopied] = useState(false);

  // Load history from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem('document_analysis_history');
      if (stored) {
        setAnalysisHistory(JSON.parse(stored));
      }
    } catch (e) {
      console.warn('Could not load history:', e);
    }
  }, []);

  // Cycle progress messages while analysis runs
  useEffect(() => {
    if (!loading) {
      setLoadStep(0);
      return;
    }
    const t = setInterval(() => setLoadStep((s) => Math.min(s + 1, LOAD_STEPS.length - 1)), 3000);
    return () => clearInterval(t);
  }, [loading]);

  // Save analysis to history
  const saveToHistory = (report: AnalysisReport) => {
    const updated = [report, ...analysisHistory].slice(0, 10); // Keep last 10
    setAnalysisHistory(updated);
    localStorage.setItem('document_analysis_history', JSON.stringify(updated));
  };

  const handleFileSelect = (selectedFile: File) => {
    setFile(selectedFile);
    setError(null);
    setAnalysis(null);
    setActiveTab('upload');
  };

  const handleRemoveFile = () => {
    setFile(null);
    setError(null);
  };

  const handleAnalyze = async () => {
    if (!file) {
      setError('Please select a file to analyze');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await analyzeDocument(file);
      setAnalysis(result);
      setShowFullImproved(false);
      setReportView('overview');
      saveToHistory(result);
      setActiveTab('report');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to analyze document');
    } finally {
      setLoading(false);
    }
  };

  const handleAnalyzeAgain = () => {
    setFile(null);
    setAnalysis(null);
    setError(null);
    setActiveTab('upload');
  };

  const handleLoadFromHistory = (report: AnalysisReport) => {
    setAnalysis(report);
    setShowFullImproved(false);
    setReportView('overview');
    setActiveTab('report');
  };

  // History helpers (UI V5)
  const persistHistory = (list: AnalysisReport[]) => {
    setAnalysisHistory(list);
    try {
      localStorage.setItem('document_analysis_history', JSON.stringify(list));
    } catch (e) {
      console.warn('Could not save history:', e);
    }
  };

  const handleDeleteHistory = (idx: number) => {
    const next = analysisHistory.filter((_, i) => i !== idx);
    persistHistory(next);
    if (next.length === 0) setActiveTab('upload');
  };

  const handleClearHistory = () => {
    if (window.confirm('Clear all analysis history?')) {
      persistHistory([]);
      setActiveTab('upload');
    }
  };

  const handleCopyImproved = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch (e) {
      console.warn('Copy failed:', e);
    }
  };

  const handleDownloadReport = () => {
    if (!analysis) return;

    const safeDocName = file?.name || analysis.document_name || 'document.pdf';
    const catScores = analysis.category_scores || {} as any;

    // Map single-analysis data into the format expected by the PDF generator
    const categoryScores = [
      { name: 'Structure', v1: catScores.Structure || 0, v2: 0 },
      { name: 'Clarity', v1: catScores.Clarity || 0, v2: 0 },
      { name: 'Persuasion', v1: catScores.Persuasion || 0, v2: 0 },
      { name: 'Content Quality', v1: catScores.Content_Quality || 0, v2: 0 },
      { name: 'Grammar & Syntax', v1: catScores.Grammar_and_Syntax || (analysis as any).grammar_score || 0, v2: 0 },
      { name: 'Accuracy', v1: catScores.Accuracy || 0, v2: 0 },
      { name: 'Tone', v1: catScores.Tone_Appropriateness || 0, v2: 0 },
      { name: 'Audience Fit', v1: catScores.Audience_Alignment || 0, v2: 0 },
    ];

    downloadProgressReportPDF({
      title: 'Document Analysis Report',
      documentName: safeDocName,
      v1Score: analysis.overall_score || 75,
      v2Score: analysis.overall_score || 75,
      gain: 0,
      categoryScores,
      synthesis: analysis.detailed_feedback || 'Analysis complete.',
      improvements: analysis.recommendations || [],
      remaining: [],
    }, `Analysis_Report_${safeDocName.replace(/\.[^/.]+$/, "")}.pdf`);
  };

  // ---------- Derived report data ----------
  const breakdownRows = analysis ? [
    { name: 'Structure', value: analysis.category_scores?.Structure || 0 },
    { name: 'Clarity', value: analysis.category_scores?.Clarity || 0 },
    { name: 'Persuasion', value: analysis.category_scores?.Persuasion || 0 },
    { name: 'Content Quality', value: analysis.category_scores?.Content_Quality || 0 },
    { name: 'Grammar', value: analysis.category_scores?.Grammar_and_Syntax || 0 },
    { name: 'Tone', value: analysis.category_scores?.Tone_Appropriateness || 0 },
    { name: 'Audience', value: analysis.category_scores?.Audience_Alignment || 0 },
  ] : [];

  const strongestRow = breakdownRows.length ? breakdownRows.reduce((a, b) => (b.value > a.value ? b : a)) : null;
  const weakestRow = breakdownRows.length ? breakdownRows.reduce((a, b) => (b.value < a.value ? b : a)) : null;

  const sevenCsChips: { name: string; value: number }[] = analysis && analysis.seven_cs_scores
    ? Object.entries(analysis.seven_cs_scores as Record<string, any>).map(([k, v]) => ({
        name: k.charAt(0).toUpperCase() + k.slice(1),
        value: typeof v === 'number' ? v : 0,
      }))
    : [];
  const strongChips = sevenCsChips.filter(c => c.value >= 60).sort((a, b) => b.value - a.value);
  const weakChips = sevenCsChips.filter(c => c.value < 60).sort((a, b) => a.value - b.value);

  const strengthNotes = analysis?.strengths || [];
  const positiveNotes = strengthNotes.filter(s => !isNegativeNote(s));
  const cautionNotes = strengthNotes.filter(s => isNegativeNote(s));

  const feedbackSections = analysis ? parseCoachFeedback(analysis.detailed_feedback || '') : [];
  const improvedFormatted = analysis?.improved_text ? formatImprovedText(analysis.improved_text) : '';
  const improvedIsLong = improvedFormatted.length > 600;
  const improvedCut = improvedFormatted.slice(0, 600);
  const improvedCutAtLine = improvedCut.lastIndexOf('\n') > 200 ? improvedCut.slice(0, improvedCut.lastIndexOf('\n')) : improvedCut;
  const improvedShown = showFullImproved || !improvedIsLong ? improvedFormatted : `${improvedCutAtLine}\n…`;

  const grade = analysis ? gradeInfo(analysis.overall_score) : null;
  const accuracyScore = analysis?.context_analysis ? (analysis.context_analysis.context_accuracy_score ?? null) : null;

  const reportViews: { id: ReportView; label: string }[] = analysis ? [
    { id: 'overview', label: 'Overview' },
    { id: 'feedback', label: 'Coach Feedback' },
    { id: 'sevencs', label: '7Cs' },
    ...(analysis.context_analysis ? [{ id: 'accuracy' as ReportView, label: 'Accuracy' }] : []),
    ...(analysis.improved_text ? [{ id: 'improved' as ReportView, label: 'Improved Text' }] : []),
  ] : [];

  const fileExt = file ? (file.name.split('.').pop() || '').toUpperCase() : '';

  const histScores = analysisHistory.map((r) => Number(r.overall_score) || 0);
  const histBest = histScores.length ? Math.max(...histScores) : 0;
  const histAvg = histScores.length ? Math.round(histScores.reduce((a, b) => a + b, 0) / histScores.length) : 0;
  const histLatest = histScores.length ? histScores[0] : 0;

  return (
    <div className="document-analyzer">
      <h1>Document Analyzer</h1>
      <p className="subtitle">
        Upload any presentation (.pptx), document, or speaker notes to get instant AI-powered feedback on clarity, structure, grammar, and the 7Cs of Communication.
      </p>

      {/* Tab Navigation */}
      <div className="tab-menu">
        <button 
          className={`tab-btn ${activeTab === 'upload' ? 'active' : ''}`}
          onClick={() => setActiveTab('upload')}
        >
          📤 Analyze Document
        </button>
        <button 
          className={`tab-btn ${activeTab === 'report' ? 'active' : ''}`}
          disabled={!analysis}
          onClick={() => setActiveTab('report')}
        >
          📊 Report {analysis ? `(${analysis.overall_score}/100)` : ''}
        </button>
        {analysisHistory.length > 0 && (
          <button 
            className={`tab-btn ${activeTab === 'history' ? 'active' : ''}`}
            onClick={() => setActiveTab('history')}
          >
            📚 History ({analysisHistory.length})
          </button>
        )}
      </div>

      {error && (
        <div className="error-message">
          <strong>Error:</strong> {error}
        </div>
      )}

      {/* TAB 1: UPLOAD & ANALYZE (UI V4) */}
      {activeTab === 'upload' && (
        <div className="step-container fadeIn">
          <h2>Upload Your Document</h2>
          <p className="step-description">
            Upload a PDF, PowerPoint (.pptx), Word document, or text file containing your presentation slides, speaker notes, or written content. I'll analyze it across multiple dimensions including grammar, clarity, structure, and the 7Cs of Communication.
          </p>

          {/* 3-step guide */}
          <div className="da2-steps">
            <div className={`da2-step ${!file ? 'current' : 'done'}`}><span>1</span>Choose file</div>
            <div className="da2-step-line" />
            <div className={`da2-step ${file && !loading ? 'current' : loading ? 'done' : ''}`}><span>2</span>Analyze</div>
            <div className="da2-step-line" />
            <div className={`da2-step ${loading ? 'current' : ''}`}><span>3</span>Get report</div>
          </div>

          <div className="upload-box-wrapper">
            <FileUploader onFileSelect={handleFileSelect} loading={loading} accept=".pdf,.pptx,.doc,.docx,.txt" />

            {/* UI_V1_DISABLED: old selected-file block
            {file && (
              <div className="selected-file-details">
                <span className="file-icon">Doc</span>
                <div>
                  <strong>{file.name}</strong>
                  <span className="file-size">({(file.size / 1024).toFixed(2)} KB)</span>
                </div>
              </div>
            )}
            */}
            {file && (
              <div className="da2-file-card">
                <span className="da2-file-ext">{fileExt || 'DOC'}</span>
                <div className="da2-file-meta">
                  <strong>{file.name}</strong>
                  <span>{formatSize(file.size)}</span>
                </div>
                {!loading && (
                  <button type="button" className="da2-mini-btn" onClick={handleRemoveFile}>✕ Remove</button>
                )}
              </div>
            )}
            
            {/* UI_V1_DISABLED: {file && !analysis && ( ... )}  <- hid the button after the first report */}
            {file && (
              <div className="action-button-container">
                <Button 
                  label={analysis ? "Analyze Again (fresh run)" : "Analyze Document"} 
                  onClick={handleAnalyze} 
                  loading={loading}
                  variant="primary"
                />
              </div>
            )}

            {loading && (
              <div className="da2-progress">
                <div className="da2-spinner" />
                <span>{LOAD_STEPS[loadStep]}</span>
              </div>
            )}
          </div>

          {/* UI_V1_DISABLED: old light "What We Analyze" box
          <div className="tips-section">
            <h4>📌 What We Analyze</h4>
            <ul>
              <li><strong>7Cs of Communication:</strong> Clear, Concise, Correct, Complete, Courteous, Concrete, Consistent</li>
              <li><strong>Grammar & Style:</strong> Spelling, punctuation, sentence structure, professional tone</li>
              <li><strong>Clarity & Structure:</strong> Logical flow, paragraph organization, readability</li>
              <li><strong>Content Quality:</strong> Relevance, depth, evidence, actionability</li>
              <li><strong>Audience Alignment:</strong> Tone, complexity, engagement level for target audience</li>
            </ul>
          </div>
          */}
          <h4 className="da2-section-title">What We Analyze</h4>
          <div className="da2-tips">
            <div className="da2-tip"><strong>7Cs of Communication</strong><span>Clear, Concise, Correct, Complete, Courteous, Concrete, Consistent</span></div>
            <div className="da2-tip"><strong>Grammar &amp; Style</strong><span>Spelling, punctuation, sentence structure, professional tone</span></div>
            <div className="da2-tip"><strong>Clarity &amp; Structure</strong><span>Logical flow, paragraph organization, readability</span></div>
            <div className="da2-tip"><strong>Content Quality</strong><span>Relevance, depth, evidence, actionability</span></div>
            <div className="da2-tip"><strong>Audience Alignment</strong><span>Tone, complexity, engagement level for target audience</span></div>
          </div>
        </div>
      )}

      {/* TAB 2: ANALYSIS REPORT (UI V4) */}
      {activeTab === 'report' && analysis && grade && (
        <div className="step-container fadeIn da2-report">
          {/* Hero header */}
          <div className="da2-hero">
            <div className="da2-hero-left">
              <span className="da2-file-chip">📄 {analysis.document_name}</span>
              <span className={`da2-grade ${grade.cls}`}>{analysis.overall_score}/100 · {grade.label}</span>
            </div>
            <div className="report-actions-buttons">
              <Button 
                label="📥 Download PDF Report" 
                onClick={handleDownloadReport} 
                variant="primary"
              />
              <Button 
                label="🔄 Analyze Another" 
                onClick={handleAnalyzeAgain} 
                variant="secondary"
              />
            </div>
          </div>

          {/* Quick stats */}
          <div className="da2-stats">
            <div className="da2-stat"><span>Overall</span><strong>{analysis.overall_score}<em>/100</em></strong></div>
            {strongestRow && <div className="da2-stat good"><span>Strongest</span><strong>{strongestRow.name} <em>{strongestRow.value}</em></strong></div>}
            {weakestRow && <div className="da2-stat bad"><span>Weakest</span><strong>{weakestRow.name} <em>{weakestRow.value}</em></strong></div>}
            {accuracyScore !== null && <div className="da2-stat"><span>Accuracy</span><strong>{accuracyScore}<em>/100</em></strong></div>}
          </div>

          {/* Inner report tabs */}
          <div className="da2-subtabs">
            {reportViews.map((v) => (
              <button
                key={v.id}
                type="button"
                className={`da2-subtab ${reportView === v.id ? 'active' : ''}`}
                onClick={() => setReportView(v.id)}
              >
                {v.label}
              </button>
            ))}
          </div>

          {/* VIEW: Overview */}
          {reportView === 'overview' && (
            <div className="da2-grid">
              <div className="da2-panel">
                <div className="da2-score-row">
                  <ScoreRing score={analysis.overall_score} />
                  <div className="da2-score-text">
                    <h3>Overall Score</h3>
                    <p>{scoreHeadline(analysis.overall_score)}</p>
                  </div>
                </div>

                <h4 className="da2-section-title">Detailed Breakdown</h4>
                <div className="da2-bars">
                  {breakdownRows.map((row) => (
                    <div key={row.name} className="da2-bar-row">
                      <span className="da2-bar-name">{row.name}</span>
                      <div className="da2-bar-track">
                        <div className="da2-bar-fill" style={{ width: `${Math.max(0, Math.min(100, row.value))}%` }} />
                      </div>
                      <span className="da2-bar-value">{row.value}/100</span>
                    </div>
                  ))}
                </div>

                <h4 className="da2-section-title">Key Strengths</h4>
                {strongChips.length > 0 ? (
                  <div className="da2-chips">
                    {strongChips.map((c) => (
                      <span key={c.name} className="da2-chip good">✓ {c.name} <em>{c.value}</em></span>
                    ))}
                  </div>
                ) : (
                  <p className="da2-muted">No criteria above 60 yet. Follow the recommendations to build strengths.</p>
                )}

                {weakChips.length > 0 && (
                  <>
                    <h4 className="da2-section-title">Needs Attention</h4>
                    <div className="da2-chips">
                      {weakChips.map((c) => (
                        <span key={c.name} className="da2-chip weak">! {c.name} <em>{c.value}</em></span>
                      ))}
                    </div>
                  </>
                )}

                {positiveNotes.length > 0 && (
                  <>
                    <h4 className="da2-section-title">Highlights</h4>
                    <ul className="da2-note-list good">
                      {positiveNotes.slice(0, 3).map((s, i) => <li key={i}>{s}</li>)}
                    </ul>
                  </>
                )}
                {cautionNotes.length > 0 && (
                  <>
                    <h4 className="da2-section-title">Flagged by Analyzer</h4>
                    <ul className="da2-note-list warn">
                      {cautionNotes.slice(0, 3).map((s, i) => <li key={i}>{s}</li>)}
                    </ul>
                  </>
                )}
              </div>

              <div className="da2-panel">
                <h3 className="da2-rec-title">Recommendations</h3>
                <p className="da2-rec-sub">Small improvements can make a big difference.</p>
                <ul className="da2-rec-list">
                  {analysis.recommendations.map((rec, i) => (
                    <li key={i}>
                      <span className="da2-rec-num">{i + 1}</span>
                      <span className="da2-rec-text">{rec}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}

          {/* VIEW: Coach Feedback */}
          {reportView === 'feedback' && (
            <div className="da2-panel da2-feedback">
              <h3>AI Coach Feedback</h3>
              <div className="da2-fb-grid">
                {feedbackSections.map((sec, i) => (
                  <div key={i} className={`da2-fb-card ${sec.items ? 'da2-fb-wide' : ''}`}>
                    {sec.title && <div className="da2-fb-title">{sec.title}</div>}
                    {sec.items ? (
                      <ul className="da2-fb-actions">
                        {sec.items.map((it, j) => (
                          <li key={j}><span className="da2-rec-num">{j + 1}</span><span>{it}</span></li>
                        ))}
                      </ul>
                    ) : (
                      <p>{sec.body}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* VIEW: 7Cs */}
          {reportView === 'sevencs' && (
            <div className="report-card seven-cs-card-container">
              <h3>7Cs of Communication Evaluation</h3>
              <div className="seven-cs-content-split">
                <div className="checklist-grid">
                  {Object.entries(analysis.seven_cs_evaluation).map(([c, val]) => {
                    const isObj = typeof val === 'object' && val !== null;
                    const status = isObj ? (val as any).status : null;
                    const feedback = isObj ? (val as any).feedback : String(val);
                    return (
                      <div key={c} className="c-item">
                        <strong>{c}:</strong>{' '}
                        {status && (
                          <span className={`status-badge ${status.toLowerCase().replace(/ /g, '-')}`}>
                            {status}
                          </span>
                        )}{' '}
                        {feedback}
                      </div>
                    );
                  })}
                </div>
                <SevenCsPieChart scores={analysis.seven_cs_scores} overallScore={analysis.overall_score} />
              </div>
            </div>
          )}

          {/* VIEW: Accuracy */}
          {reportView === 'accuracy' && analysis.context_analysis && (
            <div className="report-card" style={{ marginBottom: '1.5rem', borderLeft: '4px solid #10b981' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
                <h3 style={{ margin: 0 }}>🔍 Context Accuracy & Factual Correctness</h3>
                <span className="score-badge" style={{ backgroundColor: analysis.context_analysis.is_context_accurate ? '#10b981' : '#f59e0b' }}>
                  Accuracy: {analysis.context_analysis.context_accuracy_score ?? 85}/100
                </span>
              </div>
              
              <p style={{ fontSize: '0.9rem', color: '#e2e8f0', marginBottom: '1rem', lineHeight: 1.5 }}>
                {analysis.context_analysis.factual_correctness_summary || "Context evaluation complete. The text aligns with the domain topic."}
              </p>

              {analysis.context_analysis.inaccuracies_detected && analysis.context_analysis.inaccuracies_detected.length > 0 && (
                <div style={{ marginBottom: '1rem' }}>
                  <h4 style={{ color: '#f87171', fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.4rem' }}>
                    ⚠️ Contextual Inaccuracies / Flaws Detected:
                  </h4>
                  <ul style={{ fontSize: '0.85rem', color: '#fca5a5', paddingLeft: '1.25rem', margin: 0 }}>
                    {analysis.context_analysis.inaccuracies_detected.map((item, idx) => (
                      <li key={idx}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}

              {analysis.context_analysis.context_based_changes && analysis.context_analysis.context_based_changes.length > 0 && (
                <div>
                  <h4 style={{ color: '#38bdf8', fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.4rem' }}>
                    💡 Context-Based Recommended Changes:
                  </h4>
                  <ul style={{ fontSize: '0.85rem', color: '#93c5fd', paddingLeft: '1.25rem', margin: 0 }}>
                    {analysis.context_analysis.context_based_changes.map((item, idx) => (
                      <li key={idx}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* VIEW: Improved text */}
          {reportView === 'improved' && analysis.improved_text && (
            <div className="da2-panel da2-improved">
              <div className="da2-improved-head">
                <h3>✍️ AI-Suggested Improvement</h3>
                <button type="button" className="da2-mini-btn" onClick={() => handleCopyImproved(improvedFormatted)}>
                  {copied ? '✓ Copied' : '📋 Copy'}
                </button>
              </div>
              <pre className="da2-code">{improvedShown}</pre>
              {improvedIsLong && (
                <button type="button" className="da2-mini-btn" onClick={() => setShowFullImproved(!showFullImproved)}>
                  {showFullImproved ? 'Show less' : 'Show full text'}
                </button>
              )}
            </div>
          )}

          {/* Action Buttons */}
          <div className="proceed-action-row" style={{ gap: '1rem' }}>
            <Button 
              label="🎤 Practice with Live Coach" 
              onClick={() => navigate('/practice', { state: { analysis, document_name: analysis.document_name } })}
              variant="primary"
            />
            <Button 
              label="📤 Analyze Another Document" 
              onClick={handleAnalyzeAgain}
              variant="secondary"
            />
          </div>

          {/* UI_V1_DISABLED: old report layout is preserved in ReportViewV1_DISABLED below (not rendered) */}
        </div>
      )}

      {/* TAB 3: ANALYSIS HISTORY (UI V5) */}
      {activeTab === 'history' && analysisHistory.length > 0 && (
        <div className="step-container fadeIn">
          <div className="da2-hist-head">
            <div>
              <h2>Analysis History</h2>
              <p className="da2-muted">Your last {analysisHistory.length} document analyses. Click any card to open the full report.</p>
            </div>
            <button type="button" className="da2-mini-btn da2-danger" onClick={handleClearHistory}>🗑 Clear all</button>
          </div>

          <div className="da2-stats">
            <div className="da2-stat"><span>Total runs</span><strong>{analysisHistory.length}</strong></div>
            <div className="da2-stat good"><span>Best score</span><strong>{histBest}<em>/100</em></strong></div>
            <div className="da2-stat"><span>Average</span><strong>{histAvg}<em>/100</em></strong></div>
            <div className="da2-stat"><span>Latest</span><strong>{histLatest}<em>/100</em></strong></div>
          </div>

          <div className="da2-hist-list">
            {analysisHistory.map((report, idx) => {
              const g = gradeInfo(report.overall_score);
              const older = analysisHistory[idx + 1];
              const delta = older ? report.overall_score - older.overall_score : null;
              const when = report.analysis_timestamp ? new Date(report.analysis_timestamp) : null;
              const whenOk = when && !isNaN(when.getTime());
              return (
                <div
                  key={idx}
                  className="history-item da2-hist-item"
                  onClick={() => handleLoadFromHistory(report)}
                >
                  <div className={`da2-hist-badge ${g.cls}`}>{report.overall_score}</div>
                  <div className="da2-hist-main">
                    <strong>{report.document_name}</strong>
                    <span className="da2-hist-meta">
                      {whenOk ? `${when!.toLocaleDateString()} · ${when!.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : 'Date unknown'}
                    </span>
                    <div className="da2-hist-bar">
                      <div className={`da2-hist-bar-fill ${g.cls}`} style={{ width: `${Math.max(0, Math.min(100, report.overall_score))}%` }} />
                    </div>
                  </div>
                  <div className="da2-hist-side">
                    <span className={`da2-grade ${g.cls}`}>{g.label}</span>
                    {delta !== null && (
                      <span className={`da2-delta ${delta > 0 ? 'up' : delta < 0 ? 'down' : 'same'}`}>
                        {delta > 0 ? `▲ +${delta}` : delta < 0 ? `▼ ${delta}` : '● no change'} vs previous
                      </span>
                    )}
                    <div className="da2-hist-actions">
                      <span className="da2-hist-open">View Report →</span>
                      <button
                        type="button"
                        className="da2-hist-del"
                        title="Delete this entry"
                        onClick={(e) => { e.stopPropagation(); handleDeleteHistory(idx); }}
                      >✕</button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* UI_V1_DISABLED: old history list (white cards, name invisible on dark theme)
      {activeTab === 'history' && analysisHistory.length > 0 && (
        <div className="step-container fadeIn">
          <h2>Analysis History</h2>
          <p style={{ color: '#718096', marginBottom: '1.5rem' }}>
            Your last {analysisHistory.length} document analyses. Click any to view the full report.
          </p>

          <div className="history-list">
            {analysisHistory.map((report, idx) => (
              <div 
                key={idx} 
                className="history-item da2-hist-item"
                onClick={() => handleLoadFromHistory(report)}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <strong style={{ fontSize: '1rem' }}>{report.document_name}</strong>
                    <p style={{ fontSize: '0.85rem', color: '#718096', marginTop: '0.3rem' }}>
                      Analyzed: {new Date(report.analysis_timestamp).toLocaleDateString()}
                    </p>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div className={`da2-hist-score ${gradeInfo(report.overall_score).cls}`}>
                      {report.overall_score}/100
                    </div>
                    <span style={{ fontSize: '0.75rem', color: '#a0aec0' }}>View Report →</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      */}
    </div>
  );
};

// UI_V1_DISABLED: previous (V1) report sections, kept for reference, not rendered anywhere.
export const ReportViewV1_DISABLED: React.FC<{ analysis: AnalysisReport }> = ({ analysis }) => (
  <>
    <div className="score-summary-row">
      <div className="score-widget">
        <h3>Overall Score</h3>
        <div className="score-circle">{analysis.overall_score}</div>
        <p style={{ fontSize: '0.85rem', color: '#718096', marginTop: '0.5rem' }}>out of 100</p>
      </div>
      <div className="feedback-widget">
        <h3>AI Coach Feedback</h3>
        <p>{analysis.detailed_feedback}</p>
        {analysis.strengths && analysis.strengths.length > 0 && (
          <div style={{ marginTop: '1rem' }}>
            <h4 style={{ color: '#10b981', fontSize: '0.9rem', fontWeight: 600, marginBottom: '0.5rem' }}>✅ Strengths</h4>
            <ul style={{ fontSize: '0.9rem', lineHeight: 1.6, paddingLeft: '1.5rem' }}>
              {analysis.strengths.slice(0, 3).map((s, i) => <li key={i}>{s}</li>)}
            </ul>
          </div>
        )}
      </div>
    </div>
    <div className="visual-chart-card">
      <h3>Detailed Score Breakdown by Category</h3>
      <div className="chart-container-wrapper" style={{ width: '100%', height: 320 }}>
        <ResponsiveContainer>
          <BarChart
            data={[
              { name: 'Structure', value: analysis.category_scores.Structure || 0 },
              { name: 'Clarity', value: analysis.category_scores.Clarity || 0 },
              { name: 'Persuasion', value: analysis.category_scores.Persuasion || 0 },
              { name: 'Content Q.', value: analysis.category_scores.Content_Quality || 0 },
              { name: 'Grammar', value: analysis.category_scores.Grammar_and_Syntax || 0 },
              { name: 'Tone', value: analysis.category_scores.Tone_Appropriateness || 0 },
              { name: 'Audience', value: analysis.category_scores.Audience_Alignment || 0 },
            ]}
            margin={{ top: 20, right: 30, left: 20, bottom: 5 }}
          >
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis domain={[0, 100]} />
            <Tooltip contentStyle={{ borderRadius: '8px', backgroundColor: 'rgba(15, 23, 42, 0.95)', color: '#fff' }} />
            <Bar dataKey="value" fill="#6366f1" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
    <div className="report-sections-grid">
      <div className="report-card">
        <h3>🎯 Key Recommendations</h3>
        <ul className="rec-list">
          {analysis.recommendations.map((rec, i) => (
            <li key={i}>{rec}</li>
          ))}
        </ul>
      </div>
      {analysis.improved_text && (
        <div className="report-card">
          <h3>✍️ AI-Suggested Improvement</h3>
          <p style={{ fontSize: '0.85rem', color: '#4a5568', fontStyle: 'italic', lineHeight: 1.6, background: '#f7fafc', padding: '1rem', borderRadius: '8px', border: '1px solid #e2e8f0', whiteSpace: 'pre-line' }}>
            {analysis.improved_text.length > 300 ? `${analysis.improved_text.slice(0, 300)}...` : analysis.improved_text}
          </p>
        </div>
      )}
    </div>
  </>
);

export default DocumentAnalyzer;
