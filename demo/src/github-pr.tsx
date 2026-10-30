import React from 'react';
import {AbsoluteFill} from 'remotion';
import {ThemeName, ThemeProvider, THEMES, useGH} from './github-theme';
import {
  Avatar,
  BranchChip,
  Button,
  Collapse,
  GithubHeader,
  GithubTimelineEvent,
  HUMAN_AMBER,
  Initial,
  Label,
  Pill,
  Pointer,
  RepoHeader,
  SideSection,
  StatusBadge,
  TabItem,
  ThreadColumn,
  TitleBlock,
  useEnter,
} from './github-core';
import {Markdown} from './github-markdown';
import {
  IconCheck,
  IconCheckCircle,
  IconChevronDown,
  IconChevronRight,
  IconComment,
  IconCommit,
  IconDot,
  IconFileDiff,
  IconIssueOpened,
  IconMerge,
  IconPullRequest,
  IconX,
  IconXCircle,
} from './github-icons';

/* =====================================================================
 * Review popover ("Finish your review")
 * ===================================================================== */
export const GithubReviewPopover: React.FC<{
  choice?: 'comment' | 'approve' | 'changes';
  text?: string;
  /** show the animated cursor on "Submit review" */
  submitPointed?: boolean;
  style?: React.CSSProperties;
}> = ({choice = 'approve', text, submitPointed, style}) => {
  const t = useGH();
  const options = [
    {id: 'comment', title: 'Comment', desc: 'Submit general feedback without explicit approval.'},
    {id: 'approve', title: 'Approve', desc: 'Submit feedback and approve merging these changes.'},
    {id: 'changes', title: 'Request changes', desc: 'Submit feedback that must be addressed before merging.'},
  ] as const;
  return (
    <div
      style={{
        width: 420,
        background: t.bg,
        border: '1px solid ' + t.border,
        borderRadius: 12,
        boxShadow: t.shadow,
        fontFamily: t.font,
        color: t.text,
        fontSize: 14,
        zIndex: 30,
        ...style,
      }}
    >
      <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 16px', borderBottom: '1px solid ' + t.border}}>
        <strong style={{fontWeight: 600}}>Finish your review</strong>
        <IconX size={16} color={t.muted} />
      </div>
      <div style={{padding: 16, display: 'flex', flexDirection: 'column', gap: 12}}>
        <div
          style={{
            border: '1px solid ' + t.border,
            borderRadius: 6,
            minHeight: 64,
            padding: '8px 12px',
            color: text ? t.text : t.muted,
            background: t.bg,
          }}
        >
          {text || 'Leave a comment'}
        </div>
        {options.map((o) => {
          const on = o.id === choice;
          return (
            <div key={o.id} style={{display: 'flex', gap: 10, alignItems: 'flex-start'}}>
              <div
                style={{
                  width: 16,
                  height: 16,
                  marginTop: 3,
                  borderRadius: '50%',
                  boxSizing: 'border-box',
                  border: on ? `5px solid ${t.accent}` : '1px solid ' + t.border,
                  background: t.bg,
                  flexShrink: 0,
                }}
              />
              <div>
                <div style={{fontWeight: 600}}>{o.title}</div>
                <div style={{fontSize: 12, color: t.muted}}>{o.desc}</div>
              </div>
            </div>
          );
        })}
      </div>
      <div style={{padding: '0 16px 16px', display: 'flex', justifyContent: 'flex-end'}}>
        <Button primary pointed={submitPointed}>
          Submit review
        </Button>
      </div>
    </div>
  );
};

/* =====================================================================
 * Merge box
 * ===================================================================== */
type CheckRow = {name: string; desc?: string; state?: 'success' | 'failed' | 'running'};

const StatusIcon: React.FC<{kind: 'success' | 'danger' | 'pending' | 'merged'; size?: number}> = ({kind, size = 32}) => {
  const t = useGH();
  const m = {
    success: {bg: t.greenEmphasis, Icon: IconCheck},
    danger: {bg: t.redEmphasis, Icon: IconX},
    pending: {bg: t.attentionFg, Icon: IconDot},
    merged: {bg: t.purpleEmphasis, Icon: IconMerge},
  }[kind];
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        background: m.bg,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
      }}
    >
      <m.Icon size={Math.round(size * 0.55)} color="#fff" />
    </div>
  );
};

const MergeRow: React.FC<{
  kind: 'success' | 'danger' | 'pending';
  title: string;
  desc?: string;
  right?: React.ReactNode;
  children?: React.ReactNode;
}> = ({kind, title, desc, right, children}) => {
  const t = useGH();
  return (
    <div style={{borderTop: '1px solid ' + t.border}}>
      <div style={{display: 'flex', alignItems: 'center', gap: 16, padding: '16px'}}>
        <StatusIcon kind={kind} />
        <div style={{flex: 1}}>
          <div style={{fontWeight: 600, fontSize: 14}}>{title}</div>
          {desc ? <div style={{fontSize: 12, color: t.muted, marginTop: 2}}>{desc}</div> : null}
        </div>
        {right}
      </div>
      {children}
    </div>
  );
};

export const GithubMergeBox: React.FC<{
  /** review requirement */
  review?: 'required' | 'approved' | 'changes';
  checks?: 'running' | 'passed' | 'failed';
  checkRows?: CheckRow[];
  conflicts?: boolean;
  method?: 'squash' | 'merge' | 'rebase';
  mergeState?: 'ready' | 'confirm' | 'merged';
  commitTitle?: string;
  commitBody?: string;
  headBranch?: string;
  branchDeleted?: boolean;
  /** which button shows the animated cursor */
  pointer?: 'review' | 'merge' | 'confirm' | 'delete' | null;
  /** review popover opened from "Add your review" */
  reviewPopover?: {choice?: 'comment' | 'approve' | 'changes'; text?: string; submitPointed?: boolean} | null;
  animate?: boolean;
}> = ({
  review = 'required',
  checks = 'passed',
  checkRows,
  conflicts = false,
  method = 'squash',
  mergeState = 'ready',
  commitTitle,
  commitBody,
  headBranch = 'nebuko/1133-guard-openhands',
  branchDeleted = false,
  pointer = null,
  reviewPopover = null,
  animate,
}) => {
  const t = useGH();
  const p = useEnter(animate);
  const label = {squash: 'Squash and merge', merge: 'Create a merge commit', rebase: 'Rebase and merge'}[method];
  const mergeable = review === 'approved' && checks !== 'failed' && !conflicts;

  const wrapper: React.CSSProperties = {
    position: 'relative',
    zIndex: 3,
    border: '1px solid ' + (mergeState === 'merged' ? t.purpleEmphasis : t.border),
    borderRadius: 6,
    background: t.bg,
    fontFamily: t.font,
    color: t.text,
    opacity: p,
    transform: `translateY(${(1 - p) * 15}px)`,
  };

  if (mergeState === 'merged') {
    return (
      <div style={wrapper}>
        <div style={{display: 'flex', alignItems: 'center', gap: 16, padding: 16, background: t.doneBg, borderRadius: 5}}>
          <StatusIcon kind="merged" />
          <div style={{flex: 1}}>
            <div style={{fontWeight: 600, fontSize: 16}}>Pull request successfully merged and closed</div>
            <div style={{fontSize: 13, color: t.muted, marginTop: 2}}>
              {branchDeleted ? (
                <>
                  The <BranchChip>{headBranch}</BranchChip> branch has been deleted.
                </>
              ) : (
                <>
                  You're all set — the <BranchChip>{headBranch}</BranchChip> branch can be safely deleted.
                </>
              )}
            </div>
          </div>
          {branchDeleted ? <Button>Restore branch</Button> : <Button pointed={pointer === 'delete'}>Delete branch</Button>}
        </div>
      </div>
    );
  }

  return (
    <div style={wrapper}>
      {/* Review row */}
      <div style={{position: 'relative'}}>
        <div style={{display: 'flex', alignItems: 'center', gap: 16, padding: 16}}>
          <StatusIcon kind={review === 'approved' ? 'success' : 'danger'} />
          <div style={{flex: 1}}>
            <div style={{fontWeight: 600, fontSize: 14}}>
              {review === 'approved' ? 'Changes approved' : review === 'changes' ? 'Changes requested' : 'Review required'}
            </div>
            <div style={{fontSize: 12, color: t.muted, marginTop: 2}}>
              {review === 'approved'
                ? '1 approving review by reviewers with write access.'
                : 'At least 1 approving review is required by reviewers with write access.'}
            </div>
          </div>
          {review !== 'approved' ? (
            <Button pointed={pointer === 'review' && !reviewPopover}>Add your review</Button>
          ) : null}
        </div>
        {reviewPopover ? (
          <GithubReviewPopover
            choice={reviewPopover.choice}
            text={reviewPopover.text}
            submitPointed={reviewPopover.submitPointed}
            style={{position: 'absolute', right: 12, top: 12}}
          />
        ) : null}
      </div>

      {/* Checks row */}
      <MergeRow
        kind={checks === 'passed' ? 'success' : checks === 'failed' ? 'danger' : 'pending'}
        title={
          checks === 'passed'
            ? 'All checks have passed'
            : checks === 'failed'
            ? 'Some checks were not successful'
            : "Some checks haven't completed yet"
        }
        desc={
          checkRows
            ? `${checkRows.filter((r) => r.state === 'success' || !r.state).length} successful checks`
            : undefined
        }
        right={<IconChevronDown size={16} color={t.muted} />}
      >
        {checkRows ? (
          <div style={{padding: '0 16px 12px 64px', display: 'flex', flexDirection: 'column', gap: 8}}>
            {checkRows.map((r) => (
              <div key={r.name} style={{display: 'flex', alignItems: 'center', gap: 8, fontSize: 13}}>
                {r.state === 'failed' ? (
                  <IconXCircle size={16} color={t.red} />
                ) : r.state === 'running' ? (
                  <IconDot size={16} color={t.attentionFg} />
                ) : (
                  <IconCheckCircle size={16} color={t.green} />
                )}
                <span style={{fontWeight: 600}}>{r.name}</span>
                <span style={{color: t.muted}}>{r.desc}</span>
                <span style={{marginLeft: 'auto', color: t.accent}}>Details</span>
              </div>
            ))}
          </div>
        ) : null}
      </MergeRow>

      {/* Conflicts row */}
      <MergeRow
        kind={conflicts ? 'danger' : 'success'}
        title={conflicts ? 'This branch has conflicts that must be resolved' : 'No conflicts with base branch'}
        desc={conflicts ? undefined : 'Merging can be performed automatically.'}
      />

      {/* Action area */}
      <div style={{borderTop: '1px solid ' + t.border, padding: 16, background: t.surface, borderRadius: '0 0 5px 5px'}}>
        {mergeState === 'confirm' ? (
          <div style={{display: 'flex', gap: 12}}>
            <Avatar emoji="🧑‍💻" human size={32} ring={false} />
            <div style={{flex: 1, display: 'flex', flexDirection: 'column', gap: 8}}>
              <div style={{border: '1px solid ' + t.border, borderRadius: 6, background: t.bg, padding: '6px 12px', fontSize: 14}}>
                {commitTitle ?? 'Guard openhands import in git_session.py so lint-config runs on base deps'}
              </div>
              <div
                style={{
                  border: '1px solid ' + t.border,
                  borderRadius: 6,
                  background: t.bg,
                  padding: '8px 12px',
                  fontSize: 14,
                  minHeight: 44,
                  color: commitBody ? t.text : t.muted,
                  whiteSpace: 'pre-wrap',
                }}
              >
                {commitBody ?? 'Add an optional extended description…'}
              </div>
              <div style={{display: 'flex', gap: 8, marginTop: 4}}>
                <Button primary pointed={pointer === 'confirm'}>
                  Confirm {label.toLowerCase()}
                </Button>
                <Button>Cancel</Button>
              </div>
            </div>
          </div>
        ) : (
          <div style={{display: 'flex', alignItems: 'center', gap: 16}}>
            <Button primary split disabled={!mergeable} pointed={pointer === 'merge'}>
              {label}
            </Button>
            <span style={{fontSize: 12, color: t.muted}}>
              {mergeable ? (
                <>
                  You can also merge this with the command line. <span style={{color: t.accent}}>View command line instructions.</span>
                </>
              ) : (
                'Merging is blocked until the review requirement is met.'
              )}
            </span>
          </div>
        )}
      </div>
    </div>
  );
};

/* =====================================================================
 * Review event (+ optional review comment) and commits event
 * ===================================================================== */
export const GithubReview: React.FC<{
  actor: string;
  state?: 'approved' | 'changes' | 'commented';
  time?: string;
  body?: string;
  animate?: boolean;
}> = ({actor, state = 'approved', time, body, animate}) => {
  const t = useGH();
  const p = useEnter(animate);
  const verb = {approved: 'approved these changes', changes: 'requested changes', commented: 'reviewed'}[state];
  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 12, opacity: p, transform: `translateY(${(1 - p) * 12}px)`}}>
      <GithubTimelineEvent kind={state} actor={actor} time={time}>
        {verb}
      </GithubTimelineEvent>
      {body ? (
        <div
          style={{
            marginLeft: 56,
            position: 'relative',
            zIndex: 1,
            // an approval is a human action — thin amber outline, like the hero
            border: `1px solid ${state === 'approved' ? HUMAN_AMBER + 'b3' : t.border}`,
            borderRadius: 6,
            background: t.bg,
            padding: 16,
            fontFamily: t.font,
            fontSize: 14,
            lineHeight: 1.5,
            color: t.text,
          }}
        >
          <Markdown text={body} />
        </div>
      ) : null}
    </div>
  );
};

export const GithubCommits: React.FC<{
  actor: string;
  time?: string;
  commits: {sha: string; message: string}[];
  animate?: boolean;
}> = ({actor, time, commits, animate}) => {
  const t = useGH();
  const p = useEnter(animate);
  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 8, opacity: p, transform: `translateY(${(1 - p) * 12}px)`}}>
      <GithubTimelineEvent kind="commit" actor={actor} time={time}>
        added {commits.length} commit{commits.length > 1 ? 's' : ''}
      </GithubTimelineEvent>
      <div style={{marginLeft: 56, position: 'relative', zIndex: 1, fontFamily: t.font, fontSize: 13}}>
        {commits.map((c) => (
          <div key={c.sha} style={{display: 'flex', alignItems: 'center', gap: 8, height: 28, color: t.text}}>
            <IconCommit size={16} color={t.muted} />
            <span style={{fontWeight: 600, flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'}}>{c.message}</span>
            <span style={{fontFamily: t.mono, fontSize: 12, color: t.muted}}>{c.sha}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

/* =====================================================================
 * Files changed: toolbar + diff file
 * ===================================================================== */
export const GithubFilesToolbar: React.FC<{
  files: number;
  additions: number;
  deletions: number;
  pointer?: boolean;
  reviewPopover?: {choice?: 'comment' | 'approve' | 'changes'; text?: string; submitPointed?: boolean} | null;
}> = ({files, additions, deletions, pointer, reviewPopover}) => {
  const t = useGH();
  return (
    <div
      style={{
        position: 'relative',
        zIndex: 4,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        fontFamily: t.font,
        fontSize: 14,
        color: t.muted,
        padding: '0 0 16px',
      }}
    >
      <div>
        Showing <strong style={{color: t.text}}>{files} changed files</strong> with{' '}
        <strong style={{color: t.green}}>{additions} additions</strong> and{' '}
        <strong style={{color: t.red}}>{deletions} deletions</strong>.
      </div>
      <div style={{position: 'relative'}}>
        <Button primary pointed={pointer && !reviewPopover} icon={undefined}>
          Review changes
        </Button>
        {reviewPopover ? (
          <GithubReviewPopover
            choice={reviewPopover.choice}
            text={reviewPopover.text}
            submitPointed={reviewPopover.submitPointed}
            style={{position: 'absolute', right: 0, top: 40}}
          />
        ) : null}
      </div>
    </div>
  );
};

export const GithubDiffFile: React.FC<{
  path: string;
  /** unified diff lines: "@@ -10,6 +10,7 @@", " context", "+added", "-removed" */
  lines: string[];
}> = ({path, lines}) => {
  const t = useGH();
  const additions = lines.filter((l) => /^\+/.test(l)).length;
  const deletions = lines.filter((l) => /^-/.test(l)).length;
  let oldN = 1;
  let newN = 1;
  const rows = lines.map((l) => {
    if (l.startsWith('@@')) {
      const m = /@@ -(\d+)(?:,\d+)? \+(\d+)/.exec(l);
      if (m) {
        oldN = parseInt(m[1], 10);
        newN = parseInt(m[2], 10);
      }
      return {kind: 'hunk' as const, text: l, o: '', n: ''};
    }
    if (l.startsWith('+')) return {kind: 'add' as const, text: l.slice(1), o: '', n: String(newN++)};
    if (l.startsWith('-')) return {kind: 'del' as const, text: l.slice(1), o: String(oldN++), n: ''};
    return {kind: 'ctx' as const, text: l.startsWith(' ') ? l.slice(1) : l, o: String(oldN++), n: String(newN++)};
  });
  return (
    <div style={{border: '1px solid ' + t.border, borderRadius: 6, overflow: 'hidden', background: t.bg, fontFamily: t.font}}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '8px 16px',
          background: t.surface,
          borderBottom: '1px solid ' + t.border,
          fontSize: 13,
          color: t.text,
        }}
      >
        <IconChevronDown size={16} color={t.muted} />
        <span style={{fontFamily: t.mono, fontSize: 12, fontWeight: 600}}>{path}</span>
        <span style={{marginLeft: 'auto', fontSize: 12, color: t.muted}}>
          <span style={{color: t.green}}>+{additions}</span> <span style={{color: t.red}}>−{deletions}</span>
        </span>
      </div>
      <div style={{fontFamily: t.mono, fontSize: 12, lineHeight: '20px', color: t.text}}>
        {rows.map((r, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              background: r.kind === 'add' ? t.addBg : r.kind === 'del' ? t.delBg : r.kind === 'hunk' ? t.hunkBg : 'transparent',
            }}
          >
            <span style={{width: 48, textAlign: 'right', paddingRight: 8, color: t.muted, userSelect: 'none', flexShrink: 0}}>{r.o}</span>
            <span style={{width: 48, textAlign: 'right', paddingRight: 8, color: t.muted, userSelect: 'none', flexShrink: 0}}>{r.n}</span>
            <span style={{width: 16, textAlign: 'center', flexShrink: 0, color: r.kind === 'add' ? t.green : r.kind === 'del' ? t.red : t.muted}}>
              {r.kind === 'add' ? '+' : r.kind === 'del' ? '−' : ''}
            </span>
            <span style={{whiteSpace: 'pre', flex: 1, color: r.kind === 'hunk' ? t.muted : t.text}}>{r.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

/* =====================================================================
 * Pull request page frame
 * ===================================================================== */
export const PullRequestFrame: React.FC<{
  children: React.ReactNode;
  theme?: ThemeName;
  title?: string;
  number?: number | string;
  repoOwner?: string;
  repoName?: string;
  author?: string;
  state?: 'open' | 'merged' | 'closed' | 'draft';
  base?: string;
  head?: string;
  commits?: number;
  checks?: number;
  filesChanged?: number;
  commentCount?: number;
  /** relative time shown in the merged status line, e.g. "2 minutes ago" */
  mergedAgo?: string;
  mergedBy?: string;
  tab?: 'conversation' | 'commits' | 'checks' | 'files';
  reviewers?: {name: string; state: 'pending' | 'approved' | 'changes'}[];
  labels?: {name: string; color: string}[];
  assignees?: string[];
  linkedIssue?: {number: number | string; title: string; closed?: boolean} | null;
  participants?: string[];
  anchor?: 'top' | 'bottom';
  showRepoChrome?: boolean;
  /** 0 = full page, 1 = GitHub header and repo tabs folded away, title shrunk to a sticky bar. */
  collapse?: number;
  /** Content of a reserved band under the newest thread item (e.g. an explainer card). */
  narration?: React.ReactNode;
  narrationHeight?: number;
}> = ({
  children,
  theme = 'light',
  title = 'Guard openhands import in git_session.py so lint-config runs on base deps',
  number = 1133,
  repoOwner = 'nebuko',
  repoName = 'nebuko',
  author = 'nebuko-dev',
  state = 'open',
  base = 'main',
  head = 'nebuko/1133-guard-openhands',
  commits = 3,
  checks = 3,
  filesChanged = 2,
  commentCount = 3,
  mergedAgo = 'just now',
  mergedBy = 'alex-dev',
  tab = 'conversation',
  reviewers = [],
  labels = [],
  assignees = [],
  linkedIssue = null,
  participants = [],
  anchor = 'bottom',
  showRepoChrome = true,
  collapse = 0,
  narration,
  narrationHeight = 0,
}) => {
  const t = THEMES[theme];
  const merged = state === 'merged';
  const files = tab === 'files';
  return (
    <ThemeProvider theme={theme}>
      <AbsoluteFill style={{background: t.bg, display: 'flex', flexDirection: 'column', overflow: 'hidden', fontFamily: t.font}}>
        <Collapse open={1 - collapse}>
          <GithubHeader owner={repoOwner} repo={repoName} />
          {showRepoChrome ? <RepoHeader owner={repoOwner} repo={repoName} active="pulls" /> : null}
        </Collapse>

        <TitleBlock
          compact={collapse}
          title={title}
          number={number}
          badge={<StatusBadge type="pr" state={state} />}
          actions={
            <>
              <Button>Edit</Button>
              <Button primary>Code</Button>
            </>
          }
          subtitle={
            <>
              <strong style={{color: t.text, fontWeight: 600}}>{merged ? mergedBy : author}</strong>{' '}
              {merged ? 'merged' : state === 'closed' ? 'closed' : 'wants to merge'} {commits} commits into <BranchChip>{base}</BranchChip> from{' '}
              <BranchChip>{head}</BranchChip>
              {merged ? <> {mergedAgo}</> : null}
            </>
          }
        />

        {/* PR tabs */}
        <div style={{display: 'flex', gap: 16, padding: '0 32px', borderBottom: '1px solid ' + t.border, fontSize: 14, flexShrink: 0, fontFamily: t.font}}>
          <TabItem icon={<IconComment />} label="Conversation" count={String(commentCount)} active={tab === 'conversation'} />
          <TabItem icon={<IconCommit />} label="Commits" count={String(commits)} active={tab === 'commits'} />
          <TabItem icon={<IconCheckCircle />} label="Checks" count={String(checks)} active={tab === 'checks'} />
          <TabItem icon={<IconFileDiff />} label="Files changed" count={String(filesChanged)} active={tab === 'files'} />
        </div>

        <div style={{flex: 1, minHeight: 0, display: 'flex', padding: '24px 32px', gap: 32, overflow: 'hidden'}}>
          {files ? (
            <div style={{flex: 1, minWidth: 0}}>{children}</div>
          ) : (
            <>
              <ThreadColumn anchor={anchor} below={narration} belowHeight={narrationHeight}>
                {children}
              </ThreadColumn>
              <div style={{width: 296, flexShrink: 0, fontSize: 12}}>
                <SideSection title="Reviewers">
                  {reviewers.length === 0 ? (
                    <span style={{color: t.muted}}>No reviews</span>
                  ) : (
                    reviewers.map((r) => (
                      <div key={r.name} style={{display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600, marginBottom: 4}}>
                        <Initial name={r.name} />
                        <span style={{flex: 1}}>{r.name}</span>
                        {r.state === 'approved' ? (
                          <IconCheck size={16} color={t.green} />
                        ) : r.state === 'changes' ? (
                          <IconX size={16} color={t.red} />
                        ) : (
                          <IconDot size={14} color={t.attentionFg} />
                        )}
                      </div>
                    ))
                  )}
                </SideSection>
                <SideSection title="Assignees">
                  {assignees.length === 0 ? (
                    <span style={{color: t.muted}}>No one assigned</span>
                  ) : (
                    assignees.map((a) => (
                      <div key={a} style={{display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600}}>
                        <Initial name={a} />
                        <span>{a}</span>
                      </div>
                    ))
                  )}
                </SideSection>
                <SideSection title="Labels">
                  {labels.length === 0 ? (
                    <span style={{color: t.muted}}>None yet</span>
                  ) : (
                    <div style={{display: 'flex', gap: 6, flexWrap: 'wrap'}}>
                      {labels.map((l) => (
                        <Label key={l.name} name={l.name} color={l.color} />
                      ))}
                    </div>
                  )}
                </SideSection>
                <SideSection title="Development">
                  {linkedIssue ? (
                    <div style={{display: 'flex', alignItems: 'flex-start', gap: 8, lineHeight: 1.4}}>
                      {linkedIssue.closed ? (
                        <IconCheckCircle size={16} color={t.purple} />
                      ) : (
                        <IconIssueOpened size={16} color={t.green} />
                      )}
                      <div>
                        <div style={{fontWeight: 600}}>{linkedIssue.title}</div>
                        <div style={{color: t.muted}}>#{linkedIssue.number}</div>
                      </div>
                    </div>
                  ) : (
                    <span style={{color: t.muted}}>Successfully merging this pull request may close these issues.</span>
                  )}
                </SideSection>
                <SideSection title={`${Math.max(participants.length, 1)} participants`} gear={false} last>
                  <div style={{display: 'flex', gap: 4, flexWrap: 'wrap'}}>
                    {participants.length === 0 ? (
                      <Initial name={author} size={24} />
                    ) : (
                      participants.map((e, i) => (
                        <div
                          key={i}
                          style={{
                            width: 24,
                            height: 24,
                            borderRadius: '50%',
                            background: t.surface,
                            border: '1px solid ' + t.border,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            fontSize: 12,
                          }}
                        >
                          {e}
                        </div>
                      ))
                    )}
                  </div>
                </SideSection>
              </div>
            </>
          )}
        </div>
      </AbsoluteFill>
    </ThemeProvider>
  );
};

