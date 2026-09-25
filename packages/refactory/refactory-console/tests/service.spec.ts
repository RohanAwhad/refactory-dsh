/**
 * The service against a scriptable console process.
 *
 * The service's own job is the two picker rules — every choice described, and a
 * refusal that names what to do instead — plus the argv it hands the console.
 * The console is not run here: `service-live.spec.ts` runs it against the real
 * checkout, and this covers the boundary the service is called across.
 */
import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import { SubprocessRuntime } from '@deepseek-ai/dsh-subprocess'
import type {
  SubprocessCollectedOutputs,
  SubprocessHandle,
  SubprocessOutcome,
  SubprocessOutputRead,
  SubprocessSpawnSpec,
} from '@deepseek-ai/dsh-subprocess'
import { remoteErrorOf } from '@deepseek-ai/dsh-typert-protocol'
import { RefactoryConsole } from '../src/index.ts'
import type { Config } from '../src/index.ts'

/** What one spawned console call answers with. */
interface Scripted {
  readonly stdout: string
  readonly exitCode?: number
  readonly stderr?: string
}

function runResult(scripted: Scripted): {
  stdout: { text: string }
  stderr: { text: string }
  outcome: SubprocessOutcome
} {
  return {
    stdout: { text: scripted.stdout },
    stderr: { text: scripted.stderr ?? '' },
    outcome: { exitCode: scripted.exitCode ?? 0, signal: null },
  }
}

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
  terminated = false
  constructor(spec: SubprocessSpawnSpec, scripted: Scripted) {
    spec.signal?.addEventListener('abort', () => { this.terminated = true }, { once: true })
    const result = runResult(scripted)
    this.collected = {
      stdout: new FakeReader(result.stdout),
      stderr: new FakeReader(result.stderr),
    }
    this.done = Promise.resolve(result.outcome)
  }
  terminate(): void { this.terminated = true }
  waitForExit(): Promise<boolean> { return Promise.resolve(true) }
}

class FakeSubprocess extends SubprocessRuntime {
  spawns: SubprocessSpawnSpec[] = []
  /** One answer per call, in order; the last one repeats. */
  script: Scripted[] = [{ stdout: '{}' }]
  override resolveExecutable(command: string): Promise<string> { return Promise.resolve(command) }
  override spawnTerminal(): Promise<never> { throw new Error('the console service spawns collectors, never terminals') }
  override spawn(spec: SubprocessSpawnSpec): SubprocessHandle {
    this.spawns.push(spec)
    const scripted = this.script[Math.min(this.spawns.length - 1, this.script.length - 1)]!
    return new FakeHandle(spec, scripted)
  }
  /** Answer every call with the same JSON text. */
  answer(value: unknown, exitCode = 0): void {
    this.script = [{ stdout: JSON.stringify(value), exitCode }]
  }
  /** The argv of the last call, interpreter included. */
  get lastArgv(): readonly string[] {
    return this.spawns.at(-1)?.argv ?? []
  }
}

const CONFIG: Config = {
  repoRoot: '/repo',
  pythonBin: '/repo/.venv/bin',
  timeoutMs: 60_000,
  collectBytes: 1_048_576,
}

async function setup(): Promise<{ service: RefactoryConsole; subprocess: FakeSubprocess }> {
  const ctx = new Context()
  await ctx.plugin(FakeSubprocess)
  const subprocess = ctx.subprocess as FakeSubprocess
  return { service: new RefactoryConsole(ctx, CONFIG), subprocess }
}

const signal = (): AbortSignal => new AbortController().signal

/** One mode as the console's catalogue describes it, with only the read fields. */
function mode(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'default_pack/create',
    package: 'default_pack',
    mode: 'create',
    description: 'create a package of modes',
    startable: true,
    enabled: true,
    scored: false,
    action: 'create',
    trust: 'trusted',
    ...overrides,
  }
}

