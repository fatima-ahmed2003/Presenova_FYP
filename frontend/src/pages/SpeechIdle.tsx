import React from 'react';

/**
 * Shown on the Speech page before any analysis exists.
 * Display only: no logic, no data.
 */
const SpeechIdle: React.FC = () => {
  const previews = [
    { num: '--', unit: 'words per minute', note: 'Ideal pace is 120 to 160 wpm.', tone: 'c-violet' },
    { num: '--', unit: 'filler words', note: 'Words like um, uh, like and you know.', tone: 'c-cyan' },
    { num: '--', unit: 'word repetitions', note: 'Same word repeated back to back.', tone: 'c-violet' },
    { num: '--', unit: 'clarity score', note: 'How clear and easy to follow you are.', tone: 'c-teal' },
  ];

  const steps = [
    { title: 'Record your speech', text: 'Press Start Recording and speak naturally for at least 30 seconds.' },
    { title: 'Analyze', text: 'We check your pace, filler words, repetitions and 7 C\'s score.' },
    { title: 'Improve', text: 'Read your transcript and follow the coaching suggestions.' },
  ];

  const tips = [
    'Use a quiet room and keep the microphone close.',
    'Speak at a natural pace, do not rush.',
    'Pause instead of saying um or uh.',
    'Practice the same speech again to see your score improve.',
  ];

  return (
    <div className="sa-report">
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
          <p>Your results will appear here after you record or load a demo speech.</p>
        </div>
      </div>

      <div className="sa-stats sa-idle">
        {previews.map((p) => (
          <div key={p.unit} className="sa-stat">
            <div className="sa-stat-top">
              <div className={`sa-stat-ring ${p.tone}`}>
                <span className="sa-idle-dot"></span>
              </div>
              <div>
                <div className="sa-stat-num">{p.num}</div>
                <div className="sa-stat-unit">{p.unit}</div>
              </div>
            </div>
            <p className="sa-stat-note">{p.note}</p>
          </div>
        ))}
      </div>

      <div className="sa-two-col sa-idle-cols">
        <section className="sa-card">
          <div className="sa-card-head">
            <div className="sa-card-icon">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
            </div>
            <div>
              <h3>How it works</h3>
              <p>Three simple steps to better speaking.</p>
            </div>
          </div>
          <ol className="sa-tips">
            {steps.map((s, i) => (
              <li key={s.title}>
                <span className="sa-tip-num">{i + 1}</span>
                <span className="sa-tip-text"><strong>{s.title}</strong><br />{s.text}</span>
              </li>
            ))}
          </ol>
        </section>

        <section className="sa-card">
          <div className="sa-card-head">
            <div className="sa-card-icon">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.2 1 2V17h6v-.3c0-.8.4-1.5 1-2A7 7 0 0 0 12 2Z"></path></svg>
            </div>
            <div>
              <h3>Recording Tips</h3>
              <p>Get the best result from your session.</p>
            </div>
          </div>
          <ul className="sa-idle-tips">
            {tips.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
};

export default SpeechIdle;
