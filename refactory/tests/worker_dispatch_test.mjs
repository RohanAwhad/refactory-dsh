/** Compose the real pinned patches; intercept Codex before any product/model starts. */
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import assert from 'node:assert/strict'
const [root, directory] = process.argv.slice(2)
const project = createRequire(join(root, 'package.json'))
const installed = createRequire(project.resolve('@deepseek-ai/dsh/package.json'))
const load = async name => import(pathToFileURL(installed.resolve(name)).href)
const { composeEntries, loadOverlayPatches } = await load('@deepseek-ai/dsh-app-boot')
const headless = join(installed.resolve('@deepseek-ai/dsh-headless/package.json'), '../cordis.patch.yml')
for (const backend of ['codex', 'claude_code']) {
  const selected = JSON.parse(readFileSync(join(directory, backend + '.json')))
  const entries = composeEntries([
    [{ insert: [{ id: 'system-prompt', name: 'unused' }, { id: 'tools', name: 'unused' }] }],
    loadOverlayPatches('dispatch-test', headless), selected,
  ])
  assert.equal(entries.find(row => row.id === 'headless-runner').disabled, true)
  const driver = entries.filter(row => row.id === 'refactory-worker-driver' && !row.disabled)
  assert.equal(driver.length, 1)
  const provider = entries.find(row => row.id === 'refactory-external-worker')
  assert.equal(provider.config.providerName, 'refactory-worker')
  assert.ok(provider.name.includes(backend === 'codex' ? 'subagent-codex' : 'subagent-claude-code'))
  if (backend !== 'codex') continue
  const providers = new Map(), spawns = [], dispatches = []
  const native = await import(pathToFileURL(provider.name).href)
  native.apply({
    subagents: { registerProvider: value => providers.set(value.name, value) },
    subprocess: { spawn: spec => { spawns.push(spec); throw new Error('TEST: intercepted before product start') } },
    logger: { warn() {} }, emit() {},
  }, native.Config(provider.config))
  const parent = { session: { id: 'parent', header: { cwd: directory } } }
  const settled = Promise.withResolvers()
  const services = {
    loader: { await: async () => {} },
    agentDefaultModel: { currentSelection: () => ({ provider: 'unused', model: 'unused' }) },
    agents: { create: async () => ({ agent: parent, dispose: async () => {} }) },
    headlessStartup: { task: 'write answer.txt with 42', provider: 'test-provider', model: 'test-model' },
    subagents: { start: async (name, request) => {
      dispatches.push(name)
      return providers.get(name).start(request)
    } },
    appExit: code => settled.resolve(code),
  }
  const bridge = await import(pathToFileURL(driver[0].name).href)
  bridge.apply({ get: key => services[key], on() {} })
  assert.equal(await settled.promise, 1) // intercepted, never a product/model turn
  assert.deepEqual(dispatches, ['refactory-worker'])
  assert.equal(spawns.length, 1)
  assert.ok(spawns[0].argv.includes('app-server'))
  assert.ok(spawns[0].argv.some(value => value.includes('codex')))
  assert.equal(spawns[0].cwd, directory)
}
console.log('actual DSH composition and Codex provider spawn boundaries passed; zero model calls')
