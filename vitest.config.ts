import tsconfigPaths from 'vite-tsconfig-paths'
import { defineConfig } from 'vitest/config'
import { standardDecoratorPlugin, vitestExecArgv } from './vitest.shared.ts'

// Resolution facade shared by every plugin instance below: tsconfig.base.json
// has no include, which vite-tsconfig-paths treats as match-all, so its paths map
// applies to every test file. The map covers the eight packages this fork owns;
// every harness specifier resolves through node_modules to the published pin.
const pathsPlugin = (): ReturnType<typeof tsconfigPaths> => tsconfigPaths({ projects: ['./tsconfig.base.json'] })

export default defineConfig({
  plugins: [pathsPlugin(), standardDecoratorPlugin()],
  test: {
    // .tsx: client component specs (jsdom via per-file @vitest-environment pragma).
    include: ['packages/*/*/tests/**/*.spec.{ts,tsx}'],
    execArgv: vitestExecArgv,
    // The published harness packages ship bundled JavaScript that imports CSS
    // and undeclared third-party libraries; inlining them lets Vite apply the
    // CSS pipeline and resolve those imports from this workspace's root.
    server: { deps: { inline: [/@deepseek-ai\//] } },
    // Forked workers: Node 24 has aborted in its CJS lexer from worker threads.
    globalSetup: ['./vitest.global-setup.ts'],
    pool: 'forks',
    coverage: {
      provider: 'v8',
      include: ['packages/*/*/src/**/*.{ts,tsx}'],
      reporter: ['text'],
    },
  },
})
