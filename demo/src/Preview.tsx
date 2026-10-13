import React from 'react';
import {GithubComment, GithubIssueMainPost, GithubTimelineEvent, IssueFrame, ThemeName} from './github-ui';
import {impl, plan, trigger} from './data';

export const Preview: React.FC<{theme?: ThemeName}> = ({theme = 'light'}) => (
  <IssueFrame
    theme={theme}
    commentCount={4}
    anchor="bottom"
    composerText="/nebuko approve gate"
    participants={['🧑‍💻', '📐', '💻']}
    linkedPr={{number: 842, title: 'feat: per-issue circuit breaker limit'}}
  >
    <GithubIssueMainPost
      author="alex-dev"
      avatar="🧑‍💻"
      time="3 hours ago"
      body={
        'The circuit breaker trips for the whole repository when a single issue fails three times.\n\nWe should be able to raise the limit for **one issue** only, e.g. `/nebuko run workflow --max-failures 5`.\n\n- keep the default at 3\n- log the override in the workflow state'
      }
    />
    <GithubComment event={trigger} />
    <GithubComment event={plan} gate />
    <GithubTimelineEvent kind="pr" actor="nebuko-dev" time="12 minutes ago">
      linked a pull request that will close this issue
    </GithubTimelineEvent>
    <GithubComment event={{...impl, reactions: [{emoji: '👍', count: 2, mine: true}, {emoji: '🚀', count: 1}]}} />
  </IssueFrame>
);
