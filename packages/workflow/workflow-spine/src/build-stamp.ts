/**
 * Build-stamp mechanism for detecting stale workflow-spine builds.
 *
 * At build time, tsdown's `define` injects a SHA-256 content hash of the
 * TypeScript source files into the bundle, replacing the placeholder.
 * At runtime, `validateBuild()` compares the embedded stamp against a
 * freshly computed one and throws a clear error when they differ.
 *
 * @module @deepseek-ai/dsh-workflow-spine/build-stamp
 */

import { createHash } from 'node:crypto'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * Build-time stamp injected by tsdown's `define`. The literal placeholder
 * is replaced at bundle time; when running from source (dev/test) it keeps
 * its original value and `validateBuild()` no-ops.
 */
export const BUILD_STAMP: string = '__WORKFLOW_BUILD_STAMP__'

/** The placeholder value present when running from TypeScript source. */
const PLACEHOLDER = '__WORKFLOW_BUILD_STAMP__'

/**
 * Compute a SHA-256 content hash of all `*.ts` files in the `src/` directory,
 * sorted by filename, truncated to 16 hex characters.
 *
 * The hash is deterministic for a given set of source files: it depends only
 * on the sorted filenames and their contents, not on timestamps or metadata.
 */
export function computeSourceStamp(): string {
  const srcDir = join(__dirname, '..', 'src')
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

/**
 * Compare the embedded build stamp against a freshly computed source stamp.
 *
 * - When running from source (the placeholder is intact): no-op.
 * - When stamps match: no-op.
 * - When stamps differ: throws an error telling the developer to rebuild.
 */
export function validateBuild(): void {
  // Running from TypeScript source — nothing to validate.
  if (BUILD_STAMP === PLACEHOLDER) return

  const current = computeSourceStamp()
  if (BUILD_STAMP === current) return

  throw new Error(
    `Workflow engine build is out of date (build stamp: ${BUILD_STAMP}, `
    + `source stamp: ${current}). Run \`pnpm run build\` to rebuild.`,
  )
}
