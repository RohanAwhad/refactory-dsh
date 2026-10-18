/**
 * dm-73 expected failure cases. Each entry identifies a shape and the ordering
 * that triggers the cursor-overwrite bug. Both the invariant-skipping logic and
 * the it.fails block read this single list. The dm-73 fix removes entries from
 * here to convert expected failures into passing tests.
 */
import type { NodeView } from '../src/spine.ts'

export interface Dm73Case {
  /** Shape name from graph-shapes.ts */
  shape: string
  /** Human-readable scenario description */
  scenario: string
  /** The invariant function name that this case violates */
  invariant: 'checkNoDueDrop'
  /** A function that returns the orderFn that triggers the dm-73 bug */
  triggerOrderFn: (due: NodeView[]) => NodeView[]
}

export const DM73_SHAPES: Dm73Case[] = [
  {
    shape: 'nestedFork',
    scenario: 'inner fork drops outer sibling',
    invariant: 'checkNoDueDrop',
    triggerOrderFn: (due) => [...due].sort((a, b) => {
      if (a.id === 'inner_fork') return -1
      if (b.id === 'inner_fork') return 1
      return a.id.localeCompare(b.id)
    }),
  },
  {
    shape: 'gateInsideBranch',
    scenario: 'gate verdict drops sibling',
    invariant: 'checkNoDueDrop',
    triggerOrderFn: (due) => [...due].sort((a, b) => {
      if (a.id === 'branch_work') return -1
      if (b.id === 'branch_work') return 1
      return a.id.localeCompare(b.id)
    }),
  },
  {
    shape: 'directorInsideBranch',
    scenario: 'expand drops sibling',
    invariant: 'checkNoDueDrop',
    triggerOrderFn: (due) => [...due].sort((a, b) => {
      if (a.id === 'dir_branch') return -1
      if (b.id === 'dir_branch') return 1
      return a.id.localeCompare(b.id)
    }),
  },
  {
    shape: 'dataInsideBranch',
    scenario: 'startPass drops sibling',
    invariant: 'checkNoDueDrop',
    triggerOrderFn: (due) => [...due].sort((a, b) => {
      if (a.id === 'data_branch') return -1
      if (b.id === 'data_branch') return 1
      return a.id.localeCompare(b.id)
    }),
  },
]
