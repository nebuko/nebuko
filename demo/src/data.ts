export interface ThreadEvent {
  id: string;
  kind: 'trigger' | 'agent' | 'approve' | 'review';
  author: string;
  avatar: string;
  time: string;
  title: string;
  body: string;
  stateLabel?: string;   // le badge WORKFLOW_STATE sous le commentaire
  pinned?: boolean;
  metrics?: { input: string; output: string; toolCalls?: string; runtime?: string };
  code?: string;         // bloc code (le diff du PR)
  caption?: string;      // explainer card shown over the video while this step plays
}

// Données réelles de l'issue #839 / PR #843 — remplace par ta propre run.
export const events: ThreadEvent[] = [
  {
    id: 'e1',
    kind: 'trigger',
    author: 'you',
    avatar: '👤',
    time: '14:32',
    title: '/nebuko run workflow --name default',
    body: 'Une seule ligne dans l’issue. Le moteur prend le relais.',
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
    title: 'Analyze — triage & délégation',
    body: 'Triage terminé : limite circuit-breaker par issue. Délégation de la collecte de contexte à @@context.',
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
    body: 'J’ai tracé la limite des 25 appels de bout en bout : module Python, guard du moteur, input du reusable workflow, et la doc.',
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
    body: 'Design proposé : un modèle pydantic CircuitBreakerState, co-localisé avec le précédent SoftRetryState. Workflow en attente d’approbation humaine de l’Object Design Anchor.',
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
    body: 'Gate libérée — le state est relu, le moteur dispatche le step suivant.',
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
    title: 'Implementé — PR ouverte',
    body: 'Ajout de /nebuko update circuit-breaker --value <n> : step déterministe, sans agent. Extraits du diff :',
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
    author: 'nebuko · review (GLM — autre modèle)',
    avatar: '⚖️',
    time: '16:29',
    title: 'Review verdict — APPROVED',
    body: 'Diff complet relu contre l’Object Design Anchor approuvé. Contract fidelity : all met.',
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
