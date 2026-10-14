#!/usr/bin/env tsx
/**
 * Standalone CLI and importable helper for computing the current source stamp.
 *
 * Usage:
 *   pnpm exec tsx packages/workflow/workflow-spine/scripts/source-stamp.ts
 *
 * Prints the 16-char hex stamp to stdout.
 *
 * @module @deepseek-ai/dsh-workflow-spine/scripts/source-stamp
 */

import { createHash } from 'node:crypto'
import { readdirSync, readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __file = fileURLToPath(import.meta.url)
const srcDir = join(dirname(__file), '..', 'src')

/**
 * Compute the source stamp: a SHA-256 of all sorted `*.ts` files in `src/`,
 * truncated to 16 hex characters. Identical to `computeSourceStamp()` in
 * `src/build-stamp.ts`, kept separate so the build config can import it
 * without pulling in the full runtime module.
 */
export function computeSourceStamp(): string {
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

// When run directly as a CLI
const isMain = process.argv[1] !== undefined
  && (process.argv[1] === __file || process.argv[1].endsWith('/source-stamp.ts'))

if (isMain) {
  console.log(computeSourceStamp())
}
