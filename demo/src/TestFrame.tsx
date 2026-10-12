import React from 'react';
import { useCurrentFrame, spring, useVideoConfig } from 'remotion';
import { IssueFrame, GithubComment, GithubIssueMainPost, GH } from './components';
import { events } from './data';

export const TEST_DURATION = 480; // 16 s à 30 fps

export const TestFrame: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Nombre de commentaires débloqués (1 toutes les 60 frames / 2 sec)
  const count = Math.min(events.length, Math.floor(frame / 60) + 1);
  const visibleEvents = events.slice(0, count);

  // Hauteur estimée moyenne d'un bloc (issue + commentaire)
  const ITEM_HEIGHT = 200;

  // Calcul du scroll :
  // Dès le 2ème commentaire, tout le fil (y compris le corps de l'issue) glisse vers le haut
  const targetScroll = Math.max(0, (count - 1) * ITEM_HEIGHT);

  // Transition fluide
  const translateY = spring({
    frame,
    fps,
    from: 0,
    to: targetScroll,
    config: { damping: 200, mass: 0.6 },
  });

  return (
    <IssueFrame>
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
          position: 'relative',
          transform: `translateY(-${translateY}px)`,
          transition: 'transform 0.1s ease-out',
        }}
      >
        {/* Ligne verticale de liaison */}
        <div
          style={{
            position: 'absolute',
            top: 20,
            bottom: 0,
            left: 19,
            width: 2,
            background: GH.border,
            zIndex: 0,
          }}
        />

        {/* 1. Le corps original de l'issue (qui va maintenant défiler !) */}
        <GithubIssueMainPost
          author="alex-dev"
          avatar="👨‍💻"
          time="3 hours ago"
          body="We need a mechanism to override circuit breaker thresholds per specific issue to avoid cascading service degradation."
          code={"const config = {\n  circuitBreaker: {\n    override: true,\n    maxFailures: 3\n  }\n};"}
        />

        {/* 2. Les commentaires débloqués */}
        {visibleEvents.map((e, index) => {
          const isLatest = index === visibleEvents.length - 1;
          return (
            <GithubComment
              key={e.id}
              event={e}
              animate={isLatest}
              gate={e.pinned}
            />
          );
        })}
      </div>
    </IssueFrame>
  );
};