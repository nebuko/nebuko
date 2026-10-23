import React from 'react';
import {
  AbsoluteFill,
  Easing,
  interpolate,
  Sequence,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

/**
 * The 20s hero video for nebuko.dev (muted, loops seamlessly).
 *
 * Graphic/motion-design style — no GitHub UI screenshots (the long "how it works"
 * demo keeps that). Two compositions render the same component:
 *   Hero      1920x1080 (landing page hero)
 *   HeroPhone  1080x1920 (phone screen)
 *
 * Timeline (30fps, 600 frames = 20s):
 *   0:00  Hook — logo + "AI multi-agent workflows as code"
 *   0:03  You start it — the `/nebuko run workflow` comment + the YAML it runs
 *   0:08  Agents work — pipeline runs, pauses on the human gate, you approve, PR
 *   0:14  Yours to share — workflow as code, fork & share
 *   0:17  End card — value trio + logo + nebuko.dev (loops back into the hook)
 */

export const HERO_FPS = 30;
export const HERO_DURATION = 660; // 22s

const C = {
  bg: '#0d1117',
  bgDeep: '#010409',
  text: '#f0f6fc',
  muted: '#9198a1',
  border: '#2b323b',
  surface: '#151b23',
  violet: '#5A3DF0',
  blue: '#2F9BFF',
  amber: '#F59E0B',
  green: '#3fb950',
  red: '#f85149',
  mono: "'SF Mono', 'Cascadia Code', Consolas, 'DejaVu Sans Mono', monospace",
  sans: 'Montserrat',
};

/** Injects the Montserrat @font-face rules once per document. */
const FontFaceStyle: React.FC = () => (
  <style
    dangerouslySetInnerHTML={{
      __html: [400, 500, 600, 800]
        .map(
          (w) => `@font-face{font-family:'Montserrat';font-style:normal;font-weight:${w};
src:url('${staticFile(`Montserrat-${w === 800 ? 'ExtraBold' : w === 400 ? 'Regular' : w === 500 ? 'Medium' : 'SemiBold'}.ttf`)}') format('truetype');}`,
        )
        .join('\n'),
    }}
  />
);

/** Scene timing helpers: every scene fades/slides in and out. */
const SCENES = {
  intro: [0, 60],
  start: [60, 160],
  agents: [160, 575], // 415 frames ≈ 13.8s — plan → gate → reject → dev/review pass 2 → PR box (+1s read)
  final: [575, 660], // 85 frames ≈ 2.8s — static full-screen closing scene
} as const;

const fadeSlide = (
  frame: number,
  from: number,
  to: number,
  distance = 40,
  hold = false, // no fade-out: the scene stays at full opacity until the cut
): {opacity: number; translateY: number} => ({
  opacity: interpolate(
    frame,
    hold ? [from, from + 12] : [from, from + 12, to - 12, to],
    hold ? [0, 1] : [0, 1, 1, 0],
    {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    },
  ),
  translateY: interpolate(frame, [from, from + 12], [distance, 0], {
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  }),
});

/** Scales text with the composition width so both aspect ratios stay readable. */
const useScale = () => {
  const {width, height} = useVideoConfig();
  const portrait = height > width;
  return {s: portrait ? width / 1080 : width / 1920, portrait};
};

const Background: React.FC = () => {
  const frame = useCurrentFrame();
  const {s, portrait} = useScale();
  const drift = Math.sin((frame / HERO_DURATION) * Math.PI * 2); // loops with the video
  return (
    <AbsoluteFill style={{background: C.bgDeep}}>
      {/* Two slow-drifting brand-gradient blobs, kept subtle behind dark base. */}
      <div
        style={{
          position: 'absolute',
          left: portrait ? '-30%' : `${8 + drift * 4}%`,
          top: portrait ? `${6 + drift * 4}%` : '-25%',
          width: 900 * s,
          height: 900 * s,
          borderRadius: '50%',
          background: `radial-gradient(circle, ${C.violet}55 0%, transparent 65%)`,
          filter: `blur(${60 * s}px)`,
        }}
      />
      <div
        style={{
          position: 'absolute',
          right: portrait ? '-30%' : `${6 - drift * 4}%`,
          bottom: portrait ? `${4 - drift * 4}%` : '-30%',
          width: 1000 * s,
          height: 1000 * s,
          borderRadius: '50%',
          background: `radial-gradient(circle, ${C.blue}44 0%, transparent 65%)`,
          filter: `blur(${60 * s}px)`,
        }}
      />
      {/* Faint grid, evoking "workflows as code". */}
      <AbsoluteFill
        style={{
          opacity: 0.5,
          backgroundImage: `linear-gradient(${C.border}22 1px, transparent 1px), linear-gradient(90deg, ${C.border}22 1px, transparent 1px)`,
          backgroundSize: `${80 * s}px ${80 * s}px`,
        }}
      />
    </AbsoluteFill>
  );
};

const Headline: React.FC<{main: string; sub?: React.ReactNode; small?: boolean}> = ({main, sub, small}) => {
  const frame = useCurrentFrame();
  const {s} = useScale();
  const t = spring({frame, fps: 30, config: {damping: 200}});
  return (
    <div
      style={{
        textAlign: 'center',
        transform: `translateY(${interpolate(t, [0, 1], [30, 0]) * s}px)`,
        opacity: t,
      }}
    >
      <div
        style={{
          fontFamily: C.sans,
          fontWeight: 800,
          fontSize: (small ? 56 : 76) * s,
          lineHeight: 1.05,
          color: C.text,
        }}
      >
        {main}
      </div>
      {sub ? (
        <div
          style={{
            marginTop: 10 * s, // title ↔ subtitle: one tight unit
            fontFamily: C.sans,
            fontWeight: 500,
            fontSize: 30 * s,
            color: C.muted,
          }}
        >
          {sub}
        </div>
      ) : null}
    </div>
  );
};

const GradientWord: React.FC<{children: string; size: number}> = ({children, size}) => {
  const {s} = useScale();
  return (
    <span
      style={{
        background: `linear-gradient(90deg, ${C.blue}, ${C.violet})`, // blue → indigo
        backgroundClip: 'text',
        WebkitBackgroundClip: 'text',
        color: 'transparent',
        fontSize: size * s,
      }}
    >
      {children}
    </span>
  );
};

/** Round avatar: gradient for humans, dashed for agents. */
const Avatar: React.FC<{kind: 'human' | 'agent'; size: number; letter: string}> = ({
  kind,
  size,
  letter,
}) => {
  const {s} = useScale();
  const d = size * s;
  const base: React.CSSProperties = {
    width: d,
    height: d,
    borderRadius: '50%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    fontFamily: C.sans,
    fontWeight: 800,
    fontSize: d * 0.42,
    color: '#fff',
  };
  return kind === 'human' ? (
    <div
      style={{
        ...base,
        background: C.amber, // amber = human, matching the logo glow
        color: C.bg,
      }}
    >
      {letter}
    </div>
  ) : (
    <div
      style={{
        ...base,
        background: C.surface,
        border: `${3 * s}px dashed ${C.blue}`,
        color: C.blue,
      }}
    >
      {letter}
    </div>
  );
};

/** A chat comment that types itself out, then lands in the thread. */
/** A chat comment that types itself out — styled as a GitHub issue comment:
 *  the issue line is a header above everything, then the avatar sits outside
 *  on the left aligned with the comment's header strip, and the body below. */
/** A chat comment that types itself out — styled as a GitHub issue comment.
 *  Non-compact (issue comment): the issue line is a header above everything,
 *  the card spans the full width, and the avatar hangs OUTSIDE on the left,
 *  aligned with the comment's header strip — so the card shares edges with
 *  neighbouring boxes. Compact (inline at a step): avatar left, card right. */
const TypingBubble: React.FC<{
  from: number; // local frame typing starts
  author: string;
  kind: 'human' | 'agent';
  text: string;
  cps?: number; // characters per second
  width?: number; // width of the comment card itself
  label?: string;
  compact?: boolean; // smaller, for inline use at a step level
  issue?: boolean; // ● Open issue line above the comment
}> = ({from, author, kind, text, cps = 16, width = 760, label, compact = false, issue = false}) => {
  const frame = useCurrentFrame();
  const {s} = useScale();
  const chars = Math.max(
    0,
    Math.floor(interpolate(frame, [from, from + (text.length / cps) * 30], [0, text.length], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    })),
  );
  const typed = text.slice(0, chars);
  const done = chars >= text.length;
  const settle = spring({frame: frame - (from + (text.length / cps) * 30 + 4), fps: 30, config: {damping: 12}});
  const avatar = (
    <Avatar
      kind={kind}
      size={compact ? 36 : 52}
      letter={kind === 'human' ? 'Y' : author[0].toUpperCase()}
    />
  );
  const card = (
    <div
      style={{
        flex: 1,
        boxSizing: 'border-box',
        background: C.surface,
        border: `${2 * s}px solid ${kind === 'human' ? C.amber : C.border}`,
        borderRadius: 14 * s,
        boxShadow: done
          ? `0 ${20 * s}px ${60 * s}px rgba(0,0,0,.45)`
          : `0 ${10 * s}px ${30 * s}px rgba(0,0,0,.3)`,
        transform: `scale(${interpolate(settle, [0, 1], [1.03, 1])})`,
        overflow: 'hidden',
        textAlign: 'left',
      }}
    >
      {/* Header strip — slightly lighter, like GitHub's comment header */}
      <div
        style={{
          display: 'flex',
          gap: 12 * s,
          alignItems: 'center',
          padding: `${10 * s}px ${16 * s}px`,
          background: `${C.text}0d`,
          borderBottom: `${2 * s}px solid ${C.border}`,
        }}
      >
        <span
          style={{
            fontFamily: C.sans,
            fontWeight: 700,
            fontSize: (compact ? 20 : 26) * s,
            color: C.text,
          }}
        >
          {author}
        </span>
        <span
          style={{
            fontFamily: C.sans,
            fontWeight: 500,
            fontSize: (compact ? 16 : 21) * s,
            color: C.muted,
          }}
        >
          commented now
        </span>
        {label ? (
          <span
            style={{
              fontFamily: C.sans,
              fontWeight: 600,
              fontSize: 15 * s,
              color: C.amber,
              background: `${C.amber}22`,
              padding: `${3 * s}px ${10 * s}px`,
              borderRadius: 99 * s,
            }}
          >
            {label}
          </span>
        ) : null}
        <span
          style={{
            marginLeft: 'auto',
            fontFamily: C.sans,
            fontWeight: 800,
            fontSize: 22 * s,
            letterSpacing: 2 * s,
            color: C.muted,
          }}
        >
          ···
        </span>
      </div>
      {/* Body — the command, at least as large as the YAML lines */}
      <div
        style={{
          padding: `${(compact ? 10 : 16) * s}px ${18 * s}px`,
          fontFamily: C.mono,
          fontSize: (compact ? 24 : 32) * s,
          lineHeight: 1.45,
          color: C.text,
          whiteSpace: compact ? 'nowrap' : 'pre-wrap', // compact: one line
        }}
      >
        {typed}
        {!done ? <Caret /> : null}
      </div>
    </div>
  );
  return (
    <div style={{width: width * s, textAlign: 'left'}}>
      {/* The issue line — its own header above everything, at the left edge
          of the comment card */}
      {issue ? (
        <div
          style={{
            display: 'flex',
            gap: 14 * s,
            alignItems: 'center',
            marginBottom: 14 * s,
          }}
        >
          {/* Filled green pill — the GitHub "Open" badge people recognize fastest */}
          <span
            style={{
              fontFamily: C.sans,
              fontWeight: 700,
              fontSize: (compact ? 17 : 21) * s,
              color: '#fff',
              background: C.green,
              padding: `${5 * s}px ${16 * s}px`,
              borderRadius: 99 * s,
            }}
          >
            Open
          </span>
          <span
            style={{
              fontFamily: C.sans,
              fontWeight: 700,
              fontSize: (compact ? 20 : 26) * s,
              color: C.text,
            }}
          >
            Add dark mode toggle
          </span>
          <span
            style={{
              fontFamily: C.sans,
              fontWeight: 500,
              fontSize: (compact ? 20 : 24) * s,
              color: C.muted,
            }}
          >
            #42
          </span>
        </div>
      ) : null}
      {/* The comment itself */}
      {compact ? (
        <div style={{display: 'flex', gap: 14 * s, alignItems: 'flex-start'}}>
          {avatar}
          {card}
        </div>
      ) : (
        <div style={{position: 'relative'}}>
          {/* Avatar hanging outside on the left, aligned with the header
              strip — the card itself keeps the full width */}
          <div style={{position: 'absolute', left: -66 * s, top: 10 * s}}>{avatar}</div>
          {card}
        </div>
      )}
    </div>
  );
};
const Caret: React.FC = () => {
  const frame = useCurrentFrame();
  const {s} = useScale();
  return (
    <span
      style={{
        display: 'inline-block',
        width: 14 * s,
        height: 34 * s,
        marginLeft: 4 * s,
        background: C.blue,
        opacity: frame % 16 < 8 ? 1 : 0.15,
        verticalAlign: 'middle',
      }}
    />
  );
};

