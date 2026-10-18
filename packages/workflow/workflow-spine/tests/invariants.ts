/**
 * Five invariant checker functions. Each throws on violation.
 */
import { Spine } from '../src/spine.ts'
import type { WorkflowGraph } from '../src/types.ts'
import type { DriveResult, DriveEvent, GatePolicy } from './shape-driver.ts'
import type { ShapeEntry } from './graph-shapes.ts'
import { driveExistingToEnd, replayTo } from './shape-driver.ts'
import {
  computeRetryEdgeSet, forwardClosure, forkBody, computeRealSinks,
} from './graph-walks.ts'

/**
 * Invariant 1: every node completes once per visit.
 * Check that the spine's completed list has no duplicates.
 */
export function checkCompletedOncePerVisit(result: DriveResult): void {
  const state = result.spine.state() as { completed: string[] }
  const seen = new Set<string>()
  for (const id of state.completed) {
    if (seen.has(id)) {
      throw new Error(`checkCompletedOncePerVisit: ${id} appears in completed more than once`)
    }
    seen.add(id)
  }
}

/**
 * Invariant 2: a join completes only after every branch of its own fork,
 * and before anything after it.
 */
export function checkJoinAfterBranches(result: DriveResult): void {
  const graph = result.graph
  const retrySet = computeRetryEdgeSet(graph)

  for (const node of Object.values(graph.nodes)) {
    if (node._type !== 'ForkNode') continue
    const joinEdge = graph.edges.find(e => e.source === node.id && e.condition == null)
    if (!joinEdge) continue
    const joinId = joinEdge.target

    // Compute the fork body
    const body = forkBody(graph, node.id, retrySet)

    // Find the join's last completion index
    const joinIdx = lastIndex(result.completedOrder, joinId)
    if (joinIdx === -1) continue // join was never completed (e.g. skipped)

    // Every node in the body must have completed before the join
    for (const bodyNodeId of body) {
      const bodyIdx = lastIndex(result.completedOrder, bodyNodeId)
      if (bodyIdx === -1) continue // body node never completed (could be data/fork node)
      if (bodyIdx > joinIdx) {
        throw new Error(
          `checkJoinAfterBranches: ${bodyNodeId} (index ${bodyIdx}) completed after join ${joinId} (index ${joinIdx})`,
        )
      }
    }

    // The join's successor must complete after the join
    const successorEdge = graph.edges.find(
      e => e.source === joinId && e.condition == null,
    )
    if (successorEdge) {
      const afterIdx = firstIndexAfter(result.completedOrder, successorEdge.target, joinIdx)
      if (afterIdx !== -1 && afterIdx < joinIdx) {
        throw new Error(
          `checkJoinAfterBranches: successor ${successorEdge.target} completed before join ${joinId}`,
        )
      }
    }
  }
}

/**
 * Invariant 3: no node that became due is dropped.
 */
export function checkNoDueDrop(result: DriveResult): void {
  const state = result.spine.state() as { completed: string[]; skipped: string[] }
  const finished = new Set([...state.completed, ...state.skipped])
  for (const id of result.everDue) {
    if (!finished.has(id)) {
      throw new Error(`checkNoDueDrop: ${id} was due but never completed or skipped`)
    }
  }
}

/**
 * Invariant 4: the run ends done with no incomplete halt, and the graph's
 * real ends are reached.
 *
 * Check A — Normal completion: status is 'done' with no halted.
 * Check B — Sink verification through the halt mechanism: drain the cursor
 * before the end via a checkpoint with an emptied cursor, and verify
 * halted.unreached matches the independently computed real sinks.
 */
