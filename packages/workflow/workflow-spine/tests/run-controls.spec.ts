/**
 * Run-control tests: dm-252 (stable graph revision), dm-227 (a person's rerun
 * behind their own pause), dm-231 (targeted pause), and supporting invariants.
 *
 * Integration tests exercise applyControls through the plugin's wf_next,
 * verifying the production code path with control files. The same controls are
 * driven through the `refactory graph-control` CLI, against a spine owner, in
 * refactory/tests/test_graph_control_owner.py.
 */
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import { afterEach, describe, expect, it } from 'vitest'
import * as spinePlugin from '../src/index.ts'
import { computeGraphRevision } from '../src/index.ts'
import { Spine } from '../src/spine.ts'
import type { Config } from '../src/index.ts'
import type { WorkflowGraph } from '../src/types.ts'

// ── Scratch dir management ──────────────────────────────────────────────

const scratchDirs: string[] = []

afterEach(() => {
  for (const dir of scratchDirs.splice(0)) rmSync(dir, { recursive: true, force: true })
})

function scratch(): string {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-spine-runctl-'))
  scratchDirs.push(dir)
  return dir
}

// ── Spine-level helpers ─────────────────────────────────────────────────

function ids(spine: Spine): string[] {
  const next = spine.next()
  return next.nodes?.map(node => node.id) ?? []
}

// ── Graphs ──────────────────────────────────────────────────────────────

/** Linear graph: a → gate_qa → emit. */
function linearGraph(): WorkflowGraph {
  return {
    name: 't', start_node: 'a',
    nodes: {
      a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', writes: [], reads: [] },
      gate_qa: {
        _type: 'GateNode', id: 'gate_qa', evaluator_type: 'user',
        gate_prompt: 'ok?', reads: [],
      },
      emit: {
        _type: 'AgentNode', id: 'emit', role: 'builder',
        prompt_template: 'E', writes: ['emit.txt'], reads: [],
      },
    },
    edges: [
      { source: 'a', target: 'gate_qa' },
      { source: 'gate_qa', target: 'emit', condition: 'proceed' },
      { source: 'gate_qa', target: 'a', condition: 'reloop' },
    ],
  }
}

/** Build the graph: gate_check → synthesize_qa → gate_qa → emit | proceed.
 *  All gates are fn-evaluated so wf_verdict can supply a verdict without executeCommands. */
function reviewGraph(): WorkflowGraph {
  return {
    name: 't', start_node: 'gate_check',
    nodes: {
      gate_check: {
        _type: 'GateNode', id: 'gate_check', evaluator_type: 'fn',
        evaluator_command: 'true', reads: [],
      },
      synthesize_qa: {
        _type: 'AgentNode', id: 'synthesize_qa', role: 'builder',
        prompt_template: 'S', writes: [], reads: [],
      },
      gate_qa: {
        _type: 'GateNode', id: 'gate_qa', evaluator_type: 'fn',
        evaluator_command: 'true', reads: [],
      },
      emit: {
        _type: 'AgentNode', id: 'emit', role: 'builder',
        prompt_template: 'E', writes: ['emit.txt'], reads: [],
      },
      proceed: {
        _type: 'AgentNode', id: 'proceed', role: 'builder',
        prompt_template: 'P', writes: [], reads: [],
      },
    },
    edges: [
      { source: 'gate_check', target: 'synthesize_qa', condition: 'proceed' },
      { source: 'synthesize_qa', target: 'gate_qa' },
      { source: 'gate_qa', target: 'emit', condition: 'emit' },
      { source: 'gate_qa', target: 'proceed', condition: 'proceed' },
    ],
  }
}

// ── Plugin infrastructure ───────────────────────────────────────────────

function pluginConfig(graph: WorkflowGraph, overrides: Partial<Config> = {}): Config {
  return {
    graph,
    toolPrefix: 'wf',
    maxIterations: 3,
    interactiveOpen: false,
    openCommand: '',
    openArgs: [],
    executeCommands: false,
    commandEnv: {},
    commandTimeoutMs: 10_000,
    commandSandboxMode: 'danger-full-access',
    dataCommand: '',
    steerCommand: '',
    steerArgs: [],
    ceoDeny: [],
    ...overrides,
  }
}