/** The knob declarations one catalogue entry carries, as the wire writes them. */
const PARAMETERS = {
  rounds: { value: 3, kind: 'int', values: [], range: [1, 10], frozen: false, description: 'how many rounds' },
  temperature: { value: 0.5, kind: 'float', values: [], range: [null, null], frozen: false, description: '' },
  source_root: { value: '/tmp/src', kind: 'str', values: [], range: [null, null], frozen: true, description: 'the tree to patch' },
  preset: { value: 'broad', kind: 'str', values: ['broad', 'narrow'], range: [null, null], frozen: false, description: '' },
  target: { value: 7, kind: 'enum', values: [7, 8], range: [null, null], frozen: false, description: 'which target' },
  verbose: { value: false, kind: 'bool', values: [], range: [null, null], frozen: false, description: '' },
  // A declaration that states neither a set nor bounds: the wire omits both.
  bare: { value: 'x', kind: 'str', frozen: false, description: '' },
}

/** Those declarations as the picker reads them: every value as its text. */
const READ_PARAMETERS = {
  rounds: { value: '3', kind: 'int', values: [], range: [1, 10], frozen: false, description: 'how many rounds' },
  temperature: { value: '0.5', kind: 'float', values: [], range: [null, null], frozen: false, description: '' },
  source_root: { value: '/tmp/src', kind: 'str', values: [], range: [null, null], frozen: true, description: 'the tree to patch' },
  preset: { value: 'broad', kind: 'str', values: ['broad', 'narrow'], range: [null, null], frozen: false, description: '' },
  target: { value: '7', kind: 'enum', values: ['7', '8'], range: [null, null], frozen: false, description: 'which target' },
  verbose: { value: 'false', kind: 'bool', values: [], range: [null, null], frozen: false, description: '' },
  bare: { value: 'x', kind: 'str', values: [], range: [null, null], frozen: false, description: '' },
}

/** One run as the console's `runs` verb lists it. */
const RUN = { run_id: 'live', root: '/repo/.factory/loop/live', mode: 'p/adaptive', best_score: 1.5, finished: true }

/** One status frame, as the console answers it. */
const FRAME = {
  run_id: 'live',
  root: '/repo/.factory/loop/live',
  mode: 'p/adaptive',
  state: 'running',
  events: [{ epoch: 0, generation: 1, evaluations: 3, score: 1.25, extra: 'kept on the wire' }],
  cursor: 4,
  counters: { epoch: 0, generation: 1, evaluations: 3, steps: 1 },
  best_score: 1.25,
  finished: false,
  running: true,
}

/** The failure a Remote error carries, or a defect when it is not one. */
async function failureOf(operation: Promise<unknown>): Promise<{ code: string; details: unknown; message: string }> {
  try {
    await operation
  } catch (error: unknown) {
    const failure = remoteErrorOf(error)
    if (failure === undefined) throw error
    return { code: failure.code, details: failure.details, message: failure.message }
  }
  throw new Error('expected the operation to fail')
}

