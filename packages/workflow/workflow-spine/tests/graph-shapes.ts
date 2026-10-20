/**
 * Named graph shapes for the invariant test suite. Each shape is a hand-built
 * WorkflowGraph with metadata the driver needs to run it.
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { WorkflowGraph } from '../src/types.ts'

const __dirname = dirname(fileURLToPath(import.meta.url))

/** Metadata the driver uses to run a shape. */
export interface ShapeEntry {
  name: string
  graph: WorkflowGraph
  /** Director node ids → expand entries the driver passes. */
  directors: Record<string, Array<Record<string, unknown>>>
  /** Data node ids → item count for startPass. */
  dataPasses: Record<string, number>
  /** Gates whose reloop edge the default policy fires once. */
  gatesWithReloop: string[]
  /** Fixed verdicts for non-reloop gate routing (e.g. alternativeEnds paths). */
  fixedVerdicts: Record<string, string>
  /** When true, use seeded random orderings instead of full enumeration. */
  large: boolean
}

// ─── Shape 1: linear ─────────────────────────────────────────────

export const linear: ShapeEntry = {
  name: 'linear',
  graph: {
    name: 'linear', start_node: 'a',
    nodes: {
      a: { _type: 'AgentNode', id: 'a', role: 'r', prompt_template: '', writes: [], reads: [] },
      b: { _type: 'AgentNode', id: 'b', role: 'r', prompt_template: '', writes: [], reads: [] },
      c: { _type: 'AgentNode', id: 'c', role: 'r', prompt_template: '', writes: [], reads: [] },
    },
    edges: [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }],
  },
  directors: {}, dataPasses: {}, gatesWithReloop: [], fixedVerdicts: {}, large: false,
}

// ─── Shape 2: forkSingleBranch (QA shape) ────────────────────────

export const forkSingleBranch: ShapeEntry = {
  name: 'forkSingleBranch',
  graph: {
    name: 'forkSingleBranch', start_node: 'start',
    nodes: {
      start: { _type: 'AgentNode', id: 'start', role: 'r', prompt_template: '', writes: [], reads: [] },
      fork:  { _type: 'ForkNode',  id: 'fork', targets: ['hc', 'cr', 'qd'] },
      hc:    { _type: 'AgentNode', id: 'hc', role: 'r', prompt_template: '', writes: [], reads: [] },
      cr:    { _type: 'AgentNode', id: 'cr', role: 'r', prompt_template: '', writes: [], reads: [] },
      qd:    { _type: 'AgentNode', id: 'qd', role: 'r', prompt_template: '', writes: [], reads: [] },
      join:  { _type: 'JoinNode',  id: 'join', sources: ['hc', 'cr', 'qd'] },
      synth: { _type: 'AgentNode', id: 'synth', role: 'r', prompt_template: '', writes: [], reads: [] },
      gate:  { _type: 'GateNode',  id: 'gate', evaluator_type: 'fn', evaluator_command: 'true', reads: [] },
      end:   { _type: 'AgentNode', id: 'end', role: 'r', prompt_template: '', writes: [], reads: [] },
    },
    edges: [
      { source: 'start', target: 'fork' },
      { source: 'fork',  target: 'join' },
      { source: 'join',  target: 'synth' },
      { source: 'synth', target: 'gate' },
      { source: 'gate',  target: 'synth', condition: 'reloop' },
      { source: 'gate',  target: 'end',   condition: 'proceed' },
    ],
  },
  directors: {}, dataPasses: {}, gatesWithReloop: ['gate'], fixedVerdicts: {}, large: false,
}

// ─── Shape 3: forkMultiBranch ────────────────────────────────────

