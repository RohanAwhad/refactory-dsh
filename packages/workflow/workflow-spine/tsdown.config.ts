/**
 * Package-level tsdown config for workflow-spine.
 *
 * Computes a SHA-256 source stamp at build time and injects it into the
 * bundle via `define`, replacing the `__WORKFLOW_BUILD_STAMP__` bare
 * identifier in `src/build-stamp.ts`. This lets `validateBuild()` detect
 * at runtime when `lib/index.js` is older than the source checkout.
 */

import { join } from 'node:path'
import { defineConfig } from 'tsdown'
import { computeSourceStamp } from './src/build-stamp.ts'
import { computeWorkspaceStamp, writeWorkspaceStamp } from './src/workspace-stamp.ts'

const workspaceStamp = computeWorkspaceStamp()

// Resolve paths relative to this config file's directory (the package root),
// not the monorepo root where pnpm sets the CWD.
const pkgDir = import.meta.dirname

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
    __WORKSPACE_BUILD_STAMP__: `"${workspaceStamp ?? ''}"`,
  },
  onSuccess() {
    // Write the workspace stamp to a file Python's require_build() reads.
    // Use pkgDir so the path resolves correctly regardless of CWD.
    writeWorkspaceStamp(join(pkgDir, 'lib'), workspaceStamp)
  },
})
