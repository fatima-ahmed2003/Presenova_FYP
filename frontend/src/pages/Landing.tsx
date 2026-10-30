/**
 * Landing Page Component
 * Pre-login page for Presenova. Sections follow the project pitch deck.
 */

import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import './Landing.css';
import './LandingSections.css';

const scoreBars = [
  { label: 'Content quality', value: 88 },
  { label: 'Design', value: 84 },
  { label: 'Storytelling', value: 82 },
  { label: 'Consistency', value: 79 },
  { label: 'Accessibility', value: 76 },
  { label: 'Statistics & facts', value: 90 },
];

const sevenCs = ['Clear', 'Concise', 'Concrete', 'Correct', 'Coherent', 'Complete', 'Courteous'];

const modules = [
  { title: 'Analyse', text: 'Slides, speech and delivery' },
  { title: 'Practice', text: 'Live coach and AI rehearsal' },
  { title: 'Improve', text: 'Rewrite slides and sharpen delivery' },
  { title: 'Prepare', text: 'Viva and defence questions' },
];

const steps = [
  { title: 'Upload and set context', text: 'Add slides, documents or a recording and choose the presentation type.' },
  { title: 'Multimodal analysis', text: 'AI reviews content, speech, gaze, posture and audience fit.' },
  { title: 'Feedback and coaching', text: 'Get scores, guidance and actions you can use right away.' },
];

const stages = [
  { title: 'Analyse', text: 'Document, speech and content' },
  { title: 'Practice', text: 'Live coach and AI rehearsal' },
  { title: 'Improve', text: 'Rewrite slides and delivery' },
  { title: 'Generate', text: 'New decks from a prompt' },
  { title: 'Prepare', text: 'Viva and defence questions' },
];

const levels = [
  { title: 'Basic', text: 'Definitions, scope and motivation drawn from your document.' },
  { title: 'Intermediate', text: 'Method choices, dataset decisions and evaluation reasoning.' },
  { title: 'Advanced', text: 'Limitations, threats to validity and defence of your contribution.' },
];

const audiences = [
  { title: 'Students', text: 'Practise academic presentations, seminars and viva.' },
  { title: 'Educators', text: 'Evaluate presentations and give structured feedback.' },
  { title: 'Professionals', text: 'Prepare for interviews, meetings and corporate pitches.' },
];

const sectionIds = ['problem', 'features', 'how-it-works', 'audience'];

const showTabs = [
  {
    id: 'document',
    label: 'Document',
    img: '/screenshots/document-analyzer.png',
    title: 'Document analyzer',
    text: 'Upload a deck or document and get a scored report with the 7 Cs, strengths and fixes.',
    points: ['PPTX, PDF, DOCX and TXT supported', 'Category-wise scores', 'Downloadable PDF report'],
  },
  {
    id: 'speech',
    label: 'Speech',
    img: '/screenshots/speech-analyzer.png',
    title: 'Speech analyzer',
    text: 'Record or upload your talk and see pace, filler words and clarity with a full transcript.',
    points: ['Words per minute', 'Filler word count', 'Delivery suggestions'],
  },
  {
    id: 'live',
    label: 'Live coach',
    img: '/screenshots/live-coach.png',
    title: 'Live coach',
    text: 'Present to your webcam and microphone and get nudges while you speak.',
    points: ['Eye contact and posture', 'Real-time pace tracking', 'Instant tips'],
  },
  {
    id: 'viva',
    label: 'Viva prep',
    img: '/screenshots/viva.png',
    title: 'Viva and defence preparation',
    text: 'Presenova reads your document and generates the questions a panel would ask.',
    points: ['Basic, intermediate and advanced levels', 'Questions drawn from your own content', 'Rehearse your answers'],
  },
  {
    id: 'dashboard',
    label: 'Progress',
    img: '/screenshots/dashboard.png',
    title: 'Progress dashboard',
    text: 'Track scores and speaking pace across sessions and reopen saved reports.',
    points: ['Score improvement trend', 'Speaking pace trend', 'Saved history'],
  },
];