describe('refactoryConsole — the described choices', () => {
  it('maps the mode catalogue to the choices the picker offers, knobs included', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer([
      mode({ parameters: PARAMETERS }),
      // A mode whose package fails to import names no trust and declares nothing.
      mode({ id: 'p/gone', reason: 'no module named gone', trust: undefined }),
    ])
    expect(await service.modes(signal())).toEqual([
      {
        id: 'default_pack/create',
        package: 'default_pack',
        mode: 'create',
        description: 'create a package of modes',
        startable: true,
        enabled: true,
        scored: false,
        action: 'create',
        trust: 'trusted',
        parameters: READ_PARAMETERS,
      },
      {
        id: 'p/gone',
        package: 'default_pack',
        mode: 'create',
        description: 'create a package of modes',
        startable: true,
        enabled: true,
        scored: false,
        action: 'create',
        parameters: {},
        reason: 'no module named gone',
      },
    ])
    expect(subprocess.lastArgv).toEqual(['/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'modes'])
  })

  it('refuses a knob kind this picker cannot draw instead of rendering a wrong control', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer([mode({ parameters: { matrix: { value: 'x', kind: 'tensor', values: [], range: [] } } })])
    await expect(service.modes(signal())).rejects.toThrow('declares kind "tensor"')
    subprocess.answer([mode({ parameters: { rounds: 'not an object' } })])
    await expect(service.modes(signal())).rejects.toThrow('knob "rounds" is not an object')
  })

  it('maps the three level-two registries', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer({
      outer_loops: [{ name: 'beam', description: 'keep the best few' }],
      optimizers: [{ name: 'random', description: 'sample uniformly' }],
      evaluators: [{ name: 'score', description: 'the mode\'s own score' }],
    })
    expect(await service.strategies(signal())).toEqual({
      outer_loops: [{ name: 'beam', description: 'keep the best few' }],
      optimizers: [{ name: 'random', description: 'sample uniformly' }],
      evaluators: [{ name: 'score', description: 'the mode\'s own score' }],
    })
    expect(subprocess.lastArgv).toEqual(['/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'strategies'])
  })

  it('maps the run list, naming an absent score rather than inventing one', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer([RUN, { run_id: 'fresh', root: '/repo/.factory/loop/fresh', finished: false }])
    expect(await service.runs(signal())).toEqual([
      { run_id: 'live', root: '/repo/.factory/loop/live', mode: 'p/adaptive', best_score: 1.5, finished: true },
      { run_id: 'fresh', root: '/repo/.factory/loop/fresh', mode: null, best_score: null, finished: false },
    ])
  })

  it('lists packages from the registry catalogue, sorted, with the state each mode carries', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer({
      verb: 'registry',
      exit_code: 0,
      ok: true,
      result: {
        zeta: [{ name: 'z', description: 'the last one', package_enabled: false, trust: 'generated', where: '/repo/.factory/packages/zeta' }],
        alpha: [{ name: 'a', description: 'the first one', package_enabled: true, trust: 'trusted', where: '/repo/packages/alpha' }],
        empty: [],
      },
    })
    expect(await service.packages(signal())).toEqual([
      {
        name: 'alpha',
        enabled: true,
        where: '/repo/packages/alpha',
        modes: [{ name: 'a', description: 'the first one', trust: 'trusted' }],
      },
      // A package that declares no modes carries no state: the registry reports it
      // on each mode, so there is nothing to report rather than a guess.
      { name: 'empty', enabled: null, where: '', modes: [] },
      {
        name: 'zeta',
        enabled: false,
        where: '/repo/.factory/packages/zeta',
        modes: [{ name: 'z', description: 'the last one', trust: 'generated' }],
      },
    ])
    expect(subprocess.lastArgv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'registry', '--args', '', 'list', '',
    ])
  })
})

describe('refactoryConsole — starting a described mode', () => {
  it('starts a mode whose action is start, passing the chosen settings and knobs through', async () => {
    const { service, subprocess } = await setup()
    subprocess.script = [
      { stdout: JSON.stringify([mode({ id: 'p/adaptive', mode: 'adaptive', scored: true, action: 'start' })]) },
      { stdout: JSON.stringify({ run_id: 'live', mode: 'p/adaptive', root: '/runs/live', started: 1.5, config: { epochs: '1' }, log: 'live.log', pid: 7 }) },
    ]
    await expect(service.start(
      {
        mode: 'p/adaptive',
        runId: 'live',
        runsRoot: '/runs',
        settings: { optimizer: 'random', epochs: 1 },
        knobs: ['source_root=/tmp/pkg', 'rounds=2'],
      },
      signal(),
    )).resolves.toEqual({
      run_id: 'live',
      mode: 'p/adaptive',
      root: '/runs/live',
      started: 1.5,
      config: { epochs: '1' },
      log: 'live.log',
      pid: 7,
    })
    expect(subprocess.lastArgv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'start',
      '--run-id', 'live', '--runs-root', '/runs', '--knob', 'source_root=/tmp/pkg', '--knob', 'rounds=2',
      '--optimizer', 'random', '--epochs', '1', 'p/adaptive',
    ])
  })

  it('refuses a mode the catalogue does not offer, naming the list to choose from', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer([mode()])
    const failure = await failureOf(service.start({ mode: 'p/nope' }, signal()))
    expect(failure.code).toBe('refactory/unknown-mode')
    expect(failure.message).toContain('p/nope')
    expect(failure.details).toEqual({ mode: 'p/nope' })
    // Only the catalogue was read: no run was started.
    expect(subprocess.spawns).toHaveLength(1)
  })

  it('refuses an authoring mode with the verb that runs it', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer([mode({ id: 'default_pack/create', action: 'create', scored: false })])
    const failure = await failureOf(service.start({ mode: 'default_pack/create' }, signal()))
    expect(failure.code).toBe('refactory/authoring-mode')
    expect(failure.message).toContain('"create" action')
    expect(failure.details).toEqual({ mode: 'default_pack/create', action: 'create' })
    expect(subprocess.spawns).toHaveLength(1)
  })

  it('refuses a mode whose package is disabled, naming the package to enable', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer([mode({ id: 'p/adaptive', action: 'start', scored: true, enabled: false, startable: false })])
    const failure = await failureOf(service.start({ mode: 'p/adaptive' }, signal()))
    expect(failure.code).toBe('refactory/package-disabled')
    expect(failure.message).toContain('"default_pack"')
    expect(failure.details).toEqual({ mode: 'p/adaptive', package: 'default_pack' })
  })

  it('refuses an empty mode before reading anything', async () => {
    const { service, subprocess } = await setup()
    const failure = await failureOf(service.start({ mode: '   ' }, signal()))
    expect(failure.code).toBe('gateway/bad-request')
    expect(subprocess.spawns).toHaveLength(0)
  })
})

