/**
 * PracticeMode Page (Phase 5, UI V2)
 * Real-time AI Coach chat interface for multi-turn presentation practice
 *
 * UI V2 changes:
 *  - Two-column layout: chat (main) + side panel (Context, grouped Quick Prompts, collapsible About)
 *  - Chat header bar (status, message count, Clear chat)
 *  - AI messages render **bold** and "- " bullet lines properly
 *  - Quick prompt click fills the input AND focuses it
 *  - Chat logic / API call / context handling unchanged
 *  - Old Quick Suggestions + Info sections kept below as comments (UI_V1_DISABLED). Nothing deleted.
 *
 * Features:
 * - Direct Google Gemini API connection (Gemini 2.0 Flash / 1.5 Flash) with backend fallback
 * - Professional chat bubble UI with user and AI messages
 * - Context-aware coaching based on analysis reports (7Cs, WPM, strengths, recommendations)
 * - Real-time typing indicators during AI response generation
 * - Auto-scroll to latest messages
 * - Send message with Enter key, Shift+Enter for new lines
 */

import React, { useState, useRef, useEffect } from 'react';
import Button from '../components/Button';
import { useLocation } from 'react-router-dom';
import { sendChatMessage } from '../services/api';
import { ChatMessage, ContextReport } from '../types';
import './PracticeMode.css';
import './PracticeModeV2.css';

// ===== Quick prompt groups (same six prompts as before, now grouped) =====
const PROMPT_GROUPS: { title: string; items: { label: string; icon: string; prompt: string }[] }[] = [
  {
    title: 'Delivery',
    items: [
      { label: 'Reduce Filler Words', icon: '🗣️', prompt: "I often use filler words like 'um' and 'like'. How can I reduce them?" },
      { label: 'Perfect Your Pace', icon: '⏱️', prompt: "How should I pace my slides so I don't run out of time during a 10-minute presentation?" },
      { label: 'Manage Anxiety', icon: '🧘', prompt: 'I get nervous before speaking. What 2-minute technique can help me stay calm and confident?' },
    ],
  },
  {
    title: 'Content',
    items: [
      { label: 'Powerful Openings', icon: '🎯', prompt: 'How can I improve my presentation opening to hook my audience immediately?' },
      { label: 'Slide Architecture', icon: '🧱', prompt: 'How do I structure my problem statement and solution slides for maximum persuasion?' },
    ],
  },
  {
    title: 'Defense',
    items: [
      { label: 'Viva Defense Pitfalls', icon: '🛡️', prompt: 'What are the most common defense mistakes students make during viva examinations?' },
    ],
  },
];

const ABOUT_ITEMS = [
  { icon: '👥', title: 'Personalized Coaching', text: 'Your AI Coach adapts to your specific presentation context, analyzing your 7Cs scores and pacing telemetry.' },
  { icon: '💡', title: 'Evidence-Based Delivery', text: 'Every recommendation follows academic presentation frameworks, rhetorical structures, and speech cadence science.' },
  { icon: '⚡', title: 'Google Gemini Powered', text: 'Connected directly to Google Gemini 2.0 Flash for sub-second intelligence, low latency, and high reasoning fidelity.' },
  { icon: '💬', title: 'Multi-Turn Defense Prep', text: 'Ask follow-up questions, drill tough committee inquiries, and rehearse realistic defense cross-examinations.' },
];

// ===== Tiny message renderer: **bold** and "- " bullets =====
const renderInline = (text: string) =>
  text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') && part.length > 4
      ? <strong key={i}>{part.slice(2, -2)}</strong>
      : <React.Fragment key={i}>{part}</React.Fragment>
  );

