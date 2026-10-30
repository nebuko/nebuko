import React from 'react';
import {AbsoluteFill, Composition} from 'remotion';
import {Preview, PREVIEW_DURATION} from './Preview';
import {HeroVideo, HERO_DURATION, HERO_FPS} from './hero/HeroVideo';

/**
 * The GitHub UI is designed on a 1280x720 canvas (16:9) and scaled 1.5x to fill
 * a 1920x1080 composition, so font sizes keep their visual weight at 1080p.
 * clientHeight/offsetHeight measurements inside stay in design pixels, so the
 * thread scroll/push math is unaffected.
 */
const DESIGN_WIDTH = 1280;
const DESIGN_HEIGHT = 720;

export const ScaleTo1080p: React.FC<{children: React.ReactNode}> = ({children}) => (
  <AbsoluteFill style={{background: '#fff'}}>
    <AbsoluteFill
      style={{
        width: DESIGN_WIDTH,
        height: DESIGN_HEIGHT,
        transform: 'scale(1.5)',
        transformOrigin: 'top left',
      }}
    >
      {children}
    </AbsoluteFill>
  </AbsoluteFill>
);

export const Root: React.FC = () => (
  <>
    <Composition
      id="GithubPreview"
      component={() => (
        <ScaleTo1080p>
          <Preview />
        </ScaleTo1080p>
      )}
      durationInFrames={PREVIEW_DURATION}
      fps={30}
      width={1920}
      height={1080}
    />
    <Composition id="Hero" component={HeroVideo} durationInFrames={HERO_DURATION} fps={HERO_FPS} width={1920} height={1080} />
    <Composition id="HeroPhone" component={HeroVideo} durationInFrames={HERO_DURATION} fps={HERO_FPS} width={1080} height={1920} />
  </>
);
