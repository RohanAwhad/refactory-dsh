/**
 * Package-level tsdown config for workflow-spine.
 *
 * Computes a SHA-256 source stamp at build time and injects it into the
 * bundle via `define`, replacing the `__WORKFLOW_BUILD_STAMP__` bare
 * identifier in `src/build-stamp.ts`. This lets `validateBuild()` detect
 * at runtime when `lib/index.js` is older than the source checkout.
 */

import { defineConfig } from 'tsdown'
import { computeSourceStamp } from './src/build-stamp.ts'

export default defineConfig({
  entry: ['lib/types/index.js'],
  outDir: 'lib',
  format: ['esm'],
  platform: 'node',
  target: 'es2024',
  fixedExtension: false,
  dts: false,
  clean: false,
  define: {
    __WORKFLOW_BUILD_STAMP__: `"${computeSourceStamp()}"`,
  },
})