const faqs = [
  { q: 'Which file types can I upload?', a: 'PPTX, PDF, DOCX and TXT files can be analysed. The presentation rewriter exports an improved PPTX.' },
  { q: 'What are the 7 Cs?', a: 'Clear, concise, concrete, correct, coherent, complete and courteous. Presenova scores your content against them.' },
  { q: 'Do I need a webcam or microphone?', a: 'Document analysis needs neither. Speech analysis needs a recording or microphone, and the live coach uses your webcam and microphone.' },
  { q: 'How does viva preparation work?', a: 'Presenova reads your thesis or FYP document, retrieves the relevant context and generates questions at basic, intermediate and advanced levels.' },
  { q: 'Can I track my progress?', a: 'Yes. The dashboard shows your score and speaking pace trends, and keeps a saved history of your sessions.' },
  { q: 'Is there a desktop app?', a: 'Yes. Use the Download Desktop App link in the footer.' },
];

const waveHeights = [30, 55, 40, 70, 45, 85, 60, 35, 75, 50, 90, 40, 65, 30, 80, 55, 45, 70, 35, 60, 85, 50, 40, 75, 55, 30, 65, 45, 80, 50];
const waveFillers = [5, 6, 17, 22];

const prefersReducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const useActiveSection = (ids: string[]) => {
  const [active, setActive] = useState('');
  useEffect(() => {
    if (!('IntersectionObserver' in window)) return;
    const els = ids.map((id) => document.getElementById(id)).filter(Boolean) as HTMLElement[];
    const obs = new IntersectionObserver(
      (entries) => entries.forEach((e) => { if (e.isIntersecting) setActive(e.target.id); }),
      { rootMargin: '-40% 0px -55% 0px' }
    );
    els.forEach((el) => obs.observe(el));
    return () => obs.disconnect();
  }, [ids]);
  return active;
};

const useScrollProgress = () => {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onScroll = () => {
      const h = document.documentElement;
      const max = h.scrollHeight - h.clientHeight;
      if (ref.current) ref.current.style.transform = `scaleX(${max > 0 ? h.scrollTop / max : 0})`;
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);
  return ref;
};

