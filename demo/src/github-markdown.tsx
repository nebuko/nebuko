import React from 'react';
import ReactMarkdown, {Components} from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';
import {CodeBlock} from './github-core';
import {useGH} from './github-theme';

/* =====================================================================
 * GitHub-flavored Markdown: GFM (tables, task lists, strikethrough,
 * autolinks) + raw HTML (<details>, <picture>, <br>…; HTML comments are
 * dropped, as on GitHub). Styles follow github-markdown-css, driven by
 * the theme through CSS variables so light and dark both work.
 * ===================================================================== */

const CSS = `
.gh-md { font-size: 14px; line-height: 1.5; color: var(--md-text); word-wrap: break-word; }
.gh-md > *:first-child { margin-top: 0 !important; }
.gh-md > *:last-child { margin-bottom: 0 !important; }
.gh-md p, .gh-md blockquote, .gh-md ul, .gh-md ol, .gh-md dl, .gh-md table, .gh-md pre, .gh-md details { margin-top: 0; margin-bottom: 16px; }
.gh-md h1, .gh-md h2, .gh-md h3, .gh-md h4, .gh-md h5, .gh-md h6 { margin-top: 24px; margin-bottom: 16px; font-weight: 600; line-height: 1.25; }
.gh-md h1 { font-size: 2em; padding-bottom: .3em; border-bottom: 1px solid var(--md-border-muted); }
.gh-md h2 { font-size: 1.5em; padding-bottom: .3em; border-bottom: 1px solid var(--md-border-muted); }
.gh-md h3 { font-size: 1.25em; }
.gh-md h4 { font-size: 1em; }
.gh-md h5 { font-size: .875em; }
.gh-md h6 { font-size: .85em; color: var(--md-muted); }
.gh-md a { color: var(--md-accent); text-decoration: none; }
.gh-md strong { font-weight: 600; }
.gh-md hr { height: .25em; padding: 0; margin: 24px 0; background: var(--md-border); border: 0; }
.gh-md ul, .gh-md ol { padding-left: 2em; }
.gh-md ul ul, .gh-md ul ol, .gh-md ol ol, .gh-md ol ul { margin-top: 0; margin-bottom: 0; }
.gh-md li + li { margin-top: .25em; }
.gh-md li > p { margin-top: 16px; }
.gh-md li.task-list-item { list-style-type: none; }
.gh-md li.task-list-item input { margin: 0 .2em .25em -1.4em; vertical-align: middle; }
.gh-md ul.contains-task-list { padding-left: 2em; }
.gh-md blockquote { padding: 0 1em; color: var(--md-muted); border-left: .25em solid var(--md-border); }
.gh-md blockquote > :first-child { margin-top: 0; }
.gh-md blockquote > :last-child { margin-bottom: 0; }
.gh-md code { padding: .2em .4em; margin: 0; font-size: 85%; white-space: break-spaces; background: var(--md-code-bg); border-radius: 6px; font-family: var(--md-mono); }
.gh-md table { display: block; width: max-content; max-width: 100%; overflow: hidden; border-spacing: 0; border-collapse: collapse; font-size: 12px; }
.gh-md table th { font-weight: 600; }
.gh-md table th, .gh-md table td { padding: 4px 8px; border: 1px solid var(--md-border); }
.gh-md table tr { background: var(--md-bg); border-top: 1px solid var(--md-border-muted); }
.gh-md table tr:nth-child(2n) { background: var(--md-surface); }
.gh-md details summary { cursor: pointer; }
.gh-md details:not([open]) > *:not(summary) { display: none !important; }
.gh-md img { max-width: 100%; box-sizing: content-box; vertical-align: middle; }
.gh-md del { color: var(--md-muted); }
`;

/** GitHub turns `#123` and `@user` into links; plain text here, so color them the same way. */
const REFS = /(#\d+|@[\w-]+)/;
const IS_REF = /^(#\d+|@[\w-]+)$/;
const linkRefs = (node: React.ReactNode, key = 0): React.ReactNode => {
  if (typeof node === 'string') {
    const parts = node.split(REFS);
    if (parts.length === 1) return node;
    return parts.map((p, i) =>
      IS_REF.test(p) ? (
        <span key={`${key}-${i}`} style={{color: p.startsWith('#') ? 'var(--md-accent)' : undefined, fontWeight: p.startsWith('@') ? 600 : undefined}}>
          {p}
        </span>
      ) : (
        p
      ),
    );
  }
  if (Array.isArray(node)) return node.map((n, i) => <React.Fragment key={i}>{linkRefs(n, i)}</React.Fragment>);
  return node;
};

/** Text of a `<code>` element handed to `pre` by react-markdown. */
const codeText = (children: React.ReactNode): string => {
  const child = React.Children.toArray(children)[0];
  if (React.isValidElement<{children?: React.ReactNode}>(child)) return String(child.props.children ?? '');
  return String(child ?? '');
};

// wrap plain-text containers so #123 / @user get their GitHub color
const withRefs =
  (Tag: 'p' | 'li' | 'td' | 'strong' | 'em') =>
  ({node: _node, children, ...props}: React.HTMLAttributes<HTMLElement> & {node?: unknown}) =>
    React.createElement(Tag, props, linkRefs(children));

const components: Components = {
  // fenced code keeps the highlighted / diff-aware block used everywhere else
  pre: ({children}) => <CodeBlock code={codeText(children)} style={{marginBottom: 16}} />,
  p: withRefs('p'),
  li: withRefs('li'),
  td: withRefs('td'),
  strong: withRefs('strong'),
  em: withRefs('em'),
};

const REMARK = [remarkGfm];
const REHYPE = [rehypeRaw];

// Memoized: the text never changes between frames, and re-parsing every comment
// (remark + rehype-raw) on every frame dominates render time.
export const Markdown = React.memo<{text: string}>(({text}) => {
  const t = useGH();
  const vars = {
    '--md-text': t.text,
    '--md-muted': t.muted,
    '--md-accent': t.accent,
    '--md-border': t.border,
    '--md-border-muted': t.border,
    '--md-bg': t.bg,
    '--md-surface': t.surface,
    '--md-code-bg': t.neutralMuted,
    '--md-mono': t.mono,
  } as React.CSSProperties;
  return (
    <div className="gh-md" style={vars}>
      <style>{CSS}</style>
      <ReactMarkdown remarkPlugins={REMARK} rehypePlugins={REHYPE} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
});
