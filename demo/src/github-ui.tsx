import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {ThemeName, ThemeProvider, THEMES, useGH} from './github-theme';
import {
  CodeBlock,
  Collapse,
  CommentShell,
  Composer,
  GithubAlert,
  GithubHeader,
  Initial,
  inlineCodeStyle,
  Label,
  Markdown,
  Pointer,
  prettyJson,
  RepoHeader,
  SideSection,
  StatusBadge,
  ThreadColumn,
  TitleBlock,
  useEnter,
} from './github-core';
import {IconChevronRight, IconPin, IconPullRequest} from './github-icons';
import {ThreadEvent} from './data';

// Everything lives in small files; this one re-exports it all so
// `import {…} from './github-ui'` keeps working.
export * from './github-theme';
export * from './github-icons';
export * from './github-core';
export * from './github-pr';

/* =====================================================================
 * Issue description (first comment). Also used for the PR description.
 * ===================================================================== */
export const GithubIssueMainPost: React.FC<{
  author: string;
  avatar: string;
  time: string;
  body: string;
  code?: string;
  /** header verb: "opened this issue" (default) or "commented" for a PR */
  verb?: string;
  /** false for an agent: round → rounded-square avatar, `bot` badge, neutral header */
  human?: boolean;
}> = ({author, avatar, time, body, code, verb = 'opened this issue', human = true}) => (
  <CommentShell
    avatar={avatar}
    human={human}
    isBot={!human}
    name={author}
    verb={verb}
    time={time}
    role={human ? 'Author' : null}
  >
    <Markdown text={body} />
    {code ? <CodeBlock code={code} /> : null}
  </CommentShell>
);

/* =====================================================================
 * Agent / human comment in the thread
 * ===================================================================== */
type EventExtras = {
  reactions?: {emoji: string; count: number; mine?: boolean}[];
};

const RUN_LINK = 'View workflow run logs';

/** "Nebuko dev agent started — View workflow run logs" → last part rendered as a link. */
const renderRunTitle = (title: string, linkColor: string): React.ReactNode => {
  const i = title.lastIndexOf(RUN_LINK);
  if (i < 0) return title;
  return (
    <>
      {title.slice(0, i)}
      <span style={{color: linkColor}}>{RUN_LINK}</span>
      {title.slice(i + RUN_LINK.length)}
    </>
  );
};

