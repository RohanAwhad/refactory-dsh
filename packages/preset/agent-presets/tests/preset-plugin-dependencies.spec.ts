/**
 * Every plugin a shipped preset names must be reachable from the install anchor.
 *
 * A preset row resolves through the profile's healed `node_modules` mirror, which
 * is the transitive dependency closure of the CLI package. A row naming a workspace
 * plugin that no bundle in that closure declares fails at *session creation*, not at
 * boot — the `ceo` preset named `@deepseek-ai/dsh-tool-refactory` while the web
 * bundle never depended on it, so every session in the Web view failed to mount and
 * selecting a workspace did nothing. This is the check that would have caught it.
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..')
const PRESETS = join(REPO, 'packages', 'preset', 'agent-presets', 'presets')

/** The package a bare specifier resolves to, with any subpath stripped. */
function packageOf(specifier: string): string {
  const parts = specifier.split('/')
  return specifier.startsWith('@') ? parts.slice(0, 2).join('/') : parts[0]!
}

/** The subset of a workspace manifest this check reads. */
interface Manifest {
  name?: string
  dependencies?: Record<string, string>
  peerDependencies?: Record<string, string>
}

/** Every workspace manifest, by package name. */
function manifests(): Map<string, Manifest> {
  const found = new Map<string, Manifest>()
  const add = (path: string): void => {
    if (!existsSync(path)) return
    try {
      const pkg = JSON.parse(readFileSync(path, 'utf8')) as Manifest
      if (pkg.name !== undefined) found.set(pkg.name, pkg)
    } catch { /* a manifest that does not parse is not a package this check owns */ }
  }
  const children = (dir: string): string[] => {
    try { return readdirSync(join(REPO, dir)) } catch { return [] }
  }
  for (const group of children('packages')) {
    for (const name of children(join('packages', group))) add(join(REPO, 'packages', group, name, 'package.json'))
  }
  for (const group of children('vendor')) {
    for (const name of children(join('vendor', group))) add(join(REPO, 'vendor', group, name, 'package.json'))
  }
  for (const name of children('apps')) add(join(REPO, 'apps', name, 'package.json'))
  add(join(REPO, 'package.json'))
  return found
}

/** The transitive dependency closure of the CLI package the profile mirror heals from. */
function closure(): Set<string> {
  const found = manifests()
  const seen = new Set<string>()
  const queue = ['@deepseek-ai/dsh']
  while (queue.length > 0) {
    const name = queue.shift()!
    if (seen.has(name)) continue
    seen.add(name)
    const pkg = found.get(name)
    if (pkg === undefined) continue
    for (const specifier of [
      ...Object.keys(pkg.dependencies ?? {}),
      ...Object.keys(pkg.peerDependencies ?? {}),
    ]) queue.push(packageOf(specifier))
  }
  return seen
}

/** The plugin names a preset file names, excluding none. */
function presetPlugins(path: string): string[] {
  const text = readFileSync(path, 'utf8')
  return [...text.matchAll(/^\s*name:\s*'([^']+)'/gm)].map(match => match[1]!)
}

describe('shipped agent presets', () => {
  it('name only plugins reachable from the install anchor', () => {
    const reachable = closure()
    const missing: string[] = []
    for (const preset of readdirSync(PRESETS)) {
      const file = join(PRESETS, preset, 'agent.cordis.yml')
      if (!existsSync(file)) continue
      for (const specifier of presetPlugins(file)) {
        // `cordis:group` and other non-workspace rows are not packages.
        if (!specifier.startsWith('@deepseek-ai/')) continue
        if (specifier.includes('/src/')) continue
        const name = packageOf(specifier)
        if (!reachable.has(name)) missing.push(`${preset}: ${specifier}`)
      }
    }
    expect(missing, 'a preset row cannot resolve, so creating a session on it fails').toEqual([])
  })
})
