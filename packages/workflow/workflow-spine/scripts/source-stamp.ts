#!/usr/bin/env tsx
/**
 * Standalone CLI for computing the current source stamp.
 *
 * Usage:
 *   pnpm exec tsx packages/workflow/workflow-spine/scripts/source-stamp.ts
 *
 * Prints the 16-char hex stamp to stdout.
 *
 * @module @deepseek-ai/dsh-workflow-spine/scripts/source-stamp
 */

import { computeSourceStamp } from '../src/build-stamp.ts'
import { fileURLToPath } from 'node:url'

// When run directly as a CLI
const __file = fileURLToPath(import.meta.url)
const isMain = process.argv[1] !== undefined
  && (process.argv[1] === __file || process.argv[1].endsWith('/source-stamp.ts'))

if (isMain) {
  const stamp = computeSourceStamp()
  console.log(stamp ?? '<no source files found>')
}
