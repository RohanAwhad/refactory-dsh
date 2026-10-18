/**
 * Graph shape invariant test suite.
 *
 * Drives 14 named graph shapes through the spine in every completion order of the
 * nodes due together, checking five invariants on each run. dm-73 expected failures
 * are managed through a single list so the fix removes them in one place.
 */
import { describe, expect, it } from 'vitest'
import { shapes } from './graph-shapes.ts'
import { allOrders, seededOrders, driveToEnd } from './shape-driver.ts'
import {
  checkCompletedOncePerVisit, checkJoinAfterBranches, checkNoDueDrop,
  checkDoneAndEnds, checkRedoInvariant,
} from './invariants.ts'
import { DM73_SHAPES } from './dm73-shapes.ts'
import { Spine, validateGraph } from '../src/spine.ts'

// ─── Structural validation ──────────────────────────────────────

describe('graph shapes validate', () => {
  for (const shape of shapes) {
    it(`${shape.name} passes validateGraph`, () => {
      expect(validateGraph(shape.graph)).toEqual([])
    })
  }
})

// ─── Invariants across all orderings ─────────────────────────────

const dm73ShapeNames = new Set(DM73_SHAPES.map(c => c.shape))

describe('graph shape invariants', () => {
  for (const shape of shapes) {
    describe(shape.name, () => {
      const runs = shape.large ? seededOrders(shape, 42, 20) : allOrders(shape)
      const isDm73 = dm73ShapeNames.has(shape.name)

      runs.forEach((result, i) => {
        it(`ordering ${i}`, () => {
          checkCompletedOncePerVisit(result)
          checkJoinAfterBranches(result)
          if (!isDm73) {
            checkNoDueDrop(result)
            checkDoneAndEnds(result, shape)
          } else if (result.final.status === 'done' && !result.final.halted) {
            // dm-73 ordering that didn't trigger the bug — still check ends
            checkDoneAndEnds(result, shape)
          }
        })
      })

      if (!isDm73) {
        it('redo invariant', () => {
          checkRedoInvariant(runs[0]!, shape)
        })
      }
    })
  }
})

// ─── dm-73 expected failures ─────────────────────────────────────

describe('dm-73 expected failures', () => {
  for (const entry of DM73_SHAPES) {
    it.fails(`dm-73: ${entry.shape} — ${entry.scenario}`, () => {
      const shape = shapes.find(s => s.name === entry.shape)!
      const result = driveToEnd(shape.graph, shape, {
        orderFn: entry.triggerOrderFn,
      })
      checkNoDueDrop(result)
    })
  }
})

// ─── Redo refusal for non-redoable types ─────────────────────────

describe('redo refusal for non-redoable types', () => {
  it('planRedo refuses GateNode with upstream guidance', () => {
    const shape = shapes.find(s => s.name === 'reloopBackEdge')!
    const spine = new Spine(shape.graph)
    spine.complete('emit')
    spine.complete('materialise')
    spine.verdict('check_gate', 'proceed', 'test')
    spine.complete('done')
    expect(() => spine.planRedo('check_gate')).toThrow(/gate/)
  })

  it('planRedo refuses DataNode', () => {
    const shape = shapes.find(s => s.name === 'dataTopLevel')!
    const spine = new Spine(shape.graph)
    spine.startPass('data', 2)
    spine.complete('work')
    spine.complete('d_exit')
    spine.endPass()
    spine.complete('work')
    spine.complete('d_exit')
    spine.endPass()
    spine.complete('after')
    expect(() => spine.planRedo('data')).toThrow(/iterates its items/)
  })
})

// ─── Invariant sanity checks ─────────────────────────────────────

describe('invariant sanity checks', () => {
  it('checkCompletedOncePerVisit rejects duplicates', () => {
    const shape = shapes.find(s => s.name === 'linear')!
    const result = driveToEnd(shape.graph, shape)
    // Create a fake spine state with a duplicate
    const fakeSpine = {
      state: () => ({ completed: ['a', 'b', 'a'], skipped: [] }),
      checkpoint: () => ({ graph: shape.graph }),
    } as unknown as Spine
    const fakeResult = { ...result, spine: fakeSpine }
    expect(() => checkCompletedOncePerVisit(fakeResult)).toThrow(/checkCompletedOncePerVisit/)
  })

  it('checkNoDueDrop rejects dropped node', () => {
    const shape = shapes.find(s => s.name === 'linear')!
    const result = driveToEnd(shape.graph, shape)
    // Inject a due node that was never completed
    const fakeEverDue = new Set([...result.everDue, 'phantom'])
    const fakeSpine = {
      state: () => ({ completed: ['a', 'b', 'c'], skipped: [] }),
      checkpoint: () => ({ graph: shape.graph }),
    } as unknown as Spine
    const fakeResult = { ...result, everDue: fakeEverDue, spine: fakeSpine }
    expect(() => checkNoDueDrop(fakeResult)).toThrow(/checkNoDueDrop/)
  })

  it('checkJoinAfterBranches rejects join before branch', () => {
    const shape = shapes.find(s => s.name === 'forkSingleBranch')!
    const result = driveToEnd(shape.graph, shape)
    // Swap join before its branch in completedOrder to trigger the invariant
    const idx = result.completedOrder.indexOf('join')
    const hcIdx = result.completedOrder.indexOf('hc')
    if (idx !== -1 && hcIdx !== -1) {
      const bad = [...result.completedOrder]
      bad[hcIdx] = 'join'
      bad[idx] = 'hc'
      const fakeResult = { ...result, completedOrder: bad }
      expect(() => checkJoinAfterBranches(fakeResult)).toThrow(/checkJoinAfterBranches/)
    }
  })

  it('checkDoneAndEnds rejects non-done status', () => {
    const shape = shapes.find(s => s.name === 'linear')!
    const result = driveToEnd(shape.graph, shape)
    const fakeResult = {
      ...result,
      final: { status: 'ready' as const, nodes: [] },
    }
    expect(() => checkDoneAndEnds(fakeResult, shape)).toThrow(/checkDoneAndEnds/)
  })
})

// ─── Ordering coverage ──────────────────────────────────────────

describe('ordering coverage', () => {
  it('forkSingleBranch has multiple orderings', () => {
    const shape = shapes.find(s => s.name === 'forkSingleBranch')!
    const runs = allOrders(shape)
    expect(runs.length).toBeGreaterThan(1)
  })

  it('linear has exactly 1 ordering', () => {
    const shape = shapes.find(s => s.name === 'linear')!
    const runs = allOrders(shape)
    expect(runs.length).toBe(1)
  })
})