/** One mode used once, as the console's `run` payload reports it. */
const SINGLE = {
  run_id: 'p-adaptive-20260101-000000',
  root: '/repo/.factory/interactive/p-adaptive',
  mode: 'p/adaptive',
  state: 'finished',
  events: [],
  cursor: 0,
  counters: { epoch: 0, generation: 0, evaluations: 0, steps: 0 },
  best_score: 0.75,
  finished: true,
  running: false,
  authoring: {
    spine: 'done', completed: ['prepare'], nodes_due: [], error: null,
    name: 'demo', validation: '(no validation has run yet)', package_path: '/packages/demo', files: ['mode.py'],
  },
  facts: { name: 'demo', validation: 'ok', files: ['mode.py'], empty: '', nothing: null, count: 2, nested: { a: 1 } },
}

/** The console's `run` payload: a forwarded interactive result with the run beside it. */
function singleRun(run: unknown, overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({ verb: 'interactive', exit_code: 0, ok: true, result: {}, stderr: '', run, ...overrides })
}

describe('refactoryConsole — using one mode once', () => {
  const ADAPTIVE = mode({ id: 'p/adaptive', mode: 'adaptive', scored: true, action: 'start' })

  it('uses the mode with the knobs it was given, and reports where the run got to', async () => {
    const { service, subprocess } = await setup()
    subprocess.script = [
      { stdout: JSON.stringify([ADAPTIVE]) },
      { stdout: singleRun(SINGLE) },
    ]
    await expect(service.run(
      { mode: 'p/adaptive', knobs: ['source_root=/tmp/pkg', 'rounds=2'] },
      signal(),
    )).resolves.toEqual({
      run_id: 'p-adaptive-20260101-000000',
      root: '/repo/.factory/interactive/p-adaptive',
      mode: 'p/adaptive',
      state: 'finished',
      finished: true,
      best_score: 0.75,
      authoring: { spine: 'done', nodes_due: [], error: '' },
      // A list is carried as the JSON the run wrote; an empty member is no fact.
      facts: { name: 'demo', validation: 'ok', files: '["mode.py"]', count: '2', nested: '{"a":1}' },
    })
    expect(subprocess.lastArgv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'run',
      '--knob', 'source_root=/tmp/pkg', '--knob', 'rounds=2', 'p/adaptive',
    ])
  })

  it('reports a run that stopped at a gate a person owns as the run it is, not as a console failure', async () => {
    const { service, subprocess } = await setup()
    // The spine stopped with work due, so the forwarded entry point exits nonzero
    // and the console reports the same failure — with the run's own status beside it.
    subprocess.script = [
      { stdout: JSON.stringify([ADAPTIVE]) },
      {
        stdout: singleRun(
          {
            ...SINGLE,
            state: 'running',
            finished: false,
            best_score: null,
            authoring: { spine: 'ready', completed: ['prepare'], nodes_due: ['approve', 'overwatch'], error: null },
          },
          { exit_code: 1, ok: false, error: 'the graph did not finish: approve, overwatch still due' },
        ),
        exitCode: 2,
      },
    ]
    await expect(service.run({ mode: 'p/adaptive' }, signal())).resolves.toMatchObject({
      state: 'running',
      finished: false,
      best_score: null,
      authoring: { spine: 'ready', nodes_due: ['approve', 'overwatch'], error: '' },
    })
  })

  it('reports a gate the spine refused, with the reason the spine wrote', async () => {
    const { service, subprocess } = await setup()
    subprocess.script = [
      { stdout: JSON.stringify([ADAPTIVE]) },
      {
        stdout: singleRun(
          { ...SINGLE, state: 'failed', finished: false, authoring: { spine: 'refused', error: 'gate approve is answered by a person and nobody could be asked' } },
          { exit_code: 1, ok: false, error: 'the graph did not finish: gate approve' },
        ),
        exitCode: 2,
      },
    ]
    await expect(service.run({ mode: 'p/adaptive' }, signal())).resolves.toMatchObject({
      authoring: { spine: 'refused', error: 'gate approve is answered by a person and nobody could be asked' },
    })
  })

  it('reports a run with no graph spine and no facts without inventing either', async () => {
    const { service, subprocess } = await setup()
    subprocess.script = [
      { stdout: JSON.stringify([ADAPTIVE]) },
      { stdout: singleRun({ ...SINGLE, mode: undefined, authoring: undefined, facts: 'not an object' }) },
    ]
    await expect(service.run({ mode: 'p/adaptive' }, signal())).resolves.toMatchObject({
      mode: null,
      authoring: null,
      facts: {},
    })
  })

  it('fails with the console\'s reason when the run left no run directory', async () => {
    const { service, subprocess } = await setup()
    subprocess.script = [
      { stdout: JSON.stringify([ADAPTIVE]) },
      { stdout: JSON.stringify({ error: 'the mode graph could not be lowered' }), exitCode: 2 },
    ]
    const failure = await failureOf(service.run({ mode: 'p/adaptive' }, signal()))
    expect(failure.code).toBe('refactory/console-failed')
    expect(failure.message).toBe('the mode graph could not be lowered')
    expect(failure.details).toEqual({
      argv: ['/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'run', 'p/adaptive'],
    })
  })

  it('says the console left no run when it succeeded without one', async () => {
    const { service, subprocess } = await setup()
    subprocess.script = [
      { stdout: JSON.stringify([ADAPTIVE]) },
      { stdout: JSON.stringify({ verb: 'interactive', exit_code: 0, ok: true, result: {} }) },
    ]
    expect((await failureOf(service.run({ mode: 'p/adaptive' }, signal()))).message)
      .toBe('the console left no run directory for "p/adaptive"')
  })

  it('fails with the console\'s own reason when a failed call printed no JSON at all', async () => {
    const { service, subprocess } = await setup()
    subprocess.script = [
      { stdout: JSON.stringify([ADAPTIVE]) },
      { stdout: 'refusing\n', stderr: 'refusing\n', exitCode: 2 },
    ]
    expect((await failureOf(service.run({ mode: 'p/adaptive' }, signal()))).message).toBe('refusing')
  })

  it('refuses an authoring mode with the verb that runs it', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer([mode()])
    const failure = await failureOf(service.run({ mode: 'default_pack/create' }, signal()))
    expect(failure.code).toBe('refactory/authoring-mode')
    expect(failure.details).toEqual({ mode: 'default_pack/create', action: 'create' })
    expect(subprocess.spawns).toHaveLength(1)
  })

  it('refuses to use a mode the caller already cancelled', async () => {
    const { service, subprocess } = await setup()
    const controller = new AbortController()
    controller.abort()
    expect((await failureOf(service.run({ mode: 'p/adaptive' }, controller.signal))).code).toBe('gateway/cancelled')
    expect(subprocess.spawns).toHaveLength(0)
  })
})

