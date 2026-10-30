import React, {createContext, useContext} from 'react';
import {staticFile} from 'remotion';

export type Theme = {
  name: 'dark' | 'light';
  bg: string; // canvas
  inset: string;
  surface: string; // comment header, code blocks, bars
  surfaceHover: string; // secondary buttons
  headerBg: string; // global header
  border: string;
  text: string;
  textSoft: string;
  muted: string;
  accent: string;
  accentMuted: string; // solid tint used for "human" comment headers
  accentBorder: string;
  red: string;
  redEmphasis: string;
  yellow: string;
  green: string;
  greenEmphasis: string;
  purple: string;
  purpleEmphasis: string;
  grayEmphasis: string;
  orange: string;
  neutralMuted: string; // inline code background
  countBg: string; // counters on tabs
  humanAvatar: string;
  badgeNeutral: string; // timeline badge background
  addBg: string;
  delBg: string;
  hunkBg: string;
  synKey: string;
  synString: string;
  synLit: string;
  attentionFg: string;
  attentionBg: string;
  attentionBorder: string;
  successBg: string;
  dangerBg: string;
  doneBg: string;
  primaryText: string;
  btnPrimaryBorder: string;
  shadow: string;
  font: string;
  mono: string;
};

// 'Noto Color Emoji' comes FIRST: the only system font here (DejaVu) reports
// coverage for emoji codepoints, so appended emoji fallbacks would never be reached.
// The emoji font carries no Latin glyphs, so regular text still uses the system fonts.
const FONT = '"Noto Color Emoji", -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif';
const MONO = '"Noto Color Emoji", ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace';

/**
 * The render sandbox has no color-emoji font, so every emoji (avatars,
 * participants) renders as tofu rectangles. Bundle Noto Color Emoji instead.
 */
const EMOJI_FONT_FACE = `
@font-face {
  font-family: 'Noto Color Emoji';
  src: url('${staticFile('NotoColorEmoji.ttf')}') format('truetype');
  font-display: block;
}`;

export const DARK: Theme = {
  name: 'dark',
  bg: '#0d1117',
  inset: '#010409',
  surface: '#151b23',
  surfaceHover: '#212830',
  headerBg: '#151b23',
  border: '#3d444d',
  text: '#f0f6fc',
  textSoft: '#d1d7e0',
  muted: '#9198a1',
  accent: '#4493f8',
  accentMuted: '#111d2e',
  accentBorder: 'rgba(56,139,253,0.4)',
  red: '#f85149',
  redEmphasis: '#da3633',
  yellow: '#d29922',
  green: '#3fb950',
  greenEmphasis: '#238636',
  purple: '#ab7df8',
  purpleEmphasis: '#8957e5',
  grayEmphasis: '#6e7681',
  orange: '#f78166',
  neutralMuted: 'rgba(101,108,118,0.2)',
  countBg: 'rgba(110,118,129,0.4)',
  humanAvatar: '#1f6feb',
  badgeNeutral: '#262c36',
  addBg: 'rgba(46,160,67,0.15)',
  delBg: 'rgba(248,81,73,0.1)',
  hunkBg: 'rgba(56,139,253,0.15)',
  synKey: '#79c0ff',
  synString: '#a5d6ff',
  synLit: '#79c0ff',
  attentionFg: '#d29922',
  attentionBg: 'rgba(210,153,34,0.1)',
  attentionBorder: 'rgba(210,153,34,0.4)',
  successBg: 'rgba(46,160,67,0.15)',
  dangerBg: 'rgba(248,81,73,0.1)',
  doneBg: 'rgba(171,125,248,0.15)',
  primaryText: '#ffffff',
  btnPrimaryBorder: 'rgba(240,246,252,0.1)',
  shadow: '0 8px 24px rgba(1,4,9,0.85)',
  font: FONT,
  mono: MONO,
};

export const LIGHT: Theme = {
  name: 'light',
  bg: '#ffffff',
  inset: '#f6f8fa',
  surface: '#f6f8fa',
  surfaceHover: '#f6f8fa',
  headerBg: '#f6f8fa',
  border: '#d1d9e0',
  text: '#1f2328',
  textSoft: '#1f2328',
  muted: '#59636e',
  accent: '#0969da',
  accentMuted: '#ddf4ff',
  accentBorder: '#b6e3ff',
  red: '#d1242f',
  redEmphasis: '#cf222e',
  yellow: '#9a6700',
  green: '#1a7f37',
  greenEmphasis: '#1f883d',
  purple: '#8250df',
  purpleEmphasis: '#8250df',
  grayEmphasis: '#59636e',
  orange: '#fd8c73',
  neutralMuted: 'rgba(129,139,152,0.12)',
  countBg: 'rgba(129,139,152,0.2)',
  humanAvatar: '#0969da',
  badgeNeutral: '#eaeef2',
  addBg: '#dafbe1',
  delBg: '#ffebe9',
  hunkBg: '#ddf4ff',
  synKey: '#0550ae',
  synString: '#0a3069',
  synLit: '#0550ae',
  attentionFg: '#9a6700',
  attentionBg: '#fff8c5',
  attentionBorder: 'rgba(212,167,44,0.4)',
  successBg: '#dafbe1',
  dangerBg: '#ffebe9',
  doneBg: '#fbefff',
  primaryText: '#ffffff',
  btnPrimaryBorder: 'rgba(31,35,40,0.15)',
  shadow: '0 8px 24px rgba(140,149,159,0.3)',
  font: FONT,
  mono: MONO,
};

export const THEMES = {dark: DARK, light: LIGHT};
export type ThemeName = keyof typeof THEMES;

/** Back-compat: the original `GH` token object (dark). Prefer `useGH()` inside components. */
export const GH: Theme = DARK;
export const GH_LIGHT: Theme = LIGHT;

const ThemeCtx = createContext<Theme>(LIGHT);
export const useGH = (): Theme => useContext(ThemeCtx);

export const ThemeProvider: React.FC<{theme?: ThemeName; children: React.ReactNode}> = ({theme = 'light', children}) => (
  <>
    <style dangerouslySetInnerHTML={{__html: EMOJI_FONT_FACE}} />
    <ThemeCtx.Provider value={THEMES[theme]}>{children}</ThemeCtx.Provider>
  </>
);
