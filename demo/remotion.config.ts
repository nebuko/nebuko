import {Config} from '@remotion/cli/config';
import {execSync} from 'node:child_process';

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

// `import raw from './comments/x.md'` gives the file as a string: real GitHub comments
// are pasted there verbatim (backticks, HTML comments, <details>…) instead of escaped in data.ts.
Config.overrideWebpackConfig((config) => ({
  ...config,
  module: {
    ...config.module,
    rules: [...(config.module?.rules ?? []), {test: /\.md$/, type: 'asset/source'}],
  },
}));