describe('refactoryConsole — reading a run', () => {  it('maps a status frame, keeping only the event fields a reader reads', async () => {
  const { service, subprocess } = await setup()
  subprocess.answer(FRAME)
  expect(await service.status('/runs/live', 3, signal())).toEqual({
    run_id: 'live',
    root: '/repo/.factory/loop/live',
    mode: 'p/adaptive',
    state: 'running',
    events: [{ epoch: 0, generation: 1, evaluations: 3, score: 1.25 }],
    cursor: 4,
    counters: { epoch: 0, generation: 1, evaluations: 3, steps: 1 },
    best_score: 1.25,
    finished: false,
    running: true,
  })
  expect(subprocess.lastArgv).toEqual([
    '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'status', '--run', '/runs/live', '--after', '3',
  ])
})

it('reports a run that has not scored as no score rather than zero', async () => {
  const { service, subprocess } = await setup()
  subprocess.answer({ ...FRAME, mode: null, best_score: null, events: [], counters: {}, cursor: 0 })
  const frame = await service.status('live', 0, signal())
  expect(frame.best_score).toBeNull()
  expect(frame.mode).toBeNull()
  expect(frame.counters).toEqual({ epoch: 0, generation: 0, evaluations: 0, steps: 0 })
})

it('keeps only the event fields a reader reads, and states the ones an event omits', async () => {
  const { service, subprocess } = await setup()
  // A progress line carries only what its step recorded: a line written before a
  // score carries none, and that is an absence, not a zero.
  subprocess.answer({ ...FRAME, events: [{ epoch: 0, evaluations: 2 }, { generation: 1, score: 0.5 }] })
  const frame = await service.status('live', 0, signal())
  expect(frame.events).toEqual([{ epoch: 0, evaluations: 2 }, { generation: 1, score: 0.5 }])
})

it('reports a start whose answer names no settings as having none', async () => {
  const { service, subprocess } = await setup()
  subprocess.script = [
    { stdout: JSON.stringify([mode({ id: 'p/adaptive', mode: 'adaptive', scored: true, action: 'start' })]) },
    { stdout: JSON.stringify({ run_id: 'live', mode: 'p/adaptive', root: '/runs/live', config: 'none' }) },
  ]
  expect(await service.start({ mode: 'p/adaptive' }, signal())).toMatchObject({ config: {} })
})

it('asks a run to stop and reports whether it went', async () => {
  const { service, subprocess } = await setup()
  subprocess.answer({ stopped: true })
  expect(await service.stop('live', signal())).toEqual({ stopped: true })
  expect(subprocess.lastArgv).toEqual([
    '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'stop', '--run', 'live',
  ])
})

it('refuses an empty run address before reading anything', async () => {
  const { service, subprocess } = await setup()
  expect((await failureOf(service.status('', 0, signal()))).code).toBe('gateway/bad-request')
  expect((await failureOf(service.stop('  ', signal()))).code).toBe('gateway/bad-request')
  expect(subprocess.spawns).toHaveLength(0)
})
})

