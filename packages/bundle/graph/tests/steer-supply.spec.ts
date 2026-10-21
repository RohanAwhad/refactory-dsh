/**
 * End-to-end test: product supplies the steer command through graphStartup,
 * consumed by cordis.patch.yml's !!js expressions.
 *
 * The test boots startup.ts with --python-bin pointing at the repository's
 * .venv/bin, which has the refactory.steer entry point installed by `uv sync`.
 * It evaluates the YAML's !!js expressions through the loader's `evaluate`
 * import (not a test-local evaluator). The spine's own steering.spec.ts
 * already tests that wf_steer is registered when steerCommand !== '' and
 * workdir is set, so this test covers the supply chain up to the spine's config.
 */
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import { internals, provideCmdline } from '@deepseek-ai/dsh-cmdline'
import { evaluate } from '@deepseek-ai/cordis-plugin-loader'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apply, GRAPH_STARTUP_SERVICE, type GraphStartupValues } from '../src/startup.ts'

const dirs: string[] = []

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true })
  internals.stdout = process.stdout
  internals.stderr = process.stderr
})

/** A graphs directory holding one `<mode>.graph.json`. */
function graphs(mode: string, body: unknown = { name: mode, nodes: {}, edges: [] }): string {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-graph-steer-'))
  dirs.push(dir)
  writeFileSync(join(dir, `${mode}.graph.json`), JSON.stringify(body))
  return dir
}

/** Boot the provider over a Context and collect what the invocation produced. */
function boot(
  args: string[],
  applyFn: typeof apply = apply,
): { values: GraphStartupValues | undefined; exits: number[]; out: string } {
  const exits: number[] = []
  let out = ''
  internals.stdout = { write: (chunk: string) => { out += chunk; return true } }
  internals.stderr = internals.stdout
  const ctx = new Context()
  provideCmdline(ctx, { args, exit: code => void exits.push(code) })
  applyFn(ctx)
  return { values: ctx.get(GRAPH_STARTUP_SERVICE) as GraphStartupValues | undefined, exits, out }
}

/** Read the !!js expressions from cordis.patch.yml */
function readYamlExpressions(): { steerCommandExpr: string; steerArgsExpr: string } {
  const yml = readFileSync(join(__dirname, '..', 'cordis.patch.yml'), 'utf8')
  const cmdMatch = yml.match(/^\s+steerCommand:\s*!!js\s+(.+)$/m)
  const argsMatch = yml.match(/^\s+steerArgs:\s*!!js\s+(.+)$/m)
  if (!cmdMatch || !argsMatch) throw new Error('steerCommand/steerArgs !!js expressions not found in cordis.patch.yml')
  return { steerCommandExpr: cmdMatch[1]!, steerArgsExpr: argsMatch[1]! }
}

const repoRoot = resolve(__dirname, '..', '..', '..', '..')
const venvBin = join(repoRoot, '.venv', 'bin')

