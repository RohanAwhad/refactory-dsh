import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import { ToolCallId } from '@deepseek-ai/dsh-llm'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import { SubprocessRuntime } from '@deepseek-ai/dsh-subprocess'
import type {
  SubprocessCollectedOutputs,
  SubprocessHandle,
  SubprocessOutcome,
  SubprocessOutputRead,
  SubprocessSpawnSpec,
} from '@deepseek-ai/dsh-subprocess'

import * as tool from '../src/index.ts'
import { Console } from '../src/console.ts'

/**
 * Drives the real plugin body over a scriptable subprocess service: the tool
 * registration, its argument validation, and the exact argv it hands the
 * console. The console is not run here — `refactory/tests/test_gui_loop.py`
 * covers it against the real loop, and this covers the boundary it is called
 * across.
 */

function runResult(stdout: string, exitCode = 0, stderr = ''): {
  stdout: { text: string }
  stderr: { text: string }
  outcome: SubprocessOutcome
} {
  return { stdout: { text: stdout }, stderr: { text: stderr }, outcome: { exitCode, signal: null } }
}

type Scripted = ReturnType<typeof runResult>

class FakeReader {
  constructor(private readonly read: { text: string }) {}
  readFrom(_fromByte: number): SubprocessOutputRead {
    return { text: this.read.text, nextOffset: this.read.text.length, lossy: false }
  }
}

class FakeHandle implements SubprocessHandle {
  readonly stdin = undefined
  readonly stdout = undefined
  readonly stderr = undefined
  readonly collected: SubprocessCollectedOutputs
  readonly done: Promise<SubprocessOutcome>
  constructor(spec: SubprocessSpawnSpec, scripted: Scripted, dropReaders = false) {
    spec.signal?.addEventListener('abort', () => { this.terminated = true }, { once: true })
    this.collected = dropReaders ? {} : {
      stdout: new FakeReader(scripted.stdout),
      stderr: new FakeReader(scripted.stderr),
    }
    this.done = Promise.resolve(scripted.outcome)
  }
  terminated = false
  terminate(): void { this.terminated = true }
  waitForExit(): Promise<boolean> { return Promise.resolve(true) }
}

class FakeSubprocess extends SubprocessRuntime {
  spawns: SubprocessSpawnSpec[] = []
  handler: () => Scripted = () => runResult('{}')
  dropReaders = false
  override resolveExecutable(command: string): Promise<string> { return Promise.resolve(command) }
  override spawnTerminal(): Promise<never> { throw new Error('the refactory tool spawns collectors, never terminals') }
  override spawn(spec: SubprocessSpawnSpec): SubprocessHandle {
    this.spawns.push(spec)
    return new FakeHandle(spec, this.handler(), this.dropReaders)
  }
}

const CONFIG = {
  toolName: 'refactory',
  repoRoot: '/repo',
  pythonBin: '/repo/.venv/bin',
  timeoutMs: 60_000,
  collectBytes: 1_048_576,
}

async function setup(config: Partial<tool.Config> = {}) {
  const ctx = new Context()
  await ctx.plugin(SystemPrompt)
  await ctx.plugin(ToolRuntime)
  await ctx.plugin(FakeSubprocess)
  const subprocess = ctx.subprocess as FakeSubprocess
  await ctx.plugin(tool, { ...CONFIG, ...config })
  return { ctx, subprocess }
}

let counter = 0
function call(ctx: Context, args: unknown) {
  return ctx.tools.execute({
    signal: new AbortController().signal,
    callId: ToolCallId(`call-${++counter}`),
    name: 'refactory',
    arguments: args,
  })
}

function text(result: { content: { type: string; text?: string }[] }): string {
  return result.content.filter(block => block.type === 'text').map(block => block.text).join('')
}

/** The assembled system prompt, which is where a registered section lands. */
async function promptText(ctx: Context): Promise<string> {
  const assembly = await ctx.systemPrompt.assemble()
  return assembly.sections.map(section => typeof section.text === 'string' ? section.text : '').join('\n')
}