describe('refactoryConsole — steering a run', () => {
  /** One `steer` payload, as the console answers it: the ledger, and the gates. */
  const STEERING = {
    run: '/runs/live',
    ledger: '/runs/live/intent-ledger.md',
    exists: true,
    entries: [{ at: '2026-01-01T00:00:00+00:00', kind: 'correction', text: 'use a smaller grid' }],
    constraints: ['no numpy'],
    verdicts: { approve: 'proceed' },
    gates: [{ id: 'approve', prompt: 'approve the draft', verdicts: ['proceed', 'halt'] }],
    problems: ['no intent ledger at /runs/gone/intent-ledger.md'],
  }

  it('reads the ledger, and the gates the run is stopped at, in one call', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer(STEERING)
    expect(await service.steer({ run: '/runs/live' }, signal())).toEqual({
      run: '/runs/live',
      ledger: '/runs/live/intent-ledger.md',
      exists: true,
      entries: [{ at: '2026-01-01T00:00:00+00:00', kind: 'correction', text: 'use a smaller grid' }],
      constraints: ['no numpy'],
      verdicts: { approve: 'proceed' },
      gates: [{ id: 'approve', prompt: 'approve the draft', verdicts: ['proceed', 'halt'] }],
      problems: ['no intent ledger at /runs/gone/intent-ledger.md'],
    })
    expect(subprocess.lastArgv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'steer', '--run', '/runs/live', '--gates',
    ])
  })

  it('appends a note with the kind it was given, and takes the ledger it answers with', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer(STEERING)
    await service.steer({ run: '/runs/live', note: 'use a smaller grid', kind: 'correction' }, signal())
    expect(subprocess.lastArgv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'steer', '--run', '/runs/live',
      '--note', 'use a smaller grid', '--kind', 'correction', '--gates',
    ])
  })

  it('records a gate verdict as the console\'s own `GATE=VALUE`', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer(STEERING)
    await service.steer({ run: '/runs/live', verdicts: ['approve=proceed'] }, signal())
    expect(subprocess.lastArgv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'steer', '--run', '/runs/live',
      '--verdict', 'approve=proceed', '--gates',
    ])
  })

  it('sends control as the console\'s own flag, not as a note', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer(STEERING)
    await service.steer({ run: '/runs/live', control: 'pause' }, signal())
    expect(subprocess.lastArgv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'steer', '--run', '/runs/live',
      '--pause', '--gates',
    ])
  })

  it('omits an empty note and an empty verdict, so a read cannot write by accident', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer(STEERING)
    await service.steer({ run: '/runs/live', note: '   ', verdicts: ['  '] }, signal())
    expect(subprocess.lastArgv).toEqual([
      '/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'steer', '--run', '/runs/live', '--gates',
    ])
  })

  it('reports the console\'s refusal of an unknown run, with the argv to reproduce it', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer({ error: '/runs/absent is not a run directory' }, 2)
    const failure = await failureOf(service.steer({ run: '/runs/absent' }, signal()))
    expect(failure.code).toBe('refactory/console-failed')
    expect(failure.message).toBe('/runs/absent is not a run directory')
    expect(failure.details).toEqual({
      argv: ['/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'steer', '--run', '/runs/absent', '--gates'],
    })
  })

  it('reports the console\'s refusal of a note kind it does not know', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer({ error: "unknown note kind 'nope'; known: ['info', 'correction', 'constraint']" }, 2)
    const failure = await failureOf(service.steer(
      { run: '/runs/live', note: 'x', kind: 'nope' as never },
      signal(),
    ))
    expect(failure.code).toBe('refactory/console-failed')
    expect(failure.message).toContain("unknown note kind 'nope'")
  })

  it('refuses an empty run address before reading anything', async () => {
    const { service, subprocess } = await setup()
    const failure = await failureOf(service.steer({ run: '  ' }, signal()))
    expect(failure.code).toBe('gateway/bad-request')
    expect(subprocess.spawns).toHaveLength(0)
  })

  it('rejects an answer whose ledger shape is not the console\'s', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer({ ...STEERING, entries: 'not a list' })
    await expect(service.steer({ run: '/runs/live' }, signal())).rejects.toThrow('ledger entries is not a list')
  })
})