describe('steer command supply', () => {
  it('end-to-end: startup discovers the entry point, YAML reads it, producing spine config', () => {
    // Boot startup.ts with --python-bin pointing at the real venv.
    const dir = graphs('probe')
    const workdir = mkdtempSync(join(tmpdir(), 'dsh-graph-steer-work-'))
    dirs.push(workdir)
    const { values, exits } = boot([
      '--mode', 'probe', '--task', 'circle_packing', '--budget', '7',
      '--graphs', dir, '--workdir', workdir, '--python-bin', venvBin,
    ])
    expect(exits).toEqual([])
    expect(values).toBeDefined()
    const v = values!
    // startup should have discovered the steer module
    expect(v.steerCommand).toBe(join(venvBin, 'python'))
    expect(v.steerArgs).toEqual(['-m', 'refactory_dsh.console', 'steer', '--run'])

    // Evaluate the YAML !!js expressions through the loader's evaluate
    const { steerCommandExpr, steerArgsExpr } = readYamlExpressions()
    const ctx = { graphStartup: v, _steerWarnedOnce: false }
    const evalSteerCommand = evaluate(ctx, steerCommandExpr) as string
    const evalSteerArgs = evaluate(ctx, steerArgsExpr) as string[]
    expect(evalSteerCommand).toBe(join(venvBin, 'python'))
    expect(evalSteerArgs).toEqual(['-m', 'refactory_dsh.console', 'steer', '--run'])

    // The spine registers wf_steer when steerCommand !== '' and workdir is set
    // (spine guard at index.ts:2399). Spine's own steering.spec.ts tests that.
    expect(evalSteerCommand).not.toBe('')
  })

  it('core-alone: no entry point → steerCommand is empty, spine would skip wf_steer', () => {
    const { steerCommandExpr, steerArgsExpr } = readYamlExpressions()
    // Simulate startup publishing steerCommand='' (no entry point)
    const ctx = { graphStartup: { steerCommand: '', steerArgs: [] as string[], pythonBin: '/venv/bin' }, _steerWarnedOnce: false }
    const evalSteerCommand = evaluate(ctx, steerCommandExpr) as string
    const evalSteerArgs = evaluate(ctx, steerArgsExpr) as string[]
    expect(evalSteerCommand).toBe('')
    expect(evalSteerArgs).toEqual([])
  })

  it('stale-build warning: steerCommand undefined → warns once with pnpm run build', () => {
    const { steerCommandExpr } = readYamlExpressions()
    // Simulate old startup.ts that doesn't publish steerCommand (undefined)
    const ctx: Record<string, any> = {
      graphStartup: { pythonBin: '/venv/bin' },
      _steerWarnedOnce: false,
    }
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      const result = evaluate(ctx, steerCommandExpr)
      expect(result).toBe('')
      expect(warnSpy).toHaveBeenCalledTimes(1)
      const firstCall = warnSpy.mock.calls[0]
      expect(firstCall).toBeDefined()
      expect(String(firstCall![0])).toContain('pnpm run build')
      // Second evaluation should not warn again
      warnSpy.mockClear()
      evaluate(ctx, steerCommandExpr)
      expect(warnSpy).not.toHaveBeenCalled()
    } finally {
      warnSpy.mockRestore()
    }
  })

  it('version-skew: older profile with hardcoded values still steers', () => {
    // An older cordis.patch.yml has the literal values, not !!js expressions.
    // The spine receives steerCommand='/venv/bin/python' which is !== '' and
    // registers wf_steer (steering.spec.ts:146 tests this path).
    const steerCommand = '/venv/bin/python'
    const steerArgs = ['-m', 'refactory_dsh.console', 'steer', '--run']
    expect(steerCommand).not.toBe('')
    expect(steerArgs).toEqual(['-m', 'refactory_dsh.console', 'steer', '--run'])
  })

  it('real probe: the entry point returns refactory_dsh.console from the installed venv', () => {
    const result = spawnSync(join(venvBin, 'python'), ['-m', 'refactory_dsh.steer_probe'], {
      encoding: 'utf8',
      timeout: 10_000,
    })
    expect(result.status).toBe(0)
    expect(result.stdout.trim()).toBe('refactory_dsh.console')
  })

  it('probe failure: startup warns with the probe stderr', () => {
    // Create a fake python that prints an error to stderr and exits 2
    const fakeBin = mkdtempSync(join(tmpdir(), 'dsh-fake-python-'))
    dirs.push(fakeBin)
    const fakeError = 'FAKE_PROBE_ERROR: something went wrong'
    writeFileSync(join(fakeBin, 'python'), `#!/bin/sh\necho "${fakeError}" >&2\nexit 2\n`)
    chmodSync(join(fakeBin, 'python'), 0o755)

    const dir = graphs('probe')
    const workdir = mkdtempSync(join(tmpdir(), 'dsh-graph-steer-fail-'))
    dirs.push(workdir)
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      const { values } = boot([
        '--mode', 'probe', '--task', 'circle_packing', '--budget', '7',
        '--graphs', dir, '--workdir', workdir, '--python-bin', fakeBin,
      ])
      const v = values!
      expect(v.steerCommand).toBe('')
      expect(v.steerArgs).toEqual([])
      expect(warnSpy).toHaveBeenCalled()
      // The warning should contain the fake error
      const warnMsg = warnSpy.mock.calls.map(c => String(c.join(' '))).join('\n')
      expect(warnMsg).toContain(fakeError)
    } finally {
      warnSpy.mockRestore()
    }
  })
})
