import { describe, expect, it } from 'vitest'
import { Spine, validateGraph } from '../src/spine.ts'
import type { WorkflowGraph } from '../src/types.ts'

function ids(spine: Spine): string[] {
  const next = spine.next()
  return next.nodes?.map(node => node.id) ?? []
}

/** A switch gate: two forward outcomes, neither of them a rewind. */
const switchGraph: WorkflowGraph = {
  name: 'switch',
  start_node: 'decide',
  nodes: {
    decide: { _type: 'GateNode', id: 'decide', evaluator_type: 'fn', reads: [] },
    draft: { _type: 'FnNode', id: 'draft', command: 'echo draft', reads: [], writes: [] },
    debug: { _type: 'FnNode', id: 'debug', command: 'echo debug', reads: [], writes: [] },
    join: { _type: 'FnNode', id: 'join', command: 'true', reads: [], writes: [] },
    end: { _type: 'FnNode', id: 'end', command: 'true', reads: [], writes: [] },
  },
  edges: [
    { source: 'decide', target: 'draft', condition: 'DRAFT' },
    { source: 'decide', target: 'debug', condition: 'DEBUG' },
    { source: 'draft', target: 'join' },
    { source: 'debug', target: 'join' },
    { source: 'join', target: 'end' },
  ],
}

describe('named gate outcomes', () => {
  it('routes to a gate-named forward branch, matching case-insensitively', () => {
    const spine = new Spine(switchGraph)
    expect(ids(spine)).toEqual(['decide'])
    spine.verdict('decide', 'DRAFT')
    expect(ids(spine)).toEqual(['draft'])
    spine.complete('draft')
    expect(ids(spine)).toEqual(['join'])
  })

  it('lists the gate outcomes it accepts', () => {
    const spine = new Spine(switchGraph)
    expect(spine.next().nodes?.[0]?.verdicts).toEqual(['draft', 'debug'])
  })

  it('rejects an outcome the gate does not declare, naming the accepted ones', () => {
    const spine = new Spine(switchGraph)
    expect(() => spine.verdict('decide', 'improve')).toThrow(/accepts draft, debug/)
  })

  it('still treats reloop as a rewind, not a forward branch', () => {
    const spine = new Spine(switchGraph)
    expect(() => spine.verdict('decide', 'reloop')).toThrow(/no reloop edge/)
  })
})

describe('gate-declared reloop cap', () => {
  const graph: WorkflowGraph = {
    name: 'capped',
    start_node: 'work',
    nodes: {
      work: { _type: 'FnNode', id: 'work', command: 'true', reads: [], writes: [] },
      stop: { _type: 'GateNode', id: 'stop', evaluator_type: 'fn', reads: [], max_iterations: 5 },
      exit: { _type: 'FnNode', id: 'exit', command: 'true', reads: [], writes: [] },
    },
    edges: [
      { source: 'work', target: 'stop' },
      { source: 'stop', target: 'work', condition: 'reloop' },
      { source: 'stop', target: 'exit', condition: 'proceed' },
    ],
  }

  it('uses the gate cap rather than the runner default', () => {
    const spine = new Spine(graph, { maxIterations: 1 })
    for (let index = 0; index < 5; index += 1) {
      spine.complete('work')
      spine.verdict('stop', 'reloop')
      expect(spine.next().status).toBe('ready')
    }
    spine.complete('work')
    spine.verdict('stop', 'reloop')
    expect(spine.next().status).toBe('done')
  })

  it('exposes the cap on the gate view', () => {
    const spine = new Spine(graph)
    spine.complete('work')
    expect(spine.next().nodes?.[0]?.max_iterations).toBe(5)
  })
})

