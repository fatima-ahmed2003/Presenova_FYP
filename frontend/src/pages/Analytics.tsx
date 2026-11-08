/**
 * User Dashboard Page (Phase 4 / Phase 5 Conversion)
 * Visualizes user progress, score trends, and saved analysis history.
 * Adds Light/Dark theme toggles and session logout options.
 */

import React, { useEffect, useState } from 'react';
import {
  LineChart,
  Line,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceArea,
  LabelList,
  BarChart,
  Bar,
  Cell
} from 'recharts';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { getUserHistory, ReportItem } from '../services/api';
import './Analytics.css';
import './AnalyticsCharts.css';
import './AnalyticsHistory.css';

interface ChartDataPoint {
  date: string;
  ts: number;
  score: number;
  type: string;
}

interface SpeechDataPoint {
  date: string;
  ts: number;
  wpm: number;
  fillerWords: number;
}

type RangeKey = '7d' | '30d' | 'all';
type FilterKey = 'all' | 'document' | 'speech' | 'rewriter' | 'generator' | 'live';

interface TypeMeta {
  label: string;
  cls: string;
  filter: FilterKey;
}

// One place that decides label, badge colour and filter group for every report type
const getTypeMeta = (type: string): TypeMeta => {
  switch (type) {
    case 'document_analysis':
      return { label: 'Document Analyzer', cls: 'badge-doc', filter: 'document' };
    case 'speech_analysis':
      return { label: 'Speech Analyzer', cls: 'badge-speech', filter: 'speech' };
    case 'presentation_analysis':
    case 'presentation_rewrite':
      return { label: 'Rewriter', cls: 'badge-rewrite', filter: 'rewriter' };
    case 'presentation_generated':
      return { label: 'Generator', cls: 'badge-gen', filter: 'generator' };
    case 'live_coaching':
      return { label: 'Live Coach', cls: 'badge-live', filter: 'live' };
    default:
      return { label: 'Session', cls: 'badge-doc', filter: 'all' };
  }
};

const FILTER_TABS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'document', label: 'Document' },
  { key: 'speech', label: 'Speech' },
  { key: 'rewriter', label: 'Rewriter' },
  { key: 'generator', label: 'Generator' },
  { key: 'live', label: 'Live Coach' },
];

