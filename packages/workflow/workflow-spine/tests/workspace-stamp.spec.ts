/**
 * Tests for the workspace-wide build stamp.
 */

import { describe, expect, it } from 'vitest'
import {
  WORKSPACE_BUILD_STAMP,
  computeWorkspaceStamp,
  validateWorkspaceBuild,
} from '../src/workspace-stamp.ts'

describe('workspace-stamp', () => {
  it('leaves the workspace stamp empty when running from source', () => {
    expect(WORKSPACE_BUILD_STAMP).toBeTypeOf('string')
    expect(WORKSPACE_BUILD_STAMP).toBe('')
  })

  it('computes a 16-char hex stamp from the source tree', () => {
    const stamp = computeWorkspaceStamp()
    expect(stamp).toMatch(/^[0-9a-f]{16}$/)
  })

  it('is deterministic', () => {
    const a = computeWorkspaceStamp()
    const b = computeWorkspaceStamp()
    expect(a).toBe(b)
  })

  it('no-ops from source (empty built stamp)', () => {
    expect(() => validateWorkspaceBuild()).not.toThrow()
  })

  it('throws when stamps differ', () => {
    expect(() => validateWorkspaceBuild('0000000000000000', () => 'ffffffffffffffff'))
      .toThrow('Workspace build is out of date')
  })

  it('accepts matching stamps', () => {
    const stamp = computeWorkspaceStamp()!
    expect(() => validateWorkspaceBuild(stamp, () => stamp)).not.toThrow()
  })

  it('no-ops when source is absent', () => {
    expect(() => validateWorkspaceBuild('0000000000000000', () => null)).not.toThrow()
  })
})