/** The YAML definition of a workflow — "workflow as code". */
const YamlCard: React.FC<{from: number}> = ({from}) => {
  const frame = useCurrentFrame();
  const {s} = useScale();
  const lines: {text: string; indent: number; key: string; hl?: 'gate'}[] = [
    {text: 'workflow: plan-dev-review', indent: 0, key: 'workflow'},
    {text: 'steps:', indent: 0, key: 'steps'},
    {text: '- agent: context', indent: 2, key: 'context'},
    {text: '- agent: architect', indent: 2, key: 'architect'},
    {text: '- gate: design', indent: 2, key: 'gate', hl: 'gate'},
    {text: '- agent: dev', indent: 2, key: 'dev'},
    {text: '- agent: review', indent: 2, key: 'review'},
  ];
  const shown = lines.length; // the whole file, in one shot
  const rise = spring({frame: frame - from, fps: 30, config: {damping: 200}});
  return (
    <div
      style={{
        transform: `translateY(${interpolate(rise, [0, 1], [60, 0]) * s}px)`,
        opacity: rise,
        padding: `${28 * s}px ${36 * s}px`,
        background: C.bg,
        border: `${2 * s}px solid ${C.border}`,
        borderRadius: 16 * s,
        textAlign: 'left',
        fontFamily: C.mono,
        fontSize: 30 * s,
        lineHeight: 1.6,
        width: 580 * s, // exact same width as the comment card
        boxSizing: 'border-box',
      }}
    >
      <div
        style={{
          fontFamily: C.sans,
          fontWeight: 600,
          fontSize: 19 * s,
          color: C.muted,
          marginBottom: 14 * s,
        }}
      >
        .nebuko/workflows/plan-dev-review.yaml
      </div>
      {lines.slice(0, shown).map((l) => (
        <div key={l.key} style={{paddingLeft: l.indent * 40 * s}}>
          <span style={{color: C.blue}}>
            {l.text.split(':')[0]}
          </span>
          <span style={{color: C.muted}}>:</span>
          <span style={{color: C.text}}>{l.text.split(/:(.+)/)[1] ?? ''}</span>
          {l.hl === 'gate' ? (
            <span
              style={{
                marginLeft: 16 * s,
                fontSize: 20 * s,
                fontWeight: 600,
                color: C.amber,
                background: `${C.amber}1f`,
                padding: `${2 * s}px ${12 * s}px`,
                borderRadius: 99 * s,
              }}
            >
              human approval
            </span>
          ) : null}
        </div>
      ))}
    </div>
  );
};

