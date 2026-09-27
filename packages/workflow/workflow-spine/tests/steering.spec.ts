/**
 * The live steering channel: the `<toolPrefix>_steer` tool the driver reads for a
 * person's mid-run correction.
 *
 * The spine is generic — it learns a command, runs it with the run's work
 * directory appended, and returns the JSON it prints. These specs pin both halves
 * of that contract: the payload comes back untouched (the command's owner stays
 * the ledger format's owner), every way the command can fail becomes an
 * `unavailable` payload rather than an exception, and the tool exists only when a
 * command and a work directory are configured.
 */
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import { afterEach, describe, expect, it } from 'vitest'
import * as spinePlugin from '../src/index.ts'
import type { Config } from '../src/index.ts'
import type { WorkflowGraph } from '../src/types.ts'

const dirs: string[] = []

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true })
})

function scratch(): string {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-spine-steering-'))
  dirs.push(dir)
  return dir
}

/** An executable stub, so `spawnSync` runs it exactly as it would the console. */
function executable(body: string): string {
  const path = join(scratch(), 'steer-stub')
  writeFileSync(path, `#!/bin/sh\n${body}\n`)
  chmodSync(path, 0o755)
  return path
}

/** One agent node, so the cursor has something a model must do. */
const AGENT_GRAPH: WorkflowGraph = {
  name: 't',
  start_node: 'a',
  nodes: { a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', writes: [], reads: [] } },
  edges: [],
}

function config(overrides: Partial<Config> = {}): Config {
  return {
    graph: AGENT_GRAPH,
    toolPrefix: 'wf',
    maxIterations: 3,
    interactiveOpen: false,
    openCommand: '',
    openArgs: [],
    executeCommands: false,
    commandEnv: {},
    commandTimeoutMs: 10_000,
    commandSandboxMode: 'danger-full-access',
    dataCommand: '',
    steerCommand: '',
    steerArgs: [],
    ceoDeny: [],
    ...overrides,
  }
}

/** A context with the two services the plugin injects, and the plugin mounted. */
async function mount(overrides: Partial<Config> = {}): Promise<Context> {
  const root = new Context()
  await root.plugin(SystemPrompt)
  await root.plugin(ToolRuntime)
  await root.plugin(spinePlugin, config(overrides))
  return root
}

async function call(ctx: Context, name: string): Promise<unknown> {
  const definition = ctx.tools.get(name)
  if (definition === undefined) throw new Error(`tool ${name} not registered`)
  return definition.execute({}, {} as never)
}

describe('the steer tool', () => {
  it('supplies declared inputs and outputs to the worker delivery bridge', async () => {
    const workdir = scratch()
    const request = join(workdir, 'delivery-request.json')
    const graph: WorkflowGraph = {
      ...AGENT_GRAPH,
      nodes: { a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', reads: ['input.txt'], writes: ['result.txt'] } },
    }
    const ctx = await mount({ graph, workdir,
      agentCommand: executable(`cat > '${request}'\nprintf '%s' '{"prompt":"oriented task"}'`),
    })
    await call(ctx, 'wf_next')
    expect(JSON.parse(readFileSync(request, 'utf8'))).toEqual({
      action: 'begin', node: 'a', prompt: 'A', reads: ['input.txt'], writes: ['result.txt'],
    })
  })

  it('returns the payload the configured command prints, unchanged', async () => {
    const payload = {
      run: '/tmp/run',
      ledger: '/tmp/run/intent-ledger.md',
      exists: true,
      entries: [{ at: '2026-01-01T00:00:00+00:00', kind: 'correction', text: 'use a smaller grid' }],
      constraints: [],
      verdicts: {},
      chain: [],
      problems: [],
    }
    const workdir = scratch()
    const ctx = await mount({ steerCommand: executable(`printf '%s' '${JSON.stringify(payload)}'`), workdir })
    expect(await call(ctx, 'wf_steer')).toEqual(payload)
  })

  it('reports the last stderr line as unavailable when the command fails', async () => {
    const workdir = scratch()
    const ctx = await mount({
      steerCommand: executable("echo 'first line' >&2\necho 'no ledger at /run' >&2\nexit 3"),
      workdir,
    })
    await expect(call(ctx, 'wf_steer')).resolves.toEqual({
      status: 'unavailable',
      error: 'no ledger at /run',
    })
  })

  it('reports unavailable rather than throwing on output that is not JSON', async () => {
    const workdir = scratch()
    const ctx = await mount({ steerCommand: executable("echo 'not json at all'"), workdir })
    await expect(call(ctx, 'wf_steer')).resolves.toEqual({
      status: 'unavailable',
      error: 'not json at all',
    })
  })

  it('reports no output when the command fails and says nothing', async () => {
    const workdir = scratch()
    const ctx = await mount({ steerCommand: executable('exit 1'), workdir })
    await expect(call(ctx, 'wf_steer')).resolves.toEqual({ status: 'unavailable', error: 'no output' })
  })

  it('runs the configured command with its args and the workdir last', async () => {
    const workdir = scratch()
    const log = join(scratch(), 'argv.txt')
    const ctx = await mount({
      steerCommand: executable(`printf '%s\\n' "$@" > '${log}'\nprintf '%s' '{}'`),
      steerArgs: ['-m', 'refactory_dsh.console', 'steer', '--run'],
      workdir,
    })
    await call(ctx, 'wf_steer')
    const argv = readFileSync(log, 'utf8').trim().split('\n')
    expect(argv).toEqual(['-m', 'refactory_dsh.console', 'steer', '--run', workdir])
    expect(argv[argv.length - 1]).toBe(workdir)
  })

  it('is not registered when no steering command is configured', async () => {
    const ctx = await mount({ workdir: scratch() })
    expect(ctx.tools.get('wf_steer')).toBeUndefined()
  })

  it('is not registered without a work directory to read', async () => {
    const ctx = await mount({ steerCommand: executable("printf '%s' '{}'") })
    expect(ctx.tools.get('wf_steer')).toBeUndefined()
  })
})
