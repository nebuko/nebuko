import React from 'react';
import {Composition} from 'remotion';
import {Preview, PREVIEW_DURATION} from './Preview';
import {HeroVideo, HERO_DURATION, HERO_FPS} from './hero/HeroVideo';

export const Root: React.FC = () => (
  <>
    <Composition id="GithubPreview" component={Preview} durationInFrames={PREVIEW_DURATION} fps={30} width={1280} height={900} />
    <Composition id="Hero" component={HeroVideo} durationInFrames={HERO_DURATION} fps={HERO_FPS} width={1920} height={1080} />
    <Composition id="HeroPhone" component={HeroVideo} durationInFrames={HERO_DURATION} fps={HERO_FPS} width={1080} height={1920} />
  </>
);
