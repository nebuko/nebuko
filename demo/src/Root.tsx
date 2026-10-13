import React from 'react';
import {Composition} from 'remotion';
import {Preview} from './Preview';
import {GatePreview} from './GatePreview';
import {PrFiles, PrFlowDemo} from './PrFlowDemo';

export const Root: React.FC = () => (
  <>
    <Composition id="GithubPreview" component={Preview} durationInFrames={150} fps={30} width={1280} height={900} />
    <Composition id="GatePreview" component={GatePreview} durationInFrames={150} fps={30} width={1280} height={1100} />
    <Composition id="PrFlowDemo" component={PrFlowDemo} durationInFrames={480} fps={30} width={1280} height={1000} />
    <Composition id="PrFiles" component={PrFiles} durationInFrames={90} fps={30} width={1280} height={700} />
  </>
);