export const forkMultiBranch: ShapeEntry = {
  name: 'forkMultiBranch',
  graph: {
    name: 'forkMultiBranch', start_node: 'start',
    nodes: {
      start: { _type: 'AgentNode', id: 'start', role: 'r', prompt_template: '', writes: [], reads: [] },
      fork:  { _type: 'ForkNode', id: 'fork', targets: ['a1', 'b1'] },
      a1:    { _type: 'AgentNode', id: 'a1', role: 'r', prompt_template: '', writes: [], reads: [] },
      a2:    { _type: 'AgentNode', id: 'a2', role: 'r', prompt_template: '', writes: [], reads: [] },
      b1:    { _type: 'AgentNode', id: 'b1', role: 'r', prompt_template: '', writes: [], reads: [] },
      b2:    { _type: 'AgentNode', id: 'b2', role: 'r', prompt_template: '', writes: [], reads: [] },
      join:  { _type: 'JoinNode', id: 'join', sources: ['a2', 'b2'] },
      end:   { _type: 'AgentNode', id: 'end', role: 'r', prompt_template: '', writes: [], reads: [] },
    },
    edges: [
      { source: 'start', target: 'fork' }, { source: 'fork', target: 'join' },
      { source: 'a1', target: 'a2' }, { source: 'b1', target: 'b2' },
      { source: 'join', target: 'end' },
    ],
  },
  directors: {}, dataPasses: {}, gatesWithReloop: [], fixedVerdicts: {}, large: false,
}

// ─── Shape 4: nestedFork ─────────────────────────────────────────

export const nestedFork: ShapeEntry = {
  name: 'nestedFork',
  graph: {
    name: 'nestedFork', start_node: 'start',
    nodes: {
      start:       { _type: 'AgentNode', id: 'start', role: 'r', prompt_template: '', writes: [], reads: [] },
      outer_fork:  { _type: 'ForkNode', id: 'outer_fork', targets: ['inner_fork', 'b'] },
      inner_fork:  { _type: 'ForkNode', id: 'inner_fork', targets: ['x', 'y'] },
      x:           { _type: 'AgentNode', id: 'x', role: 'r', prompt_template: '', writes: [], reads: [] },
      y:           { _type: 'AgentNode', id: 'y', role: 'r', prompt_template: '', writes: [], reads: [] },
      inner_join:  { _type: 'JoinNode', id: 'inner_join', sources: ['x', 'y'] },
      after_inner: { _type: 'AgentNode', id: 'after_inner', role: 'r', prompt_template: '', writes: [], reads: [] },
      b:           { _type: 'AgentNode', id: 'b', role: 'r', prompt_template: '', writes: [], reads: [] },
      outer_join:  { _type: 'JoinNode', id: 'outer_join', sources: ['after_inner', 'b'] },
      end:         { _type: 'AgentNode', id: 'end', role: 'r', prompt_template: '', writes: [], reads: [] },
    },
    edges: [
      { source: 'start', target: 'outer_fork' },
      { source: 'outer_fork', target: 'outer_join' },
      { source: 'inner_fork', target: 'inner_join' },
      { source: 'inner_join', target: 'after_inner' },
      { source: 'outer_join', target: 'end' },
    ],
  },
  directors: {}, dataPasses: {}, gatesWithReloop: [], fixedVerdicts: {}, large: false,
}

// ─── Shape 5: gateInsideBranch ───────────────────────────────────

export const gateInsideBranch: ShapeEntry = {
  name: 'gateInsideBranch',
  graph: {
    name: 'gateInsideBranch', start_node: 'start',
    nodes: {
      start:       { _type: 'AgentNode', id: 'start', role: 'r', prompt_template: '', writes: [], reads: [] },
      fork:        { _type: 'ForkNode', id: 'fork', targets: ['branch_work', 'other'] },
      branch_work: { _type: 'AgentNode', id: 'branch_work', role: 'r', prompt_template: '', writes: [], reads: [] },
      branch_gate: { _type: 'GateNode', id: 'branch_gate', evaluator_type: 'fn', evaluator_command: 'true', reads: [] },
      branch_end:  { _type: 'AgentNode', id: 'branch_end', role: 'r', prompt_template: '', writes: [], reads: [] },
      other:       { _type: 'AgentNode', id: 'other', role: 'r', prompt_template: '', writes: [], reads: [] },
      join:        { _type: 'JoinNode', id: 'join', sources: ['branch_end', 'other'] },
      end:         { _type: 'AgentNode', id: 'end', role: 'r', prompt_template: '', writes: [], reads: [] },
    },
    edges: [
      { source: 'start', target: 'fork' },
      { source: 'fork', target: 'join' },
      { source: 'branch_work', target: 'branch_gate' },
      { source: 'branch_gate', target: 'branch_work', condition: 'reloop' },
      { source: 'branch_gate', target: 'branch_end', condition: 'proceed' },
      { source: 'join', target: 'end' },
    ],
  },
  directors: {}, dataPasses: {}, gatesWithReloop: ['branch_gate'], fixedVerdicts: {}, large: false,
}