const useCountUp = (target: number, duration = 1400) => {
  const [value, setValue] = useState<number>(() => (prefersReducedMotion() ? target : 0));
  useEffect(() => {
    if (prefersReducedMotion()) return;
    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      setValue(Math.round(target * (1 - Math.pow(1 - t, 3))));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);
  return value;
};

/* Browser-style frame. Shows the real screenshot if the file exists in /public/screenshots, otherwise a neutral placeholder. */
const ShotFrame: React.FC<{ src: string; alt: string }> = ({ src, alt }) => {
  const [failed, setFailed] = useState(false);
  return (
    <div className="lp-browser">
      <div className="lp-browser-bar" aria-hidden="true"><span /><span /><span /></div>
      {failed ? (
        <div className="lp-shot-fallback" aria-hidden="true">
          <i style={{ width: '40%' }} /><i style={{ width: '80%' }} /><i style={{ width: '65%' }} />
          <div className="lp-shot-fallback-grid"><b /><b /><b /></div>
        </div>
      ) : (
        <img src={src} alt={alt} loading="lazy" className="lp-shot" onError={() => setFailed(true)} />
      )}
    </div>
  );
};

const BeforeAfter: React.FC = () => {
  const [pos, setPos] = useState<number>(50);
  return (
    <div className="lp-ba" style={{ ['--pos' as string]: `${pos}%` }}>
      <div className="lp-ba-layer lp-ba-after">
        <h4>What Presenova does</h4>
        <ul>
          <li>Analyses slides and speech</li>
          <li>Coaches your delivery live</li>
          <li>Tracks progress over time</li>
        </ul>
      </div>
      <div className="lp-ba-layer lp-ba-before">
        <h4>Project Overview and Background Information</h4>
        <p>
          The system was developed by our team in order to be able to provide users with the
          ability to analyse presentations, and it is being utilised for the purpose of improving
          the communication skills of the users.
        </p>
      </div>
      <span className="lp-ba-label lp-ba-label-l">Before</span>
      <span className="lp-ba-label lp-ba-label-r">After</span>
      <div className="lp-ba-handle" aria-hidden="true"><span>‹ ›</span></div>
      <input
        type="range"
        min={0}
        max={100}
        value={pos}
        onChange={(e) => setPos(Number(e.target.value))}
        className="lp-ba-range"
        aria-label="Compare the original slide with the improved slide"
      />
    </div>
  );
};
const LogoIcon = () => (
  <img src="/logo-icon.png" alt="Presenova logo" className="landing-logo-img" />
);

const Landing: React.FC = () => {
  const { isAuthenticated, user } = useAuth();
  const [mobileMenuOpen, setMobileMenuOpen] = useState<boolean>(false);
  const [isLightTheme, setIsLightTheme] = useState<boolean>(() => {
    return document.documentElement.classList.contains('light-theme');
  });

  const toggleTheme = () => {
    const root = document.documentElement;
    if (root.classList.contains('light-theme')) {
      root.classList.remove('light-theme');
      setIsLightTheme(false);
      localStorage.setItem('presenova_theme', 'dark');
    } else {
      root.classList.add('light-theme');
      setIsLightTheme(true);
      localStorage.setItem('presenova_theme', 'light');
    }
  };

  const authTarget = isAuthenticated ? '/analytics' : '/login';
  const primaryCtaText = isAuthenticated ? 'Go to Dashboard' : 'Get Started Free';
  const closeMenu = () => setMobileMenuOpen(false);

  const activeSection = useActiveSection(sectionIds);
  const progressRef = useScrollProgress();
  const score = useCountUp(86);
  const [barsReady, setBarsReady] = useState<boolean>(prefersReducedMotion());
  const [activeTab, setActiveTab] = useState<string>(showTabs[0].id);
  const [openFaq, setOpenFaq] = useState<number | null>(0);
  const currentTab = showTabs.find((t) => t.id === activeTab) ?? showTabs[0];

  useEffect(() => {
    const t = setTimeout(() => setBarsReady(true), 150);
    return () => clearTimeout(t);
  }, []);

  const onTabKey = (e: React.KeyboardEvent, index: number) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const next = (index + (e.key === 'ArrowRight' ? 1 : showTabs.length - 1)) % showTabs.length;
    setActiveTab(showTabs[next].id);
    document.getElementById(`lp-tab-${showTabs[next].id}`)?.focus();
  };

  return (
    <div className="landing-page">
      {/* ── Navbar (unchanged logic, links updated to new sections) ── */}
      <header className="landing-nav">
        <div className="lp-progress" aria-hidden="true"><div ref={progressRef} /></div>
        <div className="landing-nav-container">
          <Link to="/" className="landing-logo">
            <LogoIcon />
            Presenova
          </Link>

          <nav>
            <ul className="landing-nav-menu">
              <li><a href="#problem" className={`landing-nav-link ${activeSection === 'problem' ? 'is-active' : ''}`}>Why Presenova</a></li>
              <li><a href="#how-it-works" className={`landing-nav-link ${activeSection === 'how-it-works' ? 'is-active' : ''}`}>How it Works</a></li>
              <li><a href="#features" className={`landing-nav-link ${activeSection === 'features' ? 'is-active' : ''}`}>Features</a></li>
              <li><a href="#audience" className={`landing-nav-link ${activeSection === 'audience' ? 'is-active' : ''}`}>Who it is for</a></li>
              {isAuthenticated && (
                <li><Link to="/analytics" className="landing-nav-link" style={{ color: 'var(--primary)' }}>Dashboard</Link></li>
              )}
            </ul>
          </nav>

          <div className="landing-nav-actions">
            <button
              onClick={toggleTheme}
              className="landing-theme-btn"
              title={isLightTheme ? 'Switch to Dark Mode' : 'Switch to Light Mode'}
              aria-label="Toggle Color Theme"
            >
              {isLightTheme ? (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
                </svg>
              ) : (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="5" />
                  <line x1="12" y1="1" x2="12" y2="3" />
                  <line x1="12" y1="21" x2="12" y2="23" />
                  <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
                  <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                  <line x1="1" y1="12" x2="3" y2="12" />
                  <line x1="21" y1="12" x2="23" y2="12" />
                  <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
                  <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
                </svg>
              )}
            </button>

            {isAuthenticated ? (
              <Link to="/analytics" className="btn btn-primary">
                Dashboard {user?.name ? `(${user.name.split(' ')[0]})` : ''}
              </Link>
            ) : (
              <>
                <Link to="/login" className="btn btn-ghost">Log In</Link>
                <Link to="/login" className="btn btn-primary">Get Started</Link>
              </>
            )}

            <button
              className="landing-hamburger"
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              aria-label="Toggle navigation menu"
            >
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                {mobileMenuOpen ? (
                  <>
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                  </>
                ) : (
                  <>
                    <line x1="3" y1="12" x2="21" y2="12" />
                    <line x1="3" y1="6" x2="21" y2="6" />
                    <line x1="3" y1="18" x2="21" y2="18" />
                  </>
                )}
              </svg>
            </button>
          </div>
        </div>

        <div className={`landing-mobile-drawer ${mobileMenuOpen ? 'open' : ''}`}>
          <div className="landing-mobile-links">
            <a href="#problem" className="landing-mobile-link" onClick={closeMenu}>Why Presenova</a>
            <a href="#how-it-works" className="landing-mobile-link" onClick={closeMenu}>How it Works</a>
            <a href="#features" className="landing-mobile-link" onClick={closeMenu}>Features</a>
            <a href="#audience" className="landing-mobile-link" onClick={closeMenu}>Who it is for</a>
            {isAuthenticated && (
              <Link to="/analytics" className="landing-mobile-link" style={{ color: 'var(--primary)' }} onClick={closeMenu}>
                Dashboard
              </Link>
            )}
          </div>
          <Link to={authTarget} className="btn btn-primary" style={{ width: '100%', minHeight: '48px' }} onClick={closeMenu}>
            {primaryCtaText}
          </Link>
        </div>
      </header>

      <main>
        {/* ── Hero ── */}
        <section className="landing-hero">
          <div className="hero-badge">
            <span>✨</span> AI-Powered Multimodal Presentation Coach
          </div>

          <h1 className="hero-headline">
            Know how effective your presentation <span>really is</span>
          </h1>

          <p className="hero-subheading">
            Presenova analyses your slides, your speech and your live delivery in one place, then
            coaches you until you are ready.
          </p>

          <div className="hero-cta-group">
            <Link to={authTarget} className="btn btn-primary" style={{ padding: '0.85rem 2rem', fontSize: '1rem' }}>
              {primaryCtaText}
            </Link>
            <a href="#how-it-works" className="btn btn-secondary" style={{ padding: '0.85rem 1.8rem', fontSize: '1rem' }}>
              See How It Works
            </a>
          </div>

          <div className="hero-mockup-wrapper" aria-hidden="true">
            <div className="mockup-header">
              <div className="mockup-dots">
                <span className="mockup-dot" />
                <span className="mockup-dot" />
                <span className="mockup-dot" />
              </div>
              <span className="mockup-title">Presenova session report</span>
              <span style={{ fontSize: '0.75rem', color: 'var(--success)', fontWeight: 600 }}>● Analysis ready</span>
            </div>

            <div className="mockup-grid">
              <div className="mockup-card">
                <span className="mockup-card-title">Overall score</span>
                <div className="mockup-score-row">
                  <span className="mockup-score-val">{score}</span>
                  <span className="mockup-score-label">Validated accuracy</span>
                </div>
                <div className="mockup-bars">
                  {scoreBars.slice(0, 3).map((b) => (
                    <div className="mockup-bar-item" key={b.label}>
                      <span>{b.label}</span>
                      <div className="mockup-bar-track"><div className="mockup-bar-fill" style={{ width: barsReady ? `${b.value}%` : 0 }} /></div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="mockup-card">
                <span className="mockup-card-title">AI practice coach</span>
                <div className="mockup-chat-preview">
                  <div className="mockup-chat-bubble mockup-chat-ai">
                    Your introduction sets up the problem well. Can you state the research gap in one sentence?
                  </div>
                  <div className="mockup-chat-bubble mockup-chat-user">
                    Existing tools coach speech alone and ignore slide content.
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ── Problem ── */}
        <section id="problem" className="landing-section">
          <div className="section-header">
            <h2 className="section-title">Presenting feels difficult.</h2>
            <p className="section-subtitle">
              Delivering a presentation is easy. Knowing how effective it really was is not.
            </p>
          </div>

          <div className="lp-split">
            <div className="lp-panel">
              <h3 className="lp-h3">The problem</h3>
              <p className="lp-text">
                Presenters rarely get structured, content-aware feedback on their work, so weak
                slides, unclear speech and nervous delivery go unnoticed until the day itself.
              </p>
              <ul className="lp-list">
                <li>Content quality and slide structure</li>
                <li>Speech pace, filler words and clarity</li>
                <li>Eye contact, posture and confidence</li>
                <li>Q&amp;A and viva preparation</li>
                <li>No personalised, measurable feedback</li>
              </ul>
            </div>

            <div className="lp-panel lp-panel-accent">
              <h3 className="lp-h3">Why existing solutions are not enough</h3>
              <p className="lp-text">
                Existing tools focus only on speech and give generic feedback, without understanding
                presentation content or quality.
              </p>
              <div className="lp-compare">
                <div>
                  <strong>ELSA Speak</strong>
                  <span>An AI speech coaching app focused on pronunciation, fluency and speaking pace.</span>
                </div>
                <div>
                  <strong>Orai</strong>
                  <span>A public-speaking practice app that works on vocal delivery and filler words.</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ── Meet Presenova ── */}
        <section id="features" className="lp-band">
          <div className="landing-section lp-split lp-center">
            <div>
              <h2 className="section-title lp-left">Meet Presenova</h2>
              <p className="section-subtitle lp-left">
                One AI-powered platform that analyses your document, your speech and your live
                delivery, then coaches you until you are ready.
              </p>
            </div>
            <div className="lp-grid-2">
              {modules.map((m) => (
                <div className="lp-mini" key={m.title}>
                  <strong>{m.title}</strong>
                  <span>{m.text}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ── See it in action (tabs + real screenshots) ── */}
        <section className="landing-section">
          <div className="section-header">
            <h2 className="section-title">See it in action</h2>
            <p className="section-subtitle">Pick a module to see what it looks like inside Presenova.</p>
          </div>

          <div className="lp-tablist" role="tablist" aria-label="Presenova modules">
            {showTabs.map((t, i) => (
              <button
                key={t.id}
                id={`lp-tab-${t.id}`}
                role="tab"
                aria-selected={activeTab === t.id}
                aria-controls="lp-tabpanel"
                tabIndex={activeTab === t.id ? 0 : -1}
                className={`lp-tab ${activeTab === t.id ? 'is-active' : ''}`}
                onClick={() => setActiveTab(t.id)}
                onKeyDown={(e) => onTabKey(e, i)}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div id="lp-tabpanel" role="tabpanel" aria-labelledby={`lp-tab-${currentTab.id}`} className="lp-tabpanel">
            <div className="lp-tabtext">
              <h3 className="lp-h3">{currentTab.title}</h3>
              <p className="lp-text">{currentTab.text}</p>
              <ul className="lp-list">
                {currentTab.points.map((pt) => <li key={pt}>{pt}</li>)}
              </ul>
            </div>
            <ShotFrame key={currentTab.id} src={currentTab.img} alt={`${currentTab.title} screen in Presenova`} />
          </div>
        </section>

        {/* ── How it works ── */}
        <section id="how-it-works" className="landing-section">
          <div className="section-header">
            <h2 className="section-title">How Presenova works</h2>
            <p className="section-subtitle">Three steps from first upload to useful feedback.</p>
          </div>
          <div className="steps-grid">
            {steps.map((s, i) => (
              <div className="step-card" key={s.title}>
                <span className="step-number">{`0${i + 1}`}</span>
                <h3 className="step-title">{s.title}</h3>
                <p className="step-text">{s.text}</p>
              </div>
            ))}
          </div>
          <ul className="lp-list lp-list-wide">
            <li>Upload PPTX, PDF, DOCX or TXT, or record your delivery.</li>
            <li>Presenova detects the presentation context and audience type.</li>
            <li>Category-wise performance scores are generated automatically.</li>
            <li>Session-wise reports and improvement suggestions are delivered.</li>
          </ul>
        </section>

        {/* ── Content analysis ── */}
        <section className="lp-band">
          <div className="landing-section lp-split">
            <div>
              <h2 className="section-title lp-left">Content and presentation analysis</h2>
              <p className="section-subtitle lp-left">
                Upload your deck or document. Presenova scores it against the 7 Cs and flags weak
                sections, with fixes you can apply.
              </p>
              <div className="lp-tags" aria-label="Supported formats">
                {['PPTX', 'PDF', 'DOCX', 'TXT'].map((t) => <span key={t} className="lp-tag-solid">{t}</span>)}
              </div>
              <div className="lp-tags" aria-label="7 Cs of communication">
                {sevenCs.map((c) => <span key={c}>{c}</span>)}
              </div>
            </div>
            <div className="lp-panel" aria-hidden="true">
              <div className="lp-score-head">
                <div className="lp-score">86<small>/100</small></div>
                <span className="lp-pill">Validated accuracy</span>
              </div>
              {scoreBars.map((b) => (
                <div className="lp-bar-row" key={b.label}>
                  <span>{b.label}</span>
                  <div className="lp-bar"><i style={{ width: `${b.value}%` }} /></div>
                  <em>{b.value}</em>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ── Speech analysis ── */}
        <section className="landing-section">
          <div className="lp-split">
            <div>
              <h2 className="section-title lp-left">Speech and vocal delivery analysis</h2>
              <p className="section-subtitle lp-left">
                Record your talk. Presenova transcribes it and measures how you actually sound.
              </p>
              <div className="lp-stats" aria-hidden="true">
                <div><b>132</b><span>Words per minute</span></div>
                <div><b className="lp-accent">14</b><span>Filler words</span></div>
                <div><b>6</b><span>Long pauses</span></div>
                <div><b>91</b><span>Clarity score</span></div>
              </div>
            </div>
            <div className="lp-panel" aria-hidden="true">
              <div className="lp-wave" aria-hidden="true">
                {waveHeights.map((h, i) => (
                  <span
                    key={i}
                    className={waveFillers.includes(i) ? 'lp-wave-filler' : ''}
                    style={{ height: `${h}%`, animationDelay: `${(i % 10) * 90}ms` }}
                  />
                ))}
              </div>
              <p className="lp-wave-legend" aria-hidden="true">Highlighted bars mark filler words.</p>
              <p className="lp-quote">
                “So today I want to walk you through our results. <mark>Um</mark>, the model reached
                ninety-six percent accuracy, and <mark>basically</mark> that means our pipeline is
                working as expected.”
              </p>
              <ul className="lp-list">
                <li>Drop three filler words per minute to sound more certain.</li>
                <li>Slow to 125 to 135 WPM on your result sections.</li>
                <li>Two phrases repeat. Vary your wording for emphasis.</li>
                <li>Pause after each key claim instead of filling the gap.</li>
              </ul>
            </div>
          </div>
        </section>

        {/* ── Live coach ── */}
        <section className="lp-band">
          <div className="landing-section lp-split">
            <div className="lp-panel" aria-hidden="true">
              <div className="lp-cam">
                <span className="lp-live">Live</span>
                <svg viewBox="0 0 200 110" width="100%" role="presentation">
                  <rect width="200" height="110" fill="#0e0a24" />
                  <circle cx="100" cy="44" r="16" fill="#3a2f6e" />
                  <path d="M60 110c0-24 18-38 40-38s40 14 40 38z" fill="#3a2f6e" />
                  <rect x="78" y="26" width="44" height="40" rx="8" fill="none" stroke="#38bdf8" strokeWidth="1.5" strokeDasharray="4 3" />
                </svg>
              </div>
              <div className="lp-stats lp-stats-sm">
                <div><b className="lp-accent">92%</b><span>Eye contact</span></div>
                <div><b>88%</b><span>Posture</span></div>
                <div><b>128</b><span>Pace (WPM)</span></div>
                <div><b>3</b><span>Filler words</span></div>
              </div>
              <div className="lp-tip">Hold the centre of the room. Your pace is drifting up.</div>
            </div>
            <div>
              <h2 className="section-title lp-left">Real-time live presentation coach</h2>
              <p className="section-subtitle lp-left">
                Present to your webcam and microphone. Presenova tracks eye contact, posture, pace
                and fillers as you speak, and nudges you while it matters.
              </p>
            </div>
          </div>
        </section>

        {/* ── AI practice coach ── */}
        <section className="landing-section">
          <div className="lp-split">
            <div>
              <h2 className="section-title lp-left">Interactive AI practice coach</h2>
              <p className="section-subtitle lp-left">
                Rehearse conversationally. The coach asks, listens, corrects and keeps the thread
                across turns.
              </p>
              <ul className="lp-list">
                <li>Multi-turn conversational rehearsal</li>
                <li>Follow-up questions on your own content</li>
                <li>Grammar and script suggestions</li>
                <li>Personalised, actionable guidance</li>
              </ul>
            </div>
            <div className="lp-panel lp-chat" aria-hidden="true">
              <div className="lp-msg lp-msg-ai">Your introduction sets up the problem well. Can you state the research gap in one sentence?</div>
              <div className="lp-msg lp-msg-me">Existing tools coach speech alone and ignore slide content and delivery.</div>
              <div className="lp-msg lp-msg-ai">Tighten it. Lead with the gap, then the consequence.</div>
              <div className="lp-input"><span>Type your answer</span><b>Send</b></div>
            </div>
          </div>
        </section>

        {/* ── Enhance & generate ── */}
        <section className="lp-band">
          <div className="landing-section">
            <div className="section-header">
              <h2 className="section-title">Enhance and generate</h2>
              <p className="section-subtitle">Improve the deck you already have, or create a new one from a single prompt.</p>
            </div>
            <div className="lp-split">
              <div className="lp-panel">
                <h3 className="lp-h3">Presentation rewriter</h3>
                <ul className="lp-list">
                  <li>Simplifies dense slides to a 6 by 6 bullet structure.</li>
                  <li>Corrects passive voice and weak filler phrases.</li>
                  <li>Rewrites wording and trims unnecessary text.</li>
                  <li>Exports an improved, ready-to-use PPTX.</li>
                </ul>
              </div>
              <div className="lp-panel">
                <h3 className="lp-h3">AI presentation generator</h3>
                <p className="lp-text">
                  Describe your topic, slide count and audience. Presenova builds a structured PPTX
                  using professional themes.
                </p>
                <div className="lp-tags" aria-hidden="true">
                  <span>Topic</span><span>Slides: 12</span><span>Tone: Academic</span><span>Audience: Panel</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ── Before / after ── */}
        <section className="landing-section">
          <div className="section-header">
            <h2 className="section-title">See the difference</h2>
            <p className="section-subtitle">Drag the divider to compare a slide before and after the rewriter.</p>
          </div>
          <BeforeAfter />
        </section>

        {/* ── Viva ── */}
        <section className="landing-section">
          <div className="section-header">
            <h2 className="section-title">Viva and defence preparation</h2>
            <p className="section-subtitle">
              Presenova reads your thesis or FYP document, retrieves the relevant context and
              generates the questions a panel would actually ask.
            </p>
          </div>
          <div className="lp-grid-3">
            {levels.map((l, i) => (
              <div className={`lp-card ${i === 2 ? 'lp-card-hot' : ''}`} key={l.title}>
                <strong>{l.title}</strong>
                <span>{l.text}</span>
              </div>
            ))}
          </div>
        </section>

        {/* ── Five stages ── */}
        <section className="lp-band">
          <div className="landing-section">
            <div className="section-header">
              <h2 className="section-title">One platform. Five stages.</h2>
              <p className="section-subtitle">Every module feeds the next, from the first draft to the final defence.</p>
            </div>
            <ol className="lp-stages">
              {stages.map((s, i) => (
                <li key={s.title}>
                  <span className="lp-stage-n">{i + 1}</span>
                  <strong>{s.title}</strong>
                  <span>{s.text}</span>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* ── Audience ── */}
        <section id="audience" className="landing-section">
          <div className="section-header">
            <h2 className="section-title">Who can use it</h2>
          </div>
          <div className="lp-grid-3">
            {audiences.map((a, i) => (
              <div className={`lp-card ${i === 2 ? 'lp-card-hot' : ''}`} key={a.title}>
                <strong>{a.title}</strong>
                <span>{a.text}</span>
              </div>
            ))}
          </div>
        </section>

        {/* ── FAQ ── */}
        <section className="landing-section lp-faq-wrap">
          <div className="section-header">
            <h2 className="section-title">Frequently asked questions</h2>
          </div>
          <div className="lp-faq">
            {faqs.map((f, i) => (
              <div className={`lp-faq-item ${openFaq === i ? 'is-open' : ''}`} key={f.q}>
                <h3>
                  <button
                    type="button"
                    aria-expanded={openFaq === i}
                    aria-controls={`lp-faq-${i}`}
                    onClick={() => setOpenFaq(openFaq === i ? null : i)}
                  >
                    {f.q}
                    <span aria-hidden="true">{openFaq === i ? '−' : '+'}</span>
                  </button>
                </h3>
                <div id={`lp-faq-${i}`} className="lp-faq-body" role="region" hidden={openFaq !== i}>
                  <p>{f.a}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ── Final CTA ── */}
        <section className="landing-cta-section">
          <div className="cta-box">
            <h2 className="cta-headline">Present smarter. Improve faster.</h2>
            <p className="cta-subtext">
              Presenova takes your presentation from practice to perfection.
            </p>
            <Link to={authTarget} className="btn btn-primary" style={{ padding: '0.9rem 2.5rem', fontSize: '1.05rem' }}>
              {primaryCtaText}
            </Link>
          </div>
        </section>
      </main>

      {/* ── Footer (unchanged) ── */}
      <footer className="landing-footer">
        <div className="footer-container">
          <div className="footer-top">
            <Link to="/" className="landing-logo">
              <LogoIcon />
              Presenova
            </Link>

            <ul className="footer-links">
              <li><a href="#features" className="footer-link">Features</a></li>
              <li><a href="#how-it-works" className="footer-link">How it Works</a></li>
              <li><Link to="/download" className="footer-link">Download Desktop App</Link></li>
              <li><Link to="/login" className="footer-link">Log In</Link></li>
            </ul>
          </div>

          <div className="footer-bottom">
            <span>Presenova — AI-Powered Presentation Coaching Platform</span>
            <span>Final Year Project (FYP) © 2026. All rights reserved.</span>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default Landing;