describe('dsh-tool-refactory', () => {
  it('registers one `refactory` tool with an action enum', async () => {
    const { ctx } = await setup()
    const schema = ctx.tools.schemas().find(entry => entry.name === 'refactory')
    expect(schema).toBeDefined()
    const action = (schema!.parameters as { properties: Record<string, { enum?: string[] }> }).properties.action
    expect(action?.enum).toEqual([
      'modes', 'runs', 'start', 'status', 'artifacts', 'leaderboard', 'candidate', 'stop',
    ])
  })

  it('runs the console through the subprocess capability with the configured interpreter', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult(JSON.stringify([{ id: 'circle_packing/adaptive', startable: true }]))
    const result = await call(ctx, { action: 'modes' })
    expect(subprocess.spawns).toHaveLength(1)
    expect(subprocess.spawns[0]!.argv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'modes',
    ])
    expect(subprocess.spawns[0]!.cwd).toBe('/repo')
    expect(text(result)).toContain('circle_packing/adaptive')
  })

  it('defaults the interpreter against the repo root rather than the process cwd', async () => {
    const { ctx, subprocess } = await setup({ pythonBin: '', repoRoot: '/elsewhere' })
    subprocess.handler = () => runResult('[]')
    await call(ctx, { action: 'modes' })
    expect(subprocess.spawns[0]!.argv[0]).toBe('/elsewhere/.venv/bin/python')
  })

  it('passes each action through as console flags and positionals', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult('{}')
    await call(ctx, {
      action: 'start',
      mode: 'circle_packing/adaptive',
      runId: 'r1',
      runsRoot: '/runs',
      settings: { epochs: 2, batch_size: 3, seed: 7 },
    })
    expect(subprocess.spawns[0]!.argv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'start',
      '--run-id', 'r1', '--runs-root', '/runs',
      '--epochs', '2', '--batch-size', '3', '--seed', '7',
      'circle_packing/adaptive',
    ])
  })

  it('spells snake_case settings as the console\'s kebab-case flags', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult('{}')
    await call(ctx, { action: 'start', mode: 'm/n', settings: { max_evaluations: 4 } })
    expect(subprocess.spawns[0]!.argv).toContain('--max-evaluations')
  })

  it('names the candidate as a positional after the run scope', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult('{}')
    await call(ctx, { action: 'candidate', run: 'r1', candidate: 'abc' })
    expect(subprocess.spawns[0]!.argv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'candidate',
      '--run', 'r1', 'abc',
    ])
  })

  it('passes a false boolean as no flag and a true one as a bare flag', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult('{}')
    await call(ctx, { action: 'artifacts', run: 'r1', contents: false })
    expect(subprocess.spawns[0]!.argv).not.toContain('--contents')
    await call(ctx, { action: 'artifacts', run: 'r1', contents: true })
    expect(subprocess.spawns[1]!.argv).toContain('--contents')
  })

  it('refuses an action missing the thing that addresses it, before spawning', async () => {
    const { ctx, subprocess } = await setup()
    expect(text(await call(ctx, { action: 'status' }))).toContain('needs `run`')
    expect(text(await call(ctx, { action: 'start' }))).toContain('needs `mode`')
    expect(text(await call(ctx, { action: 'candidate', run: 'r1' }))).toContain('needs `candidate`')
    expect(subprocess.spawns).toHaveLength(0)
  })

  it('surfaces the console\'s own refusal as a tool error', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult(JSON.stringify({ error: '/nope is not a run directory' }), 2)
    const result = await call(ctx, { action: 'status', run: '/nope' })
    expect(result.isError).toBe(true)
    expect(text(result)).toContain('/nope is not a run directory')
  })

  it('reports a console that printed no JSON instead of returning nothing', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult('not json')
    expect(text(await call(ctx, { action: 'modes' }))).toContain('printed no JSON')
  })

  it('reports a nonzero exit with no JSON and no stderr as a status', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult('', 3)
    expect(text(await call(ctx, { action: 'modes' }))).toContain('exited with 3')
  })

  it('reports a provider that returned no collected readers', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.dropReaders = true
    expect(text(await call(ctx, { action: 'modes' }))).toContain('no collected output')
  })

  it('returns the console\'s JSON beside the action that produced it', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult(JSON.stringify({ run_id: 'r1', state: 'running', counters: { epoch: 2 } }))
    const result = await call(ctx, { action: 'status', run: 'r1' })
    expect(result.isError).toBeFalsy()
    expect(text(result)).toContain('is running')
    expect(text(result)).toContain('epoch 2')
  })

  it('summarizes every action without printing the whole payload', async () => {
    const payloads: Record<string, unknown> = {
      modes: [{ id: 'a/b', startable: true }, { id: 'c/d', startable: false }],
      runs: [{ run_id: 'r1', mode: 'a/b', best_score: 1.5, finished: true }],
      start: { run_id: 'r1', mode: 'a/b' },
      status: { run_id: 'r1', state: 'finished', best_score: 1.5, counters: { epoch: 1, generation: 2, evaluations: 3 } },
      artifacts: { artifacts: [{ name: 'report', media_type: 'text/markdown', exists: true }] },
      leaderboard: [{ id: 'x', score: 1.5 }],
      candidate: { candidate: 'x', parent: 'y', rendered: 'Parameters:\n- grid: 18 → 24' },
      stop: { stopped: true },
    }
    const { ctx, subprocess } = await setup()
    for (const [action, payload] of Object.entries(payloads)) {
      subprocess.handler = () => runResult(JSON.stringify(payload))
      const args = { action, run: 'r1', mode: 'a/b', candidate: 'x' }
      const summary = text(await call(ctx, args))
      expect(summary.length, action).toBeGreaterThan(0)
      // The summary is prose about the payload; the payload itself travels
      // structured, for the card, and must not also be dumped into the text.
      expect(summary, action).not.toContain('{"')
    }
  })

  it('says a run was already gone rather than claiming it stopped', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult(JSON.stringify({ stopped: false, reason: 'the run is not running' }))
    expect(text(await call(ctx, { action: 'stop', run: 'r1' }))).toContain('the run is not running')
  })

  it('presents the call and the result under the tool tile', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult(JSON.stringify([{ id: 'a/b', startable: true }]))
    const result = await call(ctx, { action: 'modes' })
    expect(tool.presentCall({ action: 'start', mode: 'a/b' })).toMatchObject({
      card: 'generic', title: 'refactory start a/b',
    })
    expect(tool.presentResult({ action: 'modes' }, { action: 'modes', data: result.content })).toMatchObject({
      card: 'generic', title: 'refactory modes',
    })
  })

  it('projects the console payload for the browser card', () => {
    const meta = tool.presentationMeta({ action: 'status', data: { run_id: 'r1', state: 'running' } })
    expect(meta).toEqual({ action: 'status', data: { run_id: 'r1', state: 'running' } })
  })

  it('registers a prompt section telling the model a run outlives the call', async () => {
    const { ctx } = await setup()
    expect(await promptText(ctx)).toContain('continues after the call returns')
  })

  it('honours a renamed tool', async () => {
    const { ctx } = await setup({ toolName: 'loop' })
    expect(ctx.tools.schemas().map(entry => entry.name)).toContain('loop')
    expect(await promptText(ctx)).toContain('loop tool to run and read')
  })
})

describe('Console', () => {
  it('builds argv without a shell, so a mode id is never interpreted', async () => {
    const ctx = new Context()
    await ctx.plugin(FakeSubprocess)
    const console = new Console(ctx.subprocess, {
      repoRoot: '/repo', pythonBin: '/repo/.venv/bin', timeoutMs: 1_000, collectBytes: 1_024,
    })
    expect(console.argv({ verb: 'status', positional: [], options: { run: 'a b; rm -rf /' } })).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'status', '--run', 'a b; rm -rf /',
    ])
  })
})
