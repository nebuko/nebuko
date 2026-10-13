import React from 'react';
import {GithubComment, GithubIssueMainPost, IssueFrame, ThemeName} from './github-ui';
import {plan, trigger} from './data';

export const GatePreview: React.FC<{theme?: ThemeName}> = ({theme = 'dark'}) => (
  <IssueFrame theme={theme} commentCount={3} composerText="" showRepoChrome={false} participants={['🧑‍💻', '📐']}>
    <GithubIssueMainPost
      author="alex-dev"
      avatar="🧑‍💻"
      time="3 hours ago"
      body={'The circuit breaker trips for the whole repository when a single issue fails three times.\n\nWe should be able to raise the limit for **one issue** only, e.g. `/nebuko run workflow --max-failures 5`.'}
    />
    <GithubComment event={trigger} />
    <GithubComment event={plan} gate gateText="Gate: the workflow is on hold. Reply /nebuko approve gate to continue." />
  </IssueFrame>
);