// ─── Shape 6: directorTopLevel ───────────────────────────────────

export const directorTopLevel: ShapeEntry = {
  name: 'directorTopLevel',
  graph: {
    name: 'directorTopLevel', start_node: 'dir',
    nodes: {
      dir: {
        _type: 'AgentNode', id: 'dir', role: 'ceo', prompt_template: 'plan',
        writes: [], reads: [],
        expand: { plan: 'p.json', role: 'researcher', slug_field: 'slug',
                  prompt_field: 'prompt', writes_template: 'out-{slug}.md' },
      },
      after: { _type: 'AgentNode', id: 'after', role: 'r', prompt_template: '', writes: [], reads: [] },
    },
    edges: [{ source: 'dir', target: 'after' }],
  },
  directors: { dir: [{ slug: 'c1', prompt: 'p1' }, { slug: 'c2', prompt: 'p2' }] },
  dataPasses: {}, gatesWithReloop: [], fixedVerdicts: {}, large: false,
}

// ─── Shape 7: directorInsideBranch ───────────────────────────────

export const directorInsideBranch: ShapeEntry = {
  name: 'directorInsideBranch',
  graph: {
    name: 'directorInsideBranch', start_node: 'start',
    nodes: {
      start:        { _type: 'AgentNode', id: 'start', role: 'r', prompt_template: '', writes: [], reads: [] },
      fork:         { _type: 'ForkNode', id: 'fork', targets: ['dir_branch', 'other_branch'] },
      dir_branch: {
        _type: 'AgentNode', id: 'dir_branch', role: 'ceo', prompt_template: 'plan',
        writes: [], reads: [],
        expand: { plan: 'p.json', role: 'researcher', slug_field: 'slug',
                  prompt_field: 'prompt', writes_template: 'out-{slug}.md' },
      },
      other_branch: { _type: 'AgentNode', id: 'other_branch', role: 'r', prompt_template: '', writes: [], reads: [] },
      join:         { _type: 'JoinNode', id: 'join', sources: ['dir_branch', 'other_branch'] },
      end:          { _type: 'AgentNode', id: 'end', role: 'r', prompt_template: '', writes: [], reads: [] },
    },
    edges: [
      { source: 'start', target: 'fork' },
      { source: 'fork', target: 'join' },
      { source: 'join', target: 'end' },
    ],
  },
  directors: { dir_branch: [{ slug: 'c1', prompt: 'p1' }, { slug: 'c2', prompt: 'p2' }] },
  dataPasses: {}, gatesWithReloop: [], fixedVerdicts: {}, large: false,
}

// ─── Shape 8: dataTopLevel ───────────────────────────────────────

export const dataTopLevel: ShapeEntry = {
  name: 'dataTopLevel',
  graph: {
    name: 'dataTopLevel', start_node: 'data',
    nodes: {
      data:   { _type: 'DataNode', id: 'data', subgraph_entry: 'work', subgraph_exit: 'd_exit', reads: [], writes: [] },
      work:   { _type: 'AgentNode', id: 'work', role: 'r', prompt_template: '', reads: [], writes: [] },
      d_exit: { _type: 'AgentNode', id: 'd_exit', role: 'r', prompt_template: '', reads: [], writes: [] },
      after:  { _type: 'AgentNode', id: 'after', role: 'r', prompt_template: '', reads: [], writes: [] },
    },
    edges: [
      { source: 'data', target: 'after' },
      { source: 'work', target: 'd_exit' },
    ],
  },
  directors: {}, dataPasses: { data: 2 }, gatesWithReloop: [], fixedVerdicts: {}, large: false,
}

