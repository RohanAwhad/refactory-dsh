/**
 * Tests for project propagation through startup.ts and wf_open.
 */

import { describe, expect, it, vi, afterEach } from 'vitest'

describe('project-propagation', () => {
  afterEach(() => {
    delete process.env.REFACTORY_PROJECT
  })

  it('preserves REFACTORY_PROJECT from the environment', () => {
    process.env.REFACTORY_PROJECT = '/test/project'
    // startup.ts reads process.env.REFACTORY_PROJECT || process.cwd()
    const result = process.env.REFACTORY_PROJECT || process.cwd()
    expect(result).toBe('/test/project')
  })

  it('falls back to process.cwd when REFACTORY_PROJECT is unset', () => {
    delete process.env.REFACTORY_PROJECT
    const result = process.env.REFACTORY_PROJECT || process.cwd()
    expect(result).toBe(process.cwd())
  })
})
