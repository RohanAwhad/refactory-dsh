/**
 * Command correction: extending planRedo/redo for due nodes with commandCorrection,
 * and the redoCommandCorrection method. These tests prove that the command correction
 * path works for any graph topology.
 */
import { describe, expect, it } from 'vitest'
import { Spine } from '../src/spine.ts'
import type { WorkflowGraph } from '../src/types.ts'

function ids(spine: Spine): string[] {
  const next = spine.next()
  return next.nodes?.map(node => node.id) ?? []
}

// ── Linear graph ──────────────────────────────────────────────────────────

function linearGraph(): WorkflowGraph {
  return {
    name: 't', start_node: 'a',
    nodes: {
      a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', reads: [], writes: [] },
      b: { _type: 'AgentNode', id: 'b', role: 'builder', prompt_template: 'B', reads: [], writes: [] },
      c: { _type: 'AgentNode', id: 'c', role: 'builder', prompt_template: 'C', reads: [], writes: [] },
    },
    edges: [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
    ],
  }
}

describe('planRedo with commandCorrection', () => {
  it('accepts a due node (in cursor, not completed)', () => {
    const spine = new Spine(linearGraph())
    // b is not in the cursor yet — it becomes due only after a completes.
    spine.complete('a')
    expect(ids(spine)).toEqual(['b'])
    // b is due (in cursor), not completed
    const plan = spine.planRedo('b', { commandCorrection: true })
    expect(plan.nodes).toContain('b')
    expect(plan.nodes).toContain('c')
    expect(plan.start).toBe('b')
  })

  it('without commandCorrection still refuses a due node', () => {
    const spine = new Spine(linearGraph())
    spine.complete('a')
    expect(() => spine.planRedo('b'))
      .toThrow(/node b has not completed, so there is nothing to redo/)
  })

  it('accepts a completed node too', () => {
    const spine = new Spine(linearGraph())
    spine.complete('a')
    spine.complete('b')
    const plan = spine.planRedo('b', { commandCorrection: true })
    expect(plan.nodes).toContain('b')
    expect(plan.nodes).toContain('c')
  })

  it('refuses a node that is neither completed nor due', () => {
    const spine = new Spine(linearGraph())
    // c is not in cursor and not completed
    expect(() => spine.planRedo('c', { commandCorrection: true }))
      .toThrow(/node c is neither completed nor due/)
  })
})

describe('redoCommandCorrection', () => {
  it('re-dispatches a due node', () => {
    const spine = new Spine(linearGraph())
    spine.complete('a')
    expect(ids(spine)).toEqual(['b'])
    const next = spine.redoCommandCorrection('b')
    expect(next.status).toBe('ready')
    expect(ids(spine)).toEqual(['b'])
    expect(spine.state().log).toEqual(expect.arrayContaining([
      expect.stringMatching(/^command correction redid b/),
    ]))
  })

  it('without commandCorrection, redo refuses a due node', () => {
    const spine = new Spine(linearGraph())
    spine.complete('a')
    expect(() => spine.redo('b')).toThrow(/node b has not completed/)
  })
})

// ── Fork graph ────────────────────────────────────────────────────────────

function forkGraph(): WorkflowGraph {
  return {
    name: 't', start_node: 'start',
    nodes: {
      start: { _type: 'AgentNode', id: 'start', role: 'builder', prompt_template: 'S', reads: [], writes: [] },
      fork: { _type: 'ForkNode', id: 'fork', targets: ['branch_a', 'branch_b'] },
      branch_a: { _type: 'AgentNode', id: 'branch_a', role: 'builder', prompt_template: 'BA', reads: [], writes: [] },
      branch_b: { _type: 'AgentNode', id: 'branch_b', role: 'builder', prompt_template: 'BB', reads: [], writes: [] },
      join: { _type: 'JoinNode', id: 'join', sources: ['branch_a', 'branch_b'] },
      end: { _type: 'AgentNode', id: 'end', role: 'builder', prompt_template: 'E', reads: [], writes: [] },
    },
    edges: [
      { source: 'start', target: 'fork' },
      { source: 'fork', target: 'join' },
      { source: 'join', target: 'end' },
    ],
  }
}

describe('command correction inside a fork', () => {
  it('accepts a due fork branch node', () => {
    const spine = new Spine(forkGraph())
    spine.complete('start')
    spine.complete('fork')
    // branch_a and branch_b are both due in cursor
    expect(ids(spine)).toContain('branch_a')
    const plan = spine.planRedo('branch_a', { commandCorrection: true })
    expect(plan.nodes).toContain('branch_a')
    expect(plan.start).toBe('branch_a')
  })

  it('redo of a due fork branch re-dispatches it', () => {
    const spine = new Spine(forkGraph())
    spine.complete('start')
    spine.complete('fork')
    spine.redoCommandCorrection('branch_a')
    expect(ids(spine)).toContain('branch_a')
  })
})