const Analytics: React.FC = () => {
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  // ===== STATE MANAGEMENT =====
  const [reports, setReports] = useState<ReportItem[]>([]);
  const [loading, setLoading] = useState(true);

  // Dashboard navigation tabs: 'overview' (charts) vs 'history' (list of past reports)
  const [activeTab, setActiveTab] = useState<'overview' | 'history'>('overview');

  // History filter tab
  const [historyFilter, setHistoryFilter] = useState<FilterKey>('all');

  // Global theme toggle: 'dark' (default) vs 'light'
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    return (localStorage.getItem('theme') as 'dark' | 'light') || 'dark';
  });

  // Historical report overlay details modal state
  const [activeReport, setActiveReport] = useState<ReportItem | null>(null);

  // Chart date-range dropdowns
  const [scoreRange, setScoreRange] = useState<RangeKey>('7d');
  const [paceRange, setPaceRange] = useState<RangeKey>('7d');

  // ===== THEME EFFECTS =====
  useEffect(() => {
    if (theme === 'light') {
      document.documentElement.classList.add('light-theme');
    } else {
      document.documentElement.classList.remove('light-theme');
    }
    localStorage.setItem('theme', theme);
  }, [theme]);

  // ===== HISTORY DATA FETCH =====
  useEffect(() => {
    const fetchHistory = async () => {
      let serverLoaded = false;

      // 1) Server history is the source of truth
      try {
        const data = await getUserHistory();
        if (data && Array.isArray(data.reports)) {
          setReports(data.reports);
          serverLoaded = true;
        }
      } catch (err) {
        console.warn('Could not fetch server history, loading local storage cache:', err);
      }

      // 2) Local cache is used ONLY if the server could not be reached
      if (!serverLoaded) {
        try {
          const localDocs = localStorage.getItem('document_analysis_history');
          if (localDocs) {
            const parsed = JSON.parse(localDocs);
            if (Array.isArray(parsed) && parsed.length > 0) {
              const mapped = parsed.map((item: any, idx: number) => ({
                id: `local-${idx}`,
                report_type: 'document_analysis',
                report_json: item,
                user_id: user?.id || 'local',
                created_at: item.analysis_timestamp || new Date().toISOString(),
              }));
              setReports(mapped);
            }
          }
        } catch (e) {
          console.warn('Error reading local history:', e);
        }
      }

      setLoading(false);
    };

    fetchHistory();
  }, [user]);

  const toggleTheme = () => {
    setTheme(prev => prev === 'dark' ? 'light' : 'dark');
  };

  // Helper: safely normalize report_json in case it's a string or missing properties
  const getSafeReportJson = (r: ReportItem): any => {
    let json = r.report_json;
    if (typeof json === 'string') {
      try {
        json = JSON.parse(json);
      } catch {
        json = {};
      }
    }
    return json || {};
  };

  // Helper: find a numeric score in a report, or null if the report has none
  const getScore = (r: ReportItem): number | null => {
    const j = getSafeReportJson(r);
    const candidates = [
      j.overall_score,
      j.score,
      j.final_score,
      j.session_metrics?.overall_score,
      j.clarity_score,
    ];
    const found = candidates.find((c) => typeof c === 'number' && !isNaN(c));
    return typeof found === 'number' ? Math.round(found) : null;
  };

  const getDisplayName = (r: ReportItem): string => {
    const j = getSafeReportJson(r);
    return j.presentation_title || j.document_name || j.topic || j.title || 'Presentation Analysis';
  };

  const handleRefresh = async () => {
    setLoading(true);
    try {
      const data = await getUserHistory();
      if (data && Array.isArray(data.reports)) {
        setReports(data.reports);
      }
    } catch (err) {
      console.warn('Manual refresh failed, keeping cached data:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  if (loading) {
    return (
      <div className="analytics-loading">
        <div className="spinner"></div>
        <p>Loading your dashboard...</p>
      </div>
    );
  }

  // ===== DATA TRANSFORMATIONS =====

  // Dev-only demo data: open /analytics?demo=1 on localhost (never active in a production build)
  const demoMode = import.meta.env.DEV && new URLSearchParams(window.location.search).has('demo');
  const demoReports: ReportItem[] = demoMode
    ? Array.from({ length: 8 }, (_, i) => {
        const isSpeech = i % 2 === 1;
        return {
          id: `demo-${i}`,
          report_type: isSpeech ? 'speech_analysis' : 'document_analysis',
          report_json: isSpeech
            ? { overall_score: 70 + i * 2, speech_speed_wpm: 136 + (i % 3) * 3, filler_words_count: 12 - i }
            : {
                overall_score: 68 + i * 3,
                category_scores: { structure: 82, clarity: 76, persuasion: 71, content_quality: 88, call_to_action: 64 },
              },
          user_id: 'demo',
          created_at: new Date(Date.now() - (7 - i) * 86400000).toISOString(),
        } as ReportItem;
      })
    : [];
  const sourceReports = demoMode ? demoReports : reports;

  // Sort reports chronologically for trends
  const sortedReports = [...sourceReports].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
  );

  // Sort reports newest-first for the Saved History list
  const historyReports = [...sourceReports].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );

  // Group A: documents, rewriter and generator. Group B: speech and live coach.
  const isDocType = (t: string) =>
    t === 'document_analysis' ||
    t === 'presentation_analysis' ||
    t === 'presentation_rewrite' ||
    t === 'presentation_generated';
  const isSpeechType = (t: string) => t === 'speech_analysis' || t === 'live_coaching';

  const documentReports = sortedReports.filter((r) => isDocType(r.report_type));
  const speechReports = sortedReports.filter((r) => isSpeechType(r.report_type));

  // 1. Overall Score History Data (only reports that really have a score)
  const scoreHistoryData: ChartDataPoint[] = sortedReports
    .map((r) => {
      const score = getScore(r);
      if (score === null) return null;
      const ts = new Date(r.created_at).getTime();
      return {
        date: new Date(r.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' }),
        ts,
        score,
        type: getTypeMeta(r.report_type).label,
      } as ChartDataPoint;
    })
    .filter((d): d is ChartDataPoint => d !== null);

  // 2. Speech Pacing & Filler Word Data
  const speechPacingData: SpeechDataPoint[] = speechReports.map((r) => {
    const rJson = getSafeReportJson(r);
    const wpm = rJson.speech_speed_wpm || rJson.session_metrics?.avg_wpm || 0;
    const fillers = rJson.filler_words_count || rJson.session_metrics?.total_fillers || 0;

    return {
      date: new Date(r.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' }),
      ts: new Date(r.created_at).getTime(),
      wpm: typeof wpm === 'number' ? wpm : 0,
      fillerWords: typeof fillers === 'number' ? fillers : 0,
    };
  });

  // 3. Category Average Scores (latest document that has category scores)
  const latestDoc = [...documentReports]
    .reverse()
    .find((r) => {
      const cs = getSafeReportJson(r).category_scores;
      return cs && typeof cs === 'object';
    });
  const latestDocJson = latestDoc ? getSafeReportJson(latestDoc) : null;
  const categoryScoresData = latestDocJson && latestDocJson.category_scores && typeof latestDocJson.category_scores === 'object'
    ? Object.entries(latestDocJson.category_scores).map(([category, score]) => ({
        category: category.replace(/_/g, ' '),
        score: typeof score === 'number' ? score : 0,
      }))
    : [];

  // ===== STATS CALCULATIONS =====
  const totalDocs = documentReports.length;
  const totalSpeeches = speechReports.length;

  const averageScore = (list: ReportItem[]): number => {
    const scores = list.map(getScore).filter((s): s is number => s !== null);
    return scores.length > 0 ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : 0;
  };

  const avgDocScore = averageScore(documentReports);
  const avgSpeechScore = averageScore(speechReports);

  const wpmValues = speechReports
    .map((r) => {
      const rJson = getSafeReportJson(r);
      const wpm = rJson.speech_speed_wpm || rJson.session_metrics?.avg_wpm || 0;
      return typeof wpm === 'number' ? wpm : 0;
    })
    .filter((w) => w > 0);
  const avgWpm = wpmValues.length > 0
    ? Math.round(wpmValues.reduce((a, b) => a + b, 0) / wpmValues.length)
    : 0;

  // ===== WEEKLY TREND + OVERALL RING (for stat cards) =====
  const oneWeekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const docsThisWeek = documentReports.filter(
    (r) => new Date(r.created_at).getTime() >= oneWeekAgo
  ).length;
  const speechesThisWeek = speechReports.filter(
    (r) => new Date(r.created_at).getTime() >= oneWeekAgo
  ).length;

  const scoredGroups = (avgDocScore > 0 ? 1 : 0) + (avgSpeechScore > 0 ? 1 : 0);
  const overallScore = scoredGroups > 0
    ? Math.round((avgDocScore + avgSpeechScore) / scoredGroups)
    : 0;
  const ringCircumference = 2 * Math.PI * 26;
  const ringOffset = ringCircumference * (1 - overallScore / 100);

  // ===== CHART RANGE FILTERS + QUICK TIP =====
  const rangeCutoff = (range: RangeKey) =>
    range === 'all' ? 0 : Date.now() - (range === '7d' ? 7 : 30) * 24 * 60 * 60 * 1000;

  const scoreChartData = scoreHistoryData.filter((d) => d.ts >= rangeCutoff(scoreRange));
  const paceChartData = speechPacingData
    .filter((d) => d.ts >= rangeCutoff(paceRange))
    .filter((d) => d.wpm > 0);

  const tooltipStyle = {
    borderRadius: '10px',
    border: '1px solid rgba(99,102,241,0.4)',
    backgroundColor: 'var(--card-bg)',
    color: 'var(--text-main)',
  };

  const quickTip =
    avgWpm === 0
      ? 'Record your first speech to unlock pacing and filler-word insights.'
      : avgWpm > 160
        ? `You are speaking at about ${avgWpm} wpm. Slow down a little to stay clear and easy to follow.`
        : avgWpm < 100
          ? `You are speaking at about ${avgWpm} wpm. Try picking up the pace to keep your audience engaged.`
          : `Keep your speaking pace steady around ${avgWpm} wpm. It helps you sound confident and easy to follow.`;

  // ===== HISTORY FILTERING =====
  const filterCount = (key: FilterKey) =>
    key === 'all'
      ? historyReports.length
      : historyReports.filter((r) => getTypeMeta(r.report_type).filter === key).length;

  const visibleHistory =
    historyFilter === 'all'
      ? historyReports
      : historyReports.filter((r) => getTypeMeta(r.report_type).filter === historyFilter);

  return (
    <div className="analytics-page">

      {/* Premium Dashboard Header actions */}
      <div className="analytics-header">
        <div className="header-meta">
          <h1>User Dashboard</h1>
          <p className="subtitle">Welcome back, <strong>{user?.name || 'User'}</strong>! Track your progress and manage saved sessions.</p>
        </div>

        <div className="header-actions">
          {/* Refresh / Retry Button */}
          <button onClick={handleRefresh} className="theme-toggle-btn" title="Refresh Dashboard Data">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"></path>
            </svg>
          </button>

          {/* Theme Switcher Toggle */}
          <button onClick={toggleTheme} className="theme-toggle-btn" title="Toggle Light/Dark Theme">
            {theme === 'dark' ? (
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="4"></circle>
                <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"></path>
              </svg>
            ) : (
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"></path>
              </svg>
            )}
          </button>

          {/* Logout Button */}
          <button onClick={handleLogout} className="logout-btn" title="Sign Out">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"></path>
            </svg>
            <span>Logout</span>
          </button>
        </div>
      </div>

      {/* Tabs Navigation Selectors */}
      <div className="dashboard-tabs">
        <button
          className={`dash-tab ${activeTab === 'overview' ? 'active' : ''}`}
          onClick={() => setActiveTab('overview')}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="9"></rect>
            <rect x="14" y="3" width="7" height="5"></rect>
            <rect x="14" y="12" width="7" height="9"></rect>
            <rect x="3" y="16" width="7" height="5"></rect>
          </svg>
          <span>Progress Overview</span>
        </button>
        <button
          className={`dash-tab ${activeTab === 'history' ? 'active' : ''}`}
          onClick={() => setActiveTab('history')}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 8v4l3 3M21 12a9 9 0 1 1-9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"></path>
            <polyline points="21 3 21 8 16 8"></polyline>
          </svg>
          <span>Saved History</span>
        </button>
      </div>

      {/* TAB 1: OVERVIEW & PROGRESS CHARTS */}
      {activeTab === 'overview' && (
        <div className="overview-tab-content fadeIn">
          {/* Stats Overview Grid */}
          <section className="stats-grid">
            <div className="stat-card" onClick={() => navigate('/analyzer')} role="button" tabIndex={0}>
              <div className="stat-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                  <polyline points="14 2 14 8 20 8"></polyline>
                  <line x1="16" y1="13" x2="8" y2="13"></line>
                  <line x1="16" y1="17" x2="8" y2="17"></line>
                </svg>
              </div>
              <div className="stat-content">
                <h4>Presentations & Documents</h4>
                <div className="stat-value">{totalDocs}</div>
                {docsThisWeek > 0 && <p className="stat-trend">↑ {docsThisWeek} this week</p>}
                <p className="stat-subtext">Avg Score: {avgDocScore}/100</p>
              </div>
              <svg className="stat-chevron" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="9 18 15 12 9 6"></polyline>
              </svg>
            </div>

            <div className="stat-card" onClick={() => navigate('/speech')} role="button" tabIndex={0}>
              <div className="stat-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path>
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
                  <line x1="12" y1="19" x2="12" y2="22"></line>
                </svg>
              </div>
              <div className="stat-content">
                <h4>Speeches Coached</h4>
                <div className="stat-value">{totalSpeeches}</div>
                {speechesThisWeek > 0 && <p className="stat-trend">↑ {speechesThisWeek} this week</p>}
                <p className="stat-subtext">Avg Score: {avgSpeechScore}/100</p>
              </div>
              <svg className="stat-chevron" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="9 18 15 12 9 6"></polyline>
              </svg>
            </div>

            <div className="stat-card" onClick={() => navigate('/speech')} role="button" tabIndex={0}>
              <div className="stat-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="10"></circle>
                  <polyline points="12 6 12 12 16 14"></polyline>
                </svg>
              </div>
              <div className="stat-content">
                <h4>Average Speaking Pace</h4>
                <div className="stat-value">{avgWpm} <span className="stat-unit">wpm</span></div>
                <p className="stat-subtext">
                  {avgWpm === 0 ? 'No pace data yet' : avgWpm > 160 ? 'Too Fast' : avgWpm < 100 ? 'Too Slow' : 'Optimal Pace'}
                </p>
              </div>
              <svg className="stat-chevron" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="9 18 15 12 9 6"></polyline>
              </svg>
            </div>

            <div className="stat-card" onClick={() => setActiveTab('history')} role="button" tabIndex={0}>
              <div className="stat-ring">
                <svg width="64" height="64" viewBox="0 0 60 60">
                  <defs>
                    <linearGradient id="ringGrad" x1="0" y1="0" x2="1" y2="1">
                      <stop offset="0%" stopColor="#8b5cf6" />
                      <stop offset="100%" stopColor="#22d3ee" />
                    </linearGradient>
                  </defs>
                  <circle cx="30" cy="30" r="26" className="ring-track" />
                  <circle
                    cx="30"
                    cy="30"
                    r="26"
                    className="ring-progress"
                    stroke="url(#ringGrad)"
                    strokeDasharray={ringCircumference}
                    strokeDashoffset={ringOffset}
                    transform="rotate(-90 30 30)"
                  />
                </svg>
                <svg className="ring-star" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
                </svg>
              </div>
              <div className="stat-content">
                <h4>Overall Progress</h4>
                <div className="stat-value">{overallScore}%</div>
                <p className="stat-subtext">
                  {totalDocs + totalSpeeches > 1 ? 'Growing, keep practicing!' : 'Starter, build your history!'}
                </p>
              </div>
              <svg className="stat-chevron" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="9 18 15 12 9 6"></polyline>
              </svg>
            </div>
          </section>

          {/* Chart Section Grid (top row) */}
          <div className="charts-grid" style={{ marginTop: '2rem' }}>

            {/* 1. Score Improvement (area chart) */}
            <section className="chart-container-card">
              <div className="chart-head">
                <div className="chart-icon">
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="m22 7-8.5 8.5-5-5L2 17"></path>
                    <polyline points="16 7 22 7 22 13"></polyline>
                  </svg>
                </div>
                <div className="chart-head-text">
                  <h3>Score Improvement Trend</h3>
                  <p className="chart-subtitle">Your overall score from analysed documents and speeches.</p>
                </div>
                <select className="range-select" value={scoreRange} onChange={(e) => setScoreRange(e.target.value as RangeKey)}>
                  <option value="7d">Last 7 Days</option>
                  <option value="30d">Last 30 Days</option>
                  <option value="all">All Time</option>
                </select>
              </div>
              {scoreChartData.length > 0 ? (
                <div className="chart-wrapper">
                  <ResponsiveContainer width="100%" height={300}>
                    <AreaChart data={scoreChartData} margin={{ top: 24, right: 24, left: -10, bottom: 0 }}>
                      <defs>
                        <linearGradient id="scoreFill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#6366f1" stopOpacity={0.55} />
                          <stop offset="100%" stopColor="#6366f1" stopOpacity={0.02} />
                        </linearGradient>
                        <linearGradient id="scoreStroke" x1="0" y1="0" x2="1" y2="0">
                          <stop offset="0%" stopColor="#8b5cf6" />
                          <stop offset="100%" stopColor="#22d3ee" />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border-color)" />
                      <XAxis dataKey="date" stroke="var(--text-muted)" fontSize={11} tickLine={false} />
                      <YAxis domain={[0, 100]} stroke="var(--text-muted)" fontSize={11} tickLine={false} />
                      <Tooltip contentStyle={tooltipStyle} />
                      <Area
                        name="Analysis Score"
                        type="monotone"
                        dataKey="score"
                        stroke="url(#scoreStroke)"
                        strokeWidth={3}
                        fill="url(#scoreFill)"
                        dot={{ r: 4, fill: '#818cf8', strokeWidth: 0 }}
                        activeDot={{ r: 7, fill: '#22d3ee', stroke: '#ffffff', strokeWidth: 2 }}
                      >
                        <LabelList dataKey="score" position="top" fill="var(--text-main)" fontSize={11} />
                      </Area>
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <div className="chart-empty">
                  <p>No scores in this period yet. Analyse a document or speech to see your progress.</p>
                  <button className="chart-empty-btn" onClick={() => navigate('/analyzer')}>Analyse a document</button>
                </div>
              )}
            </section>

            {/* 2. Speaking Pace (with target band) */}
            <section className="chart-container-card">
              <div className="chart-head">
                <div className="chart-icon">
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path>
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
                    <line x1="12" y1="19" x2="12" y2="22"></line>
                  </svg>
                </div>
                <div className="chart-head-text">
                  <h3>Speaking Pace Trend</h3>
                  <p className="chart-subtitle">Your average words per minute (wpm).</p>
                </div>
                <select className="range-select" value={paceRange} onChange={(e) => setPaceRange(e.target.value as RangeKey)}>
                  <option value="7d">Last 7 Days</option>
                  <option value="30d">Last 30 Days</option>
                  <option value="all">All Time</option>
                </select>
              </div>
              {paceChartData.length > 0 ? (
                <>
                  <div className="chart-wrapper">
                    <ResponsiveContainer width="100%" height={270}>
                      <LineChart data={paceChartData} margin={{ top: 24, right: 24, left: -10, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border-color)" />
                        <XAxis dataKey="date" stroke="var(--text-muted)" fontSize={11} tickLine={false} />
                        <YAxis domain={[60, 200]} stroke="var(--text-muted)" fontSize={11} tickLine={false} />
                        <Tooltip contentStyle={tooltipStyle} />
                        <ReferenceArea y1={120} y2={160} fill="#22d3ee" fillOpacity={0.1} stroke="#22d3ee" strokeOpacity={0.5} strokeDasharray="5 5" />
                        <Line
                          name="Pace (WPM)"
                          type="monotone"
                          dataKey="wpm"
                          stroke="#22d3ee"
                          strokeWidth={3}
                          dot={{ r: 4, fill: '#22d3ee', strokeWidth: 0 }}
                          activeDot={{ r: 7, fill: '#8b5cf6', stroke: '#ffffff', strokeWidth: 2 }}
                        >
                          <LabelList dataKey="wpm" position="top" fill="var(--text-main)" fontSize={11} />
                        </Line>
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="chart-legend-row">
                    <span><i className="legend-dot"></i> Your pace</span>
                    <span><i className="legend-box"></i> Target range (120 – 160 wpm)</span>
                  </div>
                </>
              ) : (
                <div className="chart-empty">
                  <p>No speech sessions in this period yet. Record a speech to see your pacing.</p>
                  <button className="chart-empty-btn" onClick={() => navigate('/speech')}>Record a speech</button>
                </div>
              )}
            </section>
          </div>

          {/* Quick Tip bar */}
          <section className="quick-tip">
            <div className="quick-tip-icon">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.2 1 2V17h6v-.3c0-.8.4-1.5 1-2A7 7 0 0 0 12 2Z"></path>
              </svg>
            </div>
            <div className="quick-tip-text">
              <h4>Quick Tip</h4>
              <p>{quickTip}</p>
            </div>
            <button className="quick-tip-btn" onClick={() => navigate('/speech')}>Practice now →</button>
          </section>

          {/* Chart Section Grid (bottom row) */}
          <div className="charts-grid" style={{ marginTop: '2rem' }}>

            {/* 3. Latest Document Categories Bar Chart */}
            <section className="chart-container-card">
              <div className="chart-head">
                <div className="chart-icon">
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                    <polyline points="14 2 14 8 20 8"></polyline>
                    <line x1="16" y1="13" x2="8" y2="13"></line>
                    <line x1="16" y1="17" x2="8" y2="17"></line>
                  </svg>
                </div>
                <div className="chart-head-text">
                  <h3>Latest Document Evaluation</h3>
                  <p className="chart-subtitle">Score breakdown per category for your most recent document.</p>
                </div>
              </div>
              {latestDoc && categoryScoresData.length > 0 ? (
                <div className="chart-wrapper">
                  <ResponsiveContainer width="100%" height={300}>
                    <BarChart data={categoryScoresData} margin={{ top: 24, right: 24, left: -10, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border-color)" />
                      <XAxis dataKey="category" stroke="var(--text-muted)" fontSize={11} tickLine={false} interval={0} />
                      <YAxis domain={[0, 100]} stroke="var(--text-muted)" fontSize={11} tickLine={false} />
                      <Tooltip contentStyle={tooltipStyle} />
                      <Bar dataKey="score" radius={[6, 6, 0, 0]}>
                        {categoryScoresData.map((entry, index) => (
                          <Cell
                            key={`cell-${index}`}
                            fill={entry.score >= 80 ? '#10b981' : entry.score >= 60 ? '#fbbf24' : '#ef4444'}
                          />
                        ))}
                        <LabelList dataKey="score" position="top" fill="var(--text-main)" fontSize={11} />
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <div className="chart-empty">
                  <p>No document history found. Upload a document to view category details.</p>
                  <button className="chart-empty-btn" onClick={() => navigate('/analyzer')}>Analyse a document</button>
                </div>
              )}
            </section>

            {/* 4. Filler Words count Bar Chart */}
            <section className="chart-container-card">
              <div className="chart-head">
                <div className="chart-icon">
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="4" y1="12" x2="4" y2="12"></line>
                    <line x1="8" y1="8" x2="8" y2="16"></line>
                    <line x1="12" y1="4" x2="12" y2="20"></line>
                    <line x1="16" y1="8" x2="16" y2="16"></line>
                    <line x1="20" y1="12" x2="20" y2="12"></line>
                  </svg>
                </div>
                <div className="chart-head-text">
                  <h3>Filler Words Count</h3>
                  <p className="chart-subtitle">Occurrences of "um", "ah", "like", etc. per speech session.</p>
                </div>
              </div>
              {speechPacingData.length > 0 ? (
                <div className="chart-wrapper">
                  <ResponsiveContainer width="100%" height={300}>
                    <BarChart data={speechPacingData} margin={{ top: 24, right: 24, left: -10, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border-color)" />
                      <XAxis dataKey="date" stroke="var(--text-muted)" fontSize={11} tickLine={false} />
                      <YAxis stroke="var(--text-muted)" fontSize={11} tickLine={false} />
                      <Tooltip contentStyle={tooltipStyle} />
                      <Bar dataKey="fillerWords" fill="#f87171" name="Filler Words" radius={[6, 6, 0, 0]}>
                        <LabelList dataKey="fillerWords" position="top" fill="var(--text-main)" fontSize={11} />
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <div className="chart-empty">
                  <p>No speech data found. Record speech audio to view filler word history.</p>
                  <button className="chart-empty-btn" onClick={() => navigate('/speech')}>Record a speech</button>
                </div>
              )}
            </section>
          </div>
        </div>
      )}

      {/* TAB 2: SAVED HISTORY LIST */}
      {activeTab === 'history' && (
        <div className="history-tab-content fadeIn">
          <h2>Your Presentation Analysis History</h2>
          <p className="section-desc">Look up previous document analyses, rewrites, generated presentations, speech reports, or live sessions to see detailed scores and recommendations.</p>

          {/* Filter tabs */}
          <div className="history-filters">
            {FILTER_TABS.map((tab) => (
              <button
                key={tab.key}
                className={`filter-chip ${historyFilter === tab.key ? 'active' : ''}`}
                onClick={() => setHistoryFilter(tab.key)}
              >
                {tab.label}
                <span className="filter-count">{filterCount(tab.key)}</span>
              </button>
            ))}
          </div>

          {visibleHistory.length > 0 ? (
            <div className="history-table-wrapper">
              <table className="history-table">
                <thead>
                  <tr>
                    <th>Feature</th>
                    <th>Topic / Filename</th>
                    <th>Date & Time</th>
                    <th>Score</th>
                    <th style={{ textAlign: 'right' }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleHistory.map((report) => {
                    const score = getScore(report);
                    const meta = getTypeMeta(report.report_type);

                    return (
                      <tr key={report.id} className="history-row">
                        <td>
                          <span className={`badge-pill ${meta.cls}`}>{meta.label}</span>
                        </td>
                        <td className="history-name-cell">
                          <strong>{getDisplayName(report)}</strong>
                        </td>
                        <td className="history-date-cell">
                          {new Date(report.created_at).toLocaleString([], {
                            year: 'numeric',
                            month: 'short',
                            day: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit'
                          })}
                        </td>
                        <td>
                          {score !== null ? (
                            <span className={`history-score ${score >= 80 ? 'score-high' : score >= 60 ? 'score-med' : 'score-low'}`}>
                              {score}/100
                            </span>
                          ) : (
                            <span className="history-score score-none">—</span>
                          )}
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          <button
                            className="view-summary-btn"
                            onClick={() => setActiveReport(report)}
                          >
                            View Summary
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="no-history-box">
              <div className="placeholder-icon">📋</div>
              <h3>{historyFilter === 'all' ? 'No past history records found' : 'Nothing saved for this feature yet'}</h3>
              <p>Use the features and your sessions will show up here automatically.</p>
            </div>
          )}
        </div>
      )}

      {/* OVERLAY DETAIL MODAL FOR HISTORICAL RUNS */}
      {activeReport && (() => {
        const activeJson = getSafeReportJson(activeReport);
        const modalScore = getScore(activeReport);
        const modalMeta = getTypeMeta(activeReport.report_type);
        const recommendations = Array.isArray(activeJson.recommendations) ? activeJson.recommendations : [];

        return (
          <div className="report-modal-overlay" onClick={() => setActiveReport(null)}>
            <div className="report-modal-content fadeIn" onClick={(e) => e.stopPropagation()}>
              <div className="modal-header">
                <span className={`modal-badge badge-pill ${modalMeta.cls}`}>
                  {modalMeta.label}
                </span>
                <button className="modal-close-btn" onClick={() => setActiveReport(null)}>✕</button>
              </div>

              <h2 className="modal-title">{getDisplayName(activeReport)}</h2>
              <p className="modal-date">Saved on: {new Date(activeReport.created_at).toLocaleString()}</p>

              <div className="modal-score-section">
                <div className="modal-score-circle">
                  <span className="score-num">{modalScore !== null ? modalScore : '—'}</span>
                  <span className="score-lbl">Score</span>
                </div>
                <div className="modal-feedback-box">
                  <h3>Detailed Feedback Summary</h3>
                  <p>
                    {activeJson.detailed_feedback ||
                     (Array.isArray(activeJson.actionable_feedback) && activeJson.actionable_feedback.join('. ')) ||
                     'Detailed analysis successfully compiled for this practice session. Review category scores and suggestions below.'}
                  </p>
                </div>
              </div>

              {/* Category breakdown rendering */}
              {activeJson.category_scores && typeof activeJson.category_scores === 'object' && (
                <div className="modal-scores-grid">
                  <h3>Category Score Breakdown</h3>
                  <div className="modal-bar-list">
                    {Object.entries(activeJson.category_scores).map(([cat, val]) => {
                      const numVal = typeof val === 'number' ? val : 0;
                      return (
                        <div key={cat} className="bar-row">
                          <span className="bar-label">{cat.replace(/_/g, ' ')}</span>
                          <div className="bar-bg">
                            <div className="bar-fill" style={{ width: `${numVal}%`, background: numVal >= 80 ? '#10b981' : numVal >= 60 ? '#fbbf24' : '#ef4444' }}></div>
                          </div>
                          <span className="bar-value">{numVal}/100</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Telemetry data for audio/speech recordings */}
              {(activeJson.speech_speed_wpm || activeJson.session_metrics) && (
                <div className="speech-telemetry-box">
                  <h3>Speech Delivery Metrics</h3>
                  <div className="telemetry-grid">
                    <div className="telemetry-card">
                      <span className="tel-title">Speaking Pace</span>
                      <span className="tel-val">
                        {activeJson.speech_speed_wpm || activeJson.session_metrics?.avg_wpm || 0} WPM
                      </span>
                    </div>
                    <div className="telemetry-card">
                      <span className="tel-title">Filler Word Occurrences</span>
                      <span className="tel-val">
                        {activeJson.filler_words_count !== undefined
                          ? activeJson.filler_words_count
                          : activeJson.session_metrics?.total_fillers || 0}
                      </span>
                    </div>
                    <div className="telemetry-card">
                      <span className="tel-title">Clarity Metric</span>
                      <span className="tel-val">
                        {activeJson.clarity_score || activeJson.overall_score || 70}/100
                      </span>
                    </div>
                    {activeJson.session_metrics?.avg_eye_contact !== undefined && (
                      <div className="telemetry-card">
                        <span className="tel-title">Eye Contact Accuracy</span>
                        <span className="tel-val">
                          {activeJson.session_metrics?.avg_eye_contact}%
                        </span>
                      </div>
                    )}
                    {activeJson.session_metrics?.avg_posture !== undefined && (
                      <div className="telemetry-card">
                        <span className="tel-title">Body Posture Accuracy</span>
                        <span className="tel-val">
                          {activeJson.session_metrics?.avg_posture}%
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Recommendations list */}
              {recommendations.length > 0 && (
                <div className="modal-recommendations">
                  <h3>Key Recommendations</h3>
                  <ul className="modal-rec-list">
                    {recommendations.map((rec: string, index: number) => (
                      <li key={index}>{rec}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Professional Rewrites */}
              {activeJson.improved_text && (
                <div className="modal-rewrite">
                  <h3>Professional Rewrite Suggestion</h3>
                  <p className="rewrite-text">{activeJson.improved_text}</p>
                </div>
              )}

              <div style={{ marginTop: '2rem', display: 'flex', justifyContent: 'flex-end' }}>
                <button className="modal-done-btn" onClick={() => setActiveReport(null)}>
                  Close Details
                </button>
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
};

export default Analytics;