import React from 'react';
import {Sequence, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {Collapse, GithubComment, GithubIssueMainPost, IssueFrame, ThemeName} from './github-ui';
import {events, ThreadEvent} from './data';
import {Narration, NARRATION_HEIGHT} from './Narration';

// The engine holds the workflow on the step whose state says `dispatch: HOLD`.
const isGate = (label?: string) => !!label && /\bdispatch:\s*HOLD\b/.test(label);

const participants = [...new Set(events.map((e) => e.avatar).filter(Boolean))];

/** Frames before the first comment, between two comments, and after the last one. */
const LEAD_IN = 15;
const STEP = 30;
const HOLD = 60;
/** Human comments: typing speed (chars per frame) and the pause before the cursor moves. */
const TYPE_SPEED = 1.5;
const TYPE_PAUSE = 6;
/** `Button` presses on frames [pointerAt + 14, pointerAt + 20): post right after the click. */
const CLICK_DONE = 20;
/** Extra time on screen for a step that has a caption, so it can be read. */
const CAPTION_EXTRA = 45;

type Slot = {
  event: ThreadEvent;
  /** frame the comment lands in the thread */
  at: number;
  typeFrom?: number;
  pointerAt?: number;
  /** the step on screen: from typing (or landing) until the next step starts */
  from: number;
  to: number;
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
    t += STEP + (event.caption ? CAPTION_EXTRA : 0);
  }
  slots.forEach((s, i) => (s.to = slots[i + 1]?.from ?? t + HOLD - STEP));
  return slots;
})();

export const PREVIEW_DURATION = schedule[schedule.length - 1].to;

/** Grows from 0 to its natural height, so with the thread anchored at the bottom the older comments scroll up smoothly. */
const Reveal: React.FC<{children: React.ReactNode}> = ({children}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = spring({frame, fps, config: {damping: 200}, durationInFrames: 20});
  return (
    <Collapse open={p} style={{marginTop: -16 * (1 - p)}}>
      {children}
    </Collapse>
  );
};

export const Preview: React.FC<{theme?: ThemeName}> = ({theme = 'light'}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const shown = schedule.filter((s) => s.at <= frame).length;
  // a human comment being written: its command types into the composer, then "Comment" is clicked
  const typing = schedule.find((s) => s.typeFrom !== undefined && s.typeFrom <= frame && frame < s.at);
  // once the thread starts moving, fold GitHub's header away so the comments get the screen
  const collapse = spring({frame: frame - schedule[0].at, fps, config: {damping: 200}, durationInFrames: 25});
  const typed = typing ? typing.event.title.slice(0, Math.floor((frame - typing.typeFrom!) * TYPE_SPEED)) : '';
  return (
    <IssueFrame
      theme={theme}
      commentCount={shown}
      anchor="bottom"
      composerText={typed}
      composerPointerAt={typing?.pointerAt ?? null}
      participants={participants}
      collapse={collapse}
      narrationHeight={NARRATION_HEIGHT}
      narration={schedule.map(({event: e, from, to}) =>
        e.caption ? (
          <Sequence key={e.id} from={from} durationInFrames={to - from}>
            <Narration text={e.caption} theme={theme} />
          </Sequence>
        ) : null,
      )}
      linkedPr={{number: 843, title: 'feat: /nebuko update circuit-breaker'}}
    >
      <GithubIssueMainPost
        author="alex-dev"
        avatar="🧑‍💻"
        time="3 hours ago"
        body={
          'The circuit breaker trips for the whole repository when a single issue fails three times.\n\nWe should be able to raise the limit for **one issue** only, e.g. `/nebuko run workflow --max-failures 5`.\n\n- keep the default at 3\n- log the override in the workflow state'
        }
      />
      {schedule.map(({event: e, at}) => (
        <Sequence key={e.id} from={at} layout="none">
          <Reveal>
            <GithubComment event={e} gate={isGate(e.stateLabel)} animate />
          </Reveal>
        </Sequence>
      ))}
    </IssueFrame>
  );
};
