/**
 * Session-level repository guard for vitest.
 *
 * Takes a snapshot of the repository's shared git state (hooks, config, worktree list)
 * at setup and compares at teardown, failing the vitest run when a test changed the
 * repository.  All comparison rules live in Python (repo_guard.py); this file only
 * calls it, so the pytest and vitest guards enforce the same rules.
 *
 * Vitest 4 honours a thrown error in globalSetup teardown: it logs the error and exits
 * non-zero.  process.exitCode = 1 is set as belt-and-suspenders.
 */

import { resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'

const dir = import.meta.dirname
const python = resolve(dir, '.venv', 'bin', 'python')
const guard = resolve(dir, 'refactory', 'tests', 'repo_guard.py')

let repo = ''
let snapshotFile: string | undefined
let guardHome: string

export function setup(project?: { config?: { root?: string } }) {
  repo = project?.config?.root ?? process.cwd()

  if (!existsSync(python)) {
    console.log(`repository guard inactive: python not found at ${python}`)
    return
  }

  // Regenerate the design graph fixture so TS tests see the current graph.
  const exportScript = resolve(dir, 'refactory', 'refactory_dsh', 'tools', 'export_design_graph.py')
  if (existsSync(exportScript)) {
    const exportResult = spawnSync(python, ['-m', 'refactory_dsh.tools.export_design_graph'], {
      cwd: resolve(dir, 'refactory'),
      encoding: 'utf-8',
      timeout: 30_000,
    })
    if (exportResult.status !== 0) {
      console.log(`design graph export: ${exportResult.stderr || 'failed'}`)
    }
  }

  // Not a git checkout — report inactive rather than throw, so pnpm test
  // works in a tarball checkout that has a venv.
  const gitCheck = spawnSync('git', ['-C', repo, 'rev-parse', '--is-inside-work-tree'], {
    encoding: 'utf-8',
    timeout: 10_000,
  })
  if (gitCheck.status !== 0) {
    console.log(`repository guard inactive: ${repo} is not a git repository`)
    return
  }

  guardHome = mkdtempSync(resolve(tmpdir(), 'vitest-guard-home-'))
  process.env.REFACTORY_HOME = resolve(guardHome, 'home')
  process.env.REFACTORY_PROJECTS_DIR = resolve(guardHome, 'projects')

  const result = spawnSync(python, [guard, 'snapshot', repo], {
    encoding: 'utf-8',
    timeout: 30_000,
  })
  if (result.status !== 0) {
    throw new Error(`[repo-guard] could not take snapshot: ${result.stderr}`)
  }
  snapshotFile = resolve(guardHome, 'snapshot.json')
  writeFileSync(snapshotFile, result.stdout)
}

export function teardown() {
  if (!snapshotFile) return

  const result = spawnSync(python, [
    guard, 'compare', repo, snapshotFile,
    '--temp-root', tmpdir(),
    '--refactory-home', resolve(guardHome, 'home'),
  ], {
    encoding: 'utf-8',
    timeout: 30_000,
  })

  try { rmSync(guardHome, { recursive: true, force: true }) } catch {}

  if (result.status !== 0) {
    const message = (result.stderr || 'repository changed during vitest run').trim()
    process.exitCode = 1
    throw new Error(`[repo-guard] ${message}`)
  }
}
