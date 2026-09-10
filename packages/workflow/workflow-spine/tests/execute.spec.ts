import { spawnSync } from 'node:child_process'
import { describe, expect, it } from 'vitest'
import type { ShellExecRequest, ShellExecSpec, ShellExecutor, ShellRunResult } from '@deepseek-ai/dsh-shell'
import { commandOf, isDeterministic, NODE_ENV_KEY, runDeterministic, verdictOf } from '../src/execute.ts'
import type { CommandRunnerConfig, NodeManifest } from '../src/execute.ts'
import type { NodeView } from '../src/spine.ts'

/**
 * A shell executor that really runs the command, so these tests exercise the
 * process boundary rather than a stub's opinion of it.
 */
const localShell = {
  resolve(request: ShellExecRequest): ShellExecSpec {
    return {
      command: request.command,
      workdir: request.workdir ?? process.cwd(),
      timeoutMs: request.timeoutMs ?? 10_000,
      stdoutMaxBytes: 1_000_000,
      env: request.env,
      sandboxPolicy: undefined,
    }
  },
  async run(spec: ShellExecSpec): Promise<ShellRunResult> {
    const started = spawnSync('/bin/sh', ['-c', spec.command], {
      cwd: spec.workdir,
      encoding: 'utf8',
      timeout: spec.timeoutMs,
      env: { ...process.env, ...spec.env },
    })
    return {
      exitCode: started.status,
      signal: started.signal,
      timedOut: started.error !== undefined && 'code' in started.error && started.error.code === 'ETIMEDOUT',
      aborted: false,
      timeoutMs: spec.timeoutMs,
      stdout: { text: started.stdout ?? '', truncated: false },
      stderr: { text: started.stderr ?? '', truncated: false },
    }
  },
} as unknown as ShellExecutor

const config: CommandRunnerConfig = { env: {}, timeoutMs: 10_000 }

function fn(id: string, command?: string): NodeView {
  return command === undefined
    ? { id, _type: 'FnNode', reads: [], writes: [] }
    : { id, _type: 'FnNode', command, reads: ['in.txt'], writes: ['out.txt'] }
}

function gate(id: string, evaluatorCommand?: string): NodeView {
  const view: NodeView = { id, _type: 'GateNode', evaluator_type: 'fn', reads: [] }
  if (evaluatorCommand !== undefined) view.evaluator_command = evaluatorCommand
  return view
}

describe('which nodes the plugin runs itself', () => {
  it('claims an FnNode with a command and an fn gate with an evaluator', () => {
    expect(isDeterministic(fn('a', 'true'))).toBe(true)
    expect(isDeterministic(gate('g', 'echo proceed'))).toBe(true)
  })

  it('leaves nodes a model owns to the driver', () => {
    expect(isDeterministic(fn('a'))).toBe(false)
    expect(isDeterministic({ id: 'l', _type: 'LLMNode' })).toBe(false)
    expect(isDeterministic({ id: 'a', _type: 'AgentNode', prompt_template: 'x' })).toBe(false)
    expect(isDeterministic({ id: 'g', _type: 'GateNode', evaluator_type: 'user' })).toBe(false)
    expect(isDeterministic(gate('g'))).toBe(false)
  })

  it('reports the command it would run', () => {
    expect(commandOf(fn('a', 'echo hi'))).toBe('echo hi')
    expect(commandOf(gate('g', 'echo proceed'))).toBe('echo proceed')
    expect(commandOf(fn('a'))).toBeUndefined()
  })
})

describe('running a deterministic node', () => {
  it('runs an FnNode command and reports success', async () => {
    const outcome = await runDeterministic(localShell, fn('a', 'printf done'), config)
    expect(outcome.ok).toBe(true)
    expect(outcome.exitCode).toBe(0)
    expect(outcome.stdout).toBe('done')
  })

  it('reports a nonzero exit instead of throwing', async () => {
    const outcome = await runDeterministic(localShell, fn('a', 'echo boom >&2; exit 3'), config)
    expect(outcome.ok).toBe(false)
    expect(outcome.exitCode).toBe(3)
    expect(outcome.error).toContain('exited with code 3')
    expect(outcome.stderr).toBe('boom')
  })

  it('runs in the configured work directory', async () => {
    const outcome = await runDeterministic(localShell, fn('a', 'pwd'), { ...config, workdir: '/tmp' })
    expect(outcome.ok).toBe(true)
    expect(outcome.stdout.endsWith('/tmp')).toBe(true)
  })

  it('passes the configured environment through', async () => {
    const outcome = await runDeterministic(localShell, fn('a', 'printf "$SRF_TASK"'), {
      ...config,
      env: { SRF_TASK: 'circle_packing' },
    })
    expect(outcome.stdout).toBe('circle_packing')
  })

  it('hands the node its own declaration, so the command knows what it reads and writes', async () => {
    const outcome = await runDeterministic(localShell, fn('build_prompt_0', `printf '%s' "$${NODE_ENV_KEY}"`), config)
    expect(outcome.ok).toBe(true)
    const manifest = JSON.parse(outcome.stdout) as NodeManifest
    expect(manifest).toEqual({
      id: 'build_prompt_0',
      type: 'FnNode',
      command: 'printf \'%s\' "$DSH_WORKFLOW_NODE"',
      reads: ['in.txt'],
      writes: ['out.txt'],
    })
  })

  it('fails the node when its command produces no verdict', async () => {
    const outcome = await runDeterministic(localShell, gate('g', 'true'), config)
    expect(outcome.ok).toBe(false)
    expect(outcome.error).toContain('printed no verdict')
  })
})

describe('reading a gate verdict', () => {
  it('takes the last non-empty stdout line, lowercased', () => {
    expect(verdictOf('noise\nPROCEED\n')).toBe('proceed')
    expect(verdictOf('  RELOOP  \n\n')).toBe('reloop')
    expect(verdictOf('DRAFT')).toBe('draft')
  })

  it('is undefined for empty output', () => {
    expect(verdictOf('')).toBeUndefined()
    expect(verdictOf('\n \n')).toBeUndefined()
  })

  it('routes a gate by its evaluator output', async () => {
    const outcome = await runDeterministic(localShell, gate('action_gate', 'echo DRAFT'), config)
    expect(outcome.ok).toBe(true)
    expect(outcome.verdict).toBe('draft')
  })

  it('ignores evaluator log lines before the verdict', async () => {
    const outcome = await runDeterministic(localShell, gate('g', 'echo "log line"; echo RELOOP'), config)
    expect(outcome.verdict).toBe('reloop')
  })
})