// ─── Shape 9: dataInsideBranch ───────────────────────────────────

export const dataInsideBranch: ShapeEntry = {
  name: 'dataInsideBranch',
  graph: {
    name: 'dataInsideBranch', start_node: 'start',
    nodes: {
      start:        { _type: 'AgentNode', id: 'start', role: 'r', prompt_template: '', writes: [], reads: [] },
      fork:         { _type: 'ForkNode', id: 'fork', targets: ['data_branch', 'other_branch'] },
      data_branch:  { _type: 'DataNode', id: 'data_branch', subgraph_entry: 'd_work', subgraph_exit: 'd_exit', reads: [], writes: [] },
      d_work:       { _type: 'AgentNode', id: 'd_work', role: 'r', prompt_template: '', reads: [], writes: [] },
      d_exit:       { _type: 'AgentNode', id: 'd_exit', role: 'r', prompt_template: '', reads: [], writes: [] },
      other_branch: { _type: 'AgentNode', id: 'other_branch', role: 'r', prompt_template: '', writes: [], reads: [] },
      join:         { _type: 'JoinNode', id: 'join', sources: ['data_branch', 'other_branch'] },
      end:          { _type: 'AgentNode', id: 'end', role: 'r', prompt_template: '', writes: [], reads: [] },
    },
    edges: [
      { source: 'start', target: 'fork' },
      { source: 'fork', target: 'join' },
      { source: 'd_work', target: 'd_exit' },
      { source: 'join', target: 'end' },
    ],
  },
  directors: {}, dataPasses: { data_branch: 2 }, gatesWithReloop: [], fixedVerdicts: {}, large: false,
}

// ─── Shape 10: reloopBackEdge ────────────────────────────────────

export const reloopBackEdge: ShapeEntry = {
  name: 'reloopBackEdge',
  graph: {
    name: 'reloopBackEdge', start_node: 'emit',
    nodes: {
      emit:        { _type: 'AgentNode', id: 'emit', role: 'r', prompt_template: '', writes: [], reads: [] },
      materialise: { _type: 'FnNode',    id: 'materialise', command: 'true', reads: [], writes: [] },
      check_gate:  { _type: 'GateNode',  id: 'check_gate', evaluator_type: 'fn', evaluator_command: 'true', reads: [] },
      done:        { _type: 'AgentNode', id: 'done', role: 'r', prompt_template: '', writes: [], reads: [] },
    },
    edges: [
      { source: 'emit', target: 'materialise' },
      { source: 'materialise', target: 'check_gate' },
      { source: 'check_gate', target: 'emit', condition: 'reloop' },
      { source: 'check_gate', target: 'done', condition: 'proceed' },
    ],
  },
  directors: {}, dataPasses: {}, gatesWithReloop: ['check_gate'], fixedVerdicts: {}, large: false,
}

// ─── Shape 11: alternativeEnds (three variants) ──────────────────

const alternativeEndsGraph: WorkflowGraph = {
  name: 'alternativeEnds', start_node: 'start',
  nodes: {
    start: { _type: 'AgentNode', id: 'start', role: 'r', prompt_template: '', writes: [], reads: [] },
    gate:  { _type: 'GateNode', id: 'gate', evaluator_type: 'fn', evaluator_command: 'true', reads: [] },
    end_a: { _type: 'AgentNode', id: 'end_a', role: 'r', prompt_template: '', writes: [], reads: [] },
    end_b: { _type: 'AgentNode', id: 'end_b', role: 'r', prompt_template: '', writes: [], reads: [] },
    end_c: { _type: 'AgentNode', id: 'end_c', role: 'r', prompt_template: '', writes: [], reads: [] },
  },
  edges: [
    { source: 'start', target: 'gate' },
    { source: 'gate', target: 'end_a', condition: 'proceed' },
    { source: 'gate', target: 'end_b', condition: 'draft' },
    { source: 'gate', target: 'end_c', condition: 'halt' },
  ],
}

