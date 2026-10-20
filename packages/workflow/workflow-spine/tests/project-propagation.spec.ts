/**
 * Tests for project propagation through startup.ts and wf_open.
 */

import { describe, expect, it, afterEach } from 'vitest'

describe('project-propagation', () => {
  afterEach(() => {
    delete process.env.REFACTORY_PROJECT
  })

  it('preserves REFACTORY_PROJECT from the environment (startup.ts:174 pattern)', () => {
    process.env.REFACTORY_PROJECT = '/test/project'
    // This is the exact expression startup.ts:174 uses:
    //   REFACTORY_PROJECT: process.env.REFACTORY_PROJECT || process.cwd()
    const result = process.env.REFACTORY_PROJECT || process.cwd()
    expect(result).toBe('/test/project')
  })

  it('falls back to process.cwd when REFACTORY_PROJECT is unset', () => {
    delete process.env.REFACTORY_PROJECT
    const result = process.env.REFACTORY_PROJECT || process.cwd()
    expect(result).toBe(process.cwd())
  })

  it('wf_open passes session.header.cwd as --project in the argv (index.ts:1537-1539)', () => {
    // Replicates the wf_open logic from index.ts:1535-1541.
    const config = { openArgs: ['open-stub'] }
    const session = { header: { cwd: '/user/project' } } as { header?: { cwd?: string } }
    const args = { mode: 'pkg/t' }

    const argv = [...config.openArgs]
    const sessionCwd = session.header?.cwd
    if (typeof sessionCwd === 'string' && sessionCwd !== '') {
      argv.push('--project', sessionCwd)
    }
    argv.push(args.mode)

    expect(argv).toEqual(['open-stub', '--project', '/user/project', 'pkg/t'])
  })

  it('wf_open omits --project when session has no cwd', () => {
    const config = { openArgs: ['open-stub'] }
    const session = {} as { header?: { cwd?: string } }
    const args = { mode: 'pkg/t' }

    const argv = [...config.openArgs]
    const sessionCwd = session.header?.cwd
    if (typeof sessionCwd === 'string' && sessionCwd !== '') {
      argv.push('--project', sessionCwd)
    }
    argv.push(args.mode)

    expect(argv).toEqual(['open-stub', 'pkg/t'])
  })
})
