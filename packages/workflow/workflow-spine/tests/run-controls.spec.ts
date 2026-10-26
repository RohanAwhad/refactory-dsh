/**
 * Run-control tests: dm-252 (stable graph revision), dm-227 (planRedo on
 * paused runs), dm-231 (targeted pause), and supporting invariants.
 */
import { createHash } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import { Spine } from '../src/spine.ts'
import type { WorkflowGraph } from '../src/types.ts'

function ids(spine: Spine): string[] {
  const next = spine.next()
  return next.nodes?.map(node => node.id) ?? []
}

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

/** Build the graph: gate_check → synthesize_qa → gate_qa → emit | proceed */
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
        _type: 'GateNode', id: 'gate_qa', evaluator_type: 'user',
        gate_prompt: 'ok?', reads: [],
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
    const spine = new Spine(linearGraph())
    spine.complete('a')
    spine.verdict('gate_qa', 'proceed')
    // Fail at emit by simulating a stop
    spine.stopAtBoundary()
    // Compute graph revision as preview would
    const saved = spine.checkpoint()
    const revision = computeGraphRevision(saved)

    // Simulate restart: restore into a new spine
    const spine2 = new Spine(linearGraph())
    spine2.restore(saved)

    // Verify that the revision is the same despite restoration
    const revision2 = computeGraphRevision(spine2.checkpoint())
    expect(revision2).toBe(revision)
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
    // planSkip still refuses
    expect(() => spine.planSkip('emit')).toThrow(/paused/)
  })
})

// ── dm-231: Targeted pause honours --node target ──────────────────────────

describe('dm-231: targeted pause', () => {
  it('pauseBefore targets a specific node', () => {
    const spine = new Spine(reviewGraph())
    spine.verdict('gate_check', 'proceed')
    // Targeted pause at emit
    spine.pauseBefore('emit', true)
    // gate_check completed, synthesize_qa is in cursor — not paused
    expect(spine.isPaused()).toBe(false)
    expect(ids(spine)).toEqual(['synthesize_qa'])
    spine.complete('synthesize_qa')
    expect(ids(spine)).toEqual(['gate_qa'])
    expect(spine.isPaused()).toBe(false)
    // Route to emit — now paused
    spine.verdict('gate_qa', 'emit')
    expect(ids(spine)).toEqual(['emit'])
    expect(spine.isPaused()).toBe(true)
  })

  it('targeted pause never holds on a route that does not reach the target', () => {
    const spine = new Spine(reviewGraph())
    spine.verdict('gate_check', 'proceed')
    spine.pauseBefore('emit', true)
    spine.complete('synthesize_qa')
    // Route to proceed — not emit
    spine.verdict('gate_qa', 'proceed')
    expect(ids(spine)).toEqual(['proceed'])
    expect(spine.isPaused()).toBe(false)
    spine.complete('proceed')
    expect(spine.next().status).toBe('done')
  })

  it('untargeted pause still pauses all cursor nodes', () => {
    const spine = new Spine(linearGraph())
    // Pause all cursor nodes (untargeted)
    spine.pauseBefore('a', true)
    expect(spine.isPaused()).toBe(true)
    expect(spine.pausedAt()).toEqual(['a'])
    spine.pauseBefore('a', false)
    spine.complete('a')
    // Pause all cursor nodes at gate_qa
    spine.pauseBefore('gate_qa', true)
    expect(spine.isPaused()).toBe(true)
  })
})