// ── Gate graph: commandCorrection allows a gate node ──────────────────────

function gateGraph(): WorkflowGraph {
  return {
    name: 't', start_node: 'a',
    nodes: {
      a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', reads: [], writes: [] },
      check: { _type: 'GateNode', id: 'check', evaluator_type: 'fn', evaluator_command: 'true', reads: [] },
      b: { _type: 'AgentNode', id: 'b', role: 'builder', prompt_template: 'B', reads: [], writes: [] },
    },
    edges: [
      { source: 'a', target: 'check' },
      { source: 'check', target: 'a', condition: 'reloop' },
      { source: 'check', target: 'b', condition: 'proceed' },
    ],
  }
}

describe('command correction for a gate node', () => {
  it('commandCorrection allows planRedo on a completed gate', () => {
    const spine = new Spine(gateGraph())
    spine.complete('a')
    spine.verdict('check', 'proceed')
    spine.complete('b')
    // Without commandCorrection, planRedo on 'check' would redirect to upstream
    expect(() => spine.planRedo('check')).toThrow(/check is a gate/)
    // With commandCorrection, it's allowed
    const plan = spine.planRedo('check', { commandCorrection: true })
    expect(plan.nodes).toContain('check')
    expect(plan.nodes).toContain('b')
  })

  it('gate still refuses without commandCorrection', () => {
    const spine = new Spine(gateGraph())
    spine.complete('a')
    spine.verdict('check', 'proceed')
    spine.complete('b')
    expect(() => spine.planRedo('check')).toThrow(/check is a gate; replay a instead/)
  })
})

// ── Data body graph ───────────────────────────────────────────────────────

function dataGraph(): WorkflowGraph {
  return {
    name: 't', start_node: 'data',
    nodes: {
      data: { _type: 'DataNode', id: 'data', subgraph_entry: 'inner', subgraph_exit: 'inner', reads: [], writes: [] },
      inner: { _type: 'AgentNode', id: 'inner', role: 'builder', prompt_template: 'I', reads: [], writes: [] },
      after: { _type: 'AgentNode', id: 'after', role: 'builder', prompt_template: 'A', reads: [], writes: [] },
    },
    edges: [
      { source: 'data', target: 'inner' },
      { source: 'data', target: 'after' },
    ],
  }
}

describe('command correction with data nodes', () => {
  it('data node itself still refuses (iterates items)', () => {
    const spine = new Spine(dataGraph())
    expect(() => spine.planRedo('data', { commandCorrection: true }))
      .toThrow(/node data iterates its items/)
  })
})

// ── Nested fork graph ─────────────────────────────────────────────────────

function nestedForkGraph(): WorkflowGraph {
  return {
    name: 't', start_node: 'outer_fork',
    nodes: {
      outer_fork: { _type: 'ForkNode', id: 'outer_fork', targets: ['inner_fork', 'branch_c'] },
      inner_fork: { _type: 'ForkNode', id: 'inner_fork', targets: ['branch_a', 'branch_b'] },
      branch_a: { _type: 'AgentNode', id: 'branch_a', role: 'builder', prompt_template: 'A', reads: [], writes: [] },
      branch_b: { _type: 'AgentNode', id: 'branch_b', role: 'builder', prompt_template: 'B', reads: [], writes: [] },
      branch_c: { _type: 'AgentNode', id: 'branch_c', role: 'builder', prompt_template: 'C', reads: [], writes: [] },
      inner_join: { _type: 'JoinNode', id: 'inner_join', sources: ['branch_a', 'branch_b'] },
      outer_join: { _type: 'JoinNode', id: 'outer_join', sources: ['inner_join', 'branch_c'] },
      end: { _type: 'AgentNode', id: 'end', role: 'builder', prompt_template: 'E', reads: [], writes: [] },
    },
    edges: [
      { source: 'outer_fork', target: 'outer_join' },
      { source: 'inner_fork', target: 'inner_join' },
      { source: 'outer_join', target: 'end' },
    ],
  }
}

describe('command correction in a nested fork', () => {
  it('accepts a due node inside a nested fork', () => {
    const spine = new Spine(nestedForkGraph())
    spine.complete('outer_fork')
    spine.complete('inner_fork')
    // branch_a, branch_b, branch_c are all due
    expect(ids(spine)).toContain('branch_a')
    const plan = spine.planRedo('branch_a', { commandCorrection: true })
    expect(plan.nodes).toContain('branch_a')
    expect(plan.start).toBe('branch_a')
  })
})
