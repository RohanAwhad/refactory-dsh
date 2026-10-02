/**
 * The acceptance check for the re:factory GUI profile's layers.
 *
 * It is a script, not a spec, because what it proves is a whole-profile fact: an
 * owned `refactory` profile, provisioned from the harness's `web` template
 * by `dsh_profile.provision_plan()` and carrying both checkout bundles, declares
 * `CEO`, records `ceo` for a session created without a preset, gives that
 * session's agent the workflow tools and none of the denied ones, and mounts
 * `refactory-web`'s console, picker and brand rows with a `repoRoot` resolved
 * from the checkout rather than the caller's working directory. The harness's
 * own `web` profile is checked alongside it: it must keep exactly the template's
 * bundle list and mount none of re:factory's rows.
 *
 * It drives the *real* services rather than re-reading files: the same
 * `agentPresets.list()` the picker calls, the same `agents.create()` path
 * `session/create` takes through `SessionController`, and the same
 * `refactoryConsole/project` the panel reads. Nothing here is a stub, so a
 * passing run is the evidence the issue asks for.
 *
 * The composition is the real `ceo` preset, which requires the whole host
 * composition it is normally mounted inside. The script therefore composes a
 * bootstrap `cordis.yml` from the profile's own layers over the installed
 * harness anchor and boots the profile's real `dsh --profile refactory`
 * against it.
 *
 * Usage (from the repository root, after `pnpm run build`):
 *
 *   node packages/bundle/refactory/tests/verify-ceo-root.mjs
 *
 * It boots the owned GUI profile on a spare port, checks the facts, and exits
 * nonzero on the first one that does not hold.
 */
import { spawn, spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = resolve(HERE, '..', '..', '..', '..')
const BUNDLE = resolve(HERE, '..')

/** The re:factory-owned GUI profile the provisioning plan composes. */
const PROFILE = 'refactory'

/** The harness's own template the owned profile is initialized from. */
const WEB_TEMPLATE = 'web'

/** The interpreter that renders `dsh_profile`'s plan, from the repo's venv. */
const PYTHON = join(REPO, '.venv', 'bin', 'python')

/** Every fact the issue's acceptance criteria name, in the order it states them. */
const failures = []
function check(ok, label, detail) {
  const mark = ok ? 'ok  ' : 'FAIL'
  process.stdout.write(`${mark} ${label}${detail === undefined ? '' : `\n     ${detail}`}\n`)
  if (!ok) failures.push(label)
}

/** Run one command, returning its exit code and combined output. */
function run(command, args, options = {}) {
  const result = spawnSync(command, args, { encoding: 'utf8', ...options })
  return { code: result.status ?? 1, out: `${result.stdout ?? ''}${result.stderr ?? ''}` }
}

/**
 * The `ceoDeny` names the shipped composition declares, read from the preset
 * itself rather than restated here: a hand-copied list drifts the moment the
 * preset gains an entry, which is how `str_replace_editor` went unchecked.
 *
 * Reading it is only honest if a failed read is loud. An absent `ceoDeny:` makes
 * `indexOf` return -1, `slice(-1)` yield the last character, and the match come
 * back empty — which would make "no denied tool is present" true by vacuity. So
 * the extraction throws instead, and the run reports it as a failure.
 */
const DENIED = (() => {
  const composition = readFileSync(join(BUNDLE, 'presets', 'ceo.patch.yml'), 'utf8')
  const at = composition.indexOf('ceoDeny:')
  if (at < 0) throw new Error("the composition declares no `ceoDeny:` block to check against")
  const block = composition.slice(at)
  const names = [...block.matchAll(/^\s+-\s+(\S+)$/gmu)].map(match => match[1])
  if (names.length === 0) throw new Error('the composition\'s `ceoDeny:` block lists no tools')
  return names
})()

/** The bundle list a profile's manifest declares, or [] when it is absent. */
function bundlesOf(home, name) {
  try {
    const manifest = JSON.parse(readFileSync(join(home, 'profiles', name, 'package.json'), 'utf8'))
    return manifest.dsh?.profile?.bundles ?? []
  } catch {
    return []
  }
}

/**
 * Whether an installed harness layer declares a preset whose id is `ceo`.
 *
 * A preset is one `@deepseek-ai/dsh-agent-preset` row inside a patch file the
 * layer lists in `dsh.bundle.patch`, so the declaration is read from those files
 * rather than from a preset directory: at this pin the harness ships no preset
 * tree to walk.
 * @param name - the harness package whose declared patch files are read.
 * @returns true when the layer declares `ceo`, false when it does not, and
 * undefined when the package or its manifest cannot be resolved — which the
 * caller reports as a failure rather than letting an absent read pass for
 * "declares no ceo preset".
 */
function installedHarnessDeclaresCeo(name) {
  const dir = resolveInstalledPackage(name)
  if (dir === undefined) return undefined
  let manifest
  try {
    manifest = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'))
  } catch {
    return undefined
  }
  const declared = manifest.dsh?.bundle?.patch
  for (const file of Array.isArray(declared) ? declared : [declared]) {
    if (typeof file !== 'string') continue
    try {
      if (readFileSync(join(dir, file), 'utf8').includes('id: ceo')) return true
    } catch {
      // A patch file the manifest names but does not ship is the harness's own
      // packaging problem, not a `ceo` declaration; keep scanning the rest.
    }
  }
  return false
}

/**
 * The installed directory of one harness package, found the way the harness
 * finds a bundle: `packageDirFromAnchor` probes Node's resolution paths outward
 * from the dsh install anchor, then from a profile directory. Returns
 * `undefined` when neither resolves it, which the caller reports as a failure
 * rather than letting it read as "no ceo preset".
 * @param name - the package to resolve.
 * @returns its directory, or undefined.
 */
function resolveInstalledPackage(name) {
  const probe = run('node', ['-e', `
    const { createRequire } = require('node:module')
    const { existsSync } = require('node:fs')
    const { join } = require('node:path')
    const name = process.argv[1]
    for (const anchor of process.argv.slice(2)) {
      if (!existsSync(anchor)) continue
      for (const base of createRequire(anchor).resolve.paths(name) ?? []) {
        const candidate = join(base, name)
        if (existsSync(join(candidate, 'package.json'))) {
          process.stdout.write(candidate)
          process.exit(0)
        }
      }
    }
    process.exit(1)
  `,
  name,
  join(REPO, 'node_modules', '@deepseek-ai', 'dsh', 'package.json'),
  // The pnpm isolated layout's hoisted root is a directory, not a package, so
  // the anchor is any manifest inside it — resolution then walks outward from
  // the hoisted `node_modules`, which is where the shared copy lives. The
  // filesystem spells a scoped name as two directories, so the anchor is built
  // from the split name rather than from a literal that only fits one package.
  join(REPO, 'node_modules', '.pnpm', 'node_modules', ...name.split('/'), 'package.json'),
  ])
  return probe.code === 0 ? probe.out.trim() : undefined
}

const dsh = join(REPO, 'node_modules', '.bin', 'dsh')
if (!existsSync(dsh)) {
  process.stderr.write(`verify: no pinned launcher at ${dsh}; run pnpm install first\n`)
  process.exit(2)
}

// 1. The `ceo` preset must come from this bundle and nowhere else: the roster
//    keeps the first declaration per id, so a harness layer that declared `ceo`
//    of its own would let every live check below pass while this bundle's
//    declaration did nothing. The old mechanism — a patched
//    `@deepseek-ai/dsh-agent-presets` shipping the preset tree — is checked gone
//    first, then the installed harness layers are read for a `ceo` declaration.
//
//    Each layer is found the way the harness finds a bundle: `resolveBundleDir`
//    probes Node's `require.resolve.paths` outward from an install anchor, which
//    for a pnpm checkout lands in the hoisted `node_modules/.pnpm/node_modules`.
//    Probing by path instead would silently pass on a layout this repo does not
//    use.
const workspace = readFileSync(join(REPO, 'pnpm-workspace.yaml'), 'utf8')
check(
  !workspace.includes('dsh-agent-presets@0.2.0-rc.2'),
  'no agent-presets entry under patchedDependencies',
)
check(
  !existsSync(join(REPO, 'patches', '@deepseek-ai__dsh-agent-presets@0.2.0-rc.2.patch')),
  'the agent-presets patch file is deleted',
)
const harnessCeo = ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']
  .map(name => [name, installedHarnessDeclaresCeo(name)])
check(
  harnessCeo.every(([, declares]) => declares === false),
  'the INSTALLED harness declares no ceo preset of its own',
  harnessCeo.map(([name, declares]) => `${name}: ${String(declares)}`).join(', '),
)

// 2. The install path composes a re:factory-OWNED profile from the harness's
//    `web` template instead of appending re:factory's layers to the harness's
//    `web` profile. These checks drive `dsh_profile.provision_plan()` — the same
//    table `install.sh`, `./refactory-dsh` and `refactory doctor` use — so the
//    composition that ships is what is checked, not a copy of it made here.
const home = mkdtempSync(join(tmpdir(), 'dsh-refactory-'))
let web
try {
  // The harness's own `web` profile, created by the harness from its template
  // before the plan runs. Its bundle list must be exactly what it started as.
  const template = run(dsh, ['--profile', WEB_TEMPLATE, '--dump-config'], {
    env: { ...process.env, DSH_HOME: home },
  })
  check(template.code === 0, 'the harness web template loads', template.out.slice(0, 400))
  const templateBundles = bundlesOf(home, WEB_TEMPLATE)
  check(
    templateBundles.length > 0 && !templateBundles.some(entry => entry.includes('refactory')),
    'the harness web template starts with no re:factory layers',
    `bundles: ${templateBundles.join(', ')}`,
  )

  const plan = run(PYTHON, [
    '-c',
    'from refactory_dsh import dsh_profile; print(dsh_profile.render_plan(), end="")',
  ], {
    env: { ...process.env, DSH_HOME: home },
  })
  check(plan.code === 0, 'the provisioning plan renders', plan.out.trim())
  const steps = plan.out.split('\n').filter(Boolean).map(line => line.split('\t'))
  check(steps.length > 0, 'a fresh home needs provisioning', plan.out.trim())
  // `value` is a template for `init`, a directory under `packages/bundle/` for
  // `add`, and a package name for `remove` — so only `add` is joined to that root.
  for (const [kind, profile, value] of steps) {
    const step = kind === 'init'
      ? run(dsh, ['--profile', profile, '--from-default-profile', value, '--dump-config'], {
        env: { ...process.env, DSH_HOME: home },
      })
      : kind === 'remove'
        ? run(dsh, ['plugin', '--profile', profile, 'remove', value], {
          env: { ...process.env, DSH_HOME: home },
        })
        : run(dsh, ['plugin', '--profile', profile, 'add', join(REPO, 'packages', 'bundle', value)], {
          env: { ...process.env, DSH_HOME: home },
        })
    check(step.code === 0, `the plan ${kind}s ${value} into ${profile}`, step.out.slice(0, 400))
  }

  check(
    JSON.stringify(bundlesOf(home, WEB_TEMPLATE)) === JSON.stringify(templateBundles),
    'the plan leaves the harness web template exactly as the template defines it',
    `after: ${bundlesOf(home, WEB_TEMPLATE).join(', ')}`,
  )

  const bundles = bundlesOf(home, PROFILE)
  const webApp = bundles.indexOf('@deepseek-ai/dsh-web-app')
  check(
    webApp >= 0,
    'the owned profile was initialized from the web template',
    `bundles: ${bundles.join(', ')}`,
  )
  check(
    bundles.indexOf('@deepseek-ai/dsh-refactory') > webApp,
    'the preset layer is applied after dsh-web-app, which inserts the agent-preset-registry row',
    `bundles: ${bundles.join(', ')}`,
  )
  check(
    bundles.indexOf('@deepseek-ai/dsh-refactory-web') > webApp,
    'the web-surface layer is applied after dsh-web-app, whose rows it overrides',
    `bundles: ${bundles.join(', ')}`,
  )

  const dump = run(dsh, ['--profile', PROFILE, '--dump-config'], {
    env: { ...process.env, DSH_HOME: home },
  })
  // The launcher reports a patch whose `id` matches no row as
  // `dsh: [<layer>] patch: entry "<id>" not found` on stderr and still exits 0,
  // so the exit code alone would pass on a composition whose layer order left a
  // patch unreachable. The skipped lines are the whole symptom of that — an
  // out-of-order list keeps `default: standard` and leaves `ui-brand-official`
  // enabled — so they are matched explicitly.
  const skipped = dump.out.match(/^dsh: \[.*?\] patch: entry ".*?" not found$/gmu) ?? []
  check(
    dump.code === 0 && skipped.length === 0,
    'the composed profile dumps with every layer patch applied',
    skipped.length > 0 ? skipped.join('\n     ') : dump.out.slice(0, 400),
  )
  check(
    /- id: preset-ceo\b/.test(dump.out),
    'the preset layer inserts the ceo preset declaration',
  )
  check(dump.out.includes('default: ceo'), 'the agent-preset-registry row restates default: ceo')
  check(
    dump.out.includes('ctx.refactoryWebStartup.repoRoot'),
    'the refactory-console row reads the published checkout root',
  )
  check(
    /id: ui-brand-official[\s\S]{0,120}?disabled: true/.test(dump.out),
    'the web-surface bundle disables the official brand row',
  )
  for (const row of ['refactory-console', 'ui-refactory', 'ui-brand-refactory']) {
    check(dump.out.includes(row), `the composed profile mounts ${row}`)
  }

  // The harness's own `web` profile, re-read after the plan: a session a person
  // starts with `dsh --profile web` must find none of re:factory's rows.
  const templateAfter = run(dsh, ['--profile', WEB_TEMPLATE, '--dump-config'], {
    env: { ...process.env, DSH_HOME: home },
  })
  for (const row of [
    'preset-ceo',
    'refactoryWebStartup',
    'refactory-console',
    'ui-refactory',
    'ui-brand-refactory',
    'default: ceo',
  ]) {
    check(
      !templateAfter.out.includes(row),
      `a session on the harness web template mounts no ${row}`,
    )
  }

  // 3. Boot the profile and ask its live services.
  const port = 3099 + Math.floor(Math.random() * 400)
  web = spawn(dsh, ['--profile', PROFILE, '--port', String(port), '--no-open', '--host', '127.0.0.1'], {
    env: { ...process.env, DSH_HOME: home },
    // Run the server from somewhere that is not the checkout. `refactory-web`
    // resolves `repoRoot` from its own module location, and when the child
    // inherits this script's cwd (the repository root) `process.cwd()` would
    // satisfy the assertion below by accident, so the check could not fail for
    // the defect it names.
    cwd: tmpdir(),
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let log = ''
  web.stdout.on('data', (chunk) => { log += String(chunk) })
  web.stderr.on('data', (chunk) => { log += String(chunk) })
  const token = await waitForToken(() => log, web)
  const base = `http://127.0.0.1:${String(port)}`
  const cookie = await authenticate(base, token)

  const roster = await rpc(base, cookie, 'agentPresets/list', {})
  const value = roster?.result?.value
  const ceo = value?.presets?.find(preset => preset.id === 'ceo')
  check(ceo !== undefined, 'the roster carries the ceo row declared by the preset layer')
  check(ceo?.name === 'CEO', 'the ceo row shows the display name CEO, not the id', `name: ${String(ceo?.name)}`)
  check(ceo?.broken === undefined, 'the ceo row is not broken', String(ceo?.broken ?? ''))
  check(ceo?.isDefault === true, 'the ceo row is the roster default')
  check(
    value?.presets?.every(preset => preset.broken === undefined),
    'no row on the roster is broken',
  )

  // The page's own module manifest is where a browser plugin that never mounted
  // would be missing; that is the shape of the `Failed to load plugins` failure.
  const page = await (await fetch(`${base}/`, { headers: { cookie } })).text()
  check(page.includes('@deepseek-ai/dsh-client-ui-refactory'), 'the boot manifest lists ui-refactory')
  check(
    page.includes('@deepseek-ai/dsh-client-ui-brand-refactory'),
    'the boot manifest lists ui-brand-refactory',
  )

  // The console's `repoRoot` is the checkout, resolved by the web bundle's startup
  // provider rather than from the launcher's working directory.
  const project = await rpc(base, cookie, 'refactoryConsole/project', {})
  check(
    project?.result?.value === realpathSync(REPO),
    'refactoryConsole/project resolves the checkout root',
    String(project?.result?.value),
  )
  const modes = await rpc(base, cookie, 'refactoryConsole/modes', {})
  const catalogue = modes?.result?.value
  check(
    Array.isArray(catalogue) && catalogue.length > 0,
    'refactoryConsole/modes returns the real catalogue',
    `${Array.isArray(catalogue) ? catalogue.length : 'non-array'} rows`,
  )

  const created = await rpc(base, cookie, 'session/create', { request: {} })
  const sessionId = created?.result?.value?.sessionId
  check(
    created?.result?.value?.agentPreset === 'ceo',
    'session/create without a preset returns agentPreset: ceo',
    JSON.stringify(created?.result?.value ?? created),
  )

  const tools = await composedToolCatalog(base, cookie, sessionId, message => { log += message })
  check(tools !== undefined, 'the composed agent answers a tool-catalog question', String(tools ?? ''))
  if (tools !== undefined) {
    const names = new Set(tools)
    const workflow = [...names].filter(name => name.startsWith('wf_'))
    check(workflow.length > 0, 'the CEO agent gets the wf_* tools', workflow.sort().join(', '))
    // The preset's own `ceoDeny`, read from the composition rather than restated
    // here: a hand-copied list drifts the moment the preset gains an entry, which
    // is how `str_replace_editor` went unchecked.
    const denied = DENIED.filter(name => names.has(name))
    check(denied.length === 0, 'the ceoDeny list is absent from the catalog', denied.join(', '))
  }
} catch (error) {
  check(false, 'the acceptance run completed', error instanceof Error ? error.message : String(error))
} finally {
  web?.kill('SIGTERM')
  await stop(web)
  rmSync(home, { recursive: true, force: true })
}

process.stdout.write(failures.length === 0 ? '\nall acceptance criteria hold\n' : `\n${String(failures.length)} criteria failed\n`)
process.exit(failures.length === 0 ? 0 : 1)

/** Whether a child has stopped — by exit or by signal. `exitCode` alone is false. */
function stopped(child) {
  return child === undefined || child.exitCode !== null || child.signalCode !== null
}

/**
 * Stop a child and wait for it to actually be gone, escalating once.
 *
 * `exitCode` stays `null` for a child killed by a signal, so a check that only
 * reads `exitCode` treats a corpse as running. That matters here because the temp
 * home is removed right after: a server still shutting down logs into a deleted
 * directory and holds the port, so back-to-back runs can collide.
 */
async function stop(child) {
  if (stopped(child)) return
  const exited = new Promise(resolveExit => { child.once('exit', resolveExit) })
  if (!(await within(exited, 10_000))) child.kill('SIGKILL')
  await within(exited, 10_000)
}

/** Resolve `true` if `promise` settles within `ms`, else `false`. */
async function within(promise, ms) {
  return Promise.race([
    promise.then(() => true, () => true),
    new Promise(resolveDelay => { setTimeout(() => resolveDelay(false), ms) }),
  ])
}

/** Wait for the launcher's printed URL and return its token. */
async function waitForToken(read, child) {
  for (let attempt = 0; attempt < 240; attempt++) {
    const match = /[?&]token=([A-Za-z0-9_-]{43})/u.exec(read())
    if (match !== null) return match[1]
    // A signal death leaves `exitCode` null, so ask `stopped`, not `exitCode`:
    // otherwise an early kill spins the full two minutes before reporting.
    if (stopped(child)) throw new Error(`the GUI profile exited before readiness:\n${read()}`)
    await new Promise(resolveDelay => setTimeout(resolveDelay, 500))
  }
  throw new Error(`the web profile never printed a launch URL:\n${read()}`)
}

/** Exchange the launch token for the browser cookie. */
async function authenticate(base, token) {
  const response = await fetch(`${base}/?token=${token}`, { redirect: 'manual' })
  const setCookie = response.headers.get('set-cookie')
  if (setCookie === null) throw new Error('the token exchange omitted Set-Cookie')
  return setCookie.split(';', 1)[0]
}

/** POST one Remote envelope and return the decoded body. */
async function rpc(base, cookie, method, args) {
  const response = await fetch(`${base}/api/${method}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', cookie },
    body: JSON.stringify({ type: 'client-request', rpcId: `verify-${method}`, method, payload: { args } }),
  })
  const text = await response.text()
  try {
    return JSON.parse(text)
  } catch {
    throw new Error(`${method} answered ${String(response.status)}: ${text.slice(0, 300)}`)
  }
}

/**
 * The tool catalog of the agent the created session composed.
 *
 * Nothing lists another agent's tools over the wire, so this reads the one
 * record that is exactly that catalog: the session's `request/header`, whose
 * `tools` are the `ToolSchema[]` the agent assembled when it built its model
 * request — the same array the provider call carries.
 *
 * The header is written on the request path, so there is nothing to read until
 * the agent has proposed a turn. The script prompts once to make that happen. It
 * does not assert that the completion fails: the temp `DSH_HOME` usually has no
 * provider key, so the attempt normally dies at adapter lookup right after the
 * header lands, but on a machine with ambient credentials (a `DEEPSEEK_API_KEY`
 * in the environment) the one completion really runs. That is one cheap call,
 * not a correctness dependency — the header is already written either way.
 * @param base - the running server's origin.
 * @param cookie - the authenticated browser cookie.
 * @param sessionId - the session created without a preset.
 * @param note - sink for the run's own narration.
 * @returns every tool name the composed agent's catalog carried, or undefined.
 */
async function composedToolCatalog(base, cookie, sessionId, note) {
  if (sessionId === undefined) return undefined
  const prompted = await rpc(base, cookie, 'session/prompt', {
    request: {
      requestId: `verify-catalog-${sessionId}`,
      sessionId,
      mode: 'queue',
      content: [{ type: 'text', text: 'Reply with the single word: ready.' }],
    },
  })
  if (prompted?.result?.ok !== true) throw new Error(`session/prompt refused: ${JSON.stringify(prompted)}`)
  // The header lands with the turn; give the settle a moment. `throughSeq` must
  // be a cursor the session has actually reached — asking past it is a
  // `gateway/bad-request`, not an empty page — so the cursor comes from the
  // session list's own projection watermark.
  let cursor
  for (let attempt = 0; attempt < 60; attempt++) {
    if (cursor === undefined) {
      const listed = await rpc(base, cookie, 'session/list', { _request: {} })
      cursor = listed?.result?.value?.items?.find(row => row.sessionId === sessionId)?.projections?.asOfSeq
    }
    if (cursor !== undefined) {
      const page = await rpc(base, cookie, 'session/page', {
        request: { address: { kind: 'session', sessionId }, throughSeq: cursor },
      })
      const records = page?.result?.value?.records ?? []
      const header = records
        .map(record => record?.event)
        .filter(event => event?.type === 'request/header')
        .at(-1)
      const tools = header?.data?.header?.tools
      if (Array.isArray(tools) && tools.length > 0) {
        note(`verify: read ${String(tools.length)} tools off the composed agent's request header\n`)
        return tools.map(tool => tool.name)
      }
      cursor = undefined
    }
    await new Promise(resolveDelay => setTimeout(resolveDelay, 500))
  }
  return undefined
}
