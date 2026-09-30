/**
 * The refactory-presets startup provider over a real Context: what it publishes
 * is what the `agent-presets` row reads as its configured preset root, so a
 * wrong path is a preset roster with no `ceo` row.
 *
 * The composition is checked as bytes on disk. Nothing here imports
 * `@deepseek-ai/dsh-agent-presets`: the preset must mount from this package's
 * tree, and a test that read it through the shipped package could not catch the
 * tree going missing.
 */
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import { describe, expect, it } from 'vitest'
import {
  apply,
  REFACTORY_PRESETS_STARTUP_SERVICE,
  type RefactoryPresetsStartupValues,
} from '../src/startup.ts'

/** Boot the provider over a fresh Context and return what it published. */
function boot(): RefactoryPresetsStartupValues {
  const ctx = new Context()
  apply(ctx)
  const values = ctx.get(REFACTORY_PRESETS_STARTUP_SERVICE) as RefactoryPresetsStartupValues | undefined
  expect(values).toBeDefined()
  return values!
}

describe('refactory-presets startup provider', () => {
  it('publishes the root holding the ceo composition', () => {
    const composition = join(boot().presetRoot, 'ceo', 'agent.cordis.yml')
    expect(existsSync(composition)).toBe(true)
  })

  it('publishes a root outside any shipped agent-presets install', () => {
    // The whole point of the move: `ceo` must come from this package's tree,
    // not from a patched `@deepseek-ai/dsh-agent-presets`.
    expect(boot().presetRoot).not.toContain('dsh-agent-presets')
  })

  it('carries the display metadata the picker reads', () => {
    const metadata = readFileSync(join(boot().presetRoot, 'ceo', 'preset.yml'), 'utf8')
    expect(metadata).toContain('name: CEO')
    expect(metadata).toContain('order: 1')
  })

  it('moves the interactive workflow spine and its deny list intact', () => {
    const composition = readFileSync(join(boot().presetRoot, 'ceo', 'agent.cordis.yml'), 'utf8')
    expect(composition).toContain('interactiveOpen: true')
    expect(composition).toContain('toolPrefix: wf')
    expect(composition).toContain('ceoDeny:')
  })
})
