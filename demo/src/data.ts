import e1IssueBody from './comments/e1-issue-body.md';
import e2ArchitectAnalyze from './comments/e2-architect-analyze.md';
import e3ContextReport from './comments/e3-context-report.md';
import e4ArchitectPlan from './comments/e4-architect-plan.md';
import e5DevImplement from './comments/e5-dev-implement.md';
import e6ReviewApproved from './comments/e6-review-approved.md';
import e7MetricsReport from './comments/e7-metrics-report.md';

export interface ThreadEvent {
  id: string;
  kind: 'trigger' | 'agent' | 'approve' | 'review';
  author: string;
  avatar: string;
  time: string;
  title: string;
  body: string;
  stateLabel?: string;   // the WORKFLOW_STATE badge under the comment (or the state JSON)
  stateVersion?: string; // "v3": shown as "Workflow State (v3)" when stateLabel is JSON
  pinned?: boolean;
  metrics?: { input: string; output: string; toolCalls?: string; runtime?: string };
  code?: string;         // code block (the PR diff)
  caption?: string;      // explainer card shown over the video while this step plays
}

/**
 * A real GitHub comment, pasted verbatim in src/comments/*.md, → the ThreadEvent fields it fills.
 * The `<details>` "Workflow State (vN)" block becomes the collapsible state; the rest is the body
 * (HTML comments such as `<!-- AGENT_RUN_CONFIG … -->` are hidden by the Markdown renderer).
 */
export const fromGithub = (raw: string): Pick<ThreadEvent, 'body' | 'stateLabel' | 'stateVersion'> => {
  const details = /<details>\s*<summary>([\s\S]*?)<\/summary>([\s\S]*?)<\/details>/.exec(raw);
  const isState = !!details && /Workflow State/.test(details[1]);
  const json = isState ? /```json\s*([\s\S]*?)```/.exec(details![2])?.[1] : undefined;
  return {
    body: (isState ? raw.replace(details![0], '') : raw).trim(),
    stateLabel: json?.replace(/<!--[\s\S]*?-->/g, '').trim(),
    stateVersion: isState ? /\((v\d+)\)/.exec(details![1])?.[1] : undefined,
  };
};

// Real data from issue #839 / PR #843 — replace with your own run.
// Long comments live verbatim in ./comments/*.md and go through fromGithub().
export const events: ThreadEvent[] = [
  {
    id: 'e1',
    kind: 'trigger',
    author: 'you',
    avatar: '👤',
    time: '14:32',
    title: '/nebuko run workflow --name default',
    body: '',
    caption: 'This run workflow command starts the Nebuko workflow on the issue.',
  },
  {
    id: 'e2',
    kind: 'agent',
    author: 'nebuko ',
    avatar: '',
    time: '14:32',
    title: 'Nebuko architect agent started — View workflow run logs',
    body: ''
  },
  {
    id: 'e3',
    kind: 'agent',
    author: 'nebuko · architect',
    avatar: '📐',
    time: '14:35',
    title: '',
    ...fromGithub(e2ArchitectAnalyze),
    caption: 'The architect agent triages the issue and hands context gathering to the next agent.',
  },
  {
    id: 'e4',
    kind: 'agent',
    author: 'nebuko ',
    avatar: '',
    time: '14:36',
    title: 'Nebuko context agent started — View workflow run logs',
    body: ''
  },
  {
    id: 'e5',
    kind: 'agent',
    author: 'nebuko · context',
    avatar: '🔍',
    time: '14:45',
    title: 'Context Collection Report',
    ...fromGithub(e3ContextReport),
    stateLabel: 'WORKFLOW_STATE · step: context → plan',
    metrics: { input: '2,045,617', output: '25,443', toolCalls: '71', runtime: '5 min' },
    caption: 'The context agent traces the code involved and reports back, with token and runtime metrics.',
  },
  {
    id: 'e6',
    kind: 'agent',
    author: 'nebuko ',
    avatar: '',
    time: '14:46',
    title: 'Nebuko architect agent started — View workflow run logs',
    body: ''
  },
  {
    id: 'e7',
    kind: 'agent',
    author: 'nebuko · architect',
    avatar: '📐',
    time: '15:00',
    title: 'Architecture proposal — Object Design Anchor',
    ...fromGithub(e4ArchitectPlan),
    stateLabel: 'WORKFLOW_STATE · step: plan · dispatch: HOLD · pin: true',
    pinned: true,
    metrics: { input: '71,549', output: '18,131', toolCalls: '2', runtime: '8 min' },
    caption: 'The architect proposes a design. The workflow holds here until a human approves it.',
  },
  {
    id: 'e8',
    kind: 'approve',
    author: 'you',
    avatar: '👤',
    time: '15:12',
    title: '/nebuko approve gate',
    body: 'Gate released — the state is re-read and the engine dispatches the next step.',
    caption: 'One comment releases the gate: the engine dispatches the next step.',
  },
  {
    id: 'e9',
    kind: 'agent',
    author: 'nebuko ',
    avatar: '',
    time: '15:13',
    title: 'Nebuko dev agent started — View workflow run logs',
    body: ''
  },
  {
    id: 'e10',
    kind: 'agent',
    author: 'nebuko · dev',
    avatar: '💻',
    time: '15:52',
    title: 'Implemented — PR opened',
    ...fromGithub(e5DevImplement),
    code: `def test_with_limit_does_not_mutate_the_receiver(self):
    base = CircuitBreakerState(limit=20)
    base.with_limit(35)
    self.assertEqual(base, CircuitBreakerState(limit=20))`,
    stateLabel: 'WORKFLOW_STATE · step: implement → review',
    caption: 'The dev agent implements the change and opens a pull request.',
  },
  {
    id: 'e11',
    kind: 'agent',
    author: 'nebuko ',
    avatar: '',
    time: '15:53',
    title: 'Nebuko review agent started — View workflow run logs',
    body: ''
  },
  {
    id: 'e12',
    kind: 'review',
    author: 'nebuko · review (GLM — different model)',
    avatar: '⚖️',
    time: '16:29',
    title: 'Review verdict — APPROVED',
    ...fromGithub(e6ReviewApproved),
    stateLabel: 'WORKFLOW_STATE · matched_rule: workflow:default/step:review',
    caption: 'A review agent running on a different model checks the diff against the approved design.',
  },
];

/** Look up one event by id (throws so a typo fails loudly in the studio). */
export const eventById = (id: string): ThreadEvent => {
  const e = events.find((ev) => ev.id === id);
  if (!e) throw new Error(`No event with id "${id}" in data.ts`);
  return e;
};

// Posted by the metrics summary agent when the issue closes (the video's last comment).
export const metricsSummary: ThreadEvent = {
  id: 'metrics',
  kind: 'agent',
  author: 'nebuko · metrics',
  avatar: '📊',
  time: 'just now',
  title: '',
  caption: 'Closing the issue posts a summary: tokens, tool calls and time, per agent and for the humans.',
  ...fromGithub(e7MetricsReport),
};