async function mount(graph: WorkflowGraph, overrides: Partial<Config> = {}): Promise<Context> {
  const root = new Context()
  await root.plugin(SystemPrompt)
  await root.plugin(ToolRuntime)
  await root.plugin(spinePlugin, pluginConfig(graph, overrides))
  return root
}

async function call(ctx: Context, name: string, args: unknown = {}): Promise<unknown> {
  const definition = ctx.tools.get(name)
  if (definition === undefined) throw new Error(`tool ${name} not registered`)
  return definition.execute(args, {} as never)
}

/** Write a rerun control file into the workdir. */
function writeRerunControl(workdir: string, target: string, opts: {
  revision: string; text?: string; deferredNodes?: string[]
}): void {
  const directory = join(workdir, '.refactory', 'graph-controls')
  mkdirSync(directory, { recursive: true })
  const control: Record<string, unknown> = {
    id: '1', action: 'rerun', target, text: opts.text ?? '',
    revision: opts.revision, run_id: resolve(workdir), invocation: null,
    status: 'queued',
  }
  if (opts.deferredNodes) control.deferred_nodes = opts.deferredNodes
  writeFileSync(join(directory, '1.json'), JSON.stringify(control))
}

/** Write a pause control file into the workdir. */
function writePauseControl(workdir: string, target?: string): void {
  const directory = join(workdir, '.refactory', 'graph-controls')
  mkdirSync(directory, { recursive: true })
  const id = `pause-${Date.now()}`
  const control: Record<string, unknown> = {
    id, action: 'pause', target: target ?? '',
    run_id: resolve(workdir), invocation: null,
    status: 'queued',
  }
  writeFileSync(join(directory, `${id}.json`), JSON.stringify(control))
}

/** Write any control file; controls apply in lexical order of their names. */
function writeControl(workdir: string, name: string, body: Record<string, unknown>): void {
  const directory = join(workdir, '.refactory', 'graph-controls')
  mkdirSync(directory, { recursive: true })
  writeFileSync(join(directory, `${name}.json`), JSON.stringify({
    id: name, run_id: resolve(workdir), invocation: null, status: 'queued', text: '', target: '', ...body,
  }))
}

const checkpointPath = (workdir: string): string => join(workdir, '.refactory', 'spine-checkpoint.json')
/** The revision Python's preview hands a person: the field the spine stored. */
const storedRevision = (workdir: string): string => JSON.parse(readFileSync(checkpointPath(workdir), 'utf8')).graph_revision
const statusOf = (workdir: string): Record<string, unknown> =>
  JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8'))
const receiptOf = (workdir: string): Record<string, unknown> =>
  JSON.parse(readFileSync(join(workdir, '.refactory', 'graph-control-receipt.json'), 'utf8'))

/** A shell that runs commands for real and logs nothing of its own. */
function realShell(): object {
  return {
    resolve: (request: { command: string; workdir?: string; timeoutMs?: number; env?: Record<string, string> }) => ({
      command: request.command, workdir: request.workdir ?? process.cwd(), timeoutMs: request.timeoutMs ?? 10_000,
      onExpiry: 'kill', stdoutMaxBytes: 1_000_000, env: request.env }),
    execute: async (spec: { command: string; workdir?: string }) => {
      const done = spawnSync('bash', ['-c', spec.command], { encoding: 'utf8', timeout: 10_000, cwd: spec.workdir })
      return { result: async () => ({ exitCode: done.status ?? 1, signal: done.signal, timedOut: false, aborted: false,
        timeoutMs: 10_000, stdout: { text: done.stdout ?? '', truncated: false },
        stderr: { text: done.stderr ?? '', truncated: false } }) }
    },
  }
}

// ── dm-252: Stable graph revision ─────────────────────────────────────────

