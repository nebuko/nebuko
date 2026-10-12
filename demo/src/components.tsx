import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig, spring} from 'remotion';
import {ThreadEvent} from './data';

// Tokens GitHub (dark) — Design system Primer
export const GH = {
  bg: '#0d1117',
  surface: '#161b22',
  surfaceHover: '#21262d',
  border: '#30363d',
  text: '#e6edf3',
  muted: '#8b949e',
  accent: '#58a6ff',
  red: '#f85149',
  yellow: '#d29922',
  green: '#3fb950',
  purple: '#bc8cff',
  font: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif',
  mono: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
};

// Topbar globale
export const GithubHeader: React.FC = () => (
  <div style={{
    height: 52, background: GH.surface, borderBottom: '1px solid ' + GH.border,
    display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 24px',
    fontFamily: GH.font, color: GH.text, fontSize: 14, flexShrink: 0
  }}>
    <div style={{display: 'flex', alignItems: 'center', gap: 16}}>
      <svg height="28" viewBox="0 0 16 16" version="1.1" width="28" fill="#fff">
        <path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.84 1.13.19.43.6.81 1.55.55 0 .94.01 1.8.01 2.04 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z"></path>
      </svg>
      <span style={{fontWeight: 600, fontSize: 14}}>Dashboard</span>
      <span style={{color: GH.muted}}>/</span>
      <span>Pull requests</span>
      <span>Issues</span>
    </div>
    <div style={{display: 'flex', alignItems: 'center', gap: 12}}>
      <div style={{
        background: GH.bg, border: '1px solid ' + GH.border, borderRadius: 6,
        padding: '3px 10px', color: GH.muted, display: 'flex', alignItems: 'center', gap: 24, fontSize: 12
      }}>
        <span>Type `/` to search</span>
        <span style={{fontSize: 10, border: '1px solid ' + GH.border, borderRadius: 3, padding: '1px 4px'}}>⌘K</span>
      </div>
    </div>
  </div>
);

// Contenu initial / Description de l'issue
export const GithubIssueMainPost: React.FC<{
  author: string;
  avatar: string;
  time: string;
  body: string;
  code?: string;
}> = ({author, avatar, time, body, code}) => {
  return (
    <div style={{display: 'flex', gap: 16, position: 'relative', zIndex: 1}}>
      <div style={{
        width: 40, height: 40, borderRadius: 6, flexShrink: 0,
        background: '#1f6feb', border: '1px solid ' + GH.border,
        display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 20, zIndex: 2
      }}>
        {avatar}
      </div>

      <div style={{
        flex: 1, minWidth: 0, background: GH.surface, border: '1px solid ' + GH.border,
        borderRadius: 6, overflow: 'hidden'
      }}>
        {/* En-tête avec badge Author */}
        <div style={{
          background: GH.surfaceHover, padding: '10px 16px', borderBottom: '1px solid ' + GH.border,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontFamily: GH.font
        }}>
          <div style={{display: 'flex', alignItems: 'center', gap: 8}}>
            <span style={{fontSize: 14, fontWeight: 600, color: GH.text}}>{author}</span>
            <span style={{fontSize: 13, color: GH.muted}}>opened this issue {time}</span>
          </div>
          <div style={{display: 'flex', alignItems: 'center', gap: 8}}>
            <span style={{
              fontSize: 12, color: GH.muted, border: '1px solid ' + GH.border,
              borderRadius: 12, padding: '1px 8px', fontWeight: 500
            }}>
              Author
            </span>
            <span style={{color: GH.muted, fontSize: 14}}>⋯</span>
          </div>
        </div>

        {/* Corps de l'issue */}
        <div style={{padding: 16, display: 'flex', flexDirection: 'column', gap: 12, fontFamily: GH.font}}>
          <div style={{fontSize: 14, color: GH.text, lineHeight: 1.6, whiteSpace: 'pre-wrap'}}>
            {body}
          </div>

          {code ? (
            <pre style={{
              margin: '8px 0 0 0', padding: 12, borderRadius: 6, background: GH.bg,
              border: '1px solid ' + GH.border, fontFamily: GH.mono, fontSize: 13,
              color: GH.text, whiteSpace: 'pre-wrap', overflow: 'hidden'
            }}>{code}</pre>
          ) : null}
        </div>
      </div>
    </div>
  );
};

