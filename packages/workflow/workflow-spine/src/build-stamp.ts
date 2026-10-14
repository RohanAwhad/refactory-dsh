/**
 * Build-stamp mechanism for detecting stale workflow-spine builds.
 *
 * At build time, tsdown's `define` injects a SHA-256 content hash of the
 * TypeScript source files into the bundle, replacing the bare identifier
 * `__WORKFLOW_BUILD_STAMP__`. At runtime, `validateBuild()` compares the
 * embedded stamp against a freshly computed one and throws a clear error
 * when they differ.
 *
 * @module @deepseek-ai/dsh-workflow-spine/build-stamp
 */

import { createHash } from 'node:crypto'
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// Declared but never defined in source — tsdown `define` replaces this
// identifier at bundle time with the computed stamp string literal.
declare const __WORKFLOW_BUILD_STAMP__: string

/**
 * Build-time stamp injected by tsdown's `define`. When running from
 * TypeScript source the identifier is undefined (`typeof === 'undefined'`),
 * so the fallback empty string signals "not a built bundle" and
 * `validateBuild()` no-ops.
 */
export const BUILD_STAMP: string =
  typeof __WORKFLOW_BUILD_STAMP__ !== 'undefined' ? __WORKFLOW_BUILD_STAMP__ : ''

/**
 * Compute a SHA-256 content hash of all `*.ts` files in the `src/` directory,
 * sorted by filename, truncated to 16 hex characters.
 *
 * The hash is deterministic for a given set of source files: it depends only
 * on the sorted filenames and their contents, not on timestamps or metadata.
 *
 * Returns `null` when the `src/` directory does not exist (e.g. installed
 * package without source), so callers can distinguish "absent" from "present
 * but empty".
 * @returns the 16-character hex stamp, or `null` when there is no source to hash.
 */
export function computeSourceStamp(): string | null {
  const thisDir = dirname(fileURLToPath(import.meta.url))
  const srcDir = join(thisDir, '..', 'src')

  let files: string[]
  try {
    files = readdirSync(srcDir)
      .filter(name => name.endsWith('.ts'))
      .sort()
  } catch {
    // src/ doesn't exist — installed package without source tree.
    return null
  }

  if (files.length === 0) return null

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
 * - When running from source (BUILD_STAMP is empty): no-op.
 * - When source is absent (computeSourceStamp returns null): no-op.
 * - When stamps match: no-op.
 * - When stamps differ: throws an error telling the developer to rebuild.
 * @param built - the stamp embedded in the bundle; defaults to `BUILD_STAMP`.
 * @param source - computes the source tree's stamp; defaults to `computeSourceStamp`, and is
 *   called only when there is a built stamp to compare, so running from source never hashes.
 * @returns nothing; a stale build throws instead.
 */
export function validateBuild(
  built: string = BUILD_STAMP,
  source: () => string | null = computeSourceStamp,
): void {
  // Running from TypeScript source — the identifier wasn't replaced.
  if (built === '') return

  const current = source()
  // Source tree absent (installed package) — nothing to validate against.
  if (current === null) return

  if (built === current) return

  throw new Error(
    `Workflow engine build is out of date (build stamp: ${built}, `
    + `source stamp: ${current}). Run \`pnpm run build\` to rebuild.`,
  )
}