describe('dm-252: stable graph revision', () => {
  it('graph_revision is stable across owner_pid changes', () => {
    const spine = new Spine(linearGraph())
    spine.complete('a')
    const saved1 = { ...spine.checkpoint(), owner_pid: 1111, status: 'executing' }
    const saved2 = { ...spine.checkpoint(), owner_pid: 2222, status: 'paused' }
    expect(computeGraphRevision(saved1)).toBe(computeGraphRevision(saved2))
  })

  it('graph_revision changes when cursor changes', () => {
    const spine = new Spine(linearGraph())
    const before = computeGraphRevision(spine.checkpoint())
    spine.complete('a')
    const after = computeGraphRevision(spine.checkpoint())
    expect(before).not.toBe(after)
  })

  it('graph_revision ignores pause boundaries, so a preview stays current across pause and resume', () => {
    const spine = new Spine(linearGraph())
    spine.complete('a')
    const unpaused = computeGraphRevision(spine.checkpoint())
    spine.pauseBefore('gate_qa', true)
    expect(spine.isPaused()).toBe(true)
    expect(computeGraphRevision(spine.checkpoint())).toBe(unpaused)
  })

  it('graph_revision changes when only the cursor changes', () => {
    const saved = new Spine(linearGraph()).checkpoint()
    const state = saved.state as Record<string, unknown>
    const moved = { ...saved, state: { ...state, cursor: ['gate_qa'] } }
    expect(computeGraphRevision(moved)).not.toBe(computeGraphRevision(saved))
  })

  it('rerun control accepted after owner restart', () => {
    // Spine-level: verify revision stability across restore
    const spine = new Spine(linearGraph())
    spine.complete('a')
    spine.verdict('gate_qa', 'proceed')
    spine.stopAtBoundary()
    const saved = spine.checkpoint()
    const revision = computeGraphRevision(saved)
    const spine2 = new Spine(linearGraph())
    spine2.restore(saved)
    const revision2 = computeGraphRevision(spine2.checkpoint())
    expect(revision2).toBe(revision)
  })

  it('rerun control accepted via applyControls with graph_revision (dm-252)', async () => {
    // Integration: verify applyControls recomputes graph_revision and accepts it.
    // Fails when reverting the computeGraphRevision comparison to byte-hash only.
    const workdir = scratch()
    const graph: WorkflowGraph = {
      name: 't', start_node: 'a',
      nodes: {
        a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', reads: [], writes: [] },
        b: { _type: 'AgentNode', id: 'b', role: 'builder', prompt_template: 'B', reads: [], writes: [] },
      },
      edges: [{ source: 'a', target: 'b' }],
    }
    const ctx = await mount(graph, { workdir })
    // Advance through 'a' and reach 'b'
    await call(ctx, 'wf_next')
    await call(ctx, 'wf_done', { node: 'a' })
    // An extra wf_next brings the checkpoint to the current drain boundary.
    // In the real flow, Python's preview reads the checkpoint after the boundary.
    await call(ctx, 'wf_next')
    // Read the checkpoint's graph_revision (simulating Python's preview)
    const revision = JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-checkpoint.json'), 'utf8')).graph_revision
    expect(revision).toBeDefined()
    // Queue a rerun control targeting 'a' with graph_revision
    writeRerunControl(workdir, 'a', { revision })
    // wf_next → applyControls computes computeGraphRevision(liveSaved) and compares
    const next = await call(ctx, 'wf_next') as { status: string; nodes?: Array<{ id: string }> }
    // The control was consumed and applied
    expect(existsSync(join(workdir, '.refactory', 'graph-controls', '1.json'))).toBe(false)
    const receipt = JSON.parse(readFileSync(join(workdir, '.refactory', 'graph-control-receipt.json'), 'utf8'))
    expect(receipt.status).toBe('applied')
    // 'a' is back in cursor (reopened)
    expect(next.nodes?.map(n => n.id)).toContain('a')
  })

  /** a → b → c, driven to the boundary before c with everything persisted. */
  async function atC(workdir: string): Promise<Context> {
    const graph: WorkflowGraph = {
      name: 't', start_node: 'a',
      nodes: {
        a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', reads: [], writes: [] },
        b: { _type: 'AgentNode', id: 'b', role: 'builder', prompt_template: 'B', reads: [], writes: [] },
        c: { _type: 'AgentNode', id: 'c', role: 'builder', prompt_template: 'C', reads: [], writes: [] },
      },
      edges: [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }],
    }
    const ctx = await mount(graph, { workdir })
    await call(ctx, 'wf_next'); await call(ctx, 'wf_done', { node: 'a' })
    await call(ctx, 'wf_next'); await call(ctx, 'wf_done', { node: 'b' })
    await call(ctx, 'wf_next')
    return ctx
  }

  it('compares the revision recomputed from the live spine, never the stored field', async () => {
    // A stored revision that no longer describes the live spine (written by another
    // owner, or stale) must not let a rerun through: only the live graph decides.
    const workdir = scratch()
    const ctx = await atC(workdir)
    const saved = JSON.parse(readFileSync(checkpointPath(workdir), 'utf8'))
    writeFileSync(checkpointPath(workdir), JSON.stringify({ ...saved, graph_revision: 'not-this-graph' }))
    writeControl(workdir, '1', { action: 'rerun', target: 'a', revision: 'not-this-graph' })
    await call(ctx, 'wf_next')
    expect(receiptOf(workdir)).toMatchObject({ status: 'failed', message: 'graph changed since preview; preview invalidation again' })
  })

  it('refuses a preview taken from a pre-upgrade checkpoint, whose revision was its byte hash', async () => {
    const workdir = scratch()
    const ctx = await atC(workdir)
    const byteHash = createHash('sha256').update(readFileSync(checkpointPath(workdir))).digest('hex')
    writeControl(workdir, '1', { action: 'rerun', target: 'a', revision: byteHash })
    await call(ctx, 'wf_next')
    expect(receiptOf(workdir)).toMatchObject({ status: 'failed', message: 'graph changed since preview; preview invalidation again' })
    // The same rerun with the revision preview hands out is applied.
    writeControl(workdir, '2', { action: 'rerun', target: 'a', revision: storedRevision(workdir) })
    await call(ctx, 'wf_next')
    expect(receiptOf(workdir)).toMatchObject({ status: 'applied' })
  })
})

