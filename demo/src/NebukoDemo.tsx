import React from 'react';
import {Series, AbsoluteFill, useCurrentFrame, useVideoConfig, spring, interpolate} from 'remotion';
import {IssueFrame, GithubComment} from './components';
import {events} from './data';

export const FPS = 30;

// ─── Durées en frames (30 fps) ───
const INTRO = 90;
const TRIGGER = 90;
const ANALYZE = 110;
const CONTEXT = 130;
const PLAN = 160;
const APPROVE = 100;
const IMPLEMENT = 150;
const REVIEW = 120;
const STATS = 200;

export const TOTAL_FRAMES =
  INTRO + TRIGGER + ANALYZE + CONTEXT + PLAN + APPROVE + IMPLEMENT + REVIEW + STATS;

// ─── Titre ───
const TitleCard: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = spring({frame, fps, config: {damping: 200}});
  const s = spring({frame: frame - 15, fps, config: {damping: 200}});
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center',
      background: 'linear-gradient(135deg, #0d1117 0%, #161b22 100%)'}}>
      <div style={{fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        fontSize: 84, fontWeight: 800, color: '#e6edf3',
        opacity: t, transform: `translateY(${(1 - t) * 40}px)`}}>
        Nebuko
      </div>
      <div style={{fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        fontSize: 34, color: '#8b949e', opacity: s, marginTop: 20}}>
        Multi-agent workflow as code — dans tes issues GitHub
      </div>
    </AbsoluteFill>
  );
};

// ─── Thread (la "nouvelle" Thread, basée sur IssueFrame + GithubComment) ───
const ThreadScene: React.FC<{upTo: number; caption?: string}> = ({upTo, caption}) => {
  const visible = events.slice(Math.max(0, upTo - 3), upTo + 1);
  return (
    <IssueFrame>
      {visible.map((e, i) => (
        <GithubComment key={e.id} event={e}
          animate={i === visible.length - 1}
          gate={e.pinned} />
      ))}
      {caption ? (
        <div style={{marginTop: 16, padding: '12px 18px', borderRadius: 8,
          background: 'rgba(56, 139, 255, 0.12)', border: '1px solid #388bfd',
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          fontSize: 22, color: '#e6edf3'}}>
          {caption}
        </div>
      ) : null}
    </IssueFrame>
  );
};

// ─── Stats summary ───
const STATS_DATA = [
  {agent: 'Architect', runs: 3, input: '116,010', output: '25,038'},
  {agent: 'Context', runs: 1, input: '2,045,617', output: '25,443'},
  {agent: 'Dev', runs: 3, input: '28,314,054', output: '171,522'},
  {agent: 'Review (GLM)', runs: 1, input: '2,510,627', output: '8,707'},
];

const StatsCard: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = spring({frame, fps, config: {damping: 200}});
  return (
    <AbsoluteFill style={{background: '#0d1117', padding: 60, opacity: p,
      justifyContent: 'center'}}>
      <div style={{fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        fontSize: 40, fontWeight: 800, color: '#e6edf3', marginBottom: 8}}>
        📊 Issue close — la summary auto-postée
      </div>
      <div style={{fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        fontSize: 24, color: '#8b949e', marginBottom: 30}}>
        8 runs · 32.98M input tokens · 230K output · coût : 0 $ (abonnement)
      </div>
      <div style={{display: 'flex', flexDirection: 'column', gap: 12}}>
        {STATS_DATA.map((s, i) => {
          const row = spring({frame: frame - 30 - i * 12, fps, config: {damping: 200}});
          return (
            <div key={s.agent} style={{
              display: 'flex', justifyContent: 'space-between',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
              fontSize: 24, color: '#c9d1d9',
              background: '#161b22', border: '1px solid #30363d',
              borderRadius: 10, padding: '14px 20px',
              opacity: row, transform: `translateX(${(1 - row) * 40}px)`,
            }}>
              <span>{s.agent} <span style={{color: '#8b949e'}}>({s.runs} runs)</span></span>
              <span>↑ {s.input} · ↓ {s.output}</span>
            </div>
          );
        })}
      </div>
      <div style={{marginTop: 36,
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        fontSize: 26, color: '#7ee787'}}>
        Every step = a comment you can read, question and redirect.
      </div>
    </AbsoluteFill>
  );
};

// ─── La timeline ───
export const NebukoDemo: React.FC = () => (
  <AbsoluteFill style={{background: '#0d1117'}}>
    <Series>
      <Series.Sequence durationInFrames={INTRO}>
        <TitleCard />
      </Series.Sequence>
      <Series.Sequence durationInFrames={TRIGGER}>
        <ThreadScene upTo={0}
          caption="Un commentaire pour démarrer — pas de SaaS, pas de plugin IDE." />
      </Series.Sequence>
      <Series.Sequence durationInFrames={ANALYZE}>
        <ThreadScene upTo={1}
          caption="Chaque step poste un commentaire + le state complet (WORKFLOW_STATE JSON)." />
      </Series.Sequence>
      <Series.Sequence durationInFrames={CONTEXT}>
        <ThreadScene upTo={2}
          caption="Le contexte est tracé de bout en bout : 2M tokens input, 71 tool calls." />
      </Series.Sequence>
      <Series.Sequence durationInFrames={PLAN}>
        <ThreadScene upTo={3}
          caption="🛑 Human gate : dispatch.status = hold — le moteur s'arrête, un humain décide." />
      </Series.Sequence>
      <Series.Sequence durationInFrames={APPROVE}>
        <ThreadScene upTo={4}
          caption="Gate libérée : /nebuko approve gate" />
      </Series.Sequence>
      <Series.Sequence durationInFrames={IMPLEMENT}>
        <ThreadScene upTo={5}
          caption="Le dev implémente, ouvre le PR. Le state est dans le thread." />
      </Series.Sequence>
      <Series.Sequence durationInFrames={REVIEW}>
        <ThreadScene upTo={6}
          caption="Review par un AUTRE modèle (GLM) : adversarial par construction." />
      </Series.Sequence>
      <Series.Sequence durationInFrames={STATS}>
        <StatsCard />
      </Series.Sequence>
    </Series>
  </AbsoluteFill>
);