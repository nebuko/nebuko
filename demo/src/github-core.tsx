import React from 'react';
import {spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {Theme, useGH} from './github-theme';
import {
  IconBell,
  IconChevronDown,
  IconChevronRight,
  IconCheck,
  IconCheckCircle,
  IconCode,
  IconCommit,
  IconGear,
  IconGraph,
  IconInfo,
  IconIssueOpened,
  IconKebab,
  IconMark,
  IconMenu,
  IconMerge,
  IconPlay,
  IconPlus,
  IconPullRequest,
  IconRepo,
  IconReport,
  IconSearch,
  IconShield,
  IconSmiley,
  IconStar,
  IconTable,
  IconTag,
  IconWarning,
  IconX,
} from './github-icons';

/* =====================================================================
 * Animated pointer (fake mouse cursor) + Button
 * ===================================================================== */
export const Pointer: React.FC<{delay?: number}> = ({delay = 6}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = spring({frame: frame - delay, fps, config: {damping: 200}});
  return (
    <div
      style={{
        position: 'absolute',
        right: -8,
        bottom: -16,
        zIndex: 50,
        pointerEvents: 'none',
        opacity: p,
        transform: `translate(${(1 - p) * 48}px, ${(1 - p) * 36}px)`,
        filter: 'drop-shadow(0 1px 2px rgba(0,0,0,0.45))',
      }}
    >
      <svg width="20" height="28" viewBox="0 0 12 19">
        <path
          d="M0.8 0.8V14.2L3.9 11.3L6.4 17.3L8.7 16.3L6.2 10.4H10.8Z"
          fill="#ffffff"
          stroke="#000000"
          strokeWidth="1"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
};

/** Is the "click" of a pointer happening right now? (used for the press effect) */
export const usePress = (active: boolean, delay = 6): boolean => {
  const frame = useCurrentFrame();
  return active && frame >= delay + 14 && frame < delay + 20;
};

export const Button: React.FC<{
  children: React.ReactNode;
  primary?: boolean;
  disabled?: boolean;
  icon?: React.ReactNode;
  /** show the animated cursor on this button and a click press effect */
  pointed?: boolean;
  pointerDelay?: number;
  /** split button with a dropdown caret (e.g. "Squash and merge") */
  split?: boolean;
}> = ({children, primary, disabled, icon, pointed, pointerDelay = 6, split}) => {
  const t = useGH();
  const press = usePress(!!pointed, pointerDelay);
  const bg = primary ? t.greenEmphasis : t.surfaceHover;
  const border = '1px solid ' + (primary ? t.btnPrimaryBorder : t.border);
  const color = primary ? t.primaryText : t.text;
  return (
    <span
      style={{
        position: 'relative',
        display: 'inline-flex',
        flexShrink: 0,
        transform: press ? 'scale(0.97)' : 'none',
        filter: press ? 'brightness(0.92)' : 'none',
        opacity: disabled ? 0.55 : 1,
      }}
    >
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          height: 32,
          padding: '0 12px',
          borderRadius: split ? '6px 0 0 6px' : 6,
          fontFamily: t.font,
          fontSize: 14,
          fontWeight: 500,
          color,
          background: bg,
          border,
          whiteSpace: 'nowrap',
        }}
      >
        {icon}
        {children}
      </span>
      {split ? (
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            height: 32,
            padding: '0 8px',
            borderRadius: '0 6px 6px 0',
            color,
            background: bg,
            border,
            borderLeft: '1px solid rgba(255,255,255,0.25)',
          }}
        >
          <IconChevronDown size={16} />
        </span>
      ) : null}
      {pointed ? <Pointer delay={pointerDelay} /> : null}
    </span>
  );
};

/* =====================================================================
 * Small primitives
 * ===================================================================== */
export const Avatar: React.FC<{emoji: string; human: boolean; size?: number; ring?: boolean}> = ({
  emoji,
  human,
  size = 40,
  ring = true,
}) => {
  const t = useGH();
  return (
    <div
      style={{
        width: size,
        height: size,
        flexShrink: 0,
        // GitHub: people are round, apps/bots are rounded squares
        borderRadius: human ? '50%' : Math.round(size * 0.2),
        background: human ? t.humanAvatar : t.surface,
        border: '1px solid ' + (human ? 'transparent' : t.border),
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: size * 0.5,
        position: 'relative',
        zIndex: 2,
        // ring in canvas colour: hides the timeline line behind the avatar
        boxShadow: ring ? `0 0 0 4px ${t.bg}` : 'none',
      }}
    >
      {emoji}
    </div>
  );
};

export const Initial: React.FC<{name: string; size?: number}> = ({name, size = 20}) => {
  const t = useGH();
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        background: t.humanAvatar,
        color: '#fff',
        fontSize: size * 0.5,
        fontWeight: 600,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
      }}
    >
      {name.charAt(0).toUpperCase()}
    </div>
  );
};

export const Pill: React.FC<{children: React.ReactNode; color?: string; border?: string; bg?: string}> = ({
  children,
  color,
  border,
  bg = 'transparent',
}) => {
  const t = useGH();
  return (
    <span
      style={{
        fontSize: 12,
        fontWeight: 500,
        lineHeight: '18px',
        padding: '0 7px',
        borderRadius: 999,
        border: '1px solid ' + (border ?? t.border),
        color: color ?? t.muted,
        background: bg,
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  );
};

export const BranchChip: React.FC<{children: React.ReactNode}> = ({children}) => {
  const t = useGH();
  return (
    <span
      style={{
        fontFamily: t.mono,
        fontSize: 12,
        padding: '0 6px',
        borderRadius: 6,
        background: t.accentMuted,
        color: t.accent,
        lineHeight: '20px',
        display: 'inline-block',
      }}
    >
      {children}
    </span>
  );
};

export const Label: React.FC<{name: string; color: string}> = ({name, color}) => {
  const t = useGH();
  const n = parseInt(color.replace('#', ''), 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  const lighten = (v: number) => Math.round(v + (255 - v) * 0.4);
  const light = 0.299 * r + 0.587 * g + 0.114 * b > 160;
  const dark = t.name === 'dark';
  return (
    <span
      style={{
        fontFamily: t.font,
        fontSize: 12,
        fontWeight: 500,
        lineHeight: '18px',
        padding: '0 8px',
        borderRadius: 999,
        color: dark ? `rgb(${lighten(r)}, ${lighten(g)}, ${lighten(b)})` : light ? '#1f2328' : '#ffffff',
        background: dark ? color + '2e' : color,
        border: '1px solid ' + (dark ? color + '66' : 'transparent'),
        whiteSpace: 'nowrap',
      }}
    >
      {name}
    </span>
  );
};

export const StatusBadge: React.FC<{type: 'issue' | 'pr'; state: 'open' | 'closed' | 'merged' | 'draft'}> = ({type, state}) => {
  const t = useGH();
  const map = {
    open: {bg: t.greenEmphasis, label: 'Open', Icon: type === 'pr' ? IconPullRequest : IconIssueOpened},
    merged: {bg: t.purpleEmphasis, label: 'Merged', Icon: IconMerge},
    closed:
      type === 'pr'
        ? {bg: t.redEmphasis, label: 'Closed', Icon: IconPullRequest}
        : {bg: t.purpleEmphasis, label: 'Closed', Icon: IconCheckCircle},
    draft: {bg: t.grayEmphasis, label: 'Draft', Icon: IconPullRequest},
  }[state];
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        height: 32,
        padding: '0 12px',
        borderRadius: 999,
        fontWeight: 500,
        fontSize: 14,
        color: '#fff',
        background: map.bg,
        flexShrink: 0,
      }}
    >
      <map.Icon size={16} color="#fff" />
      {map.label}
    </span>
  );
};

/* =====================================================================
 * Markdown (tiny): paragraphs, lists, `code`, **bold**, @mention, #123
 * ===================================================================== */
const INLINE = /(`[^`]+`|\*\*[^*]+\*\*|(?<![\w*])\*[^*\s][^*]*\*(?![\w*])|(?<!\w)_[^_]+_(?!\w)|@[\w-]+|#\d+)/g;

export const inlineCodeStyle = (t: Theme): React.CSSProperties => ({
  fontFamily: t.mono,
  fontSize: '85%',
  padding: '0.2em 0.4em',
  borderRadius: 6,
  background: t.neutralMuted,
  color: t.text,
});

const renderInline = (text: string, t: Theme): React.ReactNode[] =>
  text.split(INLINE).map((part, i) => {
    if (!part) return null;
    if (part.length > 2 && part.startsWith('`') && part.endsWith('`')) {
      return (
        <code key={i} style={inlineCodeStyle(t)}>
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.length > 4 && part.startsWith('**') && part.endsWith('**')) {
      return (
        <strong key={i} style={{fontWeight: 600}}>
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.length > 2 && (/^_[^_]+_$/.test(part) || /^\*[^*]+\*$/.test(part))) {
      return <em key={i}>{part.slice(1, -1)}</em>;
    }
    if (/^@[\w-]+$/.test(part)) {
      return (
        <span key={i} style={{fontWeight: 600}}>
          {part}
        </span>
      );
    }
    if (/^#\d+$/.test(part)) {
      return (
        <span key={i} style={{color: t.accent}}>
          {part}
        </span>
      );
    }
    return part;
  });

export const Markdown: React.FC<{text: string}> = ({text}) => {
  const t = useGH();
  // <details> render folded, like on GitHub: only the summary line (its content may hold blank lines and fences)
  const summaries: string[] = [];
  const withoutDetails = text.replace(/<details>\s*(?:<summary>([\s\S]*?)<\/summary>)?[\s\S]*?<\/details>/g, (_, summary?: string) => {
    const label = (summary ?? 'Details').replace(/<[^>]+>/g, '').trim() || 'Details';
    return `\n\n\u0001${summaries.push(label) - 1}\u0001\n\n`;
  });
  // fenced code is pulled out next (it may hold blank lines), then put back as a placeholder block
  const fences: string[] = [];
  const withoutFences = withoutDetails.replace(/```\w*\n([\s\S]*?)\n?```/g, (_, code: string) => `\n\n\u0000${fences.push(code) - 1}\u0000\n\n`);
  // HTML comments (e.g. `<!-- bot-workflow-terminal -->`) are not rendered by GitHub either
  const blocks = withoutFences.replace(/<!--[\s\S]*?-->/g, '').trim().split(/\n{2,}/);
  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 12}}>
      {blocks.map((block, i) => {
        const fence = /^\u0000(\d+)\u0000$/.exec(block.trim());
        if (fence) return <CodeBlock key={i} code={fences[Number(fence[1])]} />;
        const details = /^\u0001(\d+)\u0001$/.exec(block.trim());
        if (details) {
          return (
            <div key={i} style={{display: 'flex', alignItems: 'center', gap: 6}}>
              <IconChevronRight size={16} color={t.muted} />
              <span>{renderInline(summaries[Number(details[1])], t)}</span>
            </div>
          );
        }
        const lines = block.split('\n');
        const heading = /^(#{1,6}) (.*)$/.exec(block);
        if (heading && lines.length === 1) {
          const level = heading[1].length;
          return (
            <div
              key={i}
              style={{
                fontSize: [32, 24, 20, 16, 14, 13][level - 1],
                fontWeight: 600,
                lineHeight: 1.25,
                marginTop: i ? 12 : 0,
                paddingBottom: level <= 2 ? 6 : 0,
                borderBottom: level <= 2 ? '1px solid ' + t.border : 'none',
              }}
            >
              {renderInline(heading[2], t)}
            </div>
          );
        }
        if (lines.length >= 2 && lines.every((l) => l.trim().startsWith('|')) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[1])) {
          const cells = (l: string) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
          // wide tables (e.g. 16 metric columns) get denser cells so they fit the comment
          const wide = cells(lines[0]).length > 8;
          const cell: React.CSSProperties = {
            border: '1px solid ' + t.border,
            padding: wide ? '3px 5px' : '4px 8px',
            textAlign: 'left',
            verticalAlign: 'top',
          };
          return (
            <div key={i} style={{overflowX: 'hidden'}}>
              <table style={{borderCollapse: 'collapse', fontSize: wide ? 11 : 12, lineHeight: 1.4, width: 'fit-content', maxWidth: '100%'}}>
                <thead>
                  <tr>
                    {cells(lines[0]).map((c, j) => (
                      <th key={j} style={{...cell, fontWeight: 600}}>
                        {renderInline(c, t)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {lines.slice(2).map((l, r) => (
                    <tr key={r} style={{background: r % 2 ? t.surface : 'transparent'}}>
                      {cells(l).map((c, j) => (
                        <td key={j} style={cell}>
                          {renderInline(c, t)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }
        if (lines.every((l) => /^\s*>/.test(l))) {
          return (
            <blockquote key={i} style={{margin: 0, padding: '0 1em', color: t.muted, borderLeft: '4px solid ' + t.border}}>
              {renderInline(lines.map((l) => l.replace(/^\s*> ?/, '')).join(' '), t)}
            </blockquote>
          );
        }
        if (lines.every((l) => /^\s*[-*] /.test(l))) {
          return (
            <ul key={i} style={{margin: 0, paddingLeft: 28}}>
              {lines.map((l, j) => (
                <li key={j} style={{margin: j ? '4px 0 0' : 0}}>
                  {renderInline(l.replace(/^\s*[-*] /, ''), t)}
                </li>
              ))}
            </ul>
          );
        }
        if (lines.every((l) => /^\s*\d+\. /.test(l))) {
          return (
            <ol key={i} style={{margin: 0, paddingLeft: 28}}>
              {lines.map((l, j) => (
                <li key={j} style={{margin: j ? '4px 0 0' : 0}}>
                  {renderInline(l.replace(/^\s*\d+\. /, ''), t)}
                </li>
              ))}
            </ol>
          );
        }
        return (
          <p key={i} style={{margin: 0}}>
            {lines.map((l, j) => (
              <React.Fragment key={j}>
                {j > 0 ? <br /> : null}
                {renderInline(l, t)}
              </React.Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
};

/* =====================================================================
 * Code block with light syntax highlighting (JSON-ish + diffs)
 * ===================================================================== */
const TOKEN = /("(?:[^"\\]|\\.)*")(\s*:)?|\b(true|false|null)\b|(-?\b\d+(?:\.\d+)?\b)|(\/\/.*$)/g;

const highlight = (line: string, t: Theme): React.ReactNode[] => {
  const out: React.ReactNode[] = [];
  const re = new RegExp(TOKEN.source, 'g');
  let last = 0;
  let k = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(line)) !== null) {
    if (m.index > last) out.push(line.slice(last, m.index));
    const [full, str, colon, lit, num, comment] = m;
    if (str) {
      out.push(
        <span key={k++} style={{color: colon ? t.synKey : t.synString}}>
          {str}
        </span>,
      );
      if (colon) out.push(colon);
    } else if (lit || num) {
      out.push(
        <span key={k++} style={{color: t.synLit}}>
          {full}
        </span>,
      );
    } else if (comment) {
      out.push(
        <span key={k++} style={{color: t.muted}}>
          {comment}
        </span>,
      );
    }
    last = m.index + full.length;
    if (full.length === 0) re.lastIndex++;
  }
  if (last < line.length) out.push(line.slice(last));
  return out;
};

export const prettyJson = (s: string): string => {
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
};

export const CodeBlock: React.FC<{code: string; style?: React.CSSProperties}> = ({code, style}) => {
  const t = useGH();
  const lines = code.replace(/\n$/, '').split('\n');
  const isDiff =
    lines.some((l) => l.startsWith('@@')) ||
    (lines.some((l) => /^\+/.test(l)) && lines.some((l) => /^-/.test(l)));
  return (
    <div
      style={{
        background: t.surface,
        borderRadius: 6,
        padding: 16,
        fontFamily: t.mono,
        fontSize: 12,
        lineHeight: 1.45,
        color: t.text,
        overflow: 'hidden',
        ...style,
      }}
    >
      {lines.map((l, i) => {
        const add = isDiff && /^\+/.test(l);
        const del = isDiff && /^-/.test(l);
        const hunk = isDiff && l.startsWith('@@');
        return (
          <div
            key={i}
            style={{
              margin: '0 -16px',
              padding: '0 16px',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              background: add ? t.addBg : del ? t.delBg : hunk ? t.hunkBg : 'transparent',
              color: hunk ? t.muted : t.text,
            }}
          >
            {l === '' ? ' ' : isDiff ? l : highlight(l, t)}
          </div>
        );
      })}
    </div>
  );
};

/* =====================================================================
 * Markdown alert (> [!WARNING] …)
 * ===================================================================== */
export const GithubAlert: React.FC<{
  kind?: 'note' | 'important' | 'warning' | 'caution';
  title?: string;
  children: React.ReactNode;
}> = ({kind = 'warning', title, children}) => {
  const t = useGH();
  const a = {
    note: {color: t.accent, title: 'Note', Icon: IconInfo},
    important: {color: t.purple, title: 'Important', Icon: IconReport},
    warning: {color: t.attentionFg, title: 'Warning', Icon: IconWarning},
    caution: {color: t.red, title: 'Caution', Icon: IconReport},
  }[kind];
  return (
    <div style={{borderLeft: `4px solid ${a.color}`, padding: '4px 16px', color: t.textSoft}}>
      <div style={{display: 'flex', alignItems: 'center', gap: 8, color: a.color, fontWeight: 500, marginBottom: 4}}>
        <a.Icon size={16} />
        {title ?? a.title}
      </div>
      <div>{children}</div>
    </div>
  );
};

/* =====================================================================
 * Global header + repo header + tabs
 * ===================================================================== */
const IconButton: React.FC<{children: React.ReactNode}> = ({children}) => {
  const t = useGH();
  return (
    <div
      style={{
        width: 32,
        height: 32,
        borderRadius: 6,
        border: '1px solid ' + t.border,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: t.text,
      }}
    >
      {children}
    </div>
  );
};

export const GithubHeader: React.FC<{owner?: string; repo?: string}> = ({owner = 'nebuko', repo = 'nebuko'}) => {
  const t = useGH();
  return (
    <div
      style={{
        height: 64,
        background: t.headerBg,
        borderBottom: '1px solid ' + t.border,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 32px',
        fontFamily: t.font,
        color: t.text,
        fontSize: 14,
        flexShrink: 0,
      }}
    >
      <div style={{display: 'flex', alignItems: 'center', gap: 16}}>
        <IconButton>
          <IconMenu />
        </IconButton>
        <IconMark size={32} color={t.text} />
        <div style={{display: 'flex', alignItems: 'center', gap: 6}}>
          <span style={{color: t.muted}}>{owner}</span>
          <span style={{color: t.muted}}>/</span>
          <span style={{fontWeight: 600}}>{repo}</span>
        </div>
      </div>
      <div style={{display: 'flex', alignItems: 'center', gap: 8}}>
        <div
          style={{
            width: 272,
            height: 32,
            boxSizing: 'border-box',
            background: t.bg,
            border: '1px solid ' + t.border,
            borderRadius: 6,
            padding: '0 8px',
            color: t.muted,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <IconSearch size={16} />
          <span style={{flex: 1}}>Type / to search</span>
        </div>
        <IconButton>
          <IconPlus />
        </IconButton>
        <IconButton>
          <IconIssueOpened />
        </IconButton>
        <IconButton>
          <IconPullRequest />
        </IconButton>
        <IconButton>
          <IconBell />
        </IconButton>
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: '50%',
            background: t.humanAvatar,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 16,
          }}
        >
          🧑‍💻
        </div>
      </div>
    </div>
  );
};

export const TabItem: React.FC<{icon?: React.ReactNode; label: string; count?: string; active?: boolean; extra?: React.ReactNode}> = ({
  icon,
  label,
  count,
  active,
  extra,
}) => {
  const t = useGH();
  return (
    <div
      style={{
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        height: 48,
        color: active ? t.text : t.textSoft,
        fontWeight: active ? 600 : 400,
        whiteSpace: 'nowrap',
      }}
    >
      {icon ? <span style={{color: t.muted, display: 'flex'}}>{icon}</span> : null}
      <span>{label}</span>
      {count ? (
        <span
          style={{
            fontSize: 12,
            fontWeight: 500,
            lineHeight: '18px',
            minWidth: 18,
            textAlign: 'center',
            padding: '0 6px',
            borderRadius: 999,
            background: t.countBg,
            color: t.text,
          }}
        >
          {count}
        </span>
      ) : null}
      {extra}
      {active ? (
        <div style={{position: 'absolute', left: 0, right: 0, bottom: -1, height: 2, borderRadius: 6, background: t.orange}} />
      ) : null}
    </div>
  );
};

export const RepoHeader: React.FC<{owner: string; repo: string; active?: 'issues' | 'pulls'}> = ({owner, repo, active = 'issues'}) => {
  const t = useGH();
  return (
    <div style={{background: t.headerBg, borderBottom: '1px solid ' + t.border, flexShrink: 0, fontFamily: t.font}}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '16px 32px 8px',
          fontSize: 20,
          color: t.text,
        }}
      >
        <div style={{display: 'flex', alignItems: 'center', gap: 8}}>
          <span style={{color: t.muted, display: 'flex'}}>
            <IconRepo size={18} />
          </span>
          <span style={{color: t.accent}}>{owner}</span>
          <span style={{color: t.muted}}>/</span>
          <span style={{fontWeight: 600, color: t.accent}}>{repo}</span>
          <Pill>Public</Pill>
        </div>
        <Button icon={<IconStar size={16} />}>Star</Button>
      </div>
      <div style={{display: 'flex', gap: 16, padding: '0 32px', fontSize: 14}}>
        <TabItem icon={<IconCode />} label="Code" />
        <TabItem icon={<IconIssueOpened />} label="Issues" count="24" active={active === 'issues'} />
        <TabItem icon={<IconPullRequest />} label="Pull requests" count="3" active={active === 'pulls'} />
        <TabItem icon={<IconPlay />} label="Actions" />
        <TabItem icon={<IconTable />} label="Projects" />
        <TabItem icon={<IconShield />} label="Security" />
        <TabItem icon={<IconGraph />} label="Insights" />
      </div>
    </div>
  );
};

/**
 * Folds its content away: `open` 1 = natural height, 0 = gone (grid `0fr → 1fr` trick, no measuring).
 * Drive `open` frame by frame for a smooth collapse.
 */
export const Collapse: React.FC<{
  open: number;
  children: React.ReactNode;
  style?: React.CSSProperties;
  boxRef?: React.Ref<HTMLDivElement>;
}> = ({open, children, style, boxRef}) => (
  <div ref={boxRef} style={{display: 'grid', gridTemplateRows: `${open}fr`, opacity: open, flexShrink: 0, ...style}}>
    <div style={{minHeight: 0, overflow: 'hidden'}}>{children}</div>
  </div>
);

/** Title row + status line shared by the issue and PR pages. */
export const TitleBlock: React.FC<{
  title: string;
  number: number | string;
  badge: React.ReactNode;
  subtitle: React.ReactNode;
  actions?: React.ReactNode;
  /** 0 = full header, 1 = compact sticky bar (smaller title, no status line, no actions) */
  compact?: number;
}> = ({title, number, badge, subtitle, actions, compact = 0}) => {
  const t = useGH();
  const c = compact;
  return (
    <div style={{padding: `${24 - 12 * c}px 32px ${16 - 4 * c}px`, flexShrink: 0, fontFamily: t.font}}>
      <div style={{display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 24}}>
        <div style={{display: 'flex', alignItems: 'center', fontSize: 32 - 10 * c, lineHeight: 1.25, color: t.text, fontWeight: 400 + 200 * c}}>
          {/* compact bar: the status badge moves next to the title (GitHub's sticky header does the same) */}
          {c > 0 ? (
            <span style={{display: 'inline-flex', opacity: c, marginRight: 12, transform: 'scale(0.85)', transformOrigin: 'left center'}}>{badge}</span>
          ) : null}
          <span>
            {title} <span style={{color: t.muted, fontWeight: 300}}>#{number}</span>
          </span>
        </div>
        <div style={{display: 'flex', gap: 8, flexShrink: 0, opacity: 1 - c}}>
          {actions ?? (
            <>
              <Button>Edit</Button>
              <Button primary>New issue</Button>
            </>
          )}
        </div>
      </div>
      <Collapse open={1 - c}>
        <div style={{display: 'flex', alignItems: 'center', gap: 8, marginTop: 12, fontSize: 14, color: t.muted}}>
          {badge}
          <span>{subtitle}</span>
        </div>
      </Collapse>
    </div>
  );
};

/* =====================================================================
 * Comment shell: avatar + caret + header + body + optional footer
 * ===================================================================== */
export const CommentShell: React.FC<{
  avatar: string;
  human: boolean;
  name: string;
  verb: string;
  time: string;
  role?: string | null;
  isBot?: boolean;
  headerExtra?: React.ReactNode;
  opacity?: number;
  translateY?: number;
  ring?: number;
  footer?: React.ReactNode;
  children: React.ReactNode;
}> = ({avatar, human, name, verb, time, role, isBot, headerExtra, opacity = 1, translateY = 0, ring = 0, footer, children}) => {
  const t = useGH();
  // Human comments get the blue "author" style, bots the neutral one:
  // easy to tell people from agents at a glance.
  const headBg = human ? t.accentMuted : t.surface;
  const border = human ? t.accentBorder : t.border;
  return (
    <div
      style={{
        display: 'flex',
        gap: 16,
        position: 'relative',
        zIndex: 1,
        opacity,
        transform: `translateY(${translateY}px)`,
      }}
    >
      <Avatar emoji={avatar} human={human} />
      <div style={{flex: 1, minWidth: 0, position: 'relative'}}>
        {/* caret pointing at the avatar */}
        <div
          style={{
            position: 'absolute',
            left: -6,
            top: 14,
            width: 10,
            height: 10,
            background: headBg,
            borderLeft: '1px solid ' + border,
            borderBottom: '1px solid ' + border,
            transform: 'rotate(45deg)',
            zIndex: 2,
          }}
        />
        <div
          style={{
            border: '1px solid ' + border,
            borderRadius: 6,
            overflow: 'hidden',
            background: t.bg,
            boxShadow: ring > 0 ? `0 0 0 2px rgba(68,147,248,${0.55 * ring})` : 'none',
          }}
        >
          <div
            style={{
              background: headBg,
              borderBottom: '1px solid ' + border,
              padding: '0 8px 0 16px',
              minHeight: 44,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              fontFamily: t.font,
              fontSize: 14,
              color: t.muted,
            }}
          >
            <div style={{display: 'flex', alignItems: 'center', gap: 6}}>
              <span style={{fontWeight: 600, color: t.text}}>{name}</span>
              {isBot ? <Pill>bot</Pill> : null}
              <span>
                {verb} {time}
              </span>
              {headerExtra}
            </div>
            <div style={{display: 'flex', alignItems: 'center', gap: 4}}>
              {role ? <Pill>{role}</Pill> : null}
              <div style={{width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
                <IconSmiley />
              </div>
              <div style={{width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
                <IconKebab />
              </div>
            </div>
          </div>
          <div
            style={{
              padding: 16,
              fontFamily: t.font,
              fontSize: 14,
              lineHeight: 1.5,
              color: t.text,
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
            }}
          >
            {children}
          </div>
          {footer}
        </div>
      </div>
    </div>
  );
};

/* =====================================================================
 * Lightweight timeline event ("nebuko-dev linked a pull request …")
 * ===================================================================== */
export type TimelineKind =
  | 'pr'
  | 'merged'
  | 'closed'
  | 'label'
  | 'check'
  | 'issue'
  | 'commit'
  | 'approved'
  | 'changes'
  | 'commented';

export const useEnter = (animate?: boolean): number => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return animate ? spring({frame: frame - 8, fps, config: {damping: 200}}) : 1;
};

export const GithubTimelineEvent: React.FC<{
  kind?: TimelineKind;
  actor: string;
  time?: string;
  animate?: boolean;
  /** show the animated cursor next to the text (e.g. "click the linked PR") */
  pointer?: boolean;
  children: React.ReactNode;
}> = ({kind = 'label', actor, time, animate, pointer, children}) => {
  const t = useGH();
  const p = useEnter(animate);
  const b = {
    pr: {bg: t.greenEmphasis, fg: '#fff', Icon: IconPullRequest},
    merged: {bg: t.purpleEmphasis, fg: '#fff', Icon: IconMerge},
    closed: {bg: t.purpleEmphasis, fg: '#fff', Icon: IconCheckCircle},
    label: {bg: t.badgeNeutral, fg: t.muted, Icon: IconTag},
    check: {bg: t.greenEmphasis, fg: '#fff', Icon: IconCheck},
    issue: {bg: t.badgeNeutral, fg: t.muted, Icon: IconIssueOpened},
    commit: {bg: t.badgeNeutral, fg: t.muted, Icon: IconCommit},
    approved: {bg: t.greenEmphasis, fg: '#fff', Icon: IconCheck},
    changes: {bg: t.redEmphasis, fg: '#fff', Icon: IconX},
    commented: {bg: t.badgeNeutral, fg: t.muted, Icon: IconSmiley},
  }[kind];
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        position: 'relative',
        zIndex: 1,
        opacity: p,
        transform: `translateY(${(1 - p) * 12}px)`,
        fontFamily: t.font,
        fontSize: 14,
        color: t.muted,
        minHeight: 32,
      }}
    >
      <div
        style={{
          marginLeft: 4,
          width: 32,
          height: 32,
          borderRadius: '50%',
          background: b.bg,
          color: b.fg,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          boxShadow: `0 0 0 4px ${t.bg}`,
          flexShrink: 0,
        }}
      >
        <b.Icon size={16} color={b.fg} />
      </div>
      <div style={{position: 'relative'}}>
        <strong style={{color: t.text, fontWeight: 600}}>{actor}</strong> {children}
        {time ? <span> {time}</span> : null}
        {pointer ? (
          <div style={{position: 'absolute', right: -14, bottom: 6}}>
            <Pointer />
          </div>
        ) : null}
      </div>
    </div>
  );
};

/* =====================================================================
 * Thread column (timeline line + optional composer) and sidebar bits
 * ===================================================================== */
export const ThreadColumn: React.FC<{
  children: React.ReactNode;
  anchor?: 'top' | 'bottom';
  composer?: React.ReactNode;
  /** Reserved band between the newest comment and the composer (e.g. an explainer card), right where the action happens. */
  below?: React.ReactNode;
  belowHeight?: number;
  /** Scroll through a thread taller than the screen: 0 = its top is visible, 1 = its bottom. Overrides `anchor`. */
  scroll?: number;
  /** Push the thread down by this many px (keeps the top of a tall newest comment in view, see Preview). */
  offset?: number;
  /** Ref to the visible area, to measure its height. */
  viewportRef?: React.Ref<HTMLDivElement>;
}> = ({children, anchor = 'top', composer, below, belowHeight = 0, scroll, offset = 0, viewportRef}) => {
  const t = useGH();
  const fade = t.name === 'dark' ? 'rgba(13,17,23,0)' : 'rgba(255,255,255,0)';
  return (
    <div style={{flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column'}}>
      <div
        ref={viewportRef}
        style={{
          flex: 1,
          minHeight: 0,
          position: 'relative',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: anchor === 'bottom' ? 'flex-end' : 'flex-start',
        }}
      >
        <div
          style={{
            position: scroll === undefined ? 'relative' : 'absolute',
            // top: N% of the viewport + translate: -N% of the content → slides from "top aligned" to "bottom aligned", no measuring
            ...(scroll === undefined
              ? {transform: offset ? `translateY(${offset}px)` : undefined}
              : {left: 0, right: 0, top: `${scroll * 100}%`, transform: `translateY(${-scroll * 100}%)`}),
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
            flexShrink: 0,
          }}
        >
          <div style={{position: 'absolute', left: 19, top: 20, bottom: 0, width: 2, background: t.border}} />
          {children}
        </div>
        {anchor === 'bottom' || scroll !== undefined ? (
          <div
            style={{
              position: 'absolute',
              left: 0,
              right: 0,
              top: 0,
              height: 48,
              background: `linear-gradient(${t.bg}, ${fade})`,
              pointerEvents: 'none',
              zIndex: 5,
            }}
          />
        ) : null}
      </div>
      {belowHeight > 0 ? (
        <div style={{position: 'relative', height: belowHeight, flexShrink: 0, marginTop: 16, paddingLeft: 56}}>
          {/* keep the thread's timeline line running down to the composer */}
          <div style={{position: 'absolute', left: 19, top: -16, bottom: 0, width: 2, background: t.border}} />
          <div style={{position: 'relative', height: '100%'}}>{below}</div>
        </div>
      ) : null}
      {composer}
    </div>
  );
};

export const SideSection: React.FC<{title: string; gear?: boolean; last?: boolean; children: React.ReactNode}> = ({
  title,
  gear = true,
  last,
  children,
}) => {
  const t = useGH();
  return (
    <div style={{borderBottom: last ? 'none' : '1px solid ' + t.border, padding: '16px 0'}}>
      <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', color: t.muted, fontSize: 12, fontWeight: 600}}>
        <span>{title}</span>
        {gear ? <IconGear size={16} /> : null}
      </div>
      <div style={{marginTop: 8, color: t.text, fontSize: 12}}>{children}</div>
    </div>
  );
};

export const Composer: React.FC<{
  text?: string;
  connector?: boolean;
  /** absolute frame at which the cursor flies to "Comment" and clicks it (null = no cursor) */
  pointerAt?: number | null;
}> = ({text, connector = true, pointerAt = null}) => {
  const t = useGH();
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const caretOn = Math.floor(frame / (fps / 2)) % 2 === 0;
  const has = !!text;
  return (
    <div style={{marginTop: 16, display: 'flex', gap: 16, position: 'relative', flexShrink: 0, fontFamily: t.font}}>
      {connector ? <div style={{position: 'absolute', left: 19, top: -16, width: 2, height: 16, background: t.border}} /> : null}
      <Avatar emoji="🧑‍💻" human />
      <div style={{flex: 1, minWidth: 0, border: '1px solid ' + t.border, borderRadius: 6, overflow: 'hidden', background: t.bg}}>
        <div
          style={{
            background: t.surface,
            borderBottom: '1px solid ' + t.border,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '8px 8px 0',
            fontSize: 14,
          }}
        >
          <div style={{display: 'flex', gap: 4}}>
            <span
              style={{
                padding: '6px 12px',
                border: '1px solid ' + t.border,
                borderBottom: '1px solid ' + t.bg,
                borderRadius: '6px 6px 0 0',
                background: t.bg,
                marginBottom: -1,
                color: t.text,
              }}
            >
              Write
            </span>
            <span style={{padding: '6px 12px', color: t.muted}}>Preview</span>
          </div>
          <div style={{display: 'flex', gap: 16, color: t.muted, fontFamily: t.mono, fontSize: 13, paddingBottom: 6, paddingRight: 8}}>
            <b>B</b>
            <i>i</i>
            <span>{'<>'}</span>
            <span>@</span>
          </div>
        </div>
        <div style={{padding: '12px 16px', minHeight: 56, fontSize: 14, lineHeight: 1.5, color: has ? t.text : t.muted}}>
          {has ? (
            <>
              <span style={{fontFamily: t.mono, fontSize: 13}}>{text}</span>
              <span
                style={{
                  display: 'inline-block',
                  width: 1.5,
                  height: 16,
                  marginLeft: 1,
                  verticalAlign: 'text-bottom',
                  background: caretOn ? t.text : 'transparent',
                }}
              />
            </>
          ) : (
            'Add your comment here...'
          )}
        </div>
        <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 12px', fontSize: 12, color: t.muted}}>
          <span>Markdown is supported</span>
          <div style={{display: 'flex', gap: 8}}>
            <Button icon={<IconCheckCircle size={16} color={t.purple} />}>Close issue</Button>
            <Button primary disabled={!has} pointed={pointerAt !== null} pointerDelay={pointerAt ?? undefined}>
              Comment
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};

