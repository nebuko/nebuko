import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {ThemeName, THEMES} from './github-theme';

/**
 * Explainer card ("this command starts the workflow"), meant for IssueFrame's reserved `narration` band, between the newest comment and the composer.
 * Not part of GitHub's UI: it uses the theme's inverted colors so it reads as a voice-over.
 * Frames are relative: wrap it in a <Sequence durationInFrames={…}> to set when it shows.
 */
export const NARRATION_HEIGHT = 88;

export const Narration: React.FC<{text: string; theme?: ThemeName; step?: string}> = ({text, theme = 'light', step}) => {
  const t = THEMES[theme];
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const enter = spring({frame, fps, config: {damping: 200}, durationInFrames: 15});
  const exit = interpolate(frame, [durationInFrames - 8, durationInFrames], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const p = Math.min(enter, exit);
  return (
    <div style={{height: '100%', display: 'flex', alignItems: 'center'}}>
      <div
        style={{
          width: '100%',
          boxSizing: 'border-box',
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          padding: '14px 22px 14px 16px',
          borderRadius: 12,
          background: t.text,
          color: t.bg,
          borderLeft: '5px solid ' + t.accent,
          boxShadow: t.shadow,
          fontFamily: t.font,
          fontSize: 20,
          lineHeight: 1.35,
          fontWeight: 500,
          opacity: p,
          transform: `translateY(${(1 - p) * 16}px)`,
        }}
      >
        <span
          style={{
            flexShrink: 0,
            width: 30,
            height: 30,
            borderRadius: '50%',
            background: t.accent,
            color: '#ffffff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 17,
            fontWeight: 700,
            fontFamily: 'Georgia, serif',
          }}
        >
          i
        </span>
        <span>
          {step ? <span style={{opacity: 0.6, marginRight: 8}}>{step}</span> : null}
          {text}
        </span>
      </div>
    </div>
  );
};