/* ------------------------------- scenes ------------------------------- */

const IntroScene: React.FC = () => {
  const frame = useCurrentFrame();
  const {s} = useScale();
  const logoIn = spring({frame, fps: 30, config: {damping: 12}});
  const tagIn = spring({frame: frame - 15, fps: 30, config: {damping: 200}});
  return (
    <AbsoluteFill
      style={{justifyContent: 'center', alignItems: 'center', gap: 40 * s, flexDirection: 'column'}}
    >
      <div
        style={{
          transform: `scale(${logoIn}) rotate(${interpolate(logoIn, [0, 1], [-8, 0])}deg)`,
        }}
      >
        <img
          src={staticFile('logo-horizontal-dark.svg')}
          alt="Nebuko"
          style={{width: 900 * s, height: 'auto'}}
        />
      </div>
      <div
        style={{
          fontFamily: C.sans,
          fontWeight: 600,
          fontSize: 44 * s,
          color: C.text,
          opacity: tagIn,
          transform: `translateY(${interpolate(tagIn, [0, 1], [24, 0]) * s}px)`,
          textAlign: 'center',
          maxWidth: 1400 * s,
          lineHeight: 1.3,
        }}
      >
        AI multi-agent workflows{' '}
        <GradientWord size={44}>as code</GradientWord>
        <br />
        <span style={{color: C.muted, fontWeight: 500, fontSize: 34 * s}}>
          running where you already work — your issues
        </span>
      </div>
    </AbsoluteFill>
  );
};

