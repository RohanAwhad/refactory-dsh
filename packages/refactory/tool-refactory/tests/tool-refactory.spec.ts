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
import { CONSOLE_DEFAULTS, Console } from '../src/console.ts'

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
      'modes', 'strategies', 'runs', 'start', 'run', 'status', 'artifacts', 'leaderboard',
      'candidate', 'steer', 'best', 'adopt', 'registry', 'create', 'edit', 'design', 'stop',
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

  it('maps the steering, winner and package actions onto the console verbs', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult('{}')
    await call(ctx, { action: 'strategies' })
    expect(subprocess.spawns[0]!.argv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'strategies',
    ])
    await call(ctx, { action: 'steer', run: 'r1', note: 'no numpy', kind: 'constraint' })
    expect(subprocess.spawns[1]!.argv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'steer',
      '--run', 'r1', '--note', 'no numpy', '--kind', 'constraint',
    ])
    await call(ctx, { action: 'adopt', run: 'r1', as: 'adopted_demo' })
    expect(subprocess.spawns[2]!.argv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'adopt',
      '--run', 'r1', '--as', 'adopted_demo',
    ])
    await call(ctx, { action: 'registry', packages: 'remove', target: 'phase_pack' })
    expect(subprocess.spawns[3]!.argv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'registry',
      'remove', 'phase_pack',
    ])
    await call(ctx, { action: 'create', name: 'new_pkg', brief: 'count up' })
    expect(subprocess.spawns[4]!.argv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'create',
      '--name', 'new_pkg', '--brief', 'count up',
    ])
  })

  it('refuses an adopt with no package name before spawning', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult('{}')
    expect(text(await call(ctx, { action: 'adopt', run: 'r1' }))).toContain('needs `as`')
    expect(subprocess.spawns).toHaveLength(0)
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

  it('presents through the registered definition, so the runtime renders the same view', async () => {
    const { ctx } = await setup()
    const definition = ctx.tools.get('refactory')
    expect(definition?.presentCall?.({ action: 'modes' })).toMatchObject({
      card: 'generic', title: 'refactory modes',
    })
    expect(definition?.presentResult?.({ action: 'modes' }, {
      content: [{ type: 'text', text: '0 mode(s)' }], isError: false,
    })).toMatchObject({ card: 'generic', title: 'refactory modes' })
  })
})

describe('invocationFor', () => {
  it('maps best, edit and design onto their console verbs', () => {
    expect(tool.invocationFor({ action: 'best', run: 'r1', state: true })).toMatchObject({
      verb: 'best', positional: [], options: { run: 'r1', state: true },
    })
    expect(tool.invocationFor({
      action: 'edit', mode: 'm/n', name: 'pkg', brief: 'faster', allowRegression: true, attempts: 2,
    })).toMatchObject({
      verb: 'edit',
      options: { mode: 'm/n', name: 'pkg', brief: 'faster', 'allow-regression': true, attempts: 2 },
    })
    expect(tool.invocationFor({
      action: 'design', name: 'pkg', brief: 'a thing', check: 'pytest', attempts: 3,
    })).toMatchObject({
      verb: 'design',
      options: { name: 'pkg', brief: 'a thing', check: 'pytest', attempts: 3 },
    })
  })

  it('defaults the registry verb to list, and refuses authoring calls missing an argument', () => {
    expect(tool.invocationFor({ action: 'registry' })).toMatchObject({
      verb: 'registry', positional: ['list', ''],
    })
    expect(() => tool.invocationFor({ action: 'create', name: 'p' })).toThrow(/needs `name` and `brief`/)
    expect(() => tool.invocationFor({ action: 'edit', name: 'p', brief: 'b' })).toThrow(/needs `mode`, `name` and `brief`/)
    expect(() => tool.invocationFor({ action: 'edit', mode: 'm/n', brief: 'b' })).toThrow(/needs `mode`, `name` and `brief`/)
    expect(() => tool.invocationFor({ action: 'design', brief: 'b', check: 'c' })).toThrow(/needs `name`, `brief` and `check`/)
    expect(() => tool.invocationFor({ action: 'design', name: 'p', check: 'c' })).toThrow(/needs `name`, `brief` and `check`/)
  })

  it('titles the pending call by whichever subject the action names', () => {
    expect(tool.presentCall({ action: 'modes' }).title).toBe('refactory modes')
    expect(tool.presentCall({ action: 'status', run: 'r1' }).title).toBe('refactory status r1')
    expect(tool.presentCall({ action: 'candidate', candidate: 'abc' }).title).toBe('refactory candidate abc')
  })

  it('renders a completed call whose result carries no payload', () => {
    expect(tool.presentResult({ action: 'modes' }, 'nonsense')).toMatchObject({
      card: 'generic', title: 'refactory modes', content: [{ type: 'text' }],
    })
  })
})

