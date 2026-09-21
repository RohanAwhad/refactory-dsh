/**
 * The spine plugin's composition: graph rejection at load, the cursor status it
 * leaves on disk, executing deterministic nodes, the tool renders, and the guard
 * that takes the CEO's coding tools away.
 *
 * These are the parts a run depends on but a single tool call does not reach.
 */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import { createScope } from '@deepseek-ai/dsh-scope'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import UserQuestionService from '@deepseek-ai/dsh-user-questions'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { afterEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import * as spinePlugin from '../src/index.ts'
import type { Config } from '../src/index.ts'
import type { WorkflowGraph } from '../src/types.ts'

const dirs: string[] = []

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true })
  vi.restoreAllMocks()
})

function scratch(): string {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-spine-composition-'))
  dirs.push(dir)
  return dir
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
    executeCommands: false,
    commandEnv: {},
    commandTimeoutMs: 10_000,
    commandSandboxMode: 'danger-full-access',
    ceoDeny: [],
    ...overrides,
  }
}

/** A context with the two services the plugin injects, and the plugin mounted. */
async function mount(overrides: Partial<Config> = {}): Promise<{ ctx: Context; tools: ToolRuntime }> {
  const root = new Context()
  await root.plugin(SystemPrompt)
  await root.plugin(ToolRuntime)
  await root.plugin(spinePlugin, config(overrides))
  return { ctx: root, tools: root.tools }
}

/** The shell request a node command is resolved from, as the plugin builds it. */
interface ShellRequest {
  command: string
  workdir?: string
  timeoutMs?: number
  env?: Record<string, string>
  sandboxPolicy?: unknown
}

/** A shell that reports whatever the caller asks for. */
function shell(behaviour: (command: string) => { exitCode?: number; stdout?: string; stderr?: string } = () => ({ exitCode: 0 })): object {
  return {
    resolve: (request: ShellRequest) => ({
      command: request.command,
      workdir: request.workdir ?? process.cwd(),
      timeoutMs: request.timeoutMs ?? 10_000,
      stdoutMaxBytes: 1_000_000,
      env: request.env,
      sandboxPolicy: request.sandboxPolicy,
    }),
    run: async (spec: { command: string }) => {
      const result = behaviour(spec.command)
      return {
        exitCode: result.exitCode ?? 0, signal: null, timedOut: false, aborted: false, timeoutMs: 10_000,
        stdout: { text: result.stdout ?? '', truncated: false },
        stderr: { text: result.stderr ?? '', truncated: false },
      }
    },
  }
}

async function call(ctx: Context, name: string, args: unknown = {}): Promise<unknown> {
  const definition = ctx.tools.get(name)
  if (definition === undefined) throw new Error(`tool ${name} not registered`)
  return definition.execute(args, {} as never)
}

describe('mounting a graph', () => {
  it('refuses an invalid graph at load, naming the problem', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await expect(root.plugin(spinePlugin, config({
      graph: { name: 't', start_node: 'missing', nodes: {}, edges: [] },
    }))).rejects.toThrow(/invalid workflow graph:/)
  })

  it('names the node an issue belongs to', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await expect(root.plugin(spinePlugin, config({
      graph: {
        name: 't',
        start_node: 'a',
        nodes: {
          a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', writes: [], reads: [] },
          ghost: { _type: 'AgentNode', id: 'ghost', role: 'builder', prompt_template: 'G', writes: [], reads: [] },
        },
        edges: [],
      },
    }))).rejects.toThrow(/ghost: unreachable from start_node/)
  })
})