// ── dm-227: planRedo on paused runs ───────────────────────────────────────

describe("dm-227: a person's rerun behind their own pause", () => {
  it('planRedo and redo refuse on a paused spine unless a person reruns', () => {
    const spine = new Spine(linearGraph())
    spine.complete('a')
    spine.verdict('gate_qa', 'proceed')
    spine.pauseBefore('emit', true)
    expect(spine.isPaused()).toBe(true)
    // The CEO's path: the person's pause holds.
    expect(() => spine.planRedo('a')).toThrow(/run paused before emit/)
    expect(() => spine.redo('a')).toThrow(/run paused before emit/)
    // A person's rerun: the pause is their own.
    const plan = spine.planRedo('a', { whilePaused: true })
    expect(plan.start).toBe('a')
    expect(plan.nodes).toContain('a')
    spine.redo('a', { whilePaused: true })
    expect(ids(spine)).toContain('a')
  })

  it('a paused checkpoint still describes its redo plans, so preview works while paused', () => {
    const spine = new Spine(linearGraph())
    spine.complete('a')
    spine.verdict('gate_qa', 'proceed')
    spine.pauseBefore('emit', true)
    const saved = spine.checkpoint() as { redo_plans: Record<string, { start: string }> }
    expect(saved.redo_plans.a?.start).toBe('a')
  })

  it('skip and expand still refuse on a paused spine', () => {
    const spine = new Spine(linearGraph())
    spine.complete('a')
    spine.verdict('gate_qa', 'proceed')
    spine.pauseBefore('emit', true)
    expect(spine.isPaused()).toBe(true)
    // planSkip still refuses (PIT-003)
    expect(() => spine.planSkip('emit')).toThrow(/paused/)
    // expand still refuses (PIT-003)
    expect(() => spine.expand('emit', [])).toThrow(/paused/)
  })

  it('rerun on a paused spine arms pause at redo start via applyControls (dm-227)', async () => {
    // Integration: verify applyControls applies rerun on paused spine and arms
    // a pause at the redo's start node. Fails when reverting lines 1292-1296.
    const workdir = scratch()
    const graph: WorkflowGraph = {
      name: 't', start_node: 'gate_strategy',
      nodes: {
        gate_strategy: { _type: 'AgentNode', id: 'gate_strategy', role: 'builder',
                         prompt_template: 'GS', reads: [], writes: [] },
        emit: { _type: 'AgentNode', id: 'emit', role: 'builder',
                prompt_template: 'E', reads: [], writes: [] },
      },
      edges: [{ source: 'gate_strategy', target: 'emit' }],
    }
    const ctx = await mount(graph, { workdir })
    // Drive to emit
    await call(ctx, 'wf_next')
    await call(ctx, 'wf_done', { node: 'gate_strategy' })
    // Write untargeted pause control before next wf_next
    writePauseControl(workdir)
    // wf_next → applyControls pauses at emit (current cursor)
    let next = await call(ctx, 'wf_next') as { status: string }
    expect(next.status).toBe('paused')
    // Read graph_revision
    const revision = JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-checkpoint.json'), 'utf8')).graph_revision
    // Write rerun control targeting gate_strategy
    writeRerunControl(workdir, 'gate_strategy', { revision })
    // wf_next → applyControls processes rerun: redo(gate_strategy), arms pause at gate_strategy
    next = await call(ctx, 'wf_next') as { status: string }
    expect(next.status).toBe('paused')
    // Verify the receipt shows the rerun was applied
    const receipt = JSON.parse(readFileSync(join(workdir, '.refactory', 'graph-control-receipt.json'), 'utf8'))
    expect(receipt.status).toBe('applied')
    // The status file confirms paused (at gate_strategy, not emit)
    const statusFile = JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8'))
    expect(statusFile.status).toBe('paused')
    expect(statusFile.paused_before).toEqual(['gate_strategy'])
  })

  it("the CEO's wf_redo cannot move the cursor off a person's pause", async () => {
    const workdir = scratch()
    const graph: WorkflowGraph = {
      name: 't', start_node: 'gs',
      nodes: {
        gs: { _type: 'AgentNode', id: 'gs', role: 'builder', prompt_template: 'GS', reads: [], writes: [] },
        emit: { _type: 'AgentNode', id: 'emit', role: 'builder', prompt_template: 'E', reads: [], writes: [] },
      },
      edges: [{ source: 'gs', target: 'emit' }],
    }
    const ctx = await mount(graph, { workdir })
    await call(ctx, 'wf_next')
    await call(ctx, 'wf_done', { node: 'gs' })
    writePauseControl(workdir)
    expect((await call(ctx, 'wf_next') as { status: string }).status).toBe('paused')
    await expect(call(ctx, 'wf_redo', { node: 'gs' })).rejects.toThrow(/run paused before emit/)
    expect((await call(ctx, 'wf_next') as { status: string }).status).toBe('paused')
    expect(statusOf(workdir)).toMatchObject({ status: 'paused', paused_before: ['emit'] })
  })

  it('a rerun on a live run is not held by a targeted pause armed on a route it never took', async () => {
    const workdir = scratch()
    const ctx = await mount(reviewGraph(), { workdir })
    await call(ctx, 'wf_next')
    writePauseControl(workdir, 'emit')
    await call(ctx, 'wf_verdict', { gate: 'gate_check', verdict: 'proceed' })
    await call(ctx, 'wf_next')
    await call(ctx, 'wf_done', { node: 'synthesize_qa' })
    await call(ctx, 'wf_next')
    await call(ctx, 'wf_verdict', { gate: 'gate_qa', verdict: 'proceed' })
    expect((await call(ctx, 'wf_next') as { status: string }).status).toBe('ready')
    // The pause on emit is still armed, but the run is not paused.
    expect(JSON.parse(readFileSync(checkpointPath(workdir), 'utf8')).state.pause_before).toEqual(['emit'])
    writeControl(workdir, '1', { action: 'rerun', target: 'synthesize_qa', revision: storedRevision(workdir) })
    const next = await call(ctx, 'wf_next') as { status: string; nodes?: Array<{ id: string }> }
    expect(receiptOf(workdir).status).toBe('applied')
    expect(next.status).toBe('ready')
    expect(next.nodes?.map(node => node.id)).toEqual(['synthesize_qa'])
  })

  it("holds before the redo's start node, which for a finished dataset's body is the data node", async () => {
    // Reopening a body node of a finished dataset restarts the dataset: the run
    // must hold before the data node itself, before its items are even listed.
    const workdir = scratch()
    const graph: WorkflowGraph = {
      name: 'dataset', start_node: 'data',
      nodes: {
        data: { _type: 'DataNode', id: 'data', task_ref: 'probe', subgraph_entry: 'work', subgraph_exit: 'end', reads: [], writes: [] },
        work: { _type: 'FnNode', id: 'work', command: `echo work >> ${join(workdir, 'dispatch.log')}`, reads: [], writes: [] },
        end: { _type: 'FnNode', id: 'end', command: `echo end >> ${join(workdir, 'dispatch.log')}`, reads: [], writes: [] },
        after: { _type: 'FnNode', id: 'after', command: `echo after >> ${join(workdir, 'dispatch.log')}`, reads: [], writes: [] },
      },
      edges: [{ source: 'data', target: 'after' }, { source: 'work', target: 'end' }],
    }
    // The run finished its dataset and is due at `after`; its owner restarts from there.
    const finished = new Spine(graph)
    finished.startPass('data', 1)
    finished.complete('work')
    finished.complete('end')
    finished.endPass()
    expect(finished.next().nodes?.map(node => node.id)).toEqual(['after'])
    mkdirSync(join(workdir, '.refactory'), { recursive: true })
    const saved = { ...finished.checkpoint(), status: 'ready', materialised: null, run_id: resolve(workdir) }
    writeFileSync(checkpointPath(workdir), JSON.stringify(saved))
    writeFileSync(join(workdir, '.refactory', 'resume-request.json'), JSON.stringify({
      checkpoint_sha256: createHash('sha256').update(readFileSync(checkpointPath(workdir))).digest('hex') }))
    const ctx = await mount(graph, { workdir, executeCommands: true, dataCommand: `cat > /dev/null; echo '{}' #` })
    ctx.provide('shell', realShell())
    // The person pauses, then reruns the body node `work`.
    writeControl(workdir, '1-pause', { action: 'pause' })
    writeControl(workdir, '2-rerun', { action: 'rerun', target: 'work', revision: computeGraphRevision(finished.checkpoint()) })
    const next = await call(ctx, 'wf_next') as { status: string }
    expect(receiptOf(workdir)).toMatchObject({ action: 'rerun', status: 'applied' })
    expect(next.status).toBe('paused')
    expect(statusOf(workdir)).toMatchObject({ status: 'paused', paused_before: ['data'] })
    expect(existsSync(join(workdir, 'dispatch.log'))).toBe(false)
  })
})

