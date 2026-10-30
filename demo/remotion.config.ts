import {Config} from '@remotion/cli/config';
import {execSync} from 'node:child_process';
import {mkdirSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

// On NixOS, Remotion's self-downloaded chrome-headless-shell cannot start
// (unpatched, missing libnspr4 & friends). Prefer a Chromium from the system
// (nix profile / devShell), and only fall back to the download elsewhere.
const findChromium = (): string | undefined => {
  try {
    return execSync('command -v chromium || command -v chromium-browser || command -v google-chrome-stable', {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim() || undefined;
  } catch {
    return undefined;
  }
};

const chromium = findChromium();
if (chromium) {
  Config.setBrowserExecutable(chromium);
}

/*
 * Chromium finds its platform fonts through fontconfig. Some render
 * environments (this repo's nix container) ship a chromium but NO fontconfig
 * setup — not even /etc/fonts/fonts.conf. Chromium then finds zero fonts and
 * renders NO text at all: every run fails to shape
 * ("TextRunHarfBuzz error ... font: '', glyph_count: 0"), and @font-face
 * webfonts fail too, because the whole platform font stack never initializes.
 *
 * Fix: point FONTCONFIG_FILE at a generated config that scans the fonts
 * bundled with this demo — `fontconfig/fonts/` (DejaVu Sans + Mono, the
 * system-family fallback for the GitHub UI) and `public/` (Montserrat for the
 * hero, Noto Color Emoji for the avatars). The env var propagates to the
 * browser Remotion spawns, and an explicit FONTCONFIG_FILE always wins.
 *
 * Remotion executes this file with eval() in its own module context, so
 * `__dirname` points at @remotion/cli/dist — but it chdirs to the Remotion
 * root first, which is what `process.cwd()` returns here.
 */
if (!process.env.FONTCONFIG_FILE) {
  const cacheDir = join(tmpdir(), 'nebuko-demo-fontconfig');
  mkdirSync(cacheDir, {recursive: true});
  const fontsConf = join(cacheDir, 'fonts.conf');
  writeFileSync(
    fontsConf,
    [
      '<?xml version="1.0"?>',
      '<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">',
      '<fontconfig>',
      `  <dir>${join(process.cwd(), 'fontconfig', 'fonts')}</dir>`,
      `  <dir>${join(process.cwd(), 'public')}</dir>`,
      `  <cachedir>${cacheDir}</cachedir>`,
      '  <alias><family>sans-serif</family><prefer><family>DejaVu Sans</family></prefer></alias>',
      '  <alias><family>monospace</family><prefer><family>DejaVu Sans Mono</family></prefer></alias>',
      '</fontconfig>',
    ].join('\n'),
  );
  process.env.FONTCONFIG_FILE = fontsConf;
}

// `import raw from './comments/x.md'` gives the file as a string: real GitHub comments
// are pasted there verbatim (backticks, HTML comments, <details>…) instead of escaped in data.ts.
Config.overrideWebpackConfig((config) => ({
  ...config,
  module: {
    ...config.module,
    rules: [...(config.module?.rules ?? []), {test: /\.md$/, type: 'asset/source'}],
  },
}));