describe('refactoryConsole — packages', () => {
  it('enables a package, then answers with the catalogue as it now stands', async () => {
    const { service, subprocess } = await setup()
    subprocess.script = [
      { stdout: JSON.stringify({ verb: 'registry', exit_code: 0, ok: true, result: { enabled: 'p' } }) },
      { stdout: JSON.stringify({ verb: 'registry', exit_code: 0, ok: true, result: { p: [{ name: 'adaptive', description: 'd', package_enabled: true, trust: 'trusted', where: '/p' }] } }) },
    ]
    expect(await service.setPackageEnabled('p', true, signal())).toEqual([
      { name: 'p', enabled: true, where: '/p', modes: [{ name: 'adaptive', description: 'd', trust: 'trusted' }] },
    ])
    expect(subprocess.spawns[0]?.argv.slice(3)).toEqual([
      'registry', '--args', '', 'enable', 'p',
    ])
  })

  it('disables a package, and removes one with the force the registry requires', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer({ verb: 'registry', exit_code: 0, ok: true, result: {} })
    await service.setPackageEnabled('p', false, signal())
    expect(subprocess.spawns[0]?.argv.slice(3)).toEqual(['registry', '--args', '', 'disable', 'p'])
    await service.removePackage('p', signal())
    expect(subprocess.spawns[2]?.argv.slice(3)).toEqual(['registry', '--args', '--force', 'remove', 'p'])
  })

  it('imports from a directory or git URL, naming the import when one is given', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer({ verb: 'registry', exit_code: 0, ok: true, result: {} })
    await service.importPackage('/src/pack', 'renamed', signal())
    expect(subprocess.spawns[0]?.argv.slice(3)).toEqual([
      'registry', '--args', '--as "renamed"', 'import', '/src/pack',
    ])
    await service.importPackage('/src/pack', '', signal())
    expect(subprocess.spawns[2]?.argv.slice(3)).toEqual(['registry', '--args', '', 'import', '/src/pack'])
  })

  it('refuses an empty source and an empty package name', async () => {
    const { service, subprocess } = await setup()
    expect((await failureOf(service.importPackage('  ', '', signal()))).code).toBe('gateway/bad-request')
    expect((await failureOf(service.setPackageEnabled('', true, signal()))).code).toBe('gateway/bad-request')
    expect((await failureOf(service.removePackage('', signal()))).code).toBe('gateway/bad-request')
    expect(subprocess.spawns).toHaveLength(0)
  })

  it('reports the registry\'s own refusal, from whichever stream it wrote it to', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer({ verb: 'registry', exit_code: 2, ok: false, result: { error: 'cannot remove shipped package' } })
    const failure = await failureOf(service.packages(signal()))
    expect(failure.code).toBe('refactory/console-failed')
    expect(failure.message).toBe('cannot remove shipped package')

    subprocess.answer({ verb: 'registry', exit_code: 2, ok: false, result: { output: 'printed before it failed' } })
    expect((await failureOf(service.packages(signal()))).message).toBe('printed before it failed')

    subprocess.answer({ verb: 'registry', exit_code: 2, ok: false, result: {} })
    expect((await failureOf(service.packages(signal()))).message).toBe('the registry could not list packages')
  })
})

