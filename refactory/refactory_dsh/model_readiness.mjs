/** Describe configured credentials through the installed DSH services, without a model request. */
import { createRequire } from 'node:module'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const [repo, requestedProvider, requestedModel] = process.argv.slice(2)
const fibers = []
let stage = 'installed DSH modules'
try {
  // Follow the pinned installation's public dependency graph, never pnpm hashes.
  const project = createRequire(join(repo, 'package.json'))
  const anchor = project.resolve('@deepseek-ai/dsh/package.json')
  const installed = createRequire(anchor)
  const base = createRequire(installed.resolve('@deepseek-ai/dsh-base/package.json'))
  const load = async (name, from = base) => import(pathToFileURL(from.resolve(name)).href)
  const { Context } = await load('@deepseek-ai/cordis', installed)
  const { loadProfile, composeEntries, loadOptionalPatches, loadLayeredEnv } = await load('@deepseek-ai/dsh-app-boot', installed)
  const { resolveDshHome } = await load('@deepseek-ai/dsh-home-paths', installed)
  const { default: Credentials } = await load('@deepseek-ai/dsh-credentials-local')
  const { credentialRef } = await load('@deepseek-ai/dsh-credentials', createRequire(base.resolve('@deepseek-ai/dsh-credentials-local/package.json')))
  const { default: Settings } = await load('@deepseek-ai/dsh-settings-file')
  const { default: DefaultModel } = await load('@deepseek-ai/dsh-agent-default-model')
  const home = resolveDshHome()
  stage = 'headless profile configuration'
  const profile = loadProfile('refactory readiness', 'headless', anchor, home)
  const entries = composeEntries([
    ...profile.layers.map(layer => layer.patches), profile.patches,
    loadOptionalPatches('refactory readiness', join(home, 'cordis.patch.yml')) ?? [],
  ])
  // Check the installed profile's actual entries. A custom service is not silently
  // treated as the local file provider, and !!js config is not reimplemented here.
  const config = (id, name) => {
    const entry = entries.find(candidate => candidate.id === id)
    if (!entry || entry.name !== name || entry.disabled) throw new Error('unsupported composition')
    const plain = value => value === null || ['string', 'number', 'boolean', 'undefined'].includes(typeof value)
      || (Array.isArray(value) ? value.every(plain) : typeof value === 'object'
        && Object.getPrototypeOf(value) === Object.prototype && Object.values(value).every(plain))
    if (!plain(entry.config ?? {})) throw new Error('dynamic configuration requires the full profile')
    return entry.config ?? {}
  }
  const ctx = new Context()
  // Same launch-layer owner as DSH. It may read dotenv itself; no credential
  // values are read, copied or printed by this consumer.
  ctx.provide('launchEnvironment', loadLayeredEnv('refactory readiness'))
  const mount = async (plugin, options) => {
    const fiber = ctx.plugin(plugin, options)
    fibers.push(fiber)
    await fiber.await()
  }
  stage = 'managed credential store'
  await mount(Credentials, { ...config('credentials', '@deepseek-ai/dsh-credentials-local'), watch: false })
  stage = 'model settings'
  await mount(Settings, { ...config('settings', '@deepseek-ai/dsh-settings-file'), watch: false })
  await mount(DefaultModel, config('agent-default-model', '@deepseek-ai/dsh-agent-default-model'))
  const selection = ctx.agentDefaultModel.currentSelection()
  const provider = requestedProvider || selection.provider
  const model = requestedModel || selection.model
  let connection
  if (provider === 'deepseek-official') {
    const { Config } = await load('@deepseek-ai/dsh-llm-deepseek')
    connection = ctx.settings.register('llm-deepseek', Config, {
      base: config('llm-deepseek', '@deepseek-ai/dsh-llm-deepseek'),
    }).get()
  } else {
    const { Config } = await load('@deepseek-ai/dsh-llm-pi-ai')
    const settings = ctx.settings.register('llm-pi-ai', Config, {
      base: config('llm-pi-ai', '@deepseek-ai/dsh-llm-pi-ai'),
    }).get()
    connection = settings.providers?.[provider]
    if (!connection) throw new Error('provider absent from configuration')
  }
  const ref = connection.apiKeyEnv
  const hasCredentialReference = ref !== undefined
  // Native provider authentication (ADC, grants, etc.) is not an API-key check.
  // Keep that limitation explicit instead of inventing a missing key or a pass.
  const info = hasCredentialReference ? await ctx.credentials.describe(credentialRef(ref)) : { configured: null }
  const ready = info.configured !== false
  console.log(JSON.stringify({
    ready, provider, model, authenticated: false,
    credential: { reference: ref ?? null, ...info },
    status: hasCredentialReference ? (ready ? 'configured' : 'missing-credential') : 'native-auth-unverified',
    ...(ready ? {} : { error: `Sandbox model credential ${ref} is not configured for ${provider}. Save it through DSH Settings → Models in the configured DSH home; exported keys are intentionally scrubbed. Authentication has not been tested.` }),
  }))
  if (!ready) process.exitCode = 1
} catch {
  // A parser exception may quote a secret-bearing document. Emit only our stage.
  console.log(JSON.stringify({ ready: false, authenticated: false,
    error: `Model readiness could not describe ${stage}. Check the configured headless profile, settings and managed credential store; no authenticated request was made.` }))
  process.exitCode = 1
} finally {
  for (const fiber of fibers.reverse()) await fiber.dispose()
}