export const IssueFrame: React.FC<{
  children: React.ReactNode;
  commentCount?: number;
}> = ({ children, commentCount = 4 }) => (
  <AbsoluteFill style={{ background: GH.bg, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
    <GithubHeader />

    {/* Header Repo & Titre d'issue */}
    <div style={{
      padding: '20px 32px 16px 32px', background: GH.bg, borderBottom: '1px solid ' + GH.border,
      display: 'flex', flexDirection: 'column', gap: 10, flexShrink: 0
    }}>
      <div style={{ fontFamily: GH.font, fontSize: 14, color: GH.muted, display: 'flex', alignItems: 'center', gap: 6 }}>
        <span>⬡ nebuko</span>
        <span>/</span>
        <span style={{ color: GH.text, fontWeight: 500 }}>nebuko</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ fontFamily: GH.font, fontSize: 24, color: GH.text, fontWeight: 600 }}>
          Allow to improve circuit breaker for one issue
        </span>
        <span style={{ fontFamily: GH.font, fontSize: 22, color: GH.muted, fontWeight: 300 }}>
          #839
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 2 }}>
        <span style={{
          fontFamily: GH.font, fontSize: 13, fontWeight: 600, color: '#fff',
          background: GH.green, borderRadius: 20, padding: '3px 12px', display: 'flex', alignItems: 'center', gap: 6
        }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#fff' }} /> Open
        </span>
        <span style={{ fontFamily: GH.font, fontSize: 13, color: GH.muted }}>
          <strong style={{ color: GH.text }}>alex-dev</strong> opened this issue 3 hours ago · {commentCount} comments
        </span>
      </div>
    </div>

    {/* Navigation Tabs */}
    <div style={{ display: 'flex', gap: 24, padding: '0 32px', borderBottom: '1px solid ' + GH.border, fontFamily: GH.font, fontSize: 14, flexShrink: 0 }}>
      {[
        { label: '💬 Conversation', count: commentCount },
        { label: '📝 Commits', count: 12 },
        { label: '🔀 Changes', count: 3 },
        { label: '🔗 Checks', count: null }
      ].map((t, i) => (
        <div key={t.label} style={{
          padding: '10px 0', display: 'flex', alignItems: 'center', gap: 6,
          color: i === 0 ? GH.text : GH.muted, fontWeight: i === 0 ? 600 : 400,
          borderBottom: i === 0 ? '2px solid ' + GH.yellow : '2px solid transparent'
        }}>
          <span>{t.label}</span>
          {t.count !== null && (
            <span style={{ background: GH.surfaceHover, padding: '1px 6px', borderRadius: 10, fontSize: 12 }}>
              {t.count}
            </span>
          )}
        </div>
      ))}
    </div>

    {/* Content Area */}
    <div style={{ flex: 1, display: 'flex', padding: '24px 32px', gap: 32, overflow: 'hidden' }}>
      {/* Left Column: Issue Thread */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', position: 'relative' }}>
        
        {/* Zone de défilement du fil */}
        <div style={{ flex: 1, overflow: 'hidden' }}>
          {children}
        </div>

        {/* Champ de commentaire fixe */}
        <div style={{
          marginTop: 16, borderRadius: 6, border: '1px solid ' + GH.border,
          background: GH.surface, padding: '12px 16px', fontFamily: GH.font, fontSize: 14,
          color: GH.muted, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0
        }}>
          <span>Leave a comment</span>
          <div style={{ display: 'flex', gap: 8, fontSize: 12, background: GH.bg, padding: '2px 6px', borderRadius: 4, border: '1px solid ' + GH.border }}>
            Markdown supported
          </div>
        </div>
      </div>

      {/* Right Column: Sidebar */}
      <div style={{ width: 280, display: 'flex', flexDirection: 'column', gap: 20, fontFamily: GH.font, fontSize: 13, flexShrink: 0 }}>
        <div style={{ borderBottom: '1px solid ' + GH.border, paddingBottom: 16 }}>
          <div style={{ color: GH.muted, fontWeight: 600, marginBottom: 8, display: 'flex', justifyContent: 'space-between' }}>
            <span>Assignees</span>
            <span>⚙️</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: GH.text, fontWeight: 500 }}>
            <div style={{ width: 20, height: 20, borderRadius: '50%', background: GH.accent }} />
            <span>alex-dev</span>
          </div>
        </div>

        <div style={{ borderBottom: '1px solid ' + GH.border, paddingBottom: 16 }}>
          <div style={{ color: GH.muted, fontWeight: 600, marginBottom: 8, display: 'flex', justifyContent: 'space-between' }}>
            <span>Labels</span>
            <span>⚙️</span>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <span style={{ background: '#d73a4a', color: '#fff', padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600 }}>
              bug
            </span>
            <span style={{ background: '#007ec6', color: '#fff', padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600 }}>
              circuit-breaker
            </span>
          </div>
        </div>
      </div>
    </div>
  </AbsoluteFill>
);

