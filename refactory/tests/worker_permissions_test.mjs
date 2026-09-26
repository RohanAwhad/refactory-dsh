/** Actual pinned adapters, with product launch/model transport replaced by inert seams. */
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, realpathSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { PassThrough, Writable } from 'node:stream'
import { pathToFileURL } from 'node:url'
import vm from 'node:vm'

const [root, directory] = process.argv.slice(2)
const project = createRequire(join(root, 'package.json'))
const installed = createRequire(project.resolve('@deepseek-ai/dsh/package.json'))
const boot = await import(pathToFileURL(installed.resolve('@deepseek-ai/dsh-app-boot')).href)
const base = join(dirname(installed.resolve('@deepseek-ai/dsh-base/package.json')), 'cordis.patch.yml')
const headless = join(dirname(installed.resolve('@deepseek-ai/dsh-headless/package.json')), 'cordis.patch.yml')
const request = () => ({ parent: { session: { id: 'permission-test', header: { cwd: directory } } },
  prompt: [{ type: 'text', text: 'This prompt must never reach a product or model.' }], signal: new AbortController().signal })

function providerFor(backend, permission) {
  const patch = JSON.parse(readFileSync(join(directory, `${backend}-${permission}.json`), 'utf8'))
  const entries = boot.composeEntries([boot.loadOverlayPatches('base-test', base),
    boot.loadOverlayPatches('headless-test', headless), patch])
  assert.equal(entries.find(row => row.id === 'headless-runner').disabled, true)
  assert.equal(entries.filter(row => row.id === 'refactory-worker-driver' && !row.disabled).length, 1)
  return entries.find(row => row.id === 'refactory-external-worker')
}

async function codexThreadOptions(row) {
  const native = await import(pathToFileURL(row.name).href)
  let registered, params, spawnCount = 0, modelTurns = 0
  native.apply({ subagents: { registerProvider: value => { registered = value } },
    logger: { warn() {} }, emit() {}, subprocess: { spawn(spec) {
      spawnCount++
      assert.equal(spec.cwd, directory)
      assert.ok(spec.argv.includes('app-server'))
      // This is an in-memory protocol peer, never an operating-system process.
      const stdout = new PassThrough(), done = Promise.withResolvers()
      let buffered = ''
      const stdin = new Writable({ write(chunk, _encoding, callback) {
        buffered += chunk.toString()
        let end
        while ((end = buffered.indexOf('\n')) >= 0) {
          const message = JSON.parse(buffered.slice(0, end)); buffered = buffered.slice(end + 1)
          if (message.method === 'initialize') stdout.write(JSON.stringify({ jsonrpc: '2.0', id: message.id, result: {} }) + '\n')
          else if (message.method === 'thread/start') {
            params = message.params
            stdout.write(JSON.stringify({ jsonrpc: '2.0', id: message.id,
              error: { code: -32000, message: 'Test intercepted thread setup before any model turn.' } }) + '\n')
          } else if (message.method === 'turn/start') modelTurns++
        }
        callback()
      } })
      return { stdin, stdout, done: done.promise, terminate() { done.resolve({ exitCode: 0, signal: null }) },
        async waitForExit() { await done.promise; return true } }
    } } }, native.Config(row.config))
  await assert.rejects(registered.start(request()), /thread-start/)
  assert.equal(spawnCount, 1)
  assert.equal(modelTurns, 0)
  assert.equal(params.cwd, directory)
  assert.equal(params.ephemeral, true)
  return params
}

async function claudeSdkOptions(row) {
  // Evaluate the entire installed provider unchanged. Only the official SDK
  // entry point is replaced; no SDK initialization, native process or model runs.
  const context = vm.createContext({ process, Buffer, AbortController, AbortSignal, setTimeout, clearTimeout,
    setImmediate, clearImmediate, console })
  const resolve = createRequire(realpathSync(row.name))
  let options, queryCalls = 0, nativeSpawns = 0, registered
  const module = new vm.SourceTextModule(readFileSync(row.name, 'utf8'), {
    context, identifier: row.name, initializeImportMeta(meta) { meta.url = pathToFileURL(row.name).href },
  })
  await module.link(async specifier => {
    const exports = specifier === '@anthropic-ai/claude-agent-sdk'
      ? { query(input) { queryCalls++; options = input.options; throw new Error('Intercepted before official SDK execution') } }
      : await import(specifier.startsWith('node:') ? specifier : pathToFileURL(resolve.resolve(specifier)).href)
    const names = Object.keys(exports)
    return new vm.SyntheticModule(names, function () { for (const name of names) this.setExport(name, exports[name]) }, { context })
  })
  await module.evaluate()
  const native = module.namespace
  native.apply({ subagents: { registerProvider: value => { registered = value } }, logger: { warn() {} }, emit() {},
    subprocess: { spawn() { nativeSpawns++; throw new Error('Native spawn forbidden in this test') } } }, native.Config(row.config))
  await assert.rejects(registered.start(request()), /query-start/)
  assert.equal(queryCalls, 1)
  assert.equal(nativeSpawns, 0)
  assert.equal(options.cwd, directory)
  return options
}

for (const permission of ['skip', 'implicit']) {
  const codexSkip = await codexThreadOptions(providerFor('codex', permission))
  assert.equal(codexSkip.approvalPolicy, 'never')
  assert.equal(codexSkip.sandbox, 'danger-full-access')
}
const codexDefault = await codexThreadOptions(providerFor('codex', 'default'))
assert.equal(codexDefault.approvalPolicy, 'never')
assert.equal(Object.hasOwn(codexDefault, 'sandbox'), false)
assert.equal(Object.hasOwn(codexDefault, 'approvalsReviewer'), false)

for (const permission of ['skip', 'implicit']) {
  const claudeSkip = await claudeSdkOptions(providerFor('claude_code', permission))
  assert.equal(claudeSkip.permissionMode, 'bypassPermissions')
  assert.equal(claudeSkip.allowDangerouslySkipPermissions, true)
  assert.equal(Object.hasOwn(claudeSkip, 'canUseTool'), false)
}
const claudeDefault = await claudeSdkOptions(providerFor('claude_code', 'default'))
assert.equal(claudeDefault.permissionMode, 'dontAsk')
assert.equal(Object.hasOwn(claudeDefault, 'allowDangerouslySkipPermissions'), false)
assert.equal((await claudeDefault.canUseTool()).behavior, 'deny')
console.log('Public worker permissions reached actual pinned native adapters; zero products or model turns started.')