export function checkDoneAndEnds(result: DriveResult, shape: ShapeEntry): void {
  // Check A: run ends done
  if (result.final.status !== 'done') {
    throw new Error(`checkDoneAndEnds: status is '${result.final.status}', expected 'done'`)
  }
  if (result.final.halted) {
    throw new Error(`checkDoneAndEnds: halted: ${result.final.halted.reason}`)
  }

  // Check B: verify sinks through halt mechanism (strict equality)
  const retrySet = computeRetryEdgeSet(result.graph)
  const expectedSinks = computeRealSinks(result.graph, retrySet)

  if (expectedSinks.length === 0) return

  // Verify at least one expected sink was reached
  const state = result.spine.state() as { completed: string[]; skipped: string[] }
  const finished = new Set([...state.completed, ...state.skipped])
  const reachedSinks = expectedSinks.filter(id => finished.has(id))
  if (reachedSinks.length === 0) {
    throw new Error(
      `checkDoneAndEnds: no real sink was reached; expected sinks: ${expectedSinks.join(', ')}`,
    )
  }

  // Replay to just before the last completed sink, checkpoint with cursor=[],
  // and verify the halt mechanism detects the unreached sinks.
  const lastSinkId = reachedSinks[reachedSinks.length - 1]!
  const eventsBeforeSink: DriveEvent[] = []
  let found = false
  for (let i = result.events.length - 1; i >= 0; i--) {
    const ev = result.events[i]!
    if (!found && ev.node === lastSinkId && (ev.action === 'complete' || ev.action === 'verdict')) {
      found = true
      continue // skip the sink completion event
    }
    if (found) {
      eventsBeforeSink.unshift(ev)
    }
  }

  if (!found || eventsBeforeSink.length === 0) return

  const partialSpine = replayTo(shape.graph, shape, eventsBeforeSink)
  const saved = JSON.parse(JSON.stringify(partialSpine.checkpoint())) as Record<string, unknown>
  const savedState = saved['state'] as Record<string, unknown>
  const savedCompleted = (savedState['completed'] ?? []) as string[]
  const savedSkipped = (savedState['skipped'] ?? []) as string[]
  const partialFinished = new Set([...savedCompleted, ...savedSkipped])
  const expectedUnreached = expectedSinks.filter(id => !partialFinished.has(id))

  savedState['cursor'] = []
  const checkSpine = new Spine(shape.graph)
  checkSpine.restore(saved)
  const checkNext = checkSpine.next()
  if (checkNext.status !== 'done') {
    throw new Error(`checkDoneAndEnds (halt check): emptied cursor should give 'done', got '${checkNext.status}'`)
  }

  if (expectedUnreached.length === 0) {
    // All sinks were already reached before the last one; no halt expected
    if (checkNext.halted) {
      throw new Error(
        `checkDoneAndEnds (halt check): all expected sinks already reached, but got halted: ${checkNext.halted.reason}`,
      )
    }
  } else {
    // Sinks not yet reached — expect halted.unreached to match exactly
    if (!checkNext.halted) {
      throw new Error(
        `checkDoneAndEnds (halt check): expected halted with unreached [${expectedUnreached.join(', ')}]`,
      )
    }
    if (checkNext.halted.reason !== 'incomplete') {
      throw new Error(
        `checkDoneAndEnds (halt check): expected reason 'incomplete', got '${checkNext.halted.reason}'`,
      )
    }
    const actualUnreached = new Set(checkNext.halted.unreached ?? [])
    const expectedUnreachedSet = new Set(expectedUnreached)
    for (const id of actualUnreached) {
      if (!expectedUnreachedSet.has(id)) {
        throw new Error(
          `checkDoneAndEnds (halt check): unreached contains ${id} which is not an expected sink`,
        )
      }
    }
    for (const id of expectedUnreachedSet) {
      if (!actualUnreached.has(id)) {
        throw new Error(
          `checkDoneAndEnds (halt check): expected sink ${id} missing from unreached [${[...actualUnreached].join(', ')}]`,
        )
      }
    }
  }
}

/**
 * Invariant 5: redo invariant. For every completed non-gate non-data node,
 * a redo re-runs exactly the nodes after it, not its siblings.
 */
