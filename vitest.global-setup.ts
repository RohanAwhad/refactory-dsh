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
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'

const dir = import.meta.dirname
const python = resolve(dir, '.venv', 'bin', 'python')
const guard = resolve(dir, 'refactory', 'tests', 'repo_guard.py')
const repo = dir

let snapshotFile: string | undefined
let guardHome: string

export function setup() {
  guardHome = mkdtempSync(resolve(tmpdir(), 'vitest-guard-home-'))
  process.env.REFACTORY_HOME = resolve(guardHome, 'home')
  process.env.REFACTORY_PROJECTS_DIR = resolve(guardHome, 'projects')

  const result = spawnSync(python, [guard, 'snapshot', repo], {
    encoding: 'utf-8',
    timeout: 30_000,
  })
  if (result.status !== 0) {
    console.warn('[repo-guard] could not take snapshot:', result.stderr)
    return
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
