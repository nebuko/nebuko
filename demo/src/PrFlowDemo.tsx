import React from 'react';
import {Series} from 'remotion';
import {
  GithubComment,
  GithubCommits,
  GithubDiffFile,
  GithubFilesToolbar,
  GithubIssueMainPost,
  GithubMergeBox,
  GithubReview,
  GithubTimelineEvent,
  IssueFrame,
  PullRequestFrame,
  ThemeName,
} from './github-ui';
import {eventById} from './data';

/* ---------------------------------------------------------------------
 * Example flow: agents finished → open the PR → approve → squash merge.
 * Each <Series.Sequence> is one scene; frames inside are relative to it,
 * so the animated cursor / halo effects restart at every scene.
 * ------------------------------------------------------------------- */

const COMMITS = [
  {sha: 'a1b2c3d', message: 'test: add failing test for the per-issue limit'},
  {sha: 'e4f5a6b', message: 'feat: read limit_for(issue) in the circuit breaker'},
  {sha: '9c8d7e6', message: 'docs: document the override'},
];

const CHECKS = [
  {name: 'CI / test (3.12)', desc: 'Successful in 1m 42s', state: 'success' as const},
  {name: 'CI / lint', desc: 'Successful in 18s', state: 'success' as const},
  {name: 'nebuko / review', desc: 'Successful in 3m 10s', state: 'success' as const},
];

export type Phase = 'review' | 'popover' | 'approved' | 'confirm' | 'merged';

/** What the PR page shows. The default is a hand-written example; Preview passes the real run's PR. */
export type PrContent = {
  number: number;
  title: string;
  body: string;
  /** optional code excerpt under the description */
  code?: string;
  commits: {sha: string; message: string}[];
  /** the review agent's comment */
  botReview: string;
  /** what the human types when approving */
  approveText: string;
};

export const SAMPLE_PR: PrContent = {
  number: 842,
  title: 'feat: per-issue circuit breaker limit',
  body: 'Implements the per-issue override from #839.\n\n- `limit_for(issue)` reads the limit from the workflow state\n- default stays at **3**',
  commits: COMMITS,
  botReview: 'Reviewed with a different model: no blocking issue.\n\nOne nit: log the override in the state (see `circuit_breaker.py`).',
  approveText: 'LGTM — tests cover the override.',
};

export const PrScene: React.FC<{
  phase: Phase;
  theme: ThemeName;
  pr?: PrContent;
  collapse?: number;
  narration?: React.ReactNode;
  narrationHeight?: number;
}> = ({phase, theme, pr = SAMPLE_PR, collapse, narration, narrationHeight}) => {
  const approved = phase === 'approved' || phase === 'confirm' || phase === 'merged';
  const merged = phase === 'merged';
  return (
    <PullRequestFrame
      theme={theme}
      number={pr.number}
      title={pr.title}
      commits={pr.commits.length}
      collapse={collapse}
      narration={narration}
      narrationHeight={narrationHeight}
      state={merged ? 'merged' : 'open'}
      mergedAgo="just now"
      commentCount={approved ? 5 : 3}
      reviewers={[{name: 'alex-dev', state: approved ? 'approved' : 'pending'}]}
      assignees={['nebuko-dev']}
      labels={[{name: 'bug', color: '#d73a4a'}]}
      linkedIssue={{number: 839, title: 'Allow to improve circuit breaker for one issue', closed: merged}}
      participants={['💻', '👀', '🧑‍💻']}
    >
      <GithubIssueMainPost
        author="nebuko-dev"
        avatar="💻"
        time="12 minutes ago"
        verb="commented"
        human={false}
        body={pr.body}
        code={pr.code}
      />
      <GithubCommits actor="nebuko-dev" time="12 minutes ago" commits={pr.commits} />
      <GithubReview
        actor="nebuko-review"
        state="commented"
        time="8 minutes ago"
        body={pr.botReview}
      />
      {approved ? <GithubReview actor="alex-dev" state="approved" time="just now" body={pr.approveText} animate={phase === 'approved'} /> : null}
      {merged ? (
        <GithubTimelineEvent kind="merged" actor="alex-dev" animate>
          merged commit <span style={{fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12}}>5f3e2d1</span> into main just now
        </GithubTimelineEvent>
      ) : null}
      <GithubMergeBox
        review={approved ? 'approved' : 'required'}
        checks="passed"
        checkRows={CHECKS}
        method="squash"
        mergeState={merged ? 'merged' : phase === 'confirm' ? 'confirm' : 'ready'}
        commitTitle={`${pr.title} (#${pr.number})`}
        commitBody={pr.body.split('\n')[0]}
        pointer={phase === 'review' ? 'review' : phase === 'approved' ? 'merge' : phase === 'confirm' ? 'confirm' : phase === 'merged' ? 'delete' : null}
        reviewPopover={phase === 'popover' ? {choice: 'approve', text: pr.approveText, submitPointed: true} : null}
      />
    </PullRequestFrame>
  );
};

const IssueScene: React.FC<{theme: ThemeName}> = ({theme}) => (
  <IssueFrame
    theme={theme}
    commentCount={4}
    anchor="bottom"
    participants={['🧑‍💻', '📐', '💻']}
    linkedPr={{number: 842, title: 'feat: per-issue circuit breaker limit'}}
  >
    <GithubIssueMainPost
      author="alex-dev"
      avatar="🧑‍💻"
      time="3 hours ago"
      body={'The circuit breaker trips for the whole repository when a single issue fails three times.\n\nWe should be able to raise the limit for **one issue** only.'}
    />
    <GithubComment event={eventById('e7')} />
    <GithubComment event={eventById('e10')} />
    <GithubTimelineEvent kind="pr" actor="nebuko-dev" time="12 minutes ago" pointer>
      linked a pull request #842 that will close this issue
    </GithubTimelineEvent>
  </IssueFrame>
);

export const PrFlowDemo: React.FC<{theme?: ThemeName}> = ({theme = 'light'}) => (
  <Series>
    <Series.Sequence durationInFrames={60}>
      <IssueScene theme={theme} />
    </Series.Sequence>
    <Series.Sequence durationInFrames={60}>
      <PrScene phase="review" theme={theme} />
    </Series.Sequence>
    <Series.Sequence durationInFrames={90}>
      <PrScene phase="popover" theme={theme} />
    </Series.Sequence>
    <Series.Sequence durationInFrames={90}>
      <PrScene phase="approved" theme={theme} />
    </Series.Sequence>
    <Series.Sequence durationInFrames={90}>
      <PrScene phase="confirm" theme={theme} />
    </Series.Sequence>
    <Series.Sequence durationInFrames={90}>
      <PrScene phase="merged" theme={theme} />
    </Series.Sequence>
  </Series>
);

/** Alternative path: approve from the "Files changed" tab. */
export const PrFiles: React.FC<{theme?: ThemeName}> = ({theme = 'light'}) => (
  <PullRequestFrame theme={theme} tab="files" showRepoChrome={false}>
    <GithubFilesToolbar
      files={2}
      additions={6}
      deletions={1}
      reviewPopover={{choice: 'approve', text: 'LGTM — tests cover the override.', submitPointed: true}}
    />
    <GithubDiffFile
      path="src/circuit_breaker.py"
      lines={[
        '@@ -10,6 +10,9 @@ def check(issue):',
        '     failures = count_failures(issue)',
        '-    if failures >= 3:',
        '+    limit = limit_for(issue)',
        '+    if failures >= limit:',
        '         trip()',
        '+        log_override(issue, limit)',
        '     return failures',
      ]}
    />
  </PullRequestFrame>
);
