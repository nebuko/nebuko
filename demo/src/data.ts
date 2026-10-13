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

// // Données réelles de l'issue #839 / PR #843 — remplace par ta propre run.
// export const events: ThreadEvent[] = [
//   {
//     id: 'e1',
//     kind: 'trigger',
//     author: 'you',
//     avatar: '👤',
//     time: '14:32',
//     title: '/nebuko run workflow --name default',
//     body: 'Une seule ligne dans l’issue. Le moteur prend le relais.',
//   },
//   {
//     id: 'e2',
//     kind: 'agent',
//     author: 'nebuko ',
//     avatar: '',
//     time: '14:32',
//     title: 'Nebuko architect agent started — View workflow run logs',
//     body: ''
//   },
//   {
//     id: 'e3',
//     kind: 'agent',
//     author: 'nebuko · architect',
//     avatar: '📐',
//     time: '14:35',
//     title: 'Analyze — triage & délégation',
//     body: 'Triage terminé : limite circuit-breaker par issue. Délégation de la collecte de contexte à @@context.',
//     stateLabel: 'WORKFLOW_STATE · step: analyze → context',
//   },
//   {
//     id: 'e4',
//     kind: 'agent',
//     author: 'nebuko ',
//     avatar: '',
//     time: '14:32',
//     title: 'Nebuko context agent started — View workflow run logs',
//     body: ''
//   },
//   {
//     id: 'e5',
//     kind: 'agent',
//     author: 'nebuko · context',
//     avatar: '🔍',
//     time: '14:45',
//     title: 'Context Collection Report',
//     body: 'J’ai tracé la limite des 25 appels de bout en bout : module Python, guard du moteur, input du reusable workflow, et la doc.',
//     stateLabel: 'WORKFLOW_STATE · step: context → plan',
//     metrics: { input: '2,045,617', output: '25,443', toolCalls: '71', runtime: '5 min' },
//   },
//   {
//     id: 'e6',
//     kind: 'agent',
//     author: 'nebuko ',
//     avatar: '',
//     time: '14:32',
//     title: 'Nebuko architect agent started — View workflow run logs',
//     body: ''
//   },
//   {
//     id: 'e7',
//     kind: 'agent',
//     author: 'nebuko · architect',
//     avatar: '📐',
//     time: '15:00',
//     title: 'Architecture proposal — Object Design Anchor',
//     body: 'Design proposé : un modèle pydantic CircuitBreakerState, co-localisé avec le précédent SoftRetryState. Workflow en attente d’approbation humaine de l’Object Design Anchor.',
//     stateLabel: 'WORKFLOW_STATE · step: plan · dispatch: HOLD · pin: true',
//     pinned: true,
//     metrics: { input: '71,549', output: '18,131', toolCalls: '2', runtime: '8 min' },
//   },
//   {
//     id: 'e8',
//     kind: 'approve',
//     author: 'you',
//     avatar: '👤',
//     time: '15:12',
//     title: '/nebuko approve gate',
//     body: 'Gate libérée — le state est relu, le moteur dispatche le step suivant.',
//   },
//   {
//     id: 'e9',
//     kind: 'agent',
//     author: 'nebuko ',
//     avatar: '',
//     time: '14:32',
//     title: 'Nebuko dev agent started — View workflow run logs',
//     body: ''
//   },
//   {
//     id: 'e10',
//     kind: 'agent',
//     author: 'nebuko · dev',
//     avatar: '💻',
//     time: '15:52',
//     title: 'Implementé — PR ouverte',
//     body: 'Ajout de /nebuko update circuit-breaker --value <n> : step déterministe, sans agent. Extraits du diff :',
//     code: `def test_with_limit_does_not_mutate_the_receiver(self):
//     base = CircuitBreakerState(limit=20)
//     base.with_limit(35)
//     self.assertEqual(base, CircuitBreakerState(limit=20))`,
//     stateLabel: 'WORKFLOW_STATE · step: implement → review',
//   },
//   {
//     id: 'e11',
//     kind: 'agent',
//     author: 'nebuko ',
//     avatar: '',
//     time: '14:32',
//     title: 'Nebuko review agent started — View workflow run logs',
//     body: ''
//   },
//   {
//     id: 'e12',
//     kind: 'review',
//     author: 'nebuko · review (GLM — autre modèle)',
//     avatar: '⚖️',
//     time: '16:29',
//     title: 'Review verdict — APPROVED',
//     body: 'Diff complet relu contre l’Object Design Anchor approuvé. Contract fidelity : all met.',
//     stateLabel: 'WORKFLOW_STATE · matched_rule: workflow:default/step:review',
//   },
// ];

// Small hand-written examples used by GatePreview and PrFlowDemo.
// Your real run lives in data.ts (used by Preview.tsx).
import { ThreadEvent } from './data';

export const trigger: ThreadEvent = {
  id: 's1',
  kind: 'trigger',
  avatar: '🧑‍💻',
  author: 'alex-dev',
  time: '2 hours ago',
  title: '/nebuko run workflow',
  body: '',
};

export const plan: ThreadEvent = {
  id: 's2',
  kind: 'agent',
  avatar: '📐',
  author: 'nebuko-architect',
  time: '1 hour ago',
  title: 'Architecture proposal',
  body:
    'I split the change in **2 slices**. Context found `circuit_breaker.py` and `config/limits.yaml` (see #812).\n\n- slice 1: per-issue override in the state\n- slice 2: engine reads the override',
  pinned: true,
  stateLabel: '{"step":"plan","dispatch":{"status":"hold"},"pr":null,"metrics":{"runs":3,"loops":0}}',
  metrics: { input: '12.4k', output: '1.8k', toolCalls: '9', runtime: '42s' },
};

export const impl: ThreadEvent = {
  id: 's3',
  kind: 'agent',
  avatar: '💻',
  author: 'nebuko-dev',
  time: '12 minutes ago',
  title: 'Slice 1 implemented',
  body: 'Opened #842 with the override and its tests.',
  code: '@@ circuit_breaker.py @@\n-    if failures >= 3:\n+    if failures >= limit_for(issue):\n         trip()',
  metrics: { input: '31.2k', output: '4.6k', toolCalls: '22', runtime: '3m 10s' },
};