export const GithubComment: React.FC<{
  event: ThreadEvent;
  animate?: boolean;
  gate?: boolean;
}> = ({event, animate, gate}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = animate ? spring({frame: frame - 8, fps, config: {damping: 200}}) : 1;
  const isHuman = event.kind === 'trigger' || event.kind === 'approve';

  return (
    <div style={{
      display: 'flex', gap: 16, position: 'relative', zIndex: 1,
      opacity: p, transform: `translateY(${(1 - p) * 15}px)`
    }}>
      {/* Avatar */}
      <div style={{
        width: 40, height: 40, borderRadius: 6, flexShrink: 0,
        background: isHuman ? '#1f6feb' : GH.surfaceHover, border: '1px solid ' + GH.border,
        display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 20, zIndex: 2
      }}>
        {event.avatar}
      </div>

      {/* Boîte du commentaire */}
      <div style={{
        flex: 1, minWidth: 0, background: GH.surface, border: '1px solid ' + GH.border,
        borderRadius: 6, overflow: 'hidden'
      }}>
        {/* En-tête */}
        <div style={{
          background: GH.surfaceHover, padding: '10px 16px', borderBottom: '1px solid ' + GH.border,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontFamily: GH.font
        }}>
          <div style={{display: 'flex', alignItems: 'center', gap: 8}}>
            <span style={{fontSize: 14, fontWeight: 600, color: GH.text}}>
              {event.author}
            </span>
            <span style={{fontSize: 13, color: GH.muted}}>commented {event.time}</span>
            {event.pinned ? (
              <span style={{
                fontSize: 11, color: GH.yellow, border: '1px solid #d2992240', background: '#d2992215',
                borderRadius: 4, padding: '0 6px', fontWeight: 500
              }}>📌 Pinned</span>
            ) : null}
          </div>
          <div style={{color: GH.muted, fontSize: 14}}>⋯</div>
        </div>

        {/* Contenu */}
        <div style={{padding: 16, display: 'flex', flexDirection: 'column', gap: 10}}>
          <div style={{fontSize: 16, fontWeight: 600, fontFamily: isHuman ? GH.mono : GH.font, color: isHuman ? GH.accent : GH.text}}>
            {event.title}
          </div>
          <div style={{fontSize: 14, color: '#c9d1d9', lineHeight: 1.5, fontFamily: GH.font}}>
            {event.body}
          </div>

          {/* Bloc code */}
          {event.code ? (
            <pre style={{
              margin: '6px 0 0 0', padding: 12, borderRadius: 6, background: GH.bg,
              border: '1px solid ' + GH.border, fontFamily: GH.mono, fontSize: 13,
              color: '#7ee787', whiteSpace: 'pre-wrap', overflow: 'hidden'
            }}>{event.code}</pre>
          ) : null}

          {/* State Label */}
          {event.stateLabel ? (
            <div style={{
              marginTop: 6, borderRadius: 6, border: `1px solid ${gate ? GH.red : GH.border}`,
              background: GH.bg, padding: '10px 14px'
            }}>
              <div style={{
                display: 'flex', alignItems: 'center', gap: 6,
                fontFamily: GH.font, fontSize: 13, fontWeight: 600,
                color: gate ? GH.red : GH.muted
              }}>
                <span style={{fontSize: 10}}>▼</span> Workflow State (v10)
              </div>
              <div style={{marginTop: 4, fontFamily: GH.mono, fontSize: 12, color: GH.muted}}>
                {event.stateLabel}
              </div>
            </div>
          ) : null}

          {/* Métriques */}
          {event.metrics ? (
            <div style={{
              marginTop: 6, display: 'flex', gap: 16,
              fontFamily: GH.mono, fontSize: 12, color: GH.muted
            }}>
              <span>↑ {event.metrics.input} in</span>
              <span>↓ {event.metrics.output} out</span>
              {event.metrics.toolCalls ? <span>🔧 {event.metrics.toolCalls}</span> : null}
              {event.metrics.runtime ? <span>⏱ {event.metrics.runtime}</span> : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
};