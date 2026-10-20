/**
 * Workspace-wide build-stamp mechanism.
 *
 * Extends the per-package stamp in `build-stamp.ts` to cover all TypeScript
 * packages the runtime loads. At build time, tsdown's `define` injects the
 * computed hash; at runtime, `validateWorkspaceBuild()` compares.
 *
 * @module @deepseek-ai/dsh-workflow-spine/workspace-stamp
 */

import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// Declared but never defined in source — tsdown `define` replaces this at bundle time.
declare const __WORKSPACE_BUILD_STAMP__: string

/**
 * The workspace packages whose sources feed the workspace stamp.
 * Used as a fallback when ``pnpm-workspace.yaml`` cannot be read.
 */
const FALLBACK_WORKSPACE_PACKAGES = [
  'packages/workflow/workflow-spine',
  'packages/bundle/graph',
  'packages/bundle/refactory',
  'packages/bundle/refactory-web',
  'packages/refactory/tool-refactory',
  'packages/refactory/refactory-console',
  'packages/client/ui-refactory',
  'packages/client/ui-brand-refactory',
] as const

/**
 * Resolve workspace package paths from ``pnpm-workspace.yaml``.
 *
 * Parses the ``packages:`` list (exact paths, one per ``- `` line).
 * Falls back to {@link FALLBACK_WORKSPACE_PACKAGES} when the file is
 * absent or cannot be parsed.
 */
function resolveWorkspacePackages(root: string): string[] {
  try {
    const wsFile = join(root, 'pnpm-workspace.yaml')
    const content = readFileSync(wsFile, 'utf8')
    let inPackages = false
    const packages: string[] = []
    for (const line of content.split('\n')) {
      const trimmed = line.trim()
      if (trimmed === 'packages:') { inPackages = true; continue }
      if (inPackages) {
        if (trimmed.startsWith('- ')) {
          packages.push(trimmed.slice(2).trim().replace(/^['"]|['"]$/g, ''))
        } else if (trimmed !== '' && !trimmed.startsWith('#')) {
          break
        }
      }
    }
    if (packages.length === 0) return [...FALLBACK_WORKSPACE_PACKAGES]
    // Filter to directories that have a src/ subdirectory.
    const result: string[] = []
    for (const pkg of packages) {
      try {
        readdirSync(join(root, pkg, 'src'))
        result.push(pkg)
      } catch { /* no src dir — skip */ }
    }
    return result.length > 0 ? result.sort() : [...FALLBACK_WORKSPACE_PACKAGES]
  } catch {
    return [...FALLBACK_WORKSPACE_PACKAGES]
  }
}

/**
 * Build-time workspace stamp injected by tsdown's `define`. When running from
 * TypeScript source the identifier is undefined, so the fallback empty string
 * signals "not a built bundle" and `validateWorkspaceBuild()` no-ops.
 */
export const WORKSPACE_BUILD_STAMP: string =
  typeof __WORKSPACE_BUILD_STAMP__ !== 'undefined' ? __WORKSPACE_BUILD_STAMP__ : ''

/**
 * Compute a SHA-256 content hash of all `*.ts` files in the `src/` directory
 * of each workspace package, sorted by package path then filename, truncated
 * to 16 hex characters.
 *
 * @param root - the monorepo root; defaults to four levels up from this file.
 * @returns the 16-character hex stamp, or `null` when no source exists.
 */
export function computeWorkspaceStamp(root?: string): string | null {
  const thisDir = dirname(fileURLToPath(import.meta.url))
  const repoRoot = root ?? resolve(thisDir, '..', '..', '..', '..')

  const hash = createHash('sha256')
  let foundAny = false

  const packages = resolveWorkspacePackages(repoRoot)
  for (const pkg of packages) {
    const srcDir = join(repoRoot, pkg, 'src')
    let files: string[]
    try {
      files = (readdirSync(srcDir, { recursive: true }) as string[])
        .filter(name => name.endsWith('.ts') || name.endsWith('.tsx'))
        .sort()
    } catch {
      continue
    }
    for (const file of files) {
      const relPath = `${pkg}/src/${file}`
      hash.update(relPath)
      hash.update(readFileSync(join(srcDir, file)))
      foundAny = true
    }
  }

  return foundAny ? hash.digest('hex').slice(0, 16) : null
}

/**
 * Compare the embedded workspace stamp against a freshly computed one.
 *
 * - When running from source (WORKSPACE_BUILD_STAMP is empty): no-op.
 * - When source is absent (computeWorkspaceStamp returns null): no-op.
 * - When stamps match: no-op.
 * - When stamps differ: throws an error telling the developer to rebuild.
 */
export function validateWorkspaceBuild(
  built: string = WORKSPACE_BUILD_STAMP,
  source: () => string | null = computeWorkspaceStamp,
): void {
  if (built === '') return
  const current = source()
  if (current === null) return
  if (built === current) return
  throw new Error(
    `Workspace build is out of date (workspace stamp: ${built}, `
    + `source stamp: ${current}). Run \`pnpm run build\` to rebuild.`,
  )
}

/**
 * Write the workspace stamp to a file for Python's `require_build()` to read.
 */
export function writeWorkspaceStamp(outDir: string, stamp: string | null): void {
  if (stamp !== null) {
    writeFileSync(join(outDir, '.workspace-stamp'), stamp, 'utf8')
  }
}