/** `WORKFLOW_STATE · step: plan · dispatch: HOLD` → one chip per segment. */
const StateBadge: React.FC<{label: string}> = ({label}) => {
  const t = useGH();
  const parts = label.split(' · ').map((s) => s.trim()).filter(Boolean);
  return (
    <div style={{display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center'}}>
      {parts.map((part, i) => {
        const hold = /\bHOLD\b/.test(part);
        const head = i === 0;
        return (
          <span
            key={i}
            style={{
              fontFamily: t.mono,
              fontSize: 12,
              lineHeight: '20px',
              padding: '0 8px',
              borderRadius: 999,
              border: '1px solid ' + (hold ? t.attentionBorder : t.border),
              background: hold ? t.attentionBg : head ? t.surface : 'transparent',
              color: hold ? t.attentionFg : head ? t.muted : t.text,
              fontWeight: head || hold ? 600 : 400,
              whiteSpace: 'nowrap',
            }}
          >
            {part}
          </span>
        );
      })}
    </div>
  );
};

export const GithubComment: React.FC<{
  event: ThreadEvent & EventExtras;
  animate?: boolean;
  gate?: boolean;
  gateTitle?: string;
  gateText?: string;
  stateOpen?: boolean;
}> = ({event, animate, gate, gateTitle, gateText, stateOpen = false}) => {
  if (!event) {
    throw new Error(
      '<GithubComment> received no `event`. Check the event id: ' +
        'pick an event from `events` (or `eventById(id)`) in "./data".',
    );
  }
  const t = useGH();
  const frame = useCurrentFrame();
  const p = useEnter(animate);
  // short blue halo when a new comment lands (draws the eye in a video)
  const ring = animate ? interpolate(frame, [8, 18, 55], [0, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 0;
  const isHuman = event.kind === 'trigger' || event.kind === 'approve';
  // "<agent> started — View workflow run logs": only a title, shown as a plain line
  const compact = !isHuman && !event.body && !event.code && !event.stateLabel;
  const stateIsJson = !!event.stateLabel && /^\s*[{[]/.test(event.stateLabel);

  const footer = event.metrics ? (
    <div
      style={{
        borderTop: '1px solid ' + (isHuman ? t.accentBorder : t.border),
        background: t.surface,
        padding: '8px 16px',
        display: 'flex',
        gap: 16,
        fontFamily: t.mono,
        fontSize: 12,
        color: t.muted,
      }}
    >
      <span>↑ {event.metrics.input} in</span>
      <span>↓ {event.metrics.output} out</span>
      {event.metrics.toolCalls ? (
        <span>
          {'>_'} {event.metrics.toolCalls}
        </span>
      ) : null}
      {event.metrics.runtime ? <span>{event.metrics.runtime}</span> : null}
    </div>
  ) : null;

  return (
    <CommentShell
      avatar={event.avatar || '⚙️'}
      human={isHuman}
      name={event.author.trim()}
      verb="commented"
      time={event.time}
      isBot={!isHuman}
      role={isHuman ? 'Author' : null}
      opacity={p}
      translateY={(1 - p) * 15}
      ring={ring}
      headerExtra={
        event.pinned ? (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              marginLeft: 4,
              fontSize: 12,
              fontWeight: 500,
              color: t.attentionFg,
              border: '1px solid ' + t.attentionBorder,
              background: t.attentionBg,
              borderRadius: 999,
              padding: '0 8px',
              lineHeight: '18px',
            }}
          >
            <IconPin size={12} /> Pinned
          </span>
        ) : null
      }
      footer={footer}
    >
      {event.title ? (
        isHuman ? (
          <div>
            <code style={{...inlineCodeStyle(t), fontSize: 13}}>{event.title}</code>
          </div>
        ) : compact ? (
          <div style={{color: t.textSoft}}>{renderRunTitle(event.title, t.accent)}</div>
        ) : (
          <div style={{fontSize: 16, fontWeight: 600}}>{event.title}</div>
        )
      ) : null}

      {event.body ? <Markdown text={event.body} /> : null}

      {event.code ? <CodeBlock code={event.code} /> : null}

      {gate ? (
        <GithubAlert kind="warning" title={gateTitle ?? 'Waiting for human approval'}>
          {gateText ?? 'The workflow is paused. Reply with an approval comment to continue.'}
        </GithubAlert>
      ) : null}

      {event.stateLabel && !stateIsJson ? <StateBadge label={event.stateLabel} /> : null}

      {event.stateLabel && stateIsJson ? (
        <div>
          <div style={{display: 'flex', alignItems: 'center', gap: 6, color: t.text, cursor: 'pointer'}}>
            <IconChevronRight size={16} color={t.muted} style={{transform: stateOpen ? 'rotate(90deg)' : 'none'}} />
            <span>Workflow State ({event.stateVersion ?? 'v10'})</span>
          </div>
          {stateOpen ? <CodeBlock code={prettyJson(event.stateLabel)} style={{marginTop: 8}} /> : null}
        </div>
      ) : null}

      {event.reactions && event.reactions.length > 0 ? (
        <div style={{display: 'flex', gap: 8}}>
          {event.reactions.map((r) => (
            <span
              key={r.emoji}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                height: 26,
                padding: '0 8px',
                borderRadius: 999,
                fontSize: 12,
                fontWeight: 500,
                color: r.mine ? t.accent : t.muted,
                background: r.mine ? t.accentMuted : 'transparent',
                border: '1px solid ' + (r.mine ? t.accentBorder : t.border),
              }}
            >
              <span style={{fontSize: 14}}>{r.emoji}</span>
              {r.count}
            </span>
          ))}
        </div>
      ) : null}
    </CommentShell>
  );
};

/* =====================================================================
 * Issue page frame
 * ===================================================================== */
export const IssueFrame: React.FC<{
  children: React.ReactNode;
  theme?: ThemeName;
  commentCount?: number;
  title?: string;
  number?: number | string;
  repoOwner?: string;
  repoName?: string;
  author?: string;
  openedAgo?: string;
  state?: 'open' | 'closed';
  labels?: {name: string; color: string}[];
  assignees?: string[];
  /** `pointer`: show the cursor on the PR (a number = frame at which it flies in) */
  linkedPr?: {number: number | string; title: string; merged?: boolean; pointer?: boolean | number} | null;
  participants?: string[];
  /** 'bottom' pins the newest comment to the bottom and clips older ones, chat-style. */
  anchor?: 'top' | 'bottom';
  /** Text shown in the "Add a comment" box (drive it frame by frame for a typing effect). */
  composerText?: string;
  /** Absolute frame at which the cursor clicks the composer's "Comment" button. */
  composerPointerAt?: number | null;
  /** 0 = full page, 1 = GitHub header and repo tabs folded away, title shrunk to a sticky bar. */
  collapse?: number;
  /** Content of a reserved band between the newest comment and the composer (e.g. an explainer card). */
  narration?: React.ReactNode;
  narrationHeight?: number;
  /** Scroll the thread from its top (0) to its bottom (1), for content taller than the screen. */
  scroll?: number;
  /** Push the thread down by this many px, and a ref to its visible area (see ThreadColumn). */
  threadOffset?: number;
  threadViewportRef?: React.Ref<HTMLDivElement>;
  /** false hides the right sidebar so the thread gets the full width */
  sidebar?: boolean;
  showRepoChrome?: boolean;
}> = ({
  children,
  theme = 'light',
  commentCount = 4,
  title = 'Allow to improve circuit breaker for one issue',
  number = 839,
  repoOwner = 'nebuko',
  repoName = 'nebuko',
  author = 'alex-dev',
  openedAgo = '3 hours ago',
  state = 'open',
  labels = [
    {name: 'bug', color: '#d73a4a'},
    {name: 'circuit-breaker', color: '#007ec6'},
  ],
  assignees = ['alex-dev'],
  linkedPr = null,
  participants = [],
  anchor = 'top',
  composerText,
  composerPointerAt = null,
  collapse = 0,
  narration,
  narrationHeight = 0,
  scroll,
  threadOffset,
  threadViewportRef,
  sidebar = true,
  showRepoChrome = true,
}) => {
  const t = THEMES[theme];
  return (
    <ThemeProvider theme={theme}>
      <AbsoluteFill style={{background: t.bg, display: 'flex', flexDirection: 'column', overflow: 'hidden', fontFamily: t.font}}>
        <Collapse open={1 - collapse}>
          <GithubHeader owner={repoOwner} repo={repoName} />
          {showRepoChrome ? <RepoHeader owner={repoOwner} repo={repoName} active="issues" /> : null}
        </Collapse>

        <div style={{borderBottom: '1px solid ' + t.border, flexShrink: 0}}>
          <TitleBlock
            title={title}
            number={number}
            compact={collapse}
            badge={<StatusBadge type="issue" state={state} />}
            subtitle={
              <>
                <strong style={{color: t.text, fontWeight: 600}}>{author}</strong> opened this issue {openedAgo} · {commentCount} comment{commentCount === 1 ? '' : 's'}
              </>
            }
          />
        </div>

        <div style={{flex: 1, minHeight: 0, display: 'flex', padding: '24px 32px', gap: 32, overflow: 'hidden'}}>
          <ThreadColumn
            anchor={anchor}
            composer={<Composer text={composerText} pointerAt={composerPointerAt} />}
            below={narration}
            belowHeight={narrationHeight}
            scroll={scroll}
            offset={threadOffset}
            viewportRef={threadViewportRef}
          >
            {children}
          </ThreadColumn>

          {sidebar ? (
            <div style={{width: 296, flexShrink: 0, fontSize: 12}}>
              <SideSection title="Assignees">
                {assignees.length === 0 ? (
                  <span style={{color: t.muted}}>No one assigned</span>
                ) : (
                  assignees.map((a) => (
                    <div key={a} style={{display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600}}>
                      <Initial name={a} />
                      <span>{a}</span>
                    </div>
                  ))
                )}
              </SideSection>
              <SideSection title="Labels">
                <div style={{display: 'flex', gap: 6, flexWrap: 'wrap'}}>
                  {labels.map((l) => (
                    <Label key={l.name} name={l.name} color={l.color} />
                  ))}
                </div>
              </SideSection>
              <SideSection title="Projects">
                <span style={{color: t.muted}}>None yet</span>
              </SideSection>
              <SideSection title="Milestone">
                <span style={{color: t.muted}}>No milestone</span>
              </SideSection>
              <SideSection title="Development">
                {linkedPr ? (
                  <div style={{position: 'relative', display: 'flex', alignItems: 'flex-start', gap: 8, lineHeight: 1.4}}>
                    <IconPullRequest size={16} color={linkedPr.merged ? t.purple : t.green} />
                    <div>
                      <div style={{fontWeight: 600}}>{linkedPr.title}</div>
                      <div style={{color: t.muted}}>
                        #{linkedPr.number} · {linkedPr.merged ? 'merged' : 'opened'}
                      </div>
                    </div>
                    {linkedPr.pointer ? (
                      <div style={{position: 'absolute', right: 60, bottom: 8}}>
                        <Pointer delay={typeof linkedPr.pointer === 'number' ? linkedPr.pointer : undefined} />
                      </div>
                  ) : null}
                </div>
              ) : (
                <span style={{color: t.muted}}>No branches or pull requests</span>
              )}
            </SideSection>
            <SideSection title={`${Math.max(participants.length, 1)} participants`} gear={false} last>
              <div style={{display: 'flex', gap: 4, flexWrap: 'wrap'}}>
                {participants.length === 0 ? (
                  <Initial name={author} size={24} />
                ) : (
                  participants.map((e, i) => (
                    <div
                      key={i}
                      style={{
                        width: 24,
                        height: 24,
                        borderRadius: '50%',
                        background: t.surface,
                        border: '1px solid ' + t.border,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: 12,
                      }}
                    >
                      {e}
                    </div>
                  ))
                )}
              </div>
            </SideSection>
          </div>
          ) : null}
        </div>
      </AbsoluteFill>
    </ThemeProvider>
  );
};