const StartScene: React.FC = () => {
  const {s, portrait} = useScale();
  const {height} = useVideoConfig();
  return (
    <AbsoluteFill
      style={{
        flexDirection: 'column',
        // Title group pinned toward the top; content centered in the space
        // below it — more air between title and content on both formats.
        // Title group pinned toward the top; phone sits ~5% lower.
        paddingTop: height * (portrait ? 0.15 : 0.10),
      }}
    >
      <Headline
        main="You start it."
        sub={
          portrait ? (
            <>
              Your workflow lives in your repo.
              <br />
              One comment on a GitHub issue starts it.
            </>
          ) : (
            'Your workflow lives in your repo — one comment on a GitHub issue starts it.'
          )
        }
      />
      {/* Content group: centered in the space below the title, nudged ~5%
          higher on the Hero */}
      <div
        style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          transform: `translateY(${(portrait ? -0.05 : -0.05) * height}px)`,
        }}
      >
      {/* The block scaled up ~15% — fills the frame, and keeps the small
          texts (#42, “commented now”, the file path) readable on a phone. */}
      <div
        style={{
          display: 'flex',
          flexDirection: portrait ? 'column' : 'row',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 40 * s,
          transform: `scale(${portrait ? 1.2 : 1.15})`,
        }}
      >
        <YamlCard from={10} />
        {/* A real connecting line — the link between the file and the comment */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            transform: portrait ? 'rotate(90deg)' : 'none',
            zIndex: 2, // stay above anything hanging in from the sides
          }}
        >
          <div
            style={{
              width: 70 * s,
              height: 8 * s,
              borderRadius: 4 * s,
              background: `linear-gradient(90deg, ${C.border}, ${C.blue})`,
            }}
          />
          <div
            style={{
              width: 0,
              height: 0,
              borderTop: `${10 * s}px solid transparent`,
              borderBottom: `${10 * s}px solid transparent`,
              borderLeft: `${16 * s}px solid ${C.blue}`,
            }}
          />
        </div>
        {/* On the Hero, extra space before the comment so the outside-hanging
            avatar never covers the arrow. */}
        <div style={{marginLeft: portrait ? 0 : 74 * s}}>
          <TypingBubble
            from={40}
            cps={20}
            author="you"
            kind="human"
            text="/nebuko run workflow"
            width={580}
            issue
          />
        </div>
      </div>
      </div>
    </AbsoluteFill>
  );
};

