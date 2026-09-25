/**
 * Opening a mode's graph after boot, owned by the session that opened it.
 *
 * The web chat session is one long-lived process, so the graph cannot be a
 * mount-time constant the way the `graph` profile makes it. These specs pin the
 * run-time-open composition: a stub open command lowers (here: pretends to lower)
 * a mode and prints where the graph is, `wf_open` claims the run for the calling
 * session, and every other `wf_*` tool works only on that session's run. The
 * configured-graph path is pinned too, so `interactiveOpen: false` stays exactly
 * what it was.
 */
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { afterEach, describe, expect, it } from 'vitest'
import * as spinePlugin from '../src/index.ts'
import type { Config } from '../src/index.ts'
import type { WorkflowGraph } from '../src/types.ts'

const dirs: string[] = []

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true })
})

function scratch(): string {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-spine-open-'))
  dirs.push(dir)
  return dir
}

/** An executable stub, so `spawnSync` runs it exactly as it would the console. */
function executable(body: string): string {
  const path = join(scratch(), 'open-stub')
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

/** One deterministic node, so `wf_next` runs it through the shell. */
const FN_GRAPH: WorkflowGraph = {
  name: 't',
  start_node: 'a',
  nodes: { a: { _type: 'FnNode', id: 'a', command: 'true', reads: [], writes: [] } },
  edges: [],
}

/** A lowered graph on disk, as `interactive.prepare` would leave it. */
function lower(run: string, graph: WorkflowGraph = AGENT_GRAPH): string {
  const path = join(run, 'graphs', 't.graph.json')
  mkdirSync(join(run, 'graphs'), { recursive: true })
  writeFileSync(path, JSON.stringify(graph))
  return path
}

/** The open command's payload, as the console's `open` verb returns it. */
function payload(run: string, graph: string, extra: Record<string, unknown> = {}): string {
  return JSON.stringify({
    mode: 'pkg/t',
    run,
    name: 't',
    graph,
    declarations: join(run, '.refactory', 'declarations.json'),
    ...extra,
  })
}

/** An executable that prints the payload `open` prints, for a run already lowered. */
function openStub(run: string, graph: string, log?: string, extra: Record<string, unknown> = {}): string {
  const line = log === undefined ? '' : `printf '%s\\n' "$@" > '${log}'\n`
  return executable(`${line}printf '%s' '${payload(run, graph, extra)}'`)
}

function config(overrides: Partial<Config> = {}): Config {
  return {
    toolPrefix: 'wf',
    maxIterations: 3,
    interactiveOpen: true,
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
async function mount(overrides: Partial<Config> = {}, shell?: object): Promise<Context> {
  const root = new Context()
  await root.plugin(SystemPrompt)
  await root.plugin(ToolRuntime)
  if (shell !== undefined) root.provide('shell', shell)
  await root.plugin(spinePlugin, config(overrides))
  return root
}

/** A shell that records each command's environment and succeeds. */
function captureShell(seen: Array<Record<string, string> | undefined>): object {
  return {
    resolve: (request: { command: string; workdir?: string; env?: Record<string, string> }) => ({
      command: request.command,
      workdir: request.workdir ?? process.cwd(),
      timeoutMs: 10_000,
      stdoutMaxBytes: 1_000_000,
      env: request.env,
    }),
    run: async (spec: { env?: Record<string, string> }) => {
      seen.push(spec.env)
      return {
        exitCode: 0, signal: null, timedOut: false, aborted: false, timeoutMs: 10_000,
        stdout: { text: '', truncated: false },
        stderr: { text: '', truncated: false },
      }
    },
  }
}

/** The execution facts a tool call is made with: the session that owns it. */
function as(session: string): unknown {
  return { agent: { session: { id: session } } as unknown as Agent }
}

async function call(ctx: Context, name: string, args: unknown = {}, exec: unknown = {}): Promise<unknown> {
  const definition = ctx.tools.get(name)
  if (definition === undefined) throw new Error(`tool ${name} not registered`)
  return definition.execute(args, exec as never)
}

describe('opening a graph at run time', () => {
  it('claims the run for the session and returns the start node', async () => {
    const run = scratch()
    const graph = lower(run)
    const ctx = await mount({ openCommand: openStub(run, graph) })

    const first = await call(ctx, 'wf_open', { mode: 'pkg/t' }, as('session-1'))
    expect(first).toMatchObject({ status: 'ready', nodes: [{ id: 'a' }] })

    // The claim is live: the same session's next call works on the opened run.
    expect(await call(ctx, 'wf_next', {}, as('session-1')))
      .toMatchObject({ status: 'ready', nodes: [{ id: 'a' }] })
  })

  it('refuses a call before any run is open, naming the tool to call', async () => {
    const ctx = await mount({ openCommand: executable('exit 1') })
    await expect(call(ctx, 'wf_next', {}, as('session-1')))
      .rejects.toThrow('no mode is open in this session; call wf_open with a mode first')
  })

  it('refuses a second open from the same session, distinctly', async () => {
    const run = scratch()
    const graph = lower(run)
    const ctx = await mount({ openCommand: openStub(run, graph) })
    await call(ctx, 'wf_open', { mode: 'pkg/t' }, as('session-1'))
    await expect(call(ctx, 'wf_open', { mode: 'pkg/t' }, as('session-1')))
      .rejects.toThrow('a run is already open in this session')
  })

  it('refuses a second open from another session, distinctly', async () => {
    const run = scratch()
    const graph = lower(run)
    const ctx = await mount({ openCommand: openStub(run, graph) })
    await call(ctx, 'wf_open', { mode: 'pkg/t' }, as('session-1'))
    await expect(call(ctx, 'wf_open', { mode: 'pkg/t' }, as('session-2')))
      .rejects.toThrow('a run is already open in another session')
  })

  it('refuses another session\'s tools on the opened run', async () => {
    const run = scratch()
    const graph = lower(run)
    const ctx = await mount({ openCommand: openStub(run, graph) })
    await call(ctx, 'wf_open', { mode: 'pkg/t' }, as('session-1'))
    for (const name of ['wf_next', 'wf_done', 'wf_state']) {
      await expect(call(ctx, name, { node: 'a' }, as('session-2')))
        .rejects.toThrow('a run is already open in another session')
    }
  })

  it('requires an owning agent session to open anything', async () => {
    const run = scratch()
    const graph = lower(run)
    const ctx = await mount({ openCommand: openStub(run, graph) })
    await expect(call(ctx, 'wf_open', { mode: 'pkg/t' }, {}))
      .rejects.toThrow('wf_open requires an owning agent session')
  })

  it('needs an open command to be configured', async () => {
    const ctx = await mount()
    await expect(call(ctx, 'wf_open', { mode: 'pkg/t' }, as('session-1')))
      .rejects.toThrow(/needs an open command/)
  })

  it('passes the mode, the workdir and the knobs to the open command', async () => {
    const run = scratch()
    const graph = lower(run)
    const log = join(scratch(), 'argv.txt')
    const ctx = await mount({ openCommand: openStub(run, graph, log) })
    await call(ctx, 'wf_open', { mode: 'pkg/t', workdir: run, knobs: ['a=1', 'b=2'] }, as('session-1'))
    const argv = readFileSync(log, 'utf8').trim().split('\n')
    expect(argv).toEqual(['pkg/t', '--workdir', run, '--knob', 'a=1', '--knob', 'b=2'])
  })

  it('surfaces the open command\'s refusal rather than opening nothing', async () => {
    const ctx = await mount({ openCommand: executable("echo 'no such mode' >&2\nexit 2") })
    await expect(call(ctx, 'wf_open', { mode: 'pkg/ghost' }, as('session-1')))
      .rejects.toThrow(/could not lower pkg\/ghost: no such mode/)
  })

  it('derives and creates a per-session run directory when the caller names none', async () => {
    const run = scratch()
    const graph = lower(run)
    const log = join(scratch(), 'argv.txt')
    const ctx = await mount({ openCommand: openStub(run, graph, log) })
    // A unique session id, so the derived directory cannot collide with a real one.
    const session = `derived-${Date.now()}-${Math.random().toString(36).slice(2)}`
    const derived = join(process.cwd(), '.factory', 'web', session, 'pkg-t')
    try {
      await call(ctx, 'wf_open', { mode: 'pkg/t' }, as(session))
      const argv = readFileSync(log, 'utf8').trim().split('\n')
      expect(argv).toEqual(['pkg/t', '--workdir', derived])
      expect(existsSync(derived)).toBe(true)
    } finally {
      rmSync(join(process.cwd(), '.factory', 'web', session), { recursive: true, force: true })
    }
  })

  it('runs a node command in the opened run\'s own environment', async () => {
    const run = scratch()
    const graph = lower(run, FN_GRAPH)
    const seen: Array<Record<string, string> | undefined> = []
    const ctx = await mount(
      {
        // The composition's static environment is a decoy: the payload's must win.
        openCommand: openStub(run, graph, undefined, {
          commandEnv: {
            REFACTORY_WORKDIR: run,
            REFACTORY_PROJECT: '/project',
            REFACTORY_KNOBS: '{"step":1}',
            PATH: '/venv/bin',
          },
          dataCommand: 'python -m refactory_dsh.items',
        }),
        executeCommands: true,
        commandEnv: { REFACTORY_WORKDIR: '/wrong', REFACTORY_PROJECT: '/wrong' },
      },
      captureShell(seen),
    )

    await call(ctx, 'wf_open', { mode: 'pkg/t' }, as('session-1'))
    expect(await call(ctx, 'wf_next', {}, as('session-1'))).toMatchObject({ status: 'done' })

    const environment = seen.find(env => env?.DSH_WORKFLOW_NODE !== undefined)
    expect(environment?.REFACTORY_WORKDIR).toBe(run)
    expect(environment?.REFACTORY_PROJECT).toBe('/project')
    expect(environment?.REFACTORY_KNOBS).toBe('{"step":1}')
    expect(environment?.PATH).toBe('/venv/bin')
  })

  it('falls back to the configured environment when the payload omits one', async () => {
    const run = scratch()
    const graph = lower(run, FN_GRAPH)
    const seen: Array<Record<string, string> | undefined> = []
    const ctx = await mount(
      {
        openCommand: openStub(run, graph),
        executeCommands: true,
        commandEnv: { REFACTORY_WORKDIR: '/configured' },
        dataCommand: 'loader',
      },
      captureShell(seen),
    )

    await call(ctx, 'wf_open', { mode: 'pkg/t' }, as('session-1'))
    await call(ctx, 'wf_next', {}, as('session-1'))

    const environment = seen.find(env => env?.DSH_WORKFLOW_NODE !== undefined)
    expect(environment?.REFACTORY_WORKDIR).toBe('/configured')
  })
})

describe('the configured graph still behaves as before', () => {
  it('does not register wf_open and walks the configured graph', async () => {
    const ctx = await mount({ interactiveOpen: false, graph: AGENT_GRAPH })
    expect(ctx.tools.get('wf_open')).toBeUndefined()
    expect(await call(ctx, 'wf_next', {})).toMatchObject({ status: 'ready', nodes: [{ id: 'a' }] })
  })

  it('refuses a missing graph at mount when not opening at run time', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await expect(root.plugin(spinePlugin, config({ interactiveOpen: false })))
      .rejects.toThrow(/no workflow graph is configured/)
  })
})

describe('the status of an opened run', () => {
  it('is written under the opened run\'s workdir, not the configured one', async () => {
    // The configured workdir is a different directory on purpose: the status must
    // follow the run the session opened, not the composition's own.
    const configured = scratch()
    const run = scratch()
    const graph = lower(run)
    const ctx = await mount({ openCommand: openStub(run, graph), workdir: configured })

    await call(ctx, 'wf_open', { mode: 'pkg/t' }, as('session-1'))
    await call(ctx, 'wf_next', {}, as('session-1'))

    const written = join(run, '.refactory', 'spine-status.json')
    expect(existsSync(written)).toBe(true)
    expect(JSON.parse(readFileSync(written, 'utf8'))).toEqual({ status: 'ready', nodes: ['a'], completed: [] })
    expect(existsSync(join(configured, '.refactory', 'spine-status.json'))).toBe(false)
  })
})
