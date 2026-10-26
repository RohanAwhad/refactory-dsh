/**
 * Run-control tests: dm-252 (stable graph revision), dm-227 (planRedo on
 * paused runs), dm-231 (targeted pause), and supporting invariants.
 *
 * Integration tests exercise applyControls through the plugin's wf_next,
 * verifying the production code path with control files.
 */
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import { afterEach, describe, expect, it } from 'vitest'
import * as spinePlugin from '../src/index.ts'
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

/** Build a small graph: gate_strategy → emit, with a gate that routes to emit or proceed. */
function gateGraph(): WorkflowGraph {
  return {
    name: 't', start_node: 'gate_strategy',
    nodes: {
      gate_strategy: {
        _type: 'GateNode', id: 'gate_strategy', evaluator_type: 'user',
        gate_prompt: 'proceed?', reads: [],
      },
      emit: {
        _type: 'AgentNode', id: 'emit', role: 'builder',
        prompt_template: 'Emit', writes: ['emit.txt'], reads: [],
      },
      proceed: {
        _type: 'AgentNode', id: 'proceed', role: 'builder',
        prompt_template: 'P', writes: [], reads: [],
      },
    },
    edges: [
      { source: 'gate_strategy', target: 'emit', condition: 'emit' },
      { source: 'gate_strategy', target: 'proceed', condition: 'proceed' },
    ],
  }
}

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

// ── Helpers ──────────────────────────────────────────────────────────────

/**
 * Stable stringify replacer: recursively sorts object keys for deterministic
 * JSON serialization. Mirrors the one added to index.ts.
 */
function stableStringify(_key: string, value: unknown): unknown {
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
    )
  }
  return value
}

/** Compute graph revision from a saved checkpoint, mirroring index.ts. */
function computeGraphRevision(saved: Record<string, unknown>): string {
  const state = saved.state as Record<string, unknown>
  const structural = {
    schema_version: saved.schema_version,
    source_graph: saved.source_graph,
    graph: saved.graph,
    afters_map: saved.afters_map,
    successors: saved.successors,
    redo_plans: saved.redo_plans,
    deferred_redo_plans: saved.deferred_redo_plans,
    cursor: state.cursor,
    completed: state.completed,
    skipped: state.skipped,
    gate_verdicts: state.gate_verdicts,
    iterations: state.iterations,
    feedback: state.feedback,
    gate_reasons: state.gate_reasons,
    prompts: state.prompts,
    pass: state.pass,
  }
  return createHash('sha256')
    .update(JSON.stringify(structural, stableStringify))
    .digest('hex')
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
})

// ── dm-227: planRedo on paused runs ───────────────────────────────────────

describe('dm-227: planRedo succeeds on a paused spine', () => {
  it('planRedo succeeds when paused', () => {
    const spine = new Spine(gateGraph())
    spine.verdict('gate_strategy', 'emit')
    spine.complete('emit')
    // Pause before gate_strategy (which is completed, but we can pause before emit)
    // Actually we need a graph where we can get paused. Let's use linearGraph.
    const spine2 = new Spine(linearGraph())
    spine2.complete('a')
    spine2.verdict('gate_qa', 'proceed')
    // pause before emit
    spine2.pauseBefore('emit', true)
    expect(spine2.isPaused()).toBe(true)
    // planRedo on a completed node
    const plan = spine2.planRedo('a')
    expect(plan.nodes).toContain('a')
    expect(plan.start).toBe('a')
  })

  it('redo succeeds on a paused spine', () => {
    const spine = new Spine(linearGraph())
    spine.complete('a')
    spine.verdict('gate_qa', 'proceed')
    spine.pauseBefore('emit', true)
    expect(spine.isPaused()).toBe(true)
    // redo 'a' — should succeed
    spine.redo('a')
    // cursor should be at 'a'
    expect(ids(spine)).toContain('a')
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
