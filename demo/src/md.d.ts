// See remotion.config.ts: .md files are imported as raw strings.
declare module '*.md' {
  const content: string;
  export default content;
}
