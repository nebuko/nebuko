import React from 'react';
import {Composition} from 'remotion';
import {NebukoDemo, TOTAL_FRAMES, FPS} from './NebukoDemo';
import {TestFrame, TEST_DURATION} from './TestFrame';

export const RemotionRoot: React.FC = () => (
  <>
    <Composition id="NebukoDemo" component={NebukoDemo}
      durationInFrames={TOTAL_FRAMES} fps={FPS} width={1920} height={1080} />
    <Composition id="TestFrame" component={TestFrame}
      durationInFrames={TEST_DURATION} fps={FPS} width={1920} height={1080} />
  </>
);