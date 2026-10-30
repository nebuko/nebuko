import React, {useLayoutEffect, useRef, useState} from 'react';
import {AbsoluteFill, Easing, interpolate, Sequence, Series, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {Collapse, GithubComment, GithubIssueMainPost, GithubTimelineEvent, IssueFrame, ThemeName} from './github-ui';
import {eventById, events, issueBody, metricsSummary, ThreadEvent} from './data';
import {Narration, NARRATION_HEIGHT} from './Narration';
import {Phase, PrContent, PrScene, SAMPLE_PR} from './PrFlowDemo';

// The engine holds the workflow on the step whose state says `dispatch: HOLD`.
const isGate = (label?: string) => !!label && /\bdispatch:\s*HOLD\b/.test(label);

const participants = [...new Set(events.map((e) => e.avatar).filter(Boolean))];

/** Frames before the first comment and between two comments. */
const LEAD_IN = 15;
const STEP = 90;
/** Human comments: typing speed (chars per frame) and the pause before the cursor moves. */
const TYPE_SPEED = 0.3;
const TYPE_PAUSE = 6;
/** `Button` presses on frames [pointerAt + 14, pointerAt + 20): post right after the click. */
const CLICK_DONE = 20;
/** Extra time on screen for a step that has a caption, so it can be read. */
const CAPTION_EXTRA = 45;
/**
 * Long comments land with their top in view, pause, then scroll smoothly to their end.
 * The scroll time grows with the text length (folded <details> and HTML comments don't count).
 */
const REVEAL = 20;
const READ_PAUSE = 30;
const LONG_CHARS = 600;
const SCROLL_CHARS_PER_FRAME = 30;
const SCROLL_MIN = 60;
const SCROLL_MAX = 300;
/**
 * Comments kept mounted in the issue thread. It is anchored at the bottom, so older ones sit
 * above the viewport: dropping them changes nothing on screen but keeps every frame as cheap
 * as the first one (otherwise layout + paint grow with the thread and the render slows down).
 */
const KEEP_MOUNTED = 6;
/** Gap kept above a long comment's top while it is pinned (clears the top fade). */
const TOP_MARGIN = 24;

const visibleChars = (body: string) =>
  body
    .replace(/<details>[\s\S]*?<\/details>/g, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .trim().length;

type Slot = {
  event: ThreadEvent;
  /** frame the comment lands in the thread */
  at: number;
  typeFrom?: number;
  pointerAt?: number;
  /** the step on screen: from typing (or landing) until the next step starts */
  from: number;
  to: number;
  /** long comment: frames over which it scrolls from its top to its end */
  scroll?: [number, number];
};

const isHuman = (e: ThreadEvent) => e.kind === 'trigger' || e.kind === 'approve';

/** When each comment lands. A human one is first typed in the composer, then posted with a click. */
const schedule: Slot[] = (() => {
  const slots: Slot[] = [];
  let t = LEAD_IN;
  for (const event of events) {
    if (isHuman(event)) {
      const typeFrom = t;
      const pointerAt = typeFrom + Math.ceil(event.title.length / TYPE_SPEED) + TYPE_PAUSE;
      t = pointerAt + CLICK_DONE;
      slots.push({event, at: t, typeFrom, pointerAt, from: typeFrom, to: 0});
    } else {
      slots.push({event, at: t, from: t, to: 0});
    }
    const at = t;
    t += STEP + (event.caption ? CAPTION_EXTRA : 0);
    const chars = visibleChars(event.body);
    if (chars > LONG_CHARS) {
      const from = at + REVEAL + READ_PAUSE;
      const frames = Math.min(SCROLL_MAX, Math.max(SCROLL_MIN, Math.round(chars / SCROLL_CHARS_PER_FRAME)));
      slots[slots.length - 1].scroll = [from, from + frames];
      t = Math.max(t, from + frames + READ_PAUSE);
    }
  }
  slots.forEach((s, i) => (s.to = slots[i + 1]?.from ?? t));
  return slots;
})();

const ISSUE_END = schedule[schedule.length - 1].to;

/* ---------------------------------------------------------------------
 * After the review verdict: the cursor clicks the linked PR in the issue
 * sidebar, the page cross-fades to the PR, and the human approves it.
 * ------------------------------------------------------------------- */

/** Issue page with the cursor on the linked PR, then the cross-fade length. */
const GOTO_PR = 45;
const FADE = 15;
const GOTO_CAPTION = 'The agents are done: open the pull request and review it yourself.';

const PR: PrContent = {
  ...SAMPLE_PR,
  number: 1133,
  title: 'Guard openhands import in git_session.py so lint-config runs on base deps',
  body: eventById('e10').body,
  code: eventById('e10').code,
  botReview: eventById('e12').body,
  approveText: 'LGTM — matches the approved design.',
};

const PR_STEPS: {phase: Phase; frames: number; caption: string}[] = [
  {phase: 'review', frames: 60, caption: 'The PR carries the agents\' work: commits, checks and the review agent\'s verdict.'},
  {phase: 'popover', frames: 90, caption: 'You stay the final reviewer: approve it like any other pull request.'},
  {phase: 'approved', frames: 75, caption: 'Approved: the change is ready to merge.'},
  {phase: 'confirm', frames: 60, caption: 'Squash and merge: the commit lands on main.'},
  {phase: 'merged', frames: 60, caption: 'Merged: the pull request closes issue #839.'},
];

const PR_START = ISSUE_END + GOTO_PR - FADE;
const PR_END = PR_START + PR_STEPS.reduce((n, s) => n + s.frames, 0);

/* ---------------------------------------------------------------------
 * Back on the issue: it closes, the metrics summary lands, and the thread
 * scrolls through it. Frames are relative to the closing scene.
 * ------------------------------------------------------------------- */
const CLOSE_AT = 20;
const METRICS_AT = 50;
const SCROLL_FROM = METRICS_AT + 45;
const SCROLL_FRAMES = 300;
const END_HOLD = 60;
const CLOSING_CAPTION = 'Back on the issue: the merge closed it.';

const CLOSING_START = PR_END - FADE;
export const PREVIEW_DURATION = CLOSING_START + SCROLL_FROM + SCROLL_FRAMES + END_HOLD;

/** Grows from 0 to its natural height, so with the thread anchored at the bottom the older comments scroll up smoothly. */
const Reveal: React.FC<{children: React.ReactNode; boxRef?: React.Ref<HTMLDivElement>}> = ({children, boxRef}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = spring({frame, fps, config: {damping: 200}, durationInFrames: REVEAL});
  return (
    <Collapse open={p} style={{marginTop: -16 * (1 - p)}} boxRef={boxRef}>
      {children}
    </Collapse>
  );
};

const IssueThread: React.FC<{theme: ThemeName}> = ({theme}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const shown = schedule.filter((s) => s.at <= frame).length;

  // a human comment being written: its command types into the composer, then "Comment" is clicked
  const typing = schedule.find((s) => s.typeFrom !== undefined && s.typeFrom <= frame && frame < s.at);
  // once the thread starts moving, fold GitHub's header away so the comments get the screen
  const collapse = spring({frame: frame - schedule[0].at, fps, config: {damping: 200}, durationInFrames: 25});
  // the sidebar folds with it — except right before the cursor opens the linked PR,
  // when it slides back in so the Development section is there to be clicked
  const sidebarCollapse = interpolate(
    frame,
    [ISSUE_END - 45, ISSUE_END - 15],
    [collapse, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.cubic)},
  );
  const typed = typing ? typing.event.title.slice(0, Math.floor((frame - typing.typeFrom!) * TYPE_SPEED)) : '';

  // A newest comment taller than the screen: push the thread down so its top shows, then glide to its end.
  const landed = schedule.filter((s) => s.at <= frame);
  const newest = landed[landed.length - 1];
  const newestRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({comment: 0, viewport: 0});
  useLayoutEffect(() => {
    const comment = newestRef.current?.offsetHeight ?? 0;
    const viewport = viewportRef.current?.clientHeight ?? 0;
    if (comment !== size.comment || viewport !== size.viewport) setSize({comment, viewport});
  });
  const progress = newest?.scroll
    ? interpolate(frame, newest.scroll, [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.cubic)})
    : 1;
  const offset = (1 - progress) * Math.max(0, size.comment - size.viewport + TOP_MARGIN);

  return (
    <IssueFrame
      threadOffset={offset}
      threadViewportRef={viewportRef}
      theme={theme}
      commentCount={shown}
      anchor="bottom"
      composerText={typed}
      composerPointerAt={typing?.pointerAt ?? null}
      participants={participants}
      collapse={collapse}
      sidebarCollapse={sidebarCollapse}
      narrationHeight={NARRATION_HEIGHT}
      narration={
        <>
          {schedule.map(({event: e, from, to}) =>
            e.caption ? (
              <Sequence key={e.id} from={from} durationInFrames={to - from}>
                <Narration text={e.caption} theme={theme} />
              </Sequence>
            ) : null,
          )}
          <Sequence from={ISSUE_END}>
            <Narration text={GOTO_CAPTION} theme={theme} />
          </Sequence>
        </>
      }
      linkedPr={{number: PR.number, title: PR.title, pointer: frame >= ISSUE_END ? ISSUE_END : false}}
    >
      {shown < KEEP_MOUNTED ? (
        <GithubIssueMainPost
          author="alex-dev"
          avatar="🧑‍💻"
          time="3 hours ago"
          body={issueBody}
        />
      ) : null}
      {schedule.map((slot, i) => i < shown - KEEP_MOUNTED ? null : (
        <Sequence key={slot.event.id} from={slot.at} layout="none">
          <Reveal boxRef={slot === newest ? newestRef : undefined}>
            <GithubComment event={slot.event} gate={isGate(slot.event.stateLabel)} animate />
          </Reveal>
        </Sequence>
      ))}
    </IssueFrame>
  );
};

