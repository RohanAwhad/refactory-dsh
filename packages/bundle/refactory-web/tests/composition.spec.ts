/**
 * Composed-layer test for the client-hmr disable.  Uses the full GUI profile
 * layer order — every bundle in PROFILE_TEMPLATES.web.bundles (today dsh-base,
 * dsh-web-app), then refactory, then refactory-web — and DSH's own
 * composeEntries / loadOverlayPatches from @deepseek-ai/dsh-app-boot.
 *
 * All dependencies are resolved through the root @deepseek-ai/dsh dependency
 * via createRequire chains, exactly as refactory/tests/worker_permissions_test.mjs
 * does.  No new devDependencies and no lockfile change.
 */
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { readFileSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { describe, expect, it, vi } from 'vitest'

/* ------------------------------------------------------------------ */
/*  Resolve dsh-app-boot and upstream bundles through the root's dsh  */
/* ------------------------------------------------------------------ */

const repoRoot = fileURLToPath(new URL('../../../../', import.meta.url))
const project = createRequire(join(repoRoot, 'package.json'))
const installed = createRequire(project.resolve('@deepseek-ai/dsh/package.json'))

const boot: {
  PROFILE_TEMPLATES: { web: { bundles: string[] } }
  bundlePatchFiles: (bundle: Record<string, unknown>) => string[]
  loadOverlayPatches: (label: string, file: string) => Record<string, unknown>[]
  composeEntries: (
    layers: Record<string, unknown>[][],
    warn?: (...args: unknown[]) => void,
  ) => Array<{ id?: string; name?: string; disabled?: boolean; [k: string]: unknown }>
} = await import(pathToFileURL(installed.resolve('@deepseek-ai/dsh-app-boot')).href)

/* ------------------------------------------------------------------ */
/*  Build the full GUI profile layer order                            */
/* ------------------------------------------------------------------ */

function loadBundleLayers(pkgDir: string): Record<string, unknown>[][] {
  const pkg = JSON.parse(readFileSync(join(pkgDir, 'package.json'), 'utf8'))
  const files = boot.bundlePatchFiles(pkg.dsh.bundle)
  return files.map((f: string) =>
    boot.loadOverlayPatches(pkg.name + '/' + f, join(pkgDir, f)),
  )
}

/** Template bundles from PROFILE_TEMPLATES.web (today dsh-base, dsh-web-app). */
const templateLayers = boot.PROFILE_TEMPLATES.web.bundles.flatMap((b: string) => {
  const pkgDir = dirname(installed.resolve(b + '/package.json'))
  return loadBundleLayers(pkgDir)
})

/** packages/bundle/refactory */
const refactoryLayers = loadBundleLayers(join(repoRoot, 'packages/bundle/refactory'))

const refactoryWebDir = join(repoRoot, 'packages/bundle/refactory-web')

/**
 * Load the refactory-web layers inside the test body so that a YAML parse
 * error (e.g. from a partial revert of the disabled entry) becomes a test
 * failure, not a module-load error that the coverage audit cannot attribute
 * to a specific site.
 */
function allLayersWithWeb() {
  const webLayers = loadBundleLayers(refactoryWebDir)
  return [...templateLayers, ...refactoryLayers, ...webLayers]
}

/* ------------------------------------------------------------------ */
/*  Tests                                                             */
/* ------------------------------------------------------------------ */

describe('client-hmr composition', () => {
  it('refactory-web layer patches client-hmr with disabled: true', () => {
    const webPatches = loadBundleLayers(refactoryWebDir).flat()
    const hmrPatch = webPatches.find((e: Record<string, unknown>) => e.id === 'client-hmr')
    expect(hmrPatch, 'cordis.patch.yml must contain an id-based client-hmr override outside the insert block').toBeDefined()
    expect(hmrPatch!.disabled).toBe(true)
  })

  it('client-hmr is disabled after full GUI profile composition', () => {
    const warn = vi.fn()
    const composed = boot.composeEntries(allLayersWithWeb(), warn)

    const hmr = composed.filter((e) => e.id === 'client-hmr')
    expect(hmr).toHaveLength(1)
    expect(hmr[0]!.disabled).toBe(true)
    expect(hmr[0]!.name).toBe('@deepseek-ai/dsh-client-hmr')
    expect(warn).not.toHaveBeenCalled()
  })

  it('user-layer disabled: false re-enables client-hmr', () => {
    const userLayer = [{ id: 'client-hmr', disabled: false }]
    const warn = vi.fn()
    const composed = boot.composeEntries([...allLayersWithWeb(), userLayer], warn)

    const hmr = composed.find((e) => e.id === 'client-hmr')
    expect(hmr).toBeDefined()
    expect(hmr!.disabled).toBe(false)
    expect(warn).not.toHaveBeenCalled()
  })
})
