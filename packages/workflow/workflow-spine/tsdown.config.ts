/**
 * Package-level tsdown config for workflow-spine.
 *
 * Computes a SHA-256 source stamp at build time and injects it into the
 * bundle via `define`, replacing the `__WORKFLOW_BUILD_STAMP__` placeholder
 * in `src/build-stamp.ts`. This lets `validateBuild()` detect at runtime
 * when `lib/index.js` is older than the source checkout.
 */

import { defineConfig } from 'tsdown'
import { createHash } from 'node:crypto'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

function computeSourceStamp(): string {
  const srcDir = join(__dirname, 'src')
  const files = readdirSync(srcDir)
    .filter(name => name.endsWith('.ts'))
    .sort()

  const hash = createHash('sha256')
  for (const file of files) {
    hash.update(file)
    hash.update(readFileSync(join(srcDir, file)))
  }
  return hash.digest('hex').slice(0, 16)
}

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
    '__WORKFLOW_BUILD_STAMP__': `"${computeSourceStamp()}"`,
  },
})