describe('renderResult', () => {
  function rendered(action: string, value: unknown): string {
    const blocks = tool.renderResult({ action } as Parameters<typeof tool.renderResult>[0], value)
    return blocks.map(block => block.type === 'text' ? block.text : '').join('')
  }

  it('renders modes, skipping entries that are not records', () => {
    expect(rendered('modes', { not: 'an array' })).toContain('0 mode(s)')
    expect(rendered('modes', [{ id: null, startable: true }, 'nope', { id: { nested: 1 } }]))
      .toContain('- {"nested":1}')
  })

  it('renders runs with the state each one is in', () => {
    expect(rendered('runs', { not: 'an array' })).toContain('0 run(s)')
    expect(rendered('runs', ['nope', { run_id: 'r1', mode: 'a/b' }])).toContain('in flight')
  })

  it('renders the strategy seams, skipping malformed groups and entries', () => {
    expect(rendered('strategies', 'nope')).toContain('0 outer loop(s)')
    expect(rendered('strategies', { outer_loops: 'nope' })).toContain('0 outer loop(s)')
    expect(rendered('strategies', { optimizers: ['nope', { name: 'autoresearch', description: 'searches' }] }))
      .toContain('- autoresearch: searches')
  })

  it('renders a status with no counters rather than undefined', () => {
    expect(rendered('status', { run_id: 'r1', state: 'running', counters: 'nope' }))
      .toContain('epoch 0, generation 0, 0 evaluation(s)')
  })

  it('renders a leaderboard, saying a row with no score scored nothing', () => {
    expect(rendered('leaderboard', 'nope')).toContain('0 candidate(s)')
    expect(rendered('leaderboard', ['nope', { id: 'x' }])).toContain('- x scored nothing')
  })

  it('names the candidate the payload omits as the call asked for, or nothing', () => {
    expect(rendered('candidate', {})).toContain('Candidate  against no parent:')
  })

  it('renders the intent ledger, its gate verdicts and its problems', () => {
    expect(rendered('steer', { exists: false, entries: 'nope' })).toContain('missing with 0 entries')
    const one = rendered('steer', {
      exists: true,
      entries: [{ kind: 'note', text: 'hi' }],
      verdicts: { approve: 'proceed' },
      problems: ['nothing'],
    })
    expect(one).toContain('with 1 entry')
    expect(one).toContain('- [note] hi')
    expect(one).toContain('Gate verdicts: approve=proceed.')
    expect(one).toContain('problem: nothing')
    // An entry that is not a record is counted, and skipped rather than printed.
    expect(rendered('steer', { entries: ['nope'] })).toContain('with 1 entry')
    expect(rendered('steer', { verdicts: 'nope' })).not.toContain('Gate verdicts')
  })

  it('renders the winner and the adoption', () => {
    expect(rendered('best', { candidate: 'x', score: 1.5 })).toContain('The winner is x, scoring 1.5.')
    expect(rendered('best', { candidate: 'x' })).toContain('The winner is x, scoring nothing.')
    expect(rendered('adopt', { mode: 'm/n', adopted_from: 'r1', package_root: '/p', score: 2 }))
      .toContain('The adopted mode scored 2')
    expect(rendered('adopt', { mode: 'm/n', adopted_from: 'r1', package_root: '/p' }))
      .toContain('scored nothing')
  })

  it('reports a forwarded authoring call by what it produced', () => {
    const succeeded = rendered('create', { ok: true, exit_code: 0, result: { output: 'wrote pkg' } })
    expect(succeeded).toContain('create succeeded (exit 0).')
    expect(succeeded).toContain('wrote pkg')
    expect(rendered('edit', { ok: false, exit_code: 1, result: 'nope' })).toContain('edit failed')
  })

  it('says nothing was stopped when the payload names no reason', () => {
    expect(rendered('stop', { stopped: false })).toContain('unknown')
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

  /** A console over a scripted subprocess, whose outcome each case states. */
  async function consoleOver(scripted: Scripted): Promise<{ console: Console; fake: FakeSubprocess }> {
    const ctx = new Context()
    await ctx.plugin(FakeSubprocess)
    const fake = ctx.subprocess as FakeSubprocess
    fake.handler = () => scripted
    const console = new Console(ctx.subprocess, {
      repoRoot: '/repo', pythonBin: '/repo/.venv/bin', timeoutMs: 1_000, collectBytes: 1_024,
    })
    return { console, fake }
  }

  it('decodes JSON when the caller passes no signal of its own', async () => {
    const { console } = await consoleOver(runResult('[1]'))
    await expect(console.run({ verb: 'modes' })).resolves.toMatchObject({ ok: true, value: [1] })
  })

  it('reports the console\'s stderr when it failed without a JSON reason', async () => {
    const { console } = await consoleOver(runResult('', 2, '  broken pipe\n'))
    await expect(console.run({ verb: 'modes' })).resolves.toMatchObject({ ok: false, error: 'broken pipe' })
  })

  it('names the signal when a killed console has no exit code', async () => {
    const killed: Scripted = {
      stdout: { text: '' }, stderr: { text: '' }, outcome: { exitCode: null, signal: 'SIGTERM' },
    }
    const { console } = await consoleOver(killed)
    await expect(console.run({ verb: 'modes' })).resolves.toMatchObject({
      ok: false, error: 'exited with SIGTERM',
    })
  })

  it('reports no status at all when neither exit code nor signal is known', async () => {
    const unknown: Scripted = {
      stdout: { text: '' }, stderr: { text: '' }, outcome: { exitCode: null, signal: null },
    }
    const { console } = await consoleOver(unknown)
    await expect(console.run({ verb: 'modes' })).resolves.toMatchObject({
      ok: false, error: 'exited with no status',
    })
  })

  it('falls back to stderr when a successful console printed no JSON', async () => {
    const { console } = await consoleOver(runResult('   ', 0, 'warning: nothing to say'))
    await expect(console.run({ verb: 'modes' })).resolves.toMatchObject({
      ok: false, error: 'the console printed no JSON: warning: nothing to say',
    })
  })
})


it('passes a creation\'s data-node and task directives through to the console', async () => {
  // create-v2's `--data-node` and `--task` are what make a dataset mode reachable
  // from the conversation; the console checks both after the run.
  const { ctx, subprocess } = await setup()
  subprocess.handler = () => runResult('{}')
  await call(ctx, { action: 'create', name: 'pkg', brief: 'count', dataNode: true, task: 'probe:Counting' })
  expect(subprocess.spawns[0]!.argv).toEqual([
    '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'create',
    '--name', 'pkg', '--brief', 'count', '--data-node', '--task', 'probe:Counting',
  ])
})

it('tells the model which action runs which kind of mode', async () => {
  // The description is the first thing the model reads; a caller that has to fail
  // `start` to learn that `create` is an authoring mode is a caller the tool could
  // have directed.
  const { ctx } = await setup()
  const definition = ctx.tools.get('refactory')
  expect(definition?.description).toContain('the action that runs it')
  expect(definition?.description).toContain('authoring mode by its own verb')
})

describe('authoring from the Web GUI', () => {
  it('forwards a person\'s gate answers to the authoring run', async () => {
    // Without a verdict a headless authoring run refuses `gate_strategy`, so every
    // creation from the GUI failed with exit 2 and no way to answer.
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult(JSON.stringify({ ok: true, exit_code: 0, result: {} }))
    await call(ctx, {
      action: 'create',
      name: 'p',
      brief: 'count',
      approve: ['gate_strategy=proceed', 'gate_qa=proceed'],
    })
    const argv = subprocess.spawns[0]!.argv
    expect(argv).toContain('--verdict')
    expect(argv.filter(part => part === '--verdict')).toHaveLength(2)
    expect(argv).toContain('gate_strategy=proceed')
    expect(argv).toContain('gate_qa=proceed')

    await call(ctx, { action: 'edit', mode: 'counting_pack/fixed_step', name: 'p', brief: 'x', approve: ['gate_strategy=proceed'] })
    expect(subprocess.spawns[1]!.argv).toContain('gate_strategy=proceed')

    await call(ctx, { action: 'design', name: 'a', brief: 'x', check: 'true', approve: ['gate_strategy=proceed'] })
    expect(subprocess.spawns[2]!.argv).toContain('gate_strategy=proceed')
  })

  it('shows the reason a failed authoring call gave, stderr included', async () => {
    // `create failed (exit 2)` with an empty payload was all the model or the user
    // saw: the entry point writes its refusal to stderr and the console dropped it.
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult(JSON.stringify({
      verb: 'create',
      exit_code: 2,
      ok: false,
      result: { error: 'the strategy gate is answered by a person' },
      stderr: '{"error": "the strategy gate is answered by a person"}',
    }))
    const rendered = text(await call(ctx, { action: 'create', name: 'p', brief: 'count' }))
    expect(rendered).toContain('the strategy gate is answered by a person')
  })

  it('reports an authoring run\'s own progress from status', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult(JSON.stringify({
      run_id: 'demo_pack',
      state: 'running',
      counters: { epoch: 0, generation: 0, evaluations: 0 },
      authoring: {
        spine: 'ready',
        completed: ['prepare', 'survey', 'research_director'],
        nodes_due: ['strategy_director'],
        name: 'demo_pack',
        validation: '(no validation has run yet)',
      },
    }))
    const rendered = text(await call(ctx, { action: 'status', run: '.factory/create/demo_pack' }))
    expect(rendered).toContain('demo_pack')
    expect(rendered).toContain('3 node(s) done')
    expect(rendered).toContain('strategy_director')
  })

  it('gives an authoring call long enough to finish', () => {
    // Measured, not guessed: a real creation on this checkout took 48 minutes (37
    // inner runs, one overwatch reloop), so the ceiling has to clear that with room
    // for one more reloop.
    expect(CONSOLE_DEFAULTS.timeoutMs).toBeGreaterThanOrEqual(90 * 60 * 1000)
  })
})

describe('the brief\'s declared inputs', () => {
  it('forwards each input the caller names to the creation', async () => {
    // The validate gate refuses a mode with no way to receive an input the brief
    // stated, so the directive has to survive the hop.
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult(JSON.stringify({ ok: true, exit_code: 0, result: {} }))
    await call(ctx, {
      action: 'create',
      name: 'buildroot_pack',
      brief: 'match a wheel against a package',
      approve: ['gate_strategy=proceed'],
      inputs: ['package', 'reference_wheel'],
    })
    const argv = subprocess.spawns[0]!.argv
    expect(argv.filter(part => part === '--input')).toHaveLength(2)
    expect(argv).toContain('package')
    expect(argv).toContain('reference_wheel')
  })
})

describe('running a mode once from the panel', () => {
  it('forwards a single run to the console\'s run verb, with the gate answers', async () => {
    // `start` is the optimizer; a person who has just created a mode needs to *use*
    // it, and the console had no verb for that at all.
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult(JSON.stringify({ ok: true, exit_code: 0, result: {} }))
    await call(ctx, {
      action: 'run',
      mode: 'buildroot_pack/build',
      intent: 'discover the recipe',
      approve: ['approve=proceed'],
      model: 'deepseek-v4-pro',
    })
    const argv = subprocess.spawns[0]!.argv
    expect(argv.slice(0, 4)).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'run',
    ])
    expect(argv).toContain('buildroot_pack/build')
    expect(argv[argv.indexOf('--intent') + 1]).toBe('discover the recipe')
    expect(argv[argv.indexOf('--verdict') + 1]).toBe('approve=proceed')
    expect(argv[argv.indexOf('--model') + 1]).toBe('deepseek-v4-pro')
  })

  it('reports where a single run left its artifacts and what it recorded', async () => {
    const { ctx, subprocess } = await setup()
    subprocess.handler = () => runResult(JSON.stringify({
      ok: true,
      exit_code: 0,
      result: { output: '' },
      run: {
        run_id: 'buildroot_pack-build',
        state: 'finished',
        workdir: '/repo/.factory/interactive/buildroot_pack-build',
        facts: { name: 'buildroot_pack', closeness: 1.0 },
      },
    }))
    const rendered = text(await call(ctx, { action: 'run', mode: 'buildroot_pack/build' }))
    expect(rendered).toContain('Ran buildroot_pack/build')
    expect(rendered).toContain('/repo/.factory/interactive/buildroot_pack-build')
    expect(rendered).toContain('finished')
    expect(rendered).toContain('closeness: 1')
  })

  it('needs a mode to run', async () => {
    const { ctx } = await setup()
    const failed = await call(ctx, { action: 'run' })
    expect(failed.isError).toBe(true)
    expect(text(failed)).toContain('action "run" needs `mode`')
  })
})