describe('the cursor status a run leaves behind', () => {
  it('records ready while work is due and done once it is not', async () => {
    const workdir = scratch()
    const { ctx } = await mount({ workdir })
    await call(ctx, 'wf_next')
    expect(JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8')))
      .toEqual({ status: 'ready' })

    await call(ctx, 'wf_done', { node: 'a' })
    await call(ctx, 'wf_next')
    expect(JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8')))
      .toEqual({ status: 'done' })
  })

  it('records a refusal, with the reason, when no answerer can be asked', async () => {
    const workdir = scratch()
    const { ctx } = await mount({
      workdir,
      graph: {
        name: 't',
        start_node: 'gate',
        nodes: { gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'user', reads: [] } },
        edges: [{ source: 'gate', target: 'gate', condition: 'proceed' }],
      },
    })
    await expect(call(ctx, 'wf_verdict', { gate: 'gate', verdict: 'proceed' }))
      .rejects.toThrow(/mounts no user-questions channel/)
    const recorded = JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8')) as { status: string; error: string }
    expect(recorded.status).toBe('refused')
    expect(recorded.error).toContain('gate gate')
  })

  it('records the failed node\'s own stderr, not only its exit code', async () => {
    // "exit code 4" sends a reader back to re-run the node to find out why; the
    // stderr is the reason, and the run directory is where a reader looks.
    const workdir = scratch()
    const { ctx } = await mount({
      workdir,
      executeCommands: true,
      graph: {
        name: 't',
        start_node: 'run',
        nodes: { run: { _type: 'FnNode', id: 'run', command: 'run-it', reads: [], writes: [] } },
        edges: [],
      },
    })
    ctx.provide('shell', shell(() => ({ exitCode: 4, stderr: 'broken on purpose\n' })))
    await expect(call(ctx, 'wf_next')).resolves.toMatchObject({ status: 'failed', exit_code: 4 })
    const recorded = JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8')) as { status: string; error: string }
    expect(recorded.status).toBe('failed')
    expect(recorded.error).toContain('broken on purpose')
  })

  it('records the exit code alone when a failed node said nothing', async () => {
    const workdir = scratch()
    const { ctx } = await mount({
      workdir,
      executeCommands: true,
      graph: {
        name: 't',
        start_node: 'run',
        nodes: { run: { _type: 'FnNode', id: 'run', command: 'run-it', reads: [], writes: [] } },
        edges: [],
      },
    })
    ctx.provide('shell', shell(() => ({ exitCode: 4 })))
    await expect(call(ctx, 'wf_next')).resolves.toMatchObject({ status: 'failed' })
    const recorded = JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8')) as { error: string }
    expect(recorded.error).toBe('run exited with code 4')
  })

  it('runs the graph even when the status cannot be written', async () => {
    // A file where the directory should be: the write fails and is swallowed.
    const blocked = join(scratch(), 'not-a-directory')
    writeFileSync(blocked, 'x')
    const { ctx } = await mount({ workdir: join(blocked, 'child') })
    await expect(call(ctx, 'wf_next')).resolves.toMatchObject({ status: 'ready' })
  })
})

describe('deterministic nodes', () => {
  it('hands the node over when no shell is mounted', async () => {
    const { ctx } = await mount({
      executeCommands: true,
      graph: {
        name: 't',
        start_node: 'run',
        nodes: { run: { _type: 'FnNode', id: 'run', command: 'true', reads: [], writes: [] } },
        edges: [],
      },
    })
    await expect(call(ctx, 'wf_next')).resolves.toMatchObject({ status: 'ready', nodes: [{ id: 'run' }] })
  })

  it('runs the last deterministic node and reports done', async () => {
    const { ctx } = await mount({
      workdir: scratch(),
      executeCommands: true,
      graph: {
        name: 't',
        start_node: 'run',
        nodes: { run: { _type: 'FnNode', id: 'run', command: 'true', reads: [], writes: [] } },
        edges: [],
      },
    })
    ctx.provide('shell', shell())
    await expect(call(ctx, 'wf_next')).resolves.toMatchObject({ status: 'done' })
  })
})

describe('tool presentation', () => {
  it('renders every tool\'s value as compact JSON text', async () => {
    const { ctx } = await mount()
    for (const name of ['wf_next', 'wf_done', 'wf_verdict', 'wf_edit', 'wf_expand', 'wf_state']) {
      const definition = ctx.tools.get(name)
      expect(definition).toBeDefined()
      const rendered = definition?.output.render({}, { status: 'ready' })
      expect(rendered?.[0]).toMatchObject({ type: 'text' })
    }
  })

  it('unskips a node through the edit tool', async () => {
    const { ctx } = await mount()
    await call(ctx, 'wf_edit', { node: 'a', action: 'skip' })
    expect((await call(ctx, 'wf_state')) as { skipped: string[] }).toMatchObject({ skipped: ['a'] })
    await call(ctx, 'wf_edit', { node: 'a', action: 'unskip' })
    expect((await call(ctx, 'wf_state')) as { skipped: string[] }).toMatchObject({ skipped: [] })
  })
})

describe('asking a person', () => {
  it('refuses an answer that carries no verdict', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await root.plugin(UserQuestionService)
    root.on('user-questions/request', (request: { questions: Array<{ id: string }> }) =>
      Promise.resolve({ answers: request.questions.map(question => ({ id: question.id, selected: [] })) }))
    await root.plugin(spinePlugin, config({
      graph: {
        name: 't',
        start_node: 'gate',
        nodes: { gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'user', reads: [] } },
        edges: [{ source: 'gate', target: 'gate', condition: 'proceed' }],
      },
    }))
    await expect(call(root, 'wf_verdict', { gate: 'gate', verdict: 'ignored' }))
      .rejects.toThrow(/answered without a verdict/)
  })

  it('forwards the calling agent and its abort signal to the answerer', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await root.plugin(UserQuestionService)
    let seen: { agent?: unknown; signal?: unknown } = {}
    root.on('user-questions/request', (request: { questions: Array<{ id: string }>; agent?: unknown; signal?: unknown }) => {
      seen = { agent: request.agent, signal: request.signal }
      return Promise.resolve({ answers: request.questions.map(question => ({ id: question.id, selected: ['proceed'] })) })
    })
    await root.plugin(spinePlugin, config({
      graph: {
        name: 't',
        start_node: 'gate',
        nodes: { gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'user', reads: [] } },
        edges: [{ source: 'gate', target: 'gate', condition: 'proceed' }],
      },
    }))
    const controller = new AbortController()
    const agent = { id: 'agent-1' } as unknown as Agent
    // The question service accepts only the registry's exact live root agent.
    root.provide('agents', { get: (id: string) => (id === 'agent-1' ? agent : undefined), roots: () => [agent] })
    const definition = root.tools.get('wf_verdict')
    await definition?.execute({ gate: 'gate', verdict: 'ignored' }, { agent, signal: controller.signal } as never)
    expect(seen.agent).toBe(agent)
    expect(seen.signal).toBe(controller.signal)
  })

  it('asks a gate that lists no outcomes as a free-text question', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await root.plugin(UserQuestionService)
    let asked: { options?: unknown } = {}
    root.on('user-questions/request', (request: { questions: Array<{ id: string; options?: unknown }> }) => {
      asked = request.questions[0] ?? {}
      return Promise.resolve({ answers: [{ id: 'gate', selected: [], custom: 'proceed' }] })
    })
    await root.plugin(spinePlugin, config({
      graph: {
        name: 't',
        start_node: 'gate',
        nodes: { gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'user', reads: [] } },
        edges: [{ source: 'gate', target: 'gate' }],
      },
    }))
    await expect(call(root, 'wf_verdict', { gate: 'gate', verdict: 'ignored' }))
      .resolves.toMatchObject({ status: 'routed', verdict: 'proceed', answered_by: 'user' })
    expect(asked.options).toBeUndefined()
  })

  it('records a refusal when the answerer itself fails, whatever it throws', async () => {
    const workdir = scratch()
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await root.plugin(UserQuestionService)
    // A listener may reject with anything, so the plugin must report it rather than
    // crash; a rejection that is not an Error is the case under test.
    const notAnError = { toString: () => 'no answerer here' }
    // oxlint-disable-next-line typescript/prefer-promise-reject-errors -- see above.
    root.on('user-questions/request', () => Promise.reject(notAnError))
    await root.plugin(spinePlugin, config({
      workdir,
      graph: {
        name: 't',
        start_node: 'gate',
        nodes: { gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'user', reads: [] } },
        edges: [{ source: 'gate', target: 'gate' }],
      },
    }))
    await expect(call(root, 'wf_verdict', { gate: 'gate', verdict: 'ignored' }))
      .rejects.toThrow(/could not be asked: no answerer here/)
    expect(JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8')))
      .toMatchObject({ status: 'refused' })
  })
})