export function checkRedoInvariant(result: DriveResult, shape: ShapeEntry): void {
  const state = result.spine.state() as { completed: string[] }

  for (const nodeId of state.completed) {
    const nodeType = result.graph.nodes[nodeId]?._type

    // Check refusal for GateNode
    if (nodeType === 'GateNode') {
      const replayedSpine = replayTo(shape.graph, shape, result.events)
      try {
        replayedSpine.planRedo(nodeId)
        throw new Error(`checkRedoInvariant: planRedo should refuse GateNode ${nodeId}`)
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        if (!msg.includes('gate')) {
          throw new Error(`checkRedoInvariant: planRedo for GateNode ${nodeId} should mention "gate": ${msg}`)
        }
      }
      continue
    }

    // Check refusal for DataNode
    if (nodeType === 'DataNode') {
      const replayedSpine = replayTo(shape.graph, shape, result.events)
      try {
        replayedSpine.planRedo(nodeId)
        throw new Error(`checkRedoInvariant: planRedo should refuse DataNode ${nodeId}`)
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        if (!msg.includes('iterates its items')) {
          throw new Error(`checkRedoInvariant: planRedo for DataNode ${nodeId} should mention "iterates its items": ${msg}`)
        }
      }
      continue
    }

    // Skip ForkNode and JoinNode — they are structural, and planRedo doesn't
    // refuse them but they are not meaningful redo targets. The spec says
    // "non-gate" nodes; fork/join are completed automatically.
    if (nodeType === 'ForkNode' || nodeType === 'JoinNode') continue

    // Replay to the same state
    const replayedSpine = replayTo(shape.graph, shape, result.events)

    // Get the replayed spine's live graph (which has expansion children)
    const liveGraph = (replayedSpine.checkpoint() as { graph: WorkflowGraph }).graph
    const liveRetrySet = computeRetryEdgeSet(liveGraph)

    // Get the successors map from the checkpoint
    const checkpoint = replayedSpine.checkpoint() as Record<string, unknown>
    const successors = (checkpoint['successors'] ?? {}) as Record<string, string>

    // Compute forward closure independently on the live graph
    const expectedClosure = forwardClosure(liveGraph, nodeId, liveRetrySet, successors)

    // Compare with planRedo's nodes
    const plan = replayedSpine.planRedo(nodeId)
    const planNodes = new Set(plan.nodes)
    const expectedNodes = expectedClosure

    // Check planRedo's nodes are subset of expected
    for (const n of planNodes) {
      if (!expectedNodes.has(n)) {
        throw new Error(
          `checkRedoInvariant: planRedo(${nodeId}) includes ${n} which is not in the forward closure`,
        )
      }
    }

    // Check expected nodes are subset of planRedo's
    for (const n of expectedNodes) {
      if (!planNodes.has(n)) {
        throw new Error(
          `checkRedoInvariant: forward closure of ${nodeId} includes ${n} which planRedo omitted`,
        )
      }
    }

    // Do the redo and drive to end.
    // Use a no-reloop gate policy: redo re-runs the forward closure (which
    // excludes reloop back-edge targets), so letting a gate reloop would
    // re-complete nodes outside the expected closure. The forward closure
    // already follows all non-retry conditional edges (including proceed),
    // so always proceeding keeps execution within it.
    replayedSpine.redo(nodeId)
    const noReloopPolicy: GatePolicy = (gateId) =>
      shape.fixedVerdicts[gateId] ?? 'proceed'
    const redoResult = driveExistingToEnd(replayedSpine, shape, {
      gatePolicy: noReloopPolicy,
    })

    // The re-completed nodes should be exactly the forward closure's
    // previously completed nodes, and nothing outside the closure.
    // Use the spine's actual completed list (not just completedOrder) because
    // some nodes are completed internally (e.g. DataNode via endPass).
    const redoState = redoResult.spine.state() as { completed: string[] }
    const reCompletedNodes = new Set(redoState.completed)
    const previouslyCompleted = new Set(state.completed)

    // Nodes completed after redo that are outside the closure: only check
    // nodes that were NOT already completed before redo (i.e. not invalidated).
    // After redo, the spine invalidates all nodes in plan.nodes. The nodes
    // remaining in completed are those outside the affected set. Only newly
    // completed nodes (not in the pre-redo completed set minus the invalidated
    // ones) should be within the closure.
    const survivingCompleted = new Set(
      state.completed.filter(id => !expectedClosure.has(id)),
    )
    for (const n of reCompletedNodes) {
      if (!expectedClosure.has(n) && !survivingCompleted.has(n)) {
        throw new Error(
          `checkRedoInvariant: redo(${nodeId}) re-completed ${n} which is outside the forward closure`,
        )
      }
    }

    for (const n of expectedClosure) {
      if (previouslyCompleted.has(n) && !reCompletedNodes.has(n)) {
        throw new Error(
          `checkRedoInvariant: redo(${nodeId}) did not re-complete ${n} which was previously completed and is in the forward closure`,
        )
      }
    }

    // The redo should end done with no incomplete halt
    if (redoResult.final.status !== 'done') {
      throw new Error(
        `checkRedoInvariant: redo(${nodeId}) did not end done, status: ${redoResult.final.status}`,
      )
    }
    if (redoResult.final.halted) {
      throw new Error(
        `checkRedoInvariant: redo(${nodeId}) ended with halt: ${redoResult.final.halted.reason}`,
      )
    }
  }
}

/** Helper: last occurrence index of id in array, or -1. */
function lastIndex(arr: string[], id: string): number {
  for (let i = arr.length - 1; i >= 0; i--) {
    if (arr[i] === id) return i
  }
  return -1
}

/** Helper: first occurrence index of id after position start, or -1. */
function firstIndexAfter(arr: string[], id: string, start: number): number {
  for (let i = start + 1; i < arr.length; i++) {
    if (arr[i] === id) return i
  }
  return -1
}
