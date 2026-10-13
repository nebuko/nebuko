import {Config} from '@remotion/cli/config';

// `import raw from './comments/x.md'` gives the file as a string: real GitHub comments
// are pasted there verbatim (backticks, HTML comments, <details>…) instead of escaped in data.ts.
Config.overrideWebpackConfig((config) => ({
  ...config,
  module: {
    ...config.module,
    rules: [...(config.module?.rules ?? []), {test: /\.md$/, type: 'asset/source'}],
  },
}));
