import React from 'react';
import {GithubComment, GithubIssueMainPost, IssueFrame, ThemeName} from './github-ui';
import {eventById} from './data';

export const GatePreview: React.FC<{theme?: ThemeName}> = ({theme = 'dark'}) => (
  <IssueFrame theme={theme} commentCount={3} composerText="" showRepoChrome={false} participants={['🧑‍💻', '📐']}>
    <GithubIssueMainPost
      author="alex-dev"
      avatar="🧑‍💻"
      time="3 hours ago"
      body={'The circuit breaker trips for the whole repository when a single issue fails three times.\n\nWe should be able to raise the limit for **one issue** only, e.g. `/nebuko run workflow --max-failures 5`.'}
    />
    <GithubComment event={eventById('e1')} />
    <GithubComment event={eventById('e7')} gate gateText="Gate: the workflow is on hold. Reply /nebuko approve gate to continue." />
  </IssueFrame>
);
