import React from 'react';
import {Composition} from 'remotion';
import {Preview, PREVIEW_DURATION} from './Preview';

export const Root: React.FC = () => (
  <>
    <Composition id="GithubPreview" component={Preview} durationInFrames={PREVIEW_DURATION} fps={30} width={1280} height={900} />
  </>
);
