export interface ThreadEvent {
  id: string;
  kind: 'trigger' | 'agent' | 'approve' | 'review';
  author: string;
  avatar: string;
  time: string;
  title: string;
  body: string;
  stateLabel?: string;   // the WORKFLOW_STATE badge under the comment
  pinned?: boolean;
  metrics?: { input: string; output: string; toolCalls?: string; runtime?: string };
  code?: string;         // code block (the PR diff)
  caption?: string;      // explainer card shown over the video while this step plays
}

// Real data from issue #839 / PR #843 — replace with your own run.
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
    title: 'Analyze — triage & delegation',
    body: 'Triage done: per-issue circuit-breaker limit. Delegating context collection to @@context.',
    stateLabel: 'WORKFLOW_STATE · step: analyze → context',
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
    body: 'I traced the 25-call limit end to end: Python module, engine guard, reusable workflow input, and the docs.',
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
    body: 'Proposed design: a pydantic CircuitBreakerState model, co-located with the existing SoftRetryState precedent. Workflow waiting for human approval of the Object Design Anchor.',
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
    body: 'Added /nebuko update circuit-breaker --value <n>: a deterministic step, no agent. Diff excerpts:',
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
    body: 'Full diff reviewed against the approved Object Design Anchor. Contract fidelity: all met.',
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
  body: `<!-- bot-workflow-terminal -->
## 📊 Agent Statistics Summary

| Agent | Runs | Input Tokens | Output Tokens | Cache Hit Rate | Reasoning Tokens | Cost (USD) | Avg Max Context Window | Avg Latency (ms) | Avg LLM Runtime (ms) | Avg Tokens/sec | Avg TTFT (s) | Avg Tool Calls | Tool Errors | Fix Requests | Per-Tool Calls |
|-------|------|-------------|---------------|----------------|------------------|-----------|-------------------|------------------|----------------------|----------------|--------------|----------------|-------------|--------------|----------------|
| Architect | 2 | 112,283 | 26,197 | n/a | 0 | $0.0000 | 0 | 41322 | 148175 | 121.0 | 1.56 | 3.5 | 0 | 0 | dispatch_next: 4, finish: 3 |
| Context | 1 | 2,309,637 | 31,372 | n/a | 0 | $0.0000 | 0 | 6847 | 246496 | 222.9 | 2.66 | 66.0 | 0 | 0 | dispatch_next: 1, finish: 1, task_tracker: 2, terminal: 62 |
| Dev | 7 | 48,014,546 | 450,392 | n/a | 0 | $0.0000 | 0 | 9948 | 867978 | 155.4 | 2.45 | 113.1 | 7 | 0 | dispatch_next: 11, editor: 1, file_editor: 208, finish: 12, task_tracker: 10, terminal: 550 |
| Review | 6 | 11,253,351 | 159,005 | n/a | 0 | $0.0000 | 0 | 9016 | 440148 | 3926.4 | 2.77 | 45.5 | 4 | 0 | dispatch_next: 9, finish: 11, terminal: 245, think: 8 |

**Totals:** 16 runs · 61,689,817 input tokens · 666,966 output tokens · $0.0000

## 📊 Model Statistics Summary

| Model | Runs | Input Tokens | Output Tokens | Cache Hit Rate | Reasoning Tokens | Cost (USD) | Avg Max Context Window | Avg Latency (ms) | Avg LLM Runtime (ms) | Avg Tokens/sec | Avg TTFT (s) | Avg Tool Calls | Tool Errors | Fix Requests | Per-Tool Calls |
|-------|------|-------------|---------------|----------------|------------------|-----------|-------------------|------------------|----------------------|----------------|--------------|----------------|-------------|--------------|----------------|
| default | 16 | 61,689,817 | 666,966 | n/a | 0 | $0.0000 | 0 | 13326 | 578724 | 1569.4 | 2.47 | 71.1 | 11 | 0 | dispatch_next: 25, editor: 1, file_editor: 208, finish: 27, task_tracker: 12, terminal: 857, think: 8 |

## 🧑‍💻 Human metrics

| Metric | Value |
|--------|-------|
| Human comments | 7 |
| Human interventions after implementation | 5 |
| Human workflow commands | 5 |
| Agent tool invocations | 1138 |
| Estimated human reading time | 43.4 min |
| Wall-clock duration (first agent start → close) | 2364.0 min |
| Agent time spent (benefit proxy) | 154.3 min |
| Average human response latency | 360.1 min |
| Accepted on first pass | No |

> _Human metrics are estimates/proxies derived from issue text and workflow timestamps (reading speed: 200 wpm; scope: issue body + all comments except context-agent reports, non-rendered markup excluded). Agent time spent is a proxy for human effort avoided, not a measured saving._

> _This comment was auto-generated by the metrics summary agent on issue close._`,
};