// ── dm-231: Targeted pause honours --node target ──────────────────────────

describe('dm-231: targeted pause', () => {
  // Spine-level tests: verify the pauseBefore mechanism works for targeted nodes
  it('pauseBefore targets a specific node', () => {
    const spine = new Spine(reviewGraph())
    spine.verdict('gate_check', 'proceed')
    spine.pauseBefore('emit', true)
    expect(spine.isPaused()).toBe(false)
    expect(ids(spine)).toEqual(['synthesize_qa'])
    spine.complete('synthesize_qa')
    expect(ids(spine)).toEqual(['gate_qa'])
    expect(spine.isPaused()).toBe(false)
    spine.verdict('gate_qa', 'emit')
    expect(ids(spine)).toEqual(['emit'])
    expect(spine.isPaused()).toBe(true)
  })

  it('targeted pause never holds on a route that does not reach the target', () => {
    const spine = new Spine(reviewGraph())
    spine.verdict('gate_check', 'proceed')
    spine.pauseBefore('emit', true)
    spine.complete('synthesize_qa')
    spine.verdict('gate_qa', 'proceed')
    expect(ids(spine)).toEqual(['proceed'])
    expect(spine.isPaused()).toBe(false)
    spine.complete('proceed')
    expect(spine.next().status).toBe('done')
  })

  it('untargeted pause still pauses all cursor nodes', () => {
    const spine = new Spine(linearGraph())
    spine.pauseBefore('a', true)
    expect(spine.isPaused()).toBe(true)
    expect(spine.pausedAt()).toEqual(['a'])
    spine.pauseBefore('a', false)
    spine.complete('a')
    spine.pauseBefore('gate_qa', true)
    expect(spine.isPaused()).toBe(true)
  })

  // Integration tests: verify applyControls honours request.target through control files.
  // These fail when reverting lines 1228-1234 of index.ts (targeted pause branch).
  it('targeted pause through control file holds when target reaches cursor (dm-231)', async () => {
    const workdir = scratch()
    const ctx = await mount(reviewGraph(), { workdir })
    // wf_next → gate_check is due
    await call(ctx, 'wf_next')
    // Write targeted pause control for 'emit'
    writePauseControl(workdir, 'emit')
    // Drive gate_check → proceed
    await call(ctx, 'wf_verdict', { gate: 'gate_check', verdict: 'proceed' })
    // wf_next → applyControls processes targeted pause, synthesize_qa is due (not paused)
    let next = await call(ctx, 'wf_next') as { status: string; nodes?: Array<{ id: string }> }
    expect(next.status).toBe('ready')
    expect(next.nodes?.map(n => n.id)).toEqual(['synthesize_qa'])
    // Complete synthesize_qa → gate_qa
    await call(ctx, 'wf_done', { node: 'synthesize_qa' })
    next = await call(ctx, 'wf_next') as { status: string; nodes?: Array<{ id: string }> }
    expect(next.status).toBe('ready')
    expect(next.nodes?.map(n => n.id)).toEqual(['gate_qa'])
    // Route gate_qa to emit → paused at emit
    await call(ctx, 'wf_verdict', { gate: 'gate_qa', verdict: 'emit' })
    next = await call(ctx, 'wf_next') as { status: string; nodes?: Array<{ id: string }> }
    expect(next.status).toBe('paused')
    const statusFile = JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8'))
    expect(statusFile.status).toBe('paused')
  })

  it('targeted pause through control file never holds on proceed route (dm-231)', async () => {
    const workdir = scratch()
    const ctx = await mount(reviewGraph(), { workdir })
    // wf_next → gate_check is due
    await call(ctx, 'wf_next')
    // Write targeted pause control for 'emit'
    writePauseControl(workdir, 'emit')
    // Drive gate_check → proceed
    await call(ctx, 'wf_verdict', { gate: 'gate_check', verdict: 'proceed' })
    // wf_next → applyControls processes targeted pause, synthesize_qa is due
    await call(ctx, 'wf_next')
    await call(ctx, 'wf_done', { node: 'synthesize_qa' })
    await call(ctx, 'wf_next')
    // Route gate_qa to proceed (not emit) → NOT paused
    await call(ctx, 'wf_verdict', { gate: 'gate_qa', verdict: 'proceed' })
    const next = await call(ctx, 'wf_next') as { status: string; nodes?: Array<{ id: string }> }
    expect(next.status).toBe('ready')
    expect(next.nodes?.map(n => n.id)).toEqual(['proceed'])
  })
})