/** One step of the vertical pipeline: node + label + short description. */
const StepRow: React.FC<{
  label: string;
  desc: string;
  state: 'pending' | 'running' | 'done' | 'gate' | 'rejected' | 'rewind';
  from: number; // local frame the row enters
  width?: number; // fixed row width so nodes align in the centered column
  /** Color of the done ✓ — amber for human milestones (gate approval, PR). */
  accent?: 'green' | 'amber';
}> = ({label, desc, state, from, width, accent = 'green'}) => {
  const frame = useCurrentFrame();
  const {s} = useScale();
  const inSpr = spring({frame: frame - from, fps: 30, config: {damping: 200}});
  const pulse = 0.5 + 0.5 * Math.sin(frame / 4);
  const doneColor = accent === 'amber' ? C.amber : C.green;
  const ring =
    state === 'done'
      ? doneColor
      : state === 'running'
        ? C.blue
        : state === 'gate'
          ? C.amber
          : state === 'rejected'
            ? C.red
            : state === 'rewind'
              ? C.amber
              : C.border;
  const icon =
    state === 'done' ? (
      <span style={{color: doneColor, fontSize: 34 * s}}>✓</span>
    ) : state === 'rejected' ? (
      <span style={{color: C.red, fontSize: 34 * s}}>✗</span>
    ) : state === 'rewind' ? (
      <span style={{color: C.amber, fontSize: 32 * s, display: 'inline-block', transform: `rotate(${frame * 8}deg)`}}>
        ↺
      </span>
    ) : state === 'gate' ? (
      <div style={{display: 'flex', gap: 5 * s}}>
        <div style={{width: 8 * s, height: 26 * s, background: C.amber}} />
        <div style={{width: 8 * s, height: 26 * s, background: C.amber}} />
      </div>
    ) : state === 'running' ? (
      <div
        style={{
          width: 18 * s,
          height: 18 * s,
          borderRadius: '50%',
          border: `${4 * s}px solid ${C.blue}`,
          borderTopColor: 'transparent',
          transform: `rotate(${frame * 12}deg)`,
        }}
      />
    ) : (
      <div style={{width: 14 * s, height: 14 * s, borderRadius: '50%', background: C.border}} />
    );
  return (
    <div
      style={{
        display: 'flex',
        gap: 30 * s,
        alignItems: 'center',
        opacity: inSpr,
        transform: `translateX(${interpolate(inSpr, [0, 1], [-40, 0]) * s}px)`,
        minHeight: 76 * s,
        ...(width ? {width: width * s} : {}),
      }}
    >
      <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0}}>
        <div
          style={{
            width: 72 * s,
            height: 72 * s,
            borderRadius: `${20 * s}px`,
            background: C.surface,
            border: `${3 * s}px solid ${ring}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow:
              state === 'running' || state === 'gate'
                ? `0 0 ${26 * s}px ${ring}${state === 'gate' ? '88' : '55'}`
                : 'none',
            transform: state === 'gate' ? `scale(${1 + pulse * 0.06})` : 'none',
          }}
        >
          {icon}
        </div>
      </div>
      <div style={{textAlign: 'left'}}>
        <div
          style={{
            fontFamily: C.sans,
            fontWeight: 700,
            fontSize: 36 * s,
            color: state === 'pending' ? C.muted : C.text,
            display: 'flex',
            alignItems: 'center',
            gap: 16 * s,
          }}
        >
          {label}
          {state === 'gate' ? (
            <span
              style={{
                fontWeight: 800,
                fontSize: 22 * s,
                color: C.amber,
                background: `${C.amber}1c`,
                border: `${2 * s}px solid ${C.amber}66`,
                padding: `${3 * s}px ${14 * s}px`,
                borderRadius: 99 * s,
              }}
            >
              waiting for you
            </span>
          ) : null}
          {state === 'rejected' ? (
            <span
              style={{
                fontWeight: 800,
                fontSize: 22 * s,
                color: C.red,
                background: `${C.red}1c`,
                border: `${2 * s}px solid ${C.red}66`,
                padding: `${3 * s}px ${14 * s}px`,
                borderRadius: 99 * s,
              }}
            >
              1 test failed
            </span>
          ) : null}
          {state === 'rewind' ? (
            <span
              style={{
                fontWeight: 800,
                fontSize: 22 * s,
                color: C.amber,
                background: `${C.amber}1c`,
                border: `${2 * s}px solid ${C.amber}66`,
                padding: `${3 * s}px ${14 * s}px`,
                borderRadius: 99 * s,
              }}
            >
              rework
            </span>
          ) : null}
        </div>
        <div
          style={{
            fontFamily: C.sans,
            fontWeight: 500,
            fontSize: 27 * s,
            color: C.muted,
            marginTop: 4 * s,
          }}
        >
          {desc}
        </div>
      </div>
    </div>
  );
};

/**
 * Vertical connector between two steps.
 *   off    — the step above has not handed off yet (gray)
 *   pulse  — hand-off in progress: a bright pulse travels down the bar
 *   done   — the hand-off is over, but the bar stays solid blue so the
 *            workflow's current position is always readable
 *   rewind — reject loop: a red pulse travels up the bar, back to the step above
 */
const VConnector: React.FC<{
  mode: 'off' | 'pulse' | 'done' | 'rewind';
  height?: number; // taller bar (e.g. on the phone, to span the approve box)
}> = ({mode, height = 36}) => {
  const frame = useCurrentFrame();
  const {s} = useScale();
  const pos = (frame % 14) / 14; // pulse cycle ≈ 0.47s
  const lit = mode !== 'off';
  const color = mode === 'rewind' ? C.red : C.blue;
  return (
    <div
      style={{
        width: 18 * s,
        height: height * s,
        borderRadius: 9 * s,
        marginLeft: 27 * s, // center under the 72px node
        background:
          mode === 'off' ? C.border : mode === 'done' ? `${C.blue}88` : `${color}2a`,
        border: `${2 * s}px solid ${lit ? `${color}55` : 'transparent'}`,
        boxShadow:
          mode === 'done'
            ? `0 0 ${20 * s}px ${C.blue}55`
            : lit
              ? `0 0 ${22 * s}px ${color}66`
              : 'none',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {mode === 'pulse' || mode === 'rewind' ? (
        <div
          style={{
            position: 'absolute',
            left: 0,
            width: '100%',
            height: '55%',
            top:
              mode === 'pulse'
                ? `${-55 + pos * 155}%` // travels top → bottom
                : `${100 - pos * 155}%`, // rewind: travels bottom → top
            background: `linear-gradient(180deg, transparent, ${color}, transparent)`,
            borderRadius: 6 * s,
          }}
        />
      ) : null}
    </div>
  );
};

/** Row width so every node lines up in the centered pipeline column. */
const ROW_W = 860;

const AgentsScene: React.FC = () => {
  const frame = useCurrentFrame(); // local to the Sequence
  const {s, portrait} = useScale();
  // Timings inside the 415-frame scene — the plan is fully readable before
  // anything moves, and the reject loop appears as NEW NODES below (space is
  // reserved but completely invisible until the failure happens).
  //   0-30    the five steps slide in one by one (all pending)
  //   30-60   HOLD ~1s — read the full plan
  //   60-90   context runs → done
  //   90-125  architect runs → done
  //   140-176 you type /nebuko approve gate — it stays forever at gate level
  //   125-205 gate waits (pulsing amber)
  //   205-235 dev implements (pass 1)
  //   235-260 review pass 1 → rejected (✗ stays on screen)
  //   260     the reject section fades in: explanation + dev pass 2 + review
  //            pass 2 + PR row (all space was already reserved)
  //   290-320 dev fixes & resubmits (pass 2)
  //   320-345 review pass 2 → done
  //   345     PR row turns green ✓ — held ~2.3s so it can be read
  const approved = frame >= 205;
  const rejectShown = frame >= 260;
  const stateOf = (
    key: 'context' | 'architect' | 'gate' | 'dev' | 'review' | 'dev2' | 'review2' | 'pr',
  ): 'pending' | 'running' | 'done' | 'gate' | 'rejected' => {
    switch (key) {
      case 'context':
        if (frame < 60) return 'pending';
        return frame < 90 ? 'running' : 'done';
      case 'architect':
        if (frame < 90) return 'pending';
        return frame < 125 ? 'running' : 'done';
      case 'gate':
        if (frame < 125) return 'pending';
        return approved ? 'done' : 'gate';
      case 'dev':
        if (frame < 205) return 'pending';
        return frame < 235 ? 'running' : 'done';
      case 'review':
        if (frame < 235) return 'pending';
        if (frame < 260) return 'running';
        return 'rejected'; // the ✗ stays on screen for the rest of the scene
      case 'dev2':
        if (frame < 290) return 'pending';
        return frame < 320 ? 'running' : 'done';
      case 'review2':
        if (frame < 320) return 'pending';
        return frame < 345 ? 'running' : 'done';
      case 'pr':
        return frame < 345 ? 'pending' : 'done';
    }
  };
  return (
    <AbsoluteFill
      style={{
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        // Title group ↔ content group: the same gap as in the other scenes,
        // so the headline never jumps between scenes. The phone keeps extra
        // air so the title sits higher.
        gap: (portrait ? 80 : 52) * s,
      }}
    >
      {/* Fixed title for the whole scene */}
      <Headline main="Agents work. You approve." small />
      {/* Centered vertical pipeline — its full height (including the reserved
          reject section) is occupied from frame 0, so nothing ever moves. */}
      <div style={{display: 'flex', flexDirection: 'column'}}>
        <StepRow
          label="context"
          desc="gathers repo information"
          state={stateOf('context')}
          from={0}
          width={ROW_W}
        />
        <VConnector mode={frame < 90 ? 'off' : frame < 102 ? 'pulse' : 'done'} />
        <StepRow
          label="architect"
          desc="writes the technical implementation plan"
          state={stateOf('architect')}
          from={6}
          width={ROW_W}
        />
        <VConnector mode={frame < 125 ? 'off' : frame < 137 ? 'pulse' : 'done'} />
        {/* Gate row — FIXED height with the approval comment absolutely
            positioned beside it: the bubble can never push the pipeline
            around, so the blue lines stay connected architect ⇄ gate ⇄ dev. */}
        <div
          style={{
            width: ROW_W * s,
            minHeight: 76 * s,
            position: 'relative',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          <StepRow
            label="gate"
            desc="human must approve to continue"
            state={stateOf('gate')}
            from={12}
            accent="amber" // your approval is a human milestone
          />
          {frame >= 140 ? (
            <div
              style={{
                position: 'absolute',
                // Desktop: beside the gate row. Phone: below it (there is no
                // room to the right — the screen is only 1080 wide), with the
                // space reserved by the spacer right after the row.
                left: portrait ? 102 * s : 590 * s,
                top: portrait ? `calc(100% + ${10 * s}px)` : '50%',
                transform: portrait ? 'none' : 'translateY(-50%)',
              }}
            >
              <TypingBubble
                from={140}
                author="you"
                kind="human"
                text="/nebuko approve gate"
                width={380}
                compact
              />
            </div>
          ) : null}
        </div>
        {/* One continuous bar gate ⇄ dev. On the phone it is taller, spanning
            the zone where the approve comment sits (reserved from frame 0,
            so nothing ever moves). */}
        <VConnector
          mode={frame < 205 ? 'off' : frame < 217 ? 'pulse' : 'done'}
          height={portrait ? 154 : 36}
        />
        <StepRow
          label="dev"
          desc="follows the implementation plan"
          state={stateOf('dev')}
          from={18}
          width={ROW_W}
        />
        <VConnector mode={frame < 235 ? 'off' : frame < 247 ? 'pulse' : 'done'} />
        <StepRow
          label="review"
          desc="reviews implementation against plan"
          state={stateOf('review')}
          from={24}
          width={ROW_W}
        />
        {/* Reject section — reserved from the start, but fully invisible until
            the review fails: no ghost boxes, no gray connectors. */}
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            opacity: interpolate(frame, [260, 272], [0, 1], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            }),
          }}
        >
          {/* The rejection explanation — between the ✗ review and the new
              dev, on two lines so the flow stays centered on screen */}
          <div
            style={{
              minHeight: 78 * s, // row + connector height merged: ONE bar spans it all
              display: 'flex',
              alignItems: 'center',
              position: 'relative',
            }}
          >
            {/* The blue line runs THROUGH this row, so the connector chain
                review ⇄ dev never breaks — it sits at the node column, clear
                of the text. */}
            <div
              style={{
                position: 'absolute',
                left: 27 * s,
                top: 0,
                bottom: 0,
                width: 18 * s,
                borderRadius: 9 * s,
                background: frame < 290 ? C.border : `${C.blue}88`,
                border: `${2 * s}px solid ${frame < 290 ? 'transparent' : `${C.blue}55`}`,
                boxShadow: frame < 290 ? 'none' : `0 0 ${20 * s}px ${C.blue}55`,
              }}
            />
            {/* One line, left-aligned under the review label */}
            <span
              style={{
                paddingLeft: 102 * s, // same indent as the step labels
                fontFamily: C.sans,
                fontWeight: 600,
                fontSize: 27 * s,
                color: C.text,
              }}
            >
              <span style={{color: C.red, fontWeight: 800}}>✗ 1 test failed</span>
              <span style={{color: C.muted}}> → sent back to dev</span>
            </span>
          </div>
          <StepRow
            label="dev"
            desc="fixes the failing test & resubmits"
            state={stateOf('dev2')}
            from={266}
            width={ROW_W}
          />
          <VConnector mode={frame < 320 ? 'off' : frame < 332 ? 'pulse' : 'done'} />
          <StepRow
            label="review"
            desc="reviews implementation against plan"
            state={stateOf('review2')}
            from={268}
            width={ROW_W}
          />
          <VConnector mode={frame < 345 ? 'off' : frame < 357 ? 'pulse' : 'done'} />
          {/* The final output of Nebuko — a step like the others, with a check */}
          <StepRow
            label="Pull Request"
            desc="open & ready for your validation & merge"
            state={stateOf('pr')}
            from={270}
            width={ROW_W}
            accent="amber" // the PR is yours — a human milestone
          />
        </div>
      </div>
    </AbsoluteFill>
  );
};

/**
 * Closing scene — static full-screen: what Nebuko is (workflows you own and
 * share), where it lives (GitHub, top to bottom), and the brand. One single
 * entrance, then nothing moves: the video loops right after it settles.
 */
const FinalScene: React.FC = () => {
  const frame = useCurrentFrame();
  const {s, portrait} = useScale();
  const inSpr = spring({frame, fps: 30, config: {damping: 200}});
  return (
    <AbsoluteFill
      style={{
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        gap: portrait ? 70 * s : 52 * s, // same title-to-content gap as every scene
        opacity: inSpr,
        transform: `translateY(${interpolate(inSpr, [0, 1], [40, 0]) * s}px)`,
      }}
    >
      {/* What it is — one line per idea */}
      <div style={{textAlign: 'center', maxWidth: 1500 * s}}>
        <div
          style={{
            fontFamily: C.sans,
            fontWeight: 800,
            fontSize: 56 * s, // ~12% smaller: keeps ~10% side margins, clears phone UI overlays
            lineHeight: 1.25,
          }}
        >
          Describe the way you work.
          <br />
          <GradientWord size={56}>Share it everywhere.</GradientWord>
          <br />
          Run it on GitHub.
        </div>
      </div>
      {/* Brand — logo, tagline chips, domain */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 14 * s,
        }}
      >
        <img
          src={staticFile('logo-horizontal-dark.svg')}
          alt="Nebuko"
          style={{width: 640 * s, height: 'auto'}}
        />
        <div
          style={{
            fontFamily: C.sans,
            fontWeight: 700,
            fontSize: (portrait ? 30 : 20) * s,
            letterSpacing: (portrait ? 6 : 5) * s,
            lineHeight: 1.5,
            textTransform: 'uppercase',
            color: C.muted,
            textAlign: 'center',
          }}
        >
          {portrait ? (
            <>
              Open Source
              <br />
              Bring Your Own Models
            </>
          ) : (
            <>Open Source · Bring Your Own Models</>
          )}
        </div>
        {/* The domain stands on its own: extra space above it so it reads as
            the last line of the block, not a third caps line. */}
        <div
          style={{
            fontFamily: C.sans,
            fontWeight: 600,
            fontSize: 44 * s,
            marginTop: 26 * s,
          }}
        >
          <GradientWord size={44}>nebuko.dev</GradientWord>
        </div>
      </div>
    </AbsoluteFill>
  );
};

/* ------------------------------ component ------------------------------ */

const SceneWrapper: React.FC<{
  from: number;
  duration: number;
  hold?: boolean; // keep the scene fully visible until its last frame
  children: React.ReactNode;
}> = ({from, duration, hold = false, children}) => {
  const frame = useCurrentFrame();
  const {opacity, translateY} = fadeSlide(frame, from, from + duration, 40, hold);
  return (
    <Sequence from={from} durationInFrames={duration} style={{opacity}}>
      <AbsoluteFill style={{transform: `translateY(${translateY}px)`}}>{children}</AbsoluteFill>
    </Sequence>
  );
};

export const HeroVideo: React.FC = () => {
  const frame = useCurrentFrame();
  const {s} = useScale();
  // Small watermark logo in a top corner on every scene EXCEPT the opening
  // and closing (which carry the full logo) — at ~45% opacity it is present
  // without competing with the scene. Fades in after the intro, out before
  // the closing card.
  const watermarkOpacity = interpolate(
    frame,
    [SCENES.intro[1], SCENES.intro[1] + 12, SCENES.final[0] - 12, SCENES.final[0]],
    [0, 0.45, 0.45, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );
  return (
    <AbsoluteFill style={{background: C.bg, fontFamily: C.sans, color: C.text}}>
      <FontFaceStyle />
      <Background />
      {watermarkOpacity > 0 ? (
        <img
          src={staticFile('logo-horizontal-dark.svg')}
          alt=""
          style={{
            position: 'absolute',
            top: 36 * s,
            left: 44 * s,
            width: 250 * s,
            opacity: watermarkOpacity,
          }}
        />
      ) : null}
      <SceneWrapper from={SCENES.intro[0]} duration={SCENES.intro[1] - SCENES.intro[0]}>
        <IntroScene />
      </SceneWrapper>
      <SceneWrapper from={SCENES.start[0]} duration={SCENES.start[1] - SCENES.start[0]}>
        <StartScene />
      </SceneWrapper>
      <SceneWrapper from={SCENES.agents[0]} duration={SCENES.agents[1] - SCENES.agents[0]}>
        <AgentsScene />
      </SceneWrapper>
      {/* The closing scene holds at full opacity until the last frame — the
          video loops (or stops) on it, so viewers get time to read. */}
      <SceneWrapper
        from={SCENES.final[0]}
        duration={SCENES.final[1] - SCENES.final[0]}
        hold
      >
        <FinalScene />
      </SceneWrapper>
    </AbsoluteFill>
  );
};
