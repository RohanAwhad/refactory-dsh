import { describe, expect, it } from 'vitest'
import { Spine } from '../src/spine.ts'
import type { WorkflowGraph } from '../src/types.ts'

function ids(spine: Spine): string[] {
  const next = spine.next()
  return next.nodes?.map(node => node.id) ?? []
}

describe('linear + gate', () => {
  const graph: WorkflowGraph = {
    name: 't',
    start_node: 'a',
    nodes: {
      a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', writes: [], reads: [] },
      gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'user', gate_prompt: 'ok?', reads: [] },
      b: { _type: 'AgentNode', id: 'b', role: 'builder', prompt_template: 'B', writes: [], reads: [] },
    },
    edges: [
      { source: 'a', target: 'gate' },
      { source: 'gate', target: 'b', condition: 'proceed' },
      { source: 'gate', target: 'a', condition: 'reloop' },
    ],
  }

  it('advances linearly through a gate', () => {
    const spine = new Spine(graph)
    expect(ids(spine)).toEqual(['a'])
    spine.complete('a')
    expect(ids(spine)).toEqual(['gate'])
    spine.verdict('gate', 'proceed')
    expect(ids(spine)).toEqual(['b'])
    spine.complete('b')
    expect(spine.next().status).toBe('done')
  })

  it('reloops with feedback and enforces the iteration cap', () => {
    const spine = new Spine(graph)
    spine.complete('a')
    spine.verdict('gate', 'reloop', 'try harder')
    expect(ids(spine)).toEqual(['a'])
    expect(spine.state().feedback).toEqual({ a: 'try harder' })
    for (let index = 0; index < 4; index += 1) {
      spine.complete('a')
      spine.verdict('gate', 'reloop', 'nope')
    }
    expect(spine.next().status).toBe('done')
    expect(spine.state().gate_verdicts).toEqual({ gate: 'halt' })
  })
})

describe('director expansion', () => {
  const graph: WorkflowGraph = {
    name: 't',
    start_node: 'dir',
    nodes: {
      dir: {
        _type: 'AgentNode', id: 'dir', role: 'ceo', prompt_template: 'plan',
        expand: { plan: 'p.json', role: 'researcher', slug_field: 'slug', prompt_field: 'prompt', writes_template: 'research-{slug}.md' },
      },
      after: { _type: 'FnNode', id: 'after', writes: [], command: '' },
    },
    edges: [{ source: 'dir', target: 'after' }],
  }

  it('expands children then advances to the successor', () => {
    const spine = new Spine(graph)
    spine.expand('dir', [{ slug: 'x', prompt: 'p1' }, { slug: 'y', prompt: 'p2' }])
    expect(ids(spine)).toEqual(['dir__x', 'dir__y'])
    spine.complete('dir__x')
    spine.complete('dir__y')
    expect(ids(spine)).toEqual(['after'])
  })

  it('re-expanding reverts the previous children', () => {
    const spine = new Spine(graph)
    spine.expand('dir', [{ slug: 'x', prompt: 'p1' }])
    spine.complete('dir__x')
    spine.complete('after')
    spine.expand('dir', [{ slug: 'z', prompt: 'p2' }])
    expect(ids(spine)).toEqual(['dir__z'])
    expect(spine.state()).not.toHaveProperty('dir__x')
  })
})

describe('fork / join', () => {
  const graph: WorkflowGraph = {
    name: 't',
    start_node: 'fork',
    nodes: {
      fork: { _type: 'ForkNode', id: 'fork', targets: ['c1', 'c2'] },
      c1: { _type: 'AgentNode', id: 'c1', role: 'reviewer', prompt_template: 'r1', writes: [], reads: [] },
      c2: { _type: 'AgentNode', id: 'c2', role: 'reviewer', prompt_template: 'r2', writes: [], reads: [] },
      join: { _type: 'JoinNode', id: 'join', sources: ['c1', 'c2'] },
      end: { _type: 'FnNode', id: 'end', writes: [], command: '' },
    },
    edges: [
      { source: 'fork', target: 'join' },
      { source: 'join', target: 'end' },
    ],
  }

  it('fans out in parallel then barriers at the join', () => {
    const spine = new Spine(graph)
    expect(ids(spine)).toEqual(['fork'])
    spine.complete('fork')
    expect(ids(spine)).toEqual(['c1', 'c2'])
    spine.complete('c1')
    expect(ids(spine)).toEqual(['c2'])
    spine.complete('c2')
    expect(ids(spine)).toEqual(['join'])
    spine.complete('join')
    expect(ids(spine)).toEqual(['end'])
  })
})

describe('skip mutation', () => {
  const graph: WorkflowGraph = {
    name: 't',
    start_node: 'fork',
    nodes: {
      fork: { _type: 'ForkNode', id: 'fork', targets: ['c1'] },
      c1: { _type: 'AgentNode', id: 'c1', role: 'reviewer', prompt_template: 'r', writes: [], reads: [] },
      join: { _type: 'JoinNode', id: 'join', sources: ['c1'] },
      end: { _type: 'FnNode', id: 'end', writes: [], command: '' },
    },
    edges: [
      { source: 'fork', target: 'join' },
      { source: 'join', target: 'end' },
    ],
  }

  it('skipping a fork hot-removes its subtree and jumps to the join', () => {
    const spine = new Spine(graph)
    spine.skip('fork')
    expect(ids(spine)).toEqual(['join'])
    expect(spine.state().skipped).toContain('c1')
  })
})