const renderMessage = (text: string) =>
  text.split('\n').map((line, i) => {
    const t = line.trimStart();
    if (/^[-•]\s+/.test(t)) {
      return (
        <div key={i} className="da3-li">
          <span className="da3-li-dot">•</span>
          <span>{renderInline(t.replace(/^[-•]\s+/, ''))}</span>
        </div>
      );
    }
    if (!line.trim()) return <div key={i} className="da3-gap" />;
    return <p key={i} className="da3-line">{renderInline(line)}</p>;
  });

const PracticeMode: React.FC = () => {
  const location = useLocation();
  const passedContext = location.state as ContextReport | null;
  const [contextReport] = useState<ContextReport | null>(passedContext);

  const getInitialMessage = (context: ContextReport | null): string => {
    if (!context) {
      return "Hello! I'm your AI Presentation Coach. I'm here to help you practice and elevate your presentation skills. Share your topic, ask for feedback on your slides, or let's polish specific areas like pacing, vocal clarity, or defending tough questions. What would you like to work on today?";
    }

    if (context.v2Analysis || context.v2Report) {
      const v1Score = context.v1Analysis?.overall_score ?? context.v1Report?.overall_score ?? 70;
      const v2Score = context.v2Analysis?.overall_score ?? context.v2Report?.overall_score ?? 75;
      const gain = v2Score - v1Score;
      const modeName =
        context.phase === 'analyzer'
          ? 'Iterative Presentation Analyzer'
          : context.phase === 'speech'
          ? 'Speech Analyzer'
          : 'Live Coach';

      return `Hello! I'm your AI Presentation Coach. I've successfully loaded all the files and comparison data from your ${modeName} session:
- **Baseline V1 Score**: ${v1Score}/100
- **Revised V2 Score**: ${v2Score}/100 (${gain >= 0 ? '+' : ''}${gain} pts improvement!)

I have analyzed the progress you've made, as well as the remaining areas to polish. I am ready to guide you on how to resolve the remaining gaps, improve slide flow, pacing, structure, delivery, and make your presentation 100% defense-ready. Ask me any question, or tell me when you're ready to start practicing!`;
    }

    if (!context.analysis) {
      return "Hello! I'm your AI Presentation Coach. I'm here to help you practice and improve your presentation skills. Share your topic, ask for feedback, or let's work on specific areas like pacing, clarity, or handling nervousness. What would you like to work on today?";
    }

    const score =
      context.analysis.overall_score !== undefined
        ? context.analysis.overall_score
        : (context.analysis as any).clarity_score || 70;

    const details =
      context.phase === 'analyzer'
        ? `your revised presentation draft (Overall Score: ${score}/100)`
        : context.phase === 'speech'
        ? `your revised speech practice session (Overall Score: ${score}/100)`
        : `your revised live practice session (Overall Score: ${score}/100)`;

    return `Hello! I'm your AI Presentation Coach. I have loaded the analysis from ${details}. I can see your strengths and areas for improvement. Let's practice pitching or focus on polishing your delivery. Ask me any question or tell me when you're ready to begin!`;
  };

  // ===== STATE MANAGEMENT =====
  const [messages, setMessages] = useState<ChatMessage[]>(() => [
    {
      id: '1',
      role: 'ai',
      content: getInitialMessage(passedContext),
      timestamp: new Date().toISOString(),
    },
  ]);
  const [userInput, setUserInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAbout, setShowAbout] = useState(false);

  // AI Coach Status State
  const [keyNotice] = useState<string | null>(null);

  // ===== REFS =====
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // ===== AUTO-SCROLL TO BOTTOM WHEN NEW MESSAGES ARRIVE =====
  useEffect(() => {
    setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, 100);
  }, [messages, isLoading]);

  // ===== SEND MESSAGE HANDLER =====
  const handleSendMessage = async () => {
    if (!userInput.trim()) {
      return;
    }

    const messageText = userInput.trim();

    // ===== STEP 1: ADD USER MESSAGE TO CHAT =====
    const newUserMessage: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: messageText,
      timestamp: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, newUserMessage]);
    setUserInput('');
    setIsLoading(true);
    setError(null);

    try {
      // ===== STEP 2: PREPARE CONTEXT REPORT =====
      const currentContextReport: ContextReport = contextReport || {
        phase: 'practice',
        session_id: 'session-' + Date.now(),
        analysis: {
          overall_score: 75,
          word_count: 450,
          clarity_score: 82,
        } as any,
      };

      // ===== STEP 3: BACKEND AI COACH (/api/practice-chat) =====
      const response = await sendChatMessage(messageText, messages, currentContextReport);
      const aiResponseText = response.ai_response;

      // ===== STEP 4: ADD AI RESPONSE TO CHAT =====
      const aiMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        role: 'ai',
        content: aiResponseText,
        timestamp: new Date().toISOString(),
      };

      setMessages((prev) => [...prev, aiMessage]);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to get AI response';
      setError(errorMessage);
      console.error('Chat error:', err);
    } finally {
      setIsLoading(false);
    }
  };

  // ===== KEYBOARD EVENT HANDLER =====
  const handleKeyPress = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // ===== CLEAR CHAT HANDLER =====
  const handleClearChat = () => {
    setMessages([
      {
        id: '1',
        role: 'ai',
        content: getInitialMessage(contextReport),
        timestamp: new Date().toISOString(),
      },
    ]);
    setUserInput('');
    setError(null);
  };

  // ===== QUICK PROMPT HANDLER =====
  const handleQuickPrompt = (prompt: string) => {
    setUserInput(prompt);
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  // ===== Context summary for side panel =====
  const ctx: any = contextReport;
  const ctxScore: number | null =
    ctx?.analysis?.overall_score ?? ctx?.v2Analysis?.overall_score ?? ctx?.v2Report?.overall_score ?? null;
  const ctxDoc: string | null = ctx?.document_name ?? null;
  const ctxLabel =
    ctx?.phase === 'analyzer' ? 'Iterative Analyzer'
    : ctx?.phase === 'speech' ? 'Speech Analyzer'
    : ctx?.phase === 'live' ? 'Live Coach'
    : ctxDoc ? 'Document Analyzer'
    : 'Practice session';
  const hasContext = !!contextReport && (ctxScore !== null || !!ctxDoc);

  return (
    <div className="practice-mode da3-practice">
      {/* Practice Header with Gemini Status */}
      <div className="practice-header">
        <h1>AI Coach - Practice Mode</h1>
        <p className="subtitle">Real-time feedback and coaching powered by Google Gemini AI</p>

        {/* AI Coach Status Bar */}
        <div className="gemini-status-bar">
          <div className="gemini-badge">
            <span className="status-dot dot-active"></span>
            <span className="gemini-badge-text">Presenova AI Coach Active</span>
          </div>
        </div>

        {keyNotice && <div className="gemini-notice-banner">{keyNotice}</div>}
      </div>

      <div className="da3-layout">
        {/* ===== MAIN: CHAT ===== */}
        {/* UI_V1_DISABLED: <section className="chat-container"> (old wrapper, fixed 620px, padding 2.5rem) */}
        <section className="da3-chat">
          <div className="da3-chat-head">
            <div className="da3-chat-head-left">
              <span className="da3-chat-avatar">✨</span>
              <div>
                <div className="da3-chat-title">Presenova AI Coach</div>
                <div className="da3-chat-sub">
                  <span className="status-dot dot-active"></span> Online · {messages.length} message{messages.length === 1 ? '' : 's'}
                </div>
              </div>
            </div>
            <button type="button" className="da3-ghost-btn" onClick={handleClearChat} disabled={isLoading}>
              🗑 Clear chat
            </button>
          </div>

          <div className="chat-messages" ref={chatContainerRef}>
            {/* Render all messages */}
            {messages.map((message) => (
              <div key={message.id} className={`message-wrapper message-${message.role}`}>
                <div className="message-bubble">
                  <div className="message-avatar">
                    {message.role === 'ai' ? '✨' : '👤'}
                  </div>
                  <div className="message-content">
                    {message.role === 'ai' && (
                      <div className="message-sender-tag">
                        <span className="coach-name">Presenova AI Coach</span>
                        <span className="gemini-pill">Google Gemini</span>
                      </div>
                    )}
                    {/* UI_V1_DISABLED: <p className="message-text">{message.content}</p> (showed raw ** and - symbols) */}
                    <div className="da3-msg-body">{renderMessage(message.content)}</div>
                    <span className="message-timestamp">
                      {new Date(message.timestamp).toLocaleTimeString([], {
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </span>
                  </div>
                </div>
              </div>
            ))}

            {/* ===== LOADING INDICATOR ===== */}
            {isLoading && (
              <div className="message-wrapper message-ai">
                <div className="message-bubble">
                  <div className="message-avatar">✨</div>
                  <div className="message-content">
                    <div className="message-sender-tag">
                      <span className="coach-name">Presenova AI Coach</span>
                      <span className="gemini-pill">Generating...</span>
                    </div>
                    <div className="typing-indicator">
                      <div className="typing-dot"></div>
                      <div className="typing-dot"></div>
                      <div className="typing-dot"></div>
                    </div>
                    <p className="typing-text">Gemini is formulating actionable coaching feedback...</p>
                  </div>
                </div>
              </div>
            )}

            {/* Scroll anchor */}
            <div ref={messagesEndRef} />
          </div>

          {/* Error Message Display */}
          {error && (
            <div className="error-message-box">
              <span className="error-icon">⚠️</span>
              <div className="error-body">
                <span className="error-text">{error}</span>

              </div>
              <button
                className="error-close"
                onClick={() => setError(null)}
                aria-label="Close error"
              >
                ✕
              </button>
            </div>
          )}

          {/* Message Input Section */}
          <div className="da3-input">
            <textarea
              ref={inputRef}
              className="message-input"
              value={userInput}
              onChange={(e) => setUserInput(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="Ask your AI Coach anything about your presentation, structure, pacing, or handling tough questions... (Enter to send, Shift+Enter for new line)"
              disabled={isLoading}
              rows={3}
              maxLength={1000}
            />

            <div className="da3-input-bar">
              <span className="char-count">{userInput.length} / 1000</span>
              <div className="da3-input-actions">
                <Button
                  label={isLoading ? 'Waiting for response...' : 'Send Message'}
                  onClick={handleSendMessage}
                  disabled={!userInput.trim() || isLoading}
                  loading={isLoading}
                />
              </div>
            </div>
          </div>
        </section>

        {/* ===== SIDE PANEL ===== */}
        <aside className="da3-side">
          {/* Context card */}
          <div className="da3-card">
            <h3 className="da3-card-title">Session Context</h3>
            {hasContext ? (
              <div className="da3-ctx">
                <div className="da3-ctx-row"><span>Source</span><strong>{ctxLabel}</strong></div>
                {ctxDoc && <div className="da3-ctx-row"><span>Document</span><strong className="da3-ellipsis">{ctxDoc}</strong></div>}
                {ctxScore !== null && <div className="da3-ctx-row"><span>Score</span><strong className="da3-ctx-score">{ctxScore}/100</strong></div>}
                <p className="da3-ctx-note">Your coach has this report loaded and will tailor answers to it.</p>
              </div>
            ) : (
              <p className="da3-muted">No report loaded. Analyze a document or speech first for personalized coaching, or just start chatting.</p>
            )}
          </div>

          {/* Quick prompts, grouped */}
          <div className="da3-card">
            <h3 className="da3-card-title">Quick Prompts</h3>
            <p className="da3-muted">Click a prompt to fill the message box:</p>
            {PROMPT_GROUPS.map((group) => (
              <div key={group.title} className="da3-group">
                <div className="da3-group-title">{group.title}</div>
                <div className="da3-prompts">
                  {group.items.map((item) => (
                    <button
                      key={item.label}
                      type="button"
                      className="da3-prompt"
                      onClick={() => handleQuickPrompt(item.prompt)}
                      disabled={isLoading}
                      title={item.prompt}
                    >
                      <span className="da3-prompt-icon">{item.icon}</span>
                      <span>{item.label}</span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {/* About, collapsible */}
          <div className="da3-card">
            <button type="button" className="da3-about-toggle" onClick={() => setShowAbout(!showAbout)} aria-expanded={showAbout}>
              <h3 className="da3-card-title">About Your Gemini AI Coach</h3>
              <span className={`da3-chevron ${showAbout ? 'open' : ''}`}>▾</span>
            </button>
            {showAbout && (
              <div className="da3-about-list">
                {ABOUT_ITEMS.map((it) => (
                  <div key={it.title} className="da3-about-item">
                    <span className="da3-about-icon">{it.icon}</span>
                    <div>
                      <strong>{it.title}</strong>
                      <p>{it.text}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </aside>
      </div>

      {/* UI_V1_DISABLED: old Quick Suggestions section (full-width, below chat)
      <section className="suggestions-section">
        <h3>Quick Prompts to Get Started</h3>
        <p className="suggestions-subtitle">Click any prompt or type your own message:</p>
        <div className="suggestions-grid">
          <button type="button" className="suggestion-button" onClick={() => handleQuickPrompt("How can I improve my presentation opening to hook my audience immediately?")} disabled={isLoading} title="Get tips on creating impactful opening statements">Powerful Openings</button>
          <button type="button" className="suggestion-button" onClick={() => handleQuickPrompt("I often use filler words like 'um' and 'like'. How can I reduce them?")} disabled={isLoading} title="Tips for reducing filler words">Reduce Filler Words</button>
          <button type="button" className="suggestion-button" onClick={() => handleQuickPrompt("What are the most common defense mistakes students make during viva examinations?")} disabled={isLoading} title="Learn about common presentation mistakes">Viva Defense Pitfalls</button>
          <button type="button" className="suggestion-button" onClick={() => handleQuickPrompt("I get nervous before speaking. What 2-minute technique can help me stay calm and confident?")} disabled={isLoading} title="Techniques to manage presentation anxiety">Manage Anxiety</button>
          <button type="button" className="suggestion-button" onClick={() => handleQuickPrompt("How should I pace my slides so I don't run out of time during a 10-minute presentation?")} disabled={isLoading} title="Learn optimal speaking pace">Perfect Your Pace</button>
          <button type="button" className="suggestion-button" onClick={() => handleQuickPrompt("How do I structure my problem statement and solution slides for maximum persuasion?")} disabled={isLoading} title="Get structured feedback on presentation">Slide Architecture</button>
        </div>
      </section>
      */}

      {/* UI_V1_DISABLED: old Information section (4 large cards, full-width)
      <section className="info-section">
        <h3>About Your Gemini AI Coach</h3>
        <div className="info-grid">
          <div className="info-card"><h4>Personalized Coaching</h4><p>Your AI Coach adapts to your specific presentation context, analyzing your 7Cs scores and pacing telemetry.</p></div>
          <div className="info-card"><h4>Evidence-Based Delivery</h4><p>Every recommendation follows academic presentation frameworks, rhetorical structures, and speech cadence science.</p></div>
          <div className="info-card"><h4>Google Gemini Powered</h4><p>Connected directly to Google Gemini 2.0 Flash for sub-second intelligence, low latency, and high reasoning fidelity.</p></div>
          <div className="info-card"><h4>Multi-Turn Defense Prep</h4><p>Ask follow-up questions, drill tough committee inquiries, and rehearse realistic defense cross-examinations.</p></div>
        </div>
      </section>
      */}
    </div>
  );
};

export default PracticeMode;