describe('refactoryConsole — the console boundary', () => {
  it('reports a console refusal with its message and the argv to reproduce it', async () => {
    const { service, subprocess } = await setup()
    subprocess.script = [{ stdout: JSON.stringify({ error: '/runs/absent is not a run directory' }), exitCode: 2 }]
    const failure = await failureOf(service.status('absent', 0, signal()))
    expect(failure.code).toBe('refactory/console-failed')
    expect(failure.message).toBe('/runs/absent is not a run directory')
    expect(failure.details).toEqual({
      argv: ['/repo/.venv/bin/python', '-m', 'refactory_dsh.console', 'status', '--run', 'absent', '--after', '0'],
    })
  })

  it('refuses to call a console the caller already cancelled', async () => {
    const { service, subprocess } = await setup()
    const controller = new AbortController()
    controller.abort()
    const failure = await failureOf(service.modes(controller.signal))
    expect(failure.code).toBe('gateway/cancelled')
    expect(subprocess.spawns).toHaveLength(0)
  })

  it('rejects an answer whose shape is not the console\'s, rather than rendering an empty list', async () => {
    const { service, subprocess } = await setup()
    subprocess.answer({ not: 'a list' })
    await expect(service.modes(signal())).rejects.toThrow('mode catalogue is not a list')
    subprocess.answer({ outer_loops: [1] })
    await expect(service.strategies(signal())).rejects.toThrow('outer loops holds a member that is not an object')
    subprocess.answer('not an object')
    await expect(service.strategies(signal())).rejects.toThrow('strategy answer is not an object')
    await expect(service.status('live', 0, signal())).rejects.toThrow('run status is not an object')
  })
})
