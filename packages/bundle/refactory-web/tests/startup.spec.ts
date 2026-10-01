/**
 * The refactory-web startup provider and layer over a real Context: what it
 * publishes is what the `refactory-console` row reads as `repoRoot`, and what the
 * patch carries is what replaced the `@deepseek-ai/dsh-web-app` pnpm patch.
 *
 * The published root is checked as bytes on disk rather than compared to
 * `process.cwd()`: the whole point of the move is that the two differ, and a test
 * that asserted `process.cwd()` would pass while the panel opened the wrong
 * checkout's `.factory/`.
 */
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { Context } from '@deepseek-ai/cordis'
import { describe, expect, it } from 'vitest'
import {
  apply,
  REFACTORY_WEB_STARTUP_SERVICE,
  type RefactoryWebStartupValues,
} from '../src/startup.ts'

/** Boot the provider over a fresh Context and return what it published. */
function boot(): RefactoryWebStartupValues {
  const ctx = new Context()
  apply(ctx)
  const values = ctx.get(REFACTORY_WEB_STARTUP_SERVICE) as RefactoryWebStartupValues | undefined
  expect(values).toBeDefined()
  return values!
}

/** The layer document, as the reconciliation reads it. */
const PATCH = readFileSync(fileURLToPath(new URL('../cordis.patch.yml', import.meta.url)), 'utf8')

describe('refactory-web startup provider', () => {
  it('publishes the checkout that holds this bundle and the python glue', () => {
    const root = boot().repoRoot
    expect(existsSync(join(root, 'refactory', 'docs', 'verification.md'))).toBe(true)
    expect(existsSync(join(root, 'packages', 'bundle', 'refactory-web', 'cordis.patch.yml'))).toBe(true)
  })

  it('does not publish the caller\u2019s working directory', () => {
    // `process.cwd()` is the old pnpm patch's expression, and it is wrong for a
    // profile-installed bundle: it names whoever launched dsh, not this checkout.
    // Chdir away from the checkout so a `process.cwd()` implementation would
    // actually be caught rather than agreeing by accident when vitest's cwd is the
    // checkout (the resolved root also keeps its trailing separator, which would
    // let a raw inequality pass while the value was still the caller's cwd).
    const before = process.cwd()
    const elsewhere = mkdtempSync(join(tmpdir(), 'refactory-web-cwd-'))
    try {
      process.chdir(elsewhere)
      const root = boot().repoRoot
      expect(root).not.toContain(elsewhere)
      expect(existsSync(join(root, 'packages', 'bundle', 'refactory-web', 'cordis.patch.yml'))).toBe(true)
    } finally {
      process.chdir(before)
      rmSync(elsewhere, { recursive: true, force: true })
    }
  })
})

describe('refactory-web layer', () => {
  it('carries the wf_next reminder exclusion the web-app patch supplied', () => {
    expect(PATCH).toContain('id: repeat-tool-reminder')
    expect(PATCH).toContain('exclude: [wf_next]')
  })

  it('disables the official whale and mounts the re:factory occupants', () => {
    expect(PATCH).toContain('id: ui-brand-official')
    expect(PATCH).toContain('disabled: true')
    expect(PATCH).toContain('@deepseek-ai/dsh-client-ui-brand-refactory')
  })

  it('mounts the console and the picker over the startup-published repoRoot', () => {
    expect(PATCH).toContain('@deepseek-ai/dsh-refactory-web/startup')
    expect(PATCH).toContain('@deepseek-ai/dsh-refactory-console')
    expect(PATCH).toContain('@deepseek-ai/dsh-client-ui-refactory')
    expect(PATCH).toContain('inject: [refactoryWebStartup]')
    expect(PATCH).toContain('repoRoot: !!js ctx.refactoryWebStartup.repoRoot')
  })

  it('leaves the single-owner agent-preset-registry row alone', () => {
    // `config` is replaced wholesale, so a second statement here would drop
    // `default: ceo` from refactory-presets. The comment above may name the row;
    // no entry may target it.
    expect(PATCH).not.toContain('id: agent-preset-registry')
  })
})
