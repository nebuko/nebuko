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
    },
    {
        id: 'e2',
        kind: 'agent',
        author: 'nebuko · architect',
        avatar: '📐',
        time: '14:35',
        title: 'Analyze — triage & délégation',
        body: 'Triage terminé : limite circuit-breaker par issue. Délégation de la collecte de contexte à @@context.',
        stateLabel: 'WORKFLOW_STATE · step: analyze → context',
    },
    {
        id: 'e3',
        kind: 'agent',
        author: 'nebuko · context',
        avatar: '🔍',
        time: '14:45',
        title: 'Context Collection Report',
        body: 'J’ai tracé la limite des 25 appels de bout en bout : module Python, guard du moteur, input du reusable workflow, et la doc.',
        stateLabel: 'WORKFLOW_STATE · step: context → plan',
        metrics: { input: '2,045,617', output: '25,443', toolCalls: '71', runtime: '5 min' },
    },
    {
        id: 'e4',
        kind: 'agent',
        author: 'nebuko · architect',
        avatar: '📐',
        time: '15:00',
        title: 'Architecture proposal — Object Design Anchor',
        body: 'Design proposé : un modèle pydantic CircuitBreakerState, co-localisé avec le précédent SoftRetryState. Workflow en attente d’approbation humaine de l’Object Design Anchor.',
        stateLabel: 'WORKFLOW_STATE · step: plan · dispatch: HOLD · pin: true',
        pinned: true,
        metrics: { input: '71,549', output: '18,131', toolCalls: '2', runtime: '8 min' },
    },
    {
        id: 'e5',
        kind: 'approve',
        author: 'you',
        avatar: '👤',
        time: '15:12',
        title: '/nebuko approve gate',
        body: 'Gate libérée — le state est relu, le moteur dispatche le step suivant.',
    },
    {
        id: 'e6',
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
    },
    {
        id: 'e7',
        kind: 'review',
        author: 'nebuko · review (GLM — autre modèle)',
        avatar: '⚖️',
        time: '16:29',
        title: 'Review verdict — APPROVED',
        body: 'Diff complet relu contre l’Object Design Anchor approuvé. Contract fidelity : all met.',
        stateLabel: 'WORKFLOW_STATE · matched_rule: workflow:default/step:review',
    },
];