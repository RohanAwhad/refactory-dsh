/**
 * One `refactory ceo` terminal, in a process of its own, for terminal-gate.spec.ts (dm-255).
 *
 * Boots the graph profile the way dsh does — DSH's own `boot()` over the profile's bundle
 * layers, user layer and an overlay — with only `graph-runner` (the row that would start the
 * CEO's model) and profile HMR (which waits for dsh's own entry point) turned off. A fresh
 * process matters: the loader applies rows as their modules arrive, and only a cold start
 * shows the order a real run sees. The rows load as installed, so they run against the built
 * packages (`pnpm run build`).
 *
 * It then plays the CEO's one call at a user gate, `wf_verdict` with the gate alone, against a
 * stand-in terminal, types `answer` when the gate is shown, and prints one JSON line: what the
 * call returned or threw, what the terminal showed, the cursor afterwards and the recorded status.
 *
 * Usage: node terminal-gate-run.mjs <scratch dir> <graph.json> <chat: 0|1> <answer or ''> <then wf_next: 0|1>
 *
 * `wf_next` runs whatever follows the gate, so it is asked for only of a graph whose next
 * node is a harmless command; a lowered mode's next node may be an agent.
 */
import { createRequire } from 'node:module'
import { mkdirSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { PassThrough } from 'node:stream'
import { fileURLToPath, pathToFileURL } from 'node:url'

const [scratch, graphPath, chat, answer = '', thenNext = '0'] = process.argv.slice(2)
const repoRoot = fileURLToPath(new URL('../../../../', import.meta.url))
const bundleDir = join(repoRoot, 'packages', 'bundle', 'graph')
const anchor = createRequire(join(repoRoot, 'package.json')).resolve('@deepseek-ai/dsh/package.json')
const installed = createRequire(anchor)
const load = async (name, from = installed) => import(pathToFileURL(from.resolve(name)).href)
const appBoot = await load('@deepseek-ai/dsh-app-boot')
const { provideCmdline } = await load('@deepseek-ai/dsh-cmdline')
const { DSH_LAUNCH_ENVIRONMENT_KEY } = await load('@deepseek-ai/dsh-launch-environment',
  createRequire(installed.resolve('@deepseek-ai/dsh-app-boot/package.json')))

const graph = JSON.parse(readFileSync(graphPath, 'utf8'))
const home = join(scratch, 'dsh-home')
process.env.DSH_HOME = home
process.env.DSH_TELEMETRY_DISABLED = '1'
const profileDir = join(home, 'profiles', 'graph')
mkdirSync(join(profileDir, 'node_modules', '@deepseek-ai'), { recursive: true })
symlinkSync(bundleDir, join(profileDir, 'node_modules', '@deepseek-ai', 'dsh-graph'))
writeFileSync(join(profileDir, 'package.json'), JSON.stringify({
  name: 'dsh-profile-graph', private: true,
  dependencies: { '@deepseek-ai/dsh-graph': `link:${bundleDir}` },
  dsh: { profile: { bundles: ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-graph'] } },
}))
writeFileSync(join(profileDir, 'cordis.yml'), '[]\n')
const overlay = join(scratch, 'test.patch.yml')
writeFileSync(overlay, '- id: graph-runner\n  disabled: true\n- id: hmr\n  disabled: true\n')
const graphs = join(scratch, 'graphs')
mkdirSync(graphs, { recursive: true })
writeFileSync(join(graphs, `${graph.name}.graph.json`), JSON.stringify(graph))
const workdir = join(scratch, 'run')
mkdirSync(workdir, { recursive: true })
// The files the gate reviews, as placeholders.
for (const read of graph.nodes[graph.start_node]?.reads ?? []) {
  if (read.endsWith('/')) { mkdirSync(join(workdir, read), { recursive: true }); continue }
  mkdirSync(dirname(join(workdir, read)), { recursive: true })
  writeFileSync(join(workdir, read), `${read}\n`)
}

// The stand-in terminal: always a TTY, so only `--chat` decides whether the person is asked.
const stdin = new PassThrough()
stdin.isTTY = true
Object.defineProperty(process, 'stdin', { value: stdin, configurable: true })
const shown = []
const write = process.stdout.write.bind(process.stdout)
process.stdout.write = chunk => { shown.push(String(chunk)); return true }

const profile = appBoot.loadProfile('dsh', 'graph', anchor, undefined, { userLayer: true })
const resolution = await appBoot.createRuntimeResolution({ installAnchor: anchor, profile })
const profileContext = {
  name: 'graph', dir: profile.dir, patchPath: profile.patchPath, installAnchor: anchor,
  startedBundles: profile.layers.map(layer => layer.packageName), cwd: repoRoot, home,
  overlays: appBoot.loadOverlayPatches('dsh', overlay), telemetryDisabledEnv: '1',
}
const ctx = await appBoot.boot('dsh', join(profileDir, 'cordis.yml'),
  appBoot.readProfilePatches('dsh', profileContext, profile), async host => {
    host.provide('profileContext', profileContext)
    host.provide(DSH_LAUNCH_ENVIRONMENT_KEY, appBoot.loadLayeredEnv('dsh'))
    await host.plugin(appBoot.PluginPackages, { resolution })
    provideCmdline(host, {
      args: ['run', '--mode', graph.name, '--task', 'terminal-gate', '--graphs', graphs, '--workdir', workdir,
        '--python-bin', join(repoRoot, '.venv', 'bin'), ...(chat === '1' ? ['--chat'] : [])],
      exit: () => {},
    })
  })

async function call(name, args = {}) {
  const tool = ctx.get('tools')?.get(name)
  if (tool === undefined) throw new Error(`${name} is not registered in this composition`)
  const result = await tool.execute(args, {})
  return typeof result === 'string' ? JSON.parse(result) : result
}

/** Type `text` once the terminal shows `prompt`. */
async function typeWhenShown(prompt, text) {
  for (let waited = 0; !shown.join('').includes(prompt); waited += 20) {
    if (waited > 30_000) return
    await new Promise(resolve => setTimeout(resolve, 20))
  }
  await new Promise(resolve => setTimeout(resolve, 20))
  stdin.write(`${text}\n`)
}

const outcome = {}
const typing = answer === '' ? Promise.resolve()
  : typeWhenShown('[gate ', answer).then(() => typeWhenShown('Add a note', ''))
try {
  outcome.verdict = await call('wf_verdict', { gate: graph.start_node })
} catch (error) {
  outcome.error = error instanceof Error ? error.message : String(error)
}
await Promise.race([typing, new Promise(resolve => setTimeout(resolve, 100))])
if (outcome.verdict !== undefined) {
  outcome.cursor = (await call('wf_state')).cursor
  if (thenNext === '1') outcome.next = await call('wf_next')
}
try {
  outcome.status = JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8'))
} catch { /* no status recorded */ }
outcome.shown = shown.join('')
outcome.workdir = workdir
await ctx.fiber.dispose()
write(`\n${JSON.stringify(outcome)}\n`)
process.exit(0)
