/**
 * Driver that runs a Spine to completion in a chosen node-completion order,
 * plus permutation enumeration and seeded random ordering.
 */
import { Spine } from '../src/spine.ts'
import type { WorkflowGraph } from '../src/types.ts'
import type { SpineNext, NodeView } from '../src/spine.ts'
import type { ShapeEntry } from './graph-shapes.ts'

/** One action the driver took. */
export interface DriveEvent {
  action: 'complete' | 'verdict' | 'expand' | 'startPass' | 'endPass'
  node: string
  detail?: string
}

/** Result of one drive-to-end run. */
export interface DriveResult {
  events: DriveEvent[]
  completedOrder: string[]
  everDue: Set<string>
  final: SpineNext
  spine: Spine
  graph: WorkflowGraph
}

/** Decides what verdict to issue for a gate. */
export type GatePolicy = (gateId: string, verdicts: string[], encounterIndex: number) => string

/** Create the default gate policy from a shape's metadata. */
export function defaultGatePolicy(shape: ShapeEntry): GatePolicy {
  const encounters: Record<string, number> = {}
  return (gateId: string, _verdicts: string[], _ei: number): string => {
    const count = encounters[gateId] ?? 0
    encounters[gateId] = count + 1
    if (shape.fixedVerdicts[gateId] !== undefined) return shape.fixedVerdicts[gateId]!
    if (shape.gatesWithReloop.includes(gateId) && count === 0) return 'reloop'
    return 'proceed'
  }
}

/** Drive an existing Spine to completion. */
export function driveExistingToEnd(
  spine: Spine,
  shape: ShapeEntry,
  opts?: {
    orderFn?: (due: NodeView[]) => NodeView[]
    gatePolicy?: GatePolicy
  },
): DriveResult {
  const events: DriveEvent[] = []
  const completedOrder: string[] = []
  const everDue = new Set<string>()
  const policy = opts?.gatePolicy ?? defaultGatePolicy(shape)
  const gateEncounters: Record<string, number> = {}

  const graph = JSON.parse(JSON.stringify(
    (spine.checkpoint() as { graph: WorkflowGraph }).graph,
  )) as WorkflowGraph

  for (let step = 0; step < 500; step++) {
    const next = spine.next()
    if (next.status === 'done') {
      return { events, completedOrder, everDue, final: next, spine, graph }
    }

    // Handle finished data pass
    if (next.pass?.finished === true) {
      spine.endPass()
      events.push({ action: 'endPass', node: next.pass.node })
      continue
    }

    // Record all due nodes
    for (const n of next.nodes) everDue.add(n.id)

    // Apply ordering
    const ordered = opts?.orderFn ? opts.orderFn([...next.nodes]) : [...next.nodes]

    // Process the first node that is still due
    let processed = false
    for (const node of ordered) {
      // Re-check it's still due
      const recheck = spine.next()
      if (recheck.status === 'done') {
        return { events, completedOrder, everDue, final: recheck, spine, graph }
      }
      if (!recheck.nodes.some(n => n.id === node.id)) continue

      const nodeType = node._type

      if (nodeType === 'GateNode') {
        const encounter = gateEncounters[node.id] ?? 0
        gateEncounters[node.id] = encounter + 1
        const verdict = policy(node.id, node.verdicts ?? [], encounter)
        spine.verdict(node.id, verdict, 'test')
        events.push({ action: 'verdict', node: node.id, detail: verdict })
        completedOrder.push(node.id)
        processed = true
        break
      }

      if (node.expand !== undefined && shape.directors[node.id] !== undefined) {
        spine.expand(node.id, shape.directors[node.id]!)
        events.push({ action: 'expand', node: node.id, detail: String(shape.directors[node.id]!.length) })
        completedOrder.push(node.id)
        processed = true
        break
      }

      if (nodeType === 'DataNode' && shape.dataPasses[node.id] !== undefined) {
        spine.startPass(node.id, shape.dataPasses[node.id]!)
        events.push({ action: 'startPass', node: node.id })
        processed = true
        break
      }

      // AgentNode, FnNode, Study, LLMNode, ForkNode, JoinNode, SelectionNode
      spine.complete(node.id)
      events.push({ action: 'complete', node: node.id })
      completedOrder.push(node.id)
      processed = true
      break
    }

    if (!processed) {
      throw new Error(`step ${step}: no processable node found among ${ordered.map(n => n.id).join(', ')}`)
    }
  }

  throw new Error('drive overflow: 500 steps exceeded')
}

/** Deep-copy a graph so Spine mutations (expand, etc.) don't leak. */
export function deepCopyGraph(graph: WorkflowGraph): WorkflowGraph {
  return JSON.parse(JSON.stringify(graph)) as WorkflowGraph
}

/** Create a fresh Spine and drive to completion. */
export function driveToEnd(
  graph: WorkflowGraph,
  shape: ShapeEntry,
  opts?: {
    orderFn?: (due: NodeView[]) => NodeView[]
    gatePolicy?: GatePolicy
  },
): DriveResult {
  const spine = new Spine(deepCopyGraph(graph))
  return driveExistingToEnd(spine, shape, opts)
}

/** mulberry32 PRNG. */
function mulberry32(seed: number): () => number {
  let state = seed | 0
  return () => {
    state = (state + 0x6D2B79F5) | 0
    let t = Math.imul(state ^ (state >>> 15), 1 | state)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** Fisher-Yates shuffle using a PRNG. */
function shuffleWith(arr: NodeView[], rng: () => number): NodeView[] {
  const result = [...arr]
  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1))
    const tmp = result[i]!
    result[i] = result[j]!
    result[j] = tmp
  }
  return result
}

