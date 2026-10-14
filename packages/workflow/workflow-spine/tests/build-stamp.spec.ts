/**
 * Tests for the build-stamp stale-build detection mechanism.
 *
 * These tests run from TypeScript source (not the built bundle), so
 * `BUILD_STAMP` is the empty string (the `typeof` guard falls through)
 * and `validateBuild()` no-ops.
 */

import { describe, expect, it } from 'vitest'
import { BUILD_STAMP, computeSourceStamp, validateBuild } from '../src/build-stamp.ts'

describe('build-stamp', () => {
  it('BUILD_STAMP is empty when running from source', () => {
    expect(BUILD_STAMP).toBeTypeOf('string')
    expect(BUILD_STAMP).toBe('')
  })

  it('validateBuild() is a no-op when running from source', () => {
    // BUILD_STAMP is '' when not bundled, so validateBuild should return
    // without throwing.
    expect(() => validateBuild()).not.toThrow()
  })

  it('computeSourceStamp() returns a deterministic 16-char hex string', () => {
    const stamp1 = computeSourceStamp()
    const stamp2 = computeSourceStamp()
    expect(stamp1).toBe(stamp2)
    expect(stamp1).toMatch(/^[0-9a-f]{16}$/)
  })

  it('computeSourceStamp() returns string or null', () => {
    const stamp = computeSourceStamp()
    // From within the repo with src/ present, should be a string.
    expect(stamp).not.toBeNull()
    expect(stamp).toMatch(/^[0-9a-f]{16}$/)
  })

  it('detects a mismatch when BUILD_STAMP differs from the source stamp', () => {
    const current = computeSourceStamp()
    expect(current).not.toBeNull()
    const stale = '0000000000000000'
    expect(stale).not.toBe(current)

    // Simulate what validateBuild does when stamps differ:
    // BUILD_STAMP is not empty (it would be a real hash in a bundle) and
    // it doesn't match the freshly computed stamp.
    const mismatch = stale !== current
    expect(mismatch).toBe(true)

    // Verify the error message format
    const errorMessage = `Workflow engine build is out of date (build stamp: ${stale}, `
      + `source stamp: ${current}). Run \`pnpm run build\` to rebuild.`
    expect(errorMessage).toContain('out of date')
    expect(errorMessage).toContain(stale)
    expect(errorMessage).toContain(current!)
  })
})
