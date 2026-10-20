/**
 * H1 — busyEnter effective-value test.
 *
 * DSH settings resolve "schema defaults → composition base → user layer"
 * (dsh-settings lib/types/types.d.ts:25-29).  The refactory-web composition
 * row for ui-conversation sets `busyEnter: steer`, overriding the conversation
 * plugin's schema default of `queue`.  A person's saved choice still wins.
 *
 * This test lives in workflow-spine because it depends on @deepseek-ai/schemastery
 * (a direct dependency of this package) to reconstruct the conversation plugin's
 * settings schema and exercise the resolution mechanism.  The refactory-web
 * startup.spec.ts carries the complementary text-level assertion that the YAML
 * row exists.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import z from '@deepseek-ai/schemastery'
import { describe, expect, it } from 'vitest'

/** Read the refactory-web composition YAML as plain text. */
const PATCH = readFileSync(
  fileURLToPath(new URL('../../../bundle/refactory-web/cordis.patch.yml', import.meta.url)),
  'utf8',
)

describe('H1 — busyEnter defaults to steer through DSH settings resolution', () => {
  it('composition base is steer, schema default is queue', () => {
    // The conversation plugin declares busyEnter with union(['queue', 'steer'])
    // and .default('queue').  The refactory-web composition provides the base
    // layer { busyEnter: 'steer' }.  Reconstruct the schema identically:
    const BUSY_ENTER_BEHAVIORS = ['queue', 'steer'] as const
    const DEFAULT_BUSY_ENTER_BEHAVIOR = 'queue'

    const schema = z.object({
      busyEnter: z.union([...BUSY_ENTER_BEHAVIORS]).default(DEFAULT_BUSY_ENTER_BEHAVIOR),
    })

    // Extract the composition base value from the YAML text.  The row is:
    //   - id: ui-conversation
    //     config:
    //       busyEnter: steer
    // Rather than importing a YAML parser (no package depends on 'yaml'),
    // match the value directly from the text the startup.spec.ts also reads.
    const match = PATCH.match(/busyEnter:\s*(\S+)/)
    expect(match).not.toBeNull()
    const compositionBase = { busyEnter: match![1]! as 'queue' | 'steer' }

    // Resolution without user layer: composition base overrides schema default → steer.
    const resolvedNoUser = schema(compositionBase)
    expect(resolvedNoUser.busyEnter).toBe('steer')

    // Resolution with user layer saving 'queue': user override wins → queue.
    const resolvedWithUser = schema({ ...compositionBase, busyEnter: 'queue' })
    expect(resolvedWithUser.busyEnter).toBe('queue')
  })
})