/** Generate all permutations of an array. */
function permutations<T>(arr: T[]): T[][] {
  if (arr.length <= 1) return [arr]
  const result: T[][] = []
  for (let i = 0; i < arr.length; i++) {
    const rest = [...arr.slice(0, i), ...arr.slice(i + 1)]
    for (const perm of permutations(rest)) {
      result.push([arr[i]!, ...perm])
    }
  }
  return result
}

/**
 * Enumerate all completion orderings for small shapes.
 *
 * Uses a probe-based approach: discover choice points, then generate all
 * permutation orderings. Cap at 5000 total orderings.
 */
export function allOrders(shape: ShapeEntry): DriveResult[] {
  // First, probe to discover choice points
  interface ChoicePoint { step: number; count: number }
  const choicePoints: ChoicePoint[] = []

  // Run once with identity ordering, recording choice points
  {
    let cpIndex = 0
    const spine = new Spine(deepCopyGraph(shape.graph))
    const policy = defaultGatePolicy(shape)
    const gateEncounters: Record<string, number> = {}

    for (let step = 0; step < 500; step++) {
      const next = spine.next()
      if (next.status === 'done') break
      if (next.pass?.finished === true) {
        spine.endPass()
        continue
      }

      const nodes = [...next.nodes].sort((a, b) => a.id.localeCompare(b.id))

      if (nodes.length > 1) {
        choicePoints.push({ step: cpIndex, count: nodes.length })
      }
      cpIndex++

      const node = nodes[0]!
      const nodeType = node._type

      if (nodeType === 'GateNode') {
        const encounter = gateEncounters[node.id] ?? 0
        gateEncounters[node.id] = encounter + 1
        const verdict = policy(node.id, node.verdicts ?? [], encounter)
        spine.verdict(node.id, verdict, 'test')
      } else if (node.expand !== undefined && shape.directors[node.id] !== undefined) {
        spine.expand(node.id, shape.directors[node.id]!)
      } else if (nodeType === 'DataNode' && shape.dataPasses[node.id] !== undefined) {
        spine.startPass(node.id, shape.dataPasses[node.id]!)
      } else {
        spine.complete(node.id)
      }
    }
  }

  // Count total orderings
  let total = 1
  for (const cp of choicePoints) {
    let factorial = 1
    for (let i = 2; i <= cp.count; i++) factorial *= i
    total *= factorial
  }
  if (total > 5000) {
    throw new Error(`too many orderings (${total}) — mark this shape as large`)
  }

  if (choicePoints.length === 0 || total === 1) {
    return [driveToEnd(shape.graph, shape)]
  }

  // Generate permutation indices for each choice point
  const permIndexSets: number[][][] = choicePoints.map(cp => {
    const indices = Array.from({ length: cp.count }, (_, i) => i)
    return permutations(indices)
  })

  // Cartesian product of all permutation sets
  function cartesianProduct(arrays: number[][][]): number[][][] {
    if (arrays.length === 0) return [[]]
    const [first, ...rest] = arrays
    const restProduct = cartesianProduct(rest)
    const result: number[][][] = []
    for (const item of first!) {
      for (const restItem of restProduct) {
        result.push([item, ...restItem])
      }
    }
    return result
  }

  const allCombinations = cartesianProduct(permIndexSets)

  const results: DriveResult[] = []
  for (const combination of allCombinations) {
    let cpCounter = 0
    const orderFn = (due: NodeView[]): NodeView[] => {
      if (due.length <= 1) return due
      const sorted = [...due].sort((a, b) => a.id.localeCompare(b.id))
      const permIndex = combination[cpCounter]
      cpCounter++
      if (permIndex === undefined) return sorted
      // Clip to actual length: with position-scoped scheduling, different orderings
      // can produce different numbers of concurrent nodes at a given step.
      return permIndex.filter(i => i < sorted.length).map(i => sorted[i]!)
    }

    results.push(driveToEnd(shape.graph, shape, { orderFn }))
  }

  return results
}

/** Generate count runs with seeded random orderings. */
export function seededOrders(shape: ShapeEntry, seed: number, count: number): DriveResult[] {
  const results: DriveResult[] = []
  for (let i = 0; i < count; i++) {
    const rng = mulberry32(seed + i)
    const orderFn = (due: NodeView[]): NodeView[] => shuffleWith(due, rng)
    results.push(driveToEnd(shape.graph, shape, { orderFn }))
  }
  return results
}

/**
 * Replay an exact event sequence on a fresh Spine to reach the same state.
 * After replaying an expand, the expansion children exist again.
 */
export function replayTo(graph: WorkflowGraph, shape: ShapeEntry, events: DriveEvent[]): Spine {
  const spine = new Spine(deepCopyGraph(graph))
  for (const event of events) {
    switch (event.action) {
      case 'complete':
        spine.complete(event.node)
        break
      case 'verdict':
        spine.verdict(event.node, event.detail ?? 'proceed', 'test')
        break
      case 'expand':
        spine.expand(event.node, shape.directors[event.node]!)
        break
      case 'startPass':
        spine.startPass(event.node, shape.dataPasses[event.node]!)
        break
      case 'endPass':
        spine.endPass()
        break
    }
  }
  return spine
}