/** The PR page, already scrolled like the issue (header folded), one phase per caption. */
const PrReview: React.FC<{theme: ThemeName}> = ({theme}) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{opacity: interpolate(frame, [0, FADE], [0, 1], {extrapolateRight: 'clamp'})}}>
      <Series>
        {PR_STEPS.map((step) => (
          <Series.Sequence key={step.phase} durationInFrames={step.frames}>
            <PrScene
              phase={step.phase}
              theme={theme}
              pr={PR}
              collapse={1}
              narrationHeight={NARRATION_HEIGHT}
              narration={<Narration text={step.caption} theme={theme} />}
            />
          </Series.Sequence>
        ))}
      </Series>
    </AbsoluteFill>
  );
};

/** The issue again, closed by the merge; the metrics summary lands and the thread scrolls through it. */
const IssueClosing: React.FC<{theme: ThemeName}> = ({theme}) => {
  const frame = useCurrentFrame();
  const scroll = interpolate(frame, [SCROLL_FROM, SCROLL_FROM + SCROLL_FRAMES], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });
  return (
    <AbsoluteFill style={{opacity: interpolate(frame, [0, FADE], [0, 1], {extrapolateRight: 'clamp'})}}>
      <IssueFrame
        theme={theme}
        state={frame >= CLOSE_AT ? 'closed' : 'open'}
        commentCount={events.length + (frame >= METRICS_AT ? 1 : 0)}
        composerText=""
        participants={participants}
        collapse={1}
        scroll={scroll}
        sidebar={false}
        narrationHeight={NARRATION_HEIGHT}
        narration={
          <>
            <Sequence durationInFrames={METRICS_AT}>
              <Narration text={CLOSING_CAPTION} theme={theme} />
            </Sequence>
            <Sequence from={METRICS_AT}>
              <Narration text={metricsSummary.caption ?? ''} theme={theme} />
            </Sequence>
          </>
        }
        linkedPr={{number: PR.number, title: PR.title, merged: true}}
      >
        <Sequence from={CLOSE_AT} layout="none">
          <Reveal>
            <GithubTimelineEvent kind="closed" actor="alex-dev" time="just now" animate>
              closed this as completed in #{PR.number}
            </GithubTimelineEvent>
          </Reveal>
        </Sequence>
        <Sequence from={METRICS_AT} layout="none">
          <Reveal>
            <GithubComment event={metricsSummary} animate />
          </Reveal>
        </Sequence>
      </IssueFrame>
    </AbsoluteFill>
  );
};

export const Preview: React.FC<{theme?: ThemeName}> = ({theme = 'light'}) => (
  <AbsoluteFill>
    <Sequence durationInFrames={ISSUE_END + GOTO_PR}>
      <IssueThread theme={theme} />
    </Sequence>
    <Sequence from={PR_START} durationInFrames={PR_END - PR_START}>
      <PrReview theme={theme} />
    </Sequence>
    <Sequence from={CLOSING_START}>
      <IssueClosing theme={theme} />
    </Sequence>
  </AbsoluteFill>
);