export const alternativeEnds_proceed: ShapeEntry = {
  name: 'alternativeEnds_proceed',
  graph: alternativeEndsGraph,
  directors: {}, dataPasses: {}, gatesWithReloop: [],
  fixedVerdicts: { gate: 'proceed' }, large: false,
}

export const alternativeEnds_draft: ShapeEntry = {
  name: 'alternativeEnds_draft',
  graph: alternativeEndsGraph,
  directors: {}, dataPasses: {}, gatesWithReloop: [],
  fixedVerdicts: { gate: 'draft' }, large: false,
}

export const alternativeEnds_halt: ShapeEntry = {
  name: 'alternativeEnds_halt',
  graph: alternativeEndsGraph,
  directors: {}, dataPasses: {}, gatesWithReloop: [],
  fixedVerdicts: { gate: 'halt' }, large: false,
}

// ─── Shape 12: designGraph (from fixture) ────────────────────────

const designFixture = JSON.parse(
  readFileSync(join(__dirname, 'fixtures', 'design-graph.json'), 'utf8'),
) as WorkflowGraph

export const designGraph: ShapeEntry = {
  name: 'designGraph',
  graph: designFixture,
  directors: {}, dataPasses: {},
  gatesWithReloop: ['gate_research', 'gate_pitfalls', 'gate_design_review', 'gate_strategy', 'check', 'gate_verification', 'gate_qa', 'gate_overwatch'],
  fixedVerdicts: {}, large: true,
}

// ─── Shape 13: crossContainerReloop (constructor-refused) ────────

/**
 * A graph whose gate inside a fork branch has a reloop edge to a node outside
 * the fork body. The Spine constructor refuses this at load, and validateGraph
 * returns an issue for it. NOT included in the invariant shapes array.
 */
export const crossContainerReloopGraph: WorkflowGraph = {
  name: 'crossContainerReloop', start_node: 'start',
  nodes: {
    start:       { _type: 'AgentNode', id: 'start', role: 'r', prompt_template: '', writes: [], reads: [] },
    fork:        { _type: 'ForkNode', id: 'fork', targets: ['branch_work', 'other'] },
    branch_work: { _type: 'AgentNode', id: 'branch_work', role: 'r', prompt_template: '', writes: [], reads: [] },
    branch_gate: { _type: 'GateNode', id: 'branch_gate', evaluator_type: 'fn', evaluator_command: 'true', reads: [] },
    branch_end:  { _type: 'AgentNode', id: 'branch_end', role: 'r', prompt_template: '', writes: [], reads: [] },
    other:       { _type: 'AgentNode', id: 'other', role: 'r', prompt_template: '', writes: [], reads: [] },
    join:        { _type: 'JoinNode', id: 'join', sources: ['branch_end', 'other'] },
    end:         { _type: 'AgentNode', id: 'end', role: 'r', prompt_template: '', writes: [], reads: [] },
  },
  edges: [
    { source: 'start', target: 'fork' },
    { source: 'fork', target: 'join' },
    { source: 'branch_work', target: 'branch_gate' },
    { source: 'branch_gate', target: 'start', condition: 'reloop' },
    { source: 'branch_gate', target: 'branch_end', condition: 'proceed' },
    { source: 'join', target: 'end' },
  ],
}

// ─── All shapes ──────────────────────────────────────────────────

export const shapes: ShapeEntry[] = [
  linear,
  forkSingleBranch,
  forkMultiBranch,
  nestedFork,
  gateInsideBranch,
  directorTopLevel,
  directorInsideBranch,
  dataTopLevel,
  dataInsideBranch,
  reloopBackEdge,
  alternativeEnds_proceed,
  alternativeEnds_draft,
  alternativeEnds_halt,
  designGraph,
]
