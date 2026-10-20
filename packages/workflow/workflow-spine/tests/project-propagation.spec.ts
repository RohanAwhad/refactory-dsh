/**
 * Tests for project propagation through startup.ts and wf_open.
 *
 * These tests call the real exported functions rather than re-implementing
 * the logic inline, so a change to the function breaks the test.
 */

import { describe, expect, it, afterEach } from 'vitest'
import { resolveRefactoryProject } from '../../../bundle/graph/src/startup.ts'
import { buildWfOpenArgv } from '../src/index.ts'

describe('project-propagation', () => {
  afterEach(() => {
    delete process.env.REFACTORY_PROJECT
  })

  it('preserves REFACTORY_PROJECT from the environment (startup.ts)', () => {
    process.env.REFACTORY_PROJECT = '/test/project'
    expect(resolveRefactoryProject()).toBe('/test/project')
  })

  it('falls back to process.cwd when REFACTORY_PROJECT is unset', () => {
    delete process.env.REFACTORY_PROJECT
    expect(resolveRefactoryProject()).toBe(process.cwd())
  })

  it('wf_open passes session.header.cwd as --project in the argv', () => {
    const argv = buildWfOpenArgv(['open-stub'], '/user/project', 'pkg/t')
    expect(argv).toEqual(['open-stub', '--project', '/user/project', 'pkg/t'])
  })

  it('wf_open omits --project when session has no cwd', () => {
    const argv = buildWfOpenArgv(['open-stub'], undefined, 'pkg/t')
    expect(argv).toEqual(['open-stub', 'pkg/t'])
  })
})