describe('the CEO deny guard', () => {
  function agentWith(ctx: Context | undefined, origin = 'root'): Agent {
    return { id: 'agent-1', session: { header: { origin } }, ctx } as unknown as Agent
  }

  /**
   * An agent context inside the composition. The agent runtime mints its context
   * from the agent's own composition, so `tools` resolves there; a bare scope
   * context does not carry that injection, and the proxy supplies the service the
   * guard reads from the agent's side.
   */
  function agentCtxIn(root: Context, parent: object): Context {
    const scoped = createScope(root, {}, { parent }).ctx
    return new Proxy(scoped, {
      get: (target, prop, receiver): unknown => (prop === 'tools' ? root.tools : Reflect.get(target, prop, receiver)),
    })
  }

  /** A root with the injected services, a scoped composition, and the plugin mounted. */
  async function composed(deny: string[]): Promise<{
    root: Context
    composition: Context
    compositionKey: object
    restrict: MockInstance<typeof ToolRuntime.prototype.restrict>
  }> {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    const compositionKey = {}
    const composition = createScope(root, compositionKey).ctx
    await composition.plugin(spinePlugin, config({ ceoDeny: deny }))
    // The tool registry is one instance; a scoped context resolves the same one.
    const restrict = vi.spyOn(root.tools, 'restrict')
    return { root, composition, compositionKey, restrict }
  }

  it('denies a named tool the CEO can actually reach, and disposes with the agent', async () => {
    const { root, composition, compositionKey, restrict } = await composed(['wf_next', 'ghost-tool'])
    const disposed: number[] = []
    restrict.mockImplementation(() => () => { disposed.push(1) })
    const agent = agentWith(agentCtxIn(root, compositionKey))

    composition.emit('agent/created', { agent })
    expect(restrict).toHaveBeenCalledWith({ deny: ['wf_next'] })

    composition.emit('agent/disposed', { agent })
    expect(disposed).toHaveLength(1)
  })

  it('leaves a subagent alone', async () => {
    const { root, composition, compositionKey, restrict } = await composed(['wf_next'])
    const agentCtx = createScope(root, {}, { parent: compositionKey }).ctx
    composition.emit('agent/created', { agent: agentWith(agentCtx, 'subagent') })
    expect(restrict).not.toHaveBeenCalled()
  })

  it('leaves an agent outside the composition alone', async () => {
    const { root, composition, restrict } = await composed(['wf_next'])
    const elsewhere = createScope(root, {}).ctx
    composition.emit('agent/created', { agent: agentWith(elsewhere) })
    expect(restrict).not.toHaveBeenCalled()
  })

  it('skips the guard when the composition is unscoped', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await root.plugin(spinePlugin, config({ ceoDeny: ['wf_next'] }))
    const restrict = vi.spyOn(root.tools, 'restrict')
    root.emit('agent/created', { agent: agentWith(root) })
    expect(restrict).not.toHaveBeenCalled()
  })

  it('leaves an unscoped agent alone', async () => {
    const { root, composition, restrict } = await composed(['wf_next'])
    composition.emit('agent/created', { agent: agentWith(root) })
    expect(restrict).not.toHaveBeenCalled()
  })

  it('denies nothing when the list names tools the CEO cannot reach', async () => {
    const { root, composition, compositionKey, restrict } = await composed(['not-a-tool', 'pwsh'])
    const agentCtx = createScope(root, {}, { parent: compositionKey }).ctx
    composition.emit('agent/created', { agent: agentWith(agentCtx) })
    expect(restrict).not.toHaveBeenCalled()
  })

  it('disposes an agent the guard never restricted without failing', async () => {
    const { root, composition } = await composed(['wf_next'])
    expect(() => { composition.emit('agent/disposed', { agent: agentWith(root) }) }).not.toThrow()
  })
})