describe('multi-node parallel branches', () => {
  const graph: WorkflowGraph = {
    name: 'fan',
    start_node: 'fork',
    nodes: {
      fork: { _type: 'ForkNode', id: 'fork', targets: ['a1', 'b1'] },
      a1: { _type: 'FnNode', id: 'a1', command: 'true', reads: [], writes: [] },
      a2: { _type: 'FnNode', id: 'a2', command: 'true', reads: [], writes: [] },
      b1: { _type: 'FnNode', id: 'b1', command: 'true', reads: [], writes: [] },
      b2: { _type: 'FnNode', id: 'b2', command: 'true', reads: [], writes: [] },
      join: { _type: 'JoinNode', id: 'join', sources: ['a2', 'b2'] },
      end: { _type: 'FnNode', id: 'end', command: 'true', reads: [], writes: [] },
    },
    edges: [
      { source: 'fork', target: 'join' },
      { source: 'a1', target: 'a2' },
      { source: 'b1', target: 'b2' },
      { source: 'join', target: 'end' },
    ],
  }

  it('advances each branch independently and barriers at the join', () => {
    const spine = new Spine(graph)
    spine.complete('fork')
    expect(ids(spine)).toEqual(['a1', 'b1'])
    spine.complete('a1')
    expect(ids(spine)).toEqual(['b1', 'a2'])
    spine.complete('a2')
    expect(ids(spine)).toEqual(['b1'])
    spine.complete('b1')
    expect(ids(spine)).toEqual(['b2'])
    spine.complete('b2')
    expect(ids(spine)).toEqual(['join'])
    spine.complete('join')
    expect(ids(spine)).toEqual(['end'])
  })
})

describe('validateGraph', () => {
  it('accepts a walkable graph', () => {
    expect(validateGraph(switchGraph)).toEqual([])
  })

  it('reports a missing start node and dangling edges', () => {
    const broken: WorkflowGraph = {
      name: 'broken',
      start_node: 'nope',
      nodes: { a: { _type: 'FnNode', id: 'a', command: 'true' } },
      edges: [{ source: 'a', target: 'ghost' }],
    }
    const messages = validateGraph(broken).map(issue => issue.message)
    expect(messages).toContain("start_node 'nope' is not a node")
    expect(messages).toContain("edge target 'ghost' is not a node")
  })

  it('reports a node with two unconditional edges, which has no defined successor', () => {
    const ambiguous: WorkflowGraph = {
      name: 'ambiguous',
      start_node: 'a',
      nodes: {
        a: { _type: 'FnNode', id: 'a', command: 'true' },
        b: { _type: 'FnNode', id: 'b', command: 'true' },
        c: { _type: 'FnNode', id: 'c', command: 'true' },
      },
      edges: [{ source: 'a', target: 'b' }, { source: 'a', target: 'c' }],
    }
    expect(validateGraph(ambiguous).map(issue => issue.message)).toEqual([
      'node has more than one unconditional edge',
    ])
  })

  it('reports a duplicated gate verdict and a non-executable node type', () => {
    const duplicated: WorkflowGraph = {
      name: 'duplicated',
      start_node: 'gate',
      nodes: {
        gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'fn' },
        a: { _type: 'FnNode', id: 'a', command: 'true' },
        b: { _type: 'FnNode', id: 'b', command: 'true' },
        exotic: { _type: 'SubgraphForkNode', id: 'exotic' },
      },
      edges: [
        { source: 'gate', target: 'a', condition: 'proceed' },
        { source: 'gate', target: 'b', condition: 'PROCEED' },
        { source: 'gate', target: 'exotic' },
      ],
    }
    const messages = validateGraph(duplicated).map(issue => issue.message)
    expect(messages).toContain("two edges carry the verdict 'proceed'")
    expect(messages).toContain("node type 'SubgraphForkNode' is not executable")
  })

  it('reports nodes the walk can never reach', () => {
    const orphaned: WorkflowGraph = {
      name: 'orphaned',
      start_node: 'a',
      nodes: {
        a: { _type: 'FnNode', id: 'a', command: 'true' },
        b: { _type: 'FnNode', id: 'b', command: 'true' },
        stranded: { _type: 'FnNode', id: 'stranded', command: 'true' },
      },
      edges: [{ source: 'a', target: 'b' }],
    }
    expect(validateGraph(orphaned)).toEqual([
      { node: 'stranded', message: 'unreachable from start_node' },
    ])
  })
})
