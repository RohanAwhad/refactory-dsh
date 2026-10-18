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

export const DM73_SHAPES: Dm73Case[] = []
