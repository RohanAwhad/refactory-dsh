/**
 * Tests for the build-stamp stale-build detection mechanism.
 *
 * These tests run from TypeScript source (not the built bundle), so
 * `BUILD_STAMP` is the empty string (the `typeof` guard falls through)
 * and `validateBuild()` no-ops unless a test passes the stamps to compare.
 */

import { describe, expect, it } from 'vitest'
import { BUILD_STAMP, computeSourceStamp, validateBuild } from '../src/build-stamp.ts'

describe('build-stamp', () => {
  it('leaves the build stamp empty when running from source', () => {
    expect(BUILD_STAMP).toBeTypeOf('string')
    expect(BUILD_STAMP).toBe('')
  })

  it('accepts any source when running from source', () => {
    expect(() => validateBuild()).not.toThrow()
  })

  it('hashes the source to the same 16-character hex stamp every time', () => {
    const stamp1 = computeSourceStamp()
    const stamp2 = computeSourceStamp()
    expect(stamp1).toBe(stamp2)
    expect(stamp1).toMatch(/^[0-9a-f]{16}$/)
  })

  it('throws, naming both stamps, when the build stamp differs from the source', () => {
    const stale = '0000000000000000'
    const current = 'ffffffffffffffff'
    expect(() => validateBuild(stale, () => current)).toThrow(
      `Workflow engine build is out of date (build stamp: ${stale}, `
      + `source stamp: ${current}). Run \`pnpm run build\` to rebuild.`,
    )
  })

  it('accepts a build whose stamp matches the source', () => {
    const current = computeSourceStamp()
    expect(current).not.toBeNull()
    expect(() => validateBuild(current!)).not.toThrow()
  })

  it('accepts a build when there is no source tree to compare against', () => {
    expect(() => validateBuild('0000000000000000', () => null)).not.toThrow()
  })
})
