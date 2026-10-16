import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { describe, expect, it } from 'vitest'
import { CHAT } from '../src/generated-help.ts'
import { invocationFor } from '../src/index.ts'
import { Console } from '../src/console.ts'

const root = resolve(import.meta.dirname, '../../../..')
const coverage = JSON.parse(readFileSync(resolve(root, 'refactory/refactory_dsh/data/coverage.json'), 'utf8'))

describe('generated chat adapters against the actual Python parser', () => {
  for (const [action, metadata] of Object.entries(CHAT)) {
    it(`${action} accepts its documented example through the real invocation mapping`, () => {
      const example = coverage.operations[metadata.verb].examples[0].arguments as Record<string, unknown>
      const args: Record<string, unknown> = { action }
      for (const [alias, dest] of Object.entries(metadata.fields)) if (dest in example) args[alias] = example[dest]
      const invocation = invocationFor(args as unknown as Parameters<typeof invocationFor>[0])
      // Only argv construction; Python parses without dispatching any operation.
      const console = new Console(null as never, { repoRoot: root, pythonBin: resolve(root, '.venv/bin'), timeoutMs: 1000, collectBytes: 1000 })
      const argv = console.argv(invocation).slice(3)
      const parsed = spawnSync(resolve(root, '.venv/bin/python'), ['-c', 'import sys,json; from refactory_dsh.console import _parser; print(json.dumps(vars(_parser().parse_args(sys.argv[1:]))))', ...argv], { cwd: root, encoding: 'utf8' })
      expect(parsed.status, parsed.stderr).toBe(0)
      expect(JSON.parse(parsed.stdout).verb).toBe(metadata.verb)
    })
  }
})

describe('how a chat design run is delivered reaches the console', () => {
  it('maps noPr, refine and reviewComments onto the design verb\'s flags', () => {
    const invocation = invocationFor({
      action: 'design', name: 'report_format', brief: 'rename the flag', check: 'pytest',
      noPr: true, refine: 'https://github.com/owner/repository/pull/12', reviewComments: true,
    } as unknown as Parameters<typeof invocationFor>[0])
    const console = new Console(null as never, { repoRoot: root, pythonBin: resolve(root, '.venv/bin'), timeoutMs: 1000, collectBytes: 1000 })
    const argv = console.argv(invocation).slice(3)
    const parsed = spawnSync(resolve(root, '.venv/bin/python'), ['-c', 'import sys,json; from refactory_dsh.console import _parser; print(json.dumps(vars(_parser().parse_args(sys.argv[1:]))))', ...argv], { cwd: root, encoding: 'utf8' })
    expect(parsed.status, parsed.stderr).toBe(0)
    expect(JSON.parse(parsed.stdout)).toMatchObject({
      verb: 'design', no_pr: true, refine: 'https://github.com/owner/repository/pull/12', review_comments: true,
    })
  })
})
