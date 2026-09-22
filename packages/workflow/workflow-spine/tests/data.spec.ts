/**
 * A data node's item loop: the cursor's per-item pass machine, the graph rules the
 * loop relies on, and the plugin driving the loader commands around each pass.
 *
 * Issue #59. The walking executor runs a data node's whole loop in one process
 * because it owns the graph; the spine walks the subgraph the way it walks
 * everything else, one node at a time, so the item loop is split across three
 * loader commands that the composition configures.
 */
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Spine, validateGraph } from '../src/spine.ts'
import { dataCommandOf } from '../src/execute.ts'
import * as spinePlugin from '../src/index.ts'
import type { Config } from '../src/index.ts'
import type { WorkflowGraph, WorkflowNode } from '../src/types.ts'

const dirs: string[] = []

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true })
  vi.restoreAllMocks()
})

function scratch(): string {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-spine-data-'))
  dirs.push(dir)
  return dir
}

/**
 * A data node over a two-node subgraph whose exit is model-owned.
 *
 * The agent node is the point: a subgraph the runtime could run entirely by itself
 * would not need the loop driven from outside.
 */
function dataGraph(): WorkflowGraph {
  return {
    name: 'dataset',
    start_node: 'data',
    nodes: {
      data: {
        _type: 'DataNode', id: 'data',
        task_ref: 'probe', subgraph_entry: 'work', subgraph_exit: 'end',
        reads: [], writes: ['current_item.json'],
      },
      work: { _type: 'FnNode', id: 'work', command: 'true', reads: [], writes: [] },
      end: { _type: 'AgentNode', id: 'end', role: 'builder', prompt_template: 'E', reads: [], writes: [] },
      after: { _type: 'FnNode', id: 'after', command: 'true', reads: [], writes: [] },
    },
    edges: [
      { source: 'data', target: 'after' },
      { source: 'work', target: 'end' },
    ],
  }
}

describe("a data node's pass machine", () => {
  it('runs one pass per item, ending the data node after the last exit', () => {
    const spine = new Spine(dataGraph(), { maxIterations: 2 })
    expect(spine.next().nodes?.[0]).toMatchObject({ id: 'data', _type: 'DataNode' })

    expect(spine.startPass('data', 2)).toMatchObject({
      status: 'ready',
      pass: { node: 'data', count: 2, index: 0, entry: 'work', exit: 'end', finished: false },
    })
    expect(spine.next().nodes?.[0]).toMatchObject({ id: 'work' })

    spine.complete('work')
    expect(spine.passOf()).toMatchObject({ index: 0, finished: false })
    expect(spine.complete('end')).toMatchObject({ pass: { index: 0, finished: true } })

    // The second item: the same subgraph, from its entry again.
    expect(spine.endPass().nodes?.[0]).toMatchObject({ id: 'work' })
    expect(spine.passOf()).toMatchObject({ index: 1, finished: false })

    spine.complete('work')
    spine.complete('end')
    // The last item completes the data node and reaches its successor.
    expect(spine.endPass().nodes?.[0]).toMatchObject({ id: 'after' })
    expect(spine.passOf()).toBeUndefined()
    expect(spine.state().completed).toContain('data')
  })

  it("does not walk out of the subgraph through the exit's own edge", () => {
    const graph = dataGraph()
    const spine = new Spine(
      { ...graph, edges: [...graph.edges, { source: 'end', target: 'after' }] },
      { maxIterations: 2 },
    )
    spine.startPass('data', 1)
    spine.complete('work')
    const afterExit = spine.complete('end')
    expect(afterExit.status).toBe('done')
    expect(afterExit.nodes).toBeUndefined()
    expect(spine.endPass().nodes?.[0]).toMatchObject({ id: 'after' })
  })

  it('refuses to close a pass its exit has not finished', () => {
    const spine = new Spine(dataGraph(), { maxIterations: 2 })
    expect(() => spine.endPass()).toThrow(/no data pass/)
    spine.startPass('data', 1)
    expect(() => spine.endPass()).toThrow(/has not finished/)
  })

  it('refuses an empty selection and a node that is not a data node', () => {
    const spine = new Spine(dataGraph(), { maxIterations: 2 })
    expect(() => spine.startPass('data', 0)).toThrow(/empty selection/)
    expect(() => spine.startPass('work', 1)).toThrow(/not a data node/)
  })
})

describe('the graph rules the pass machine relies on', () => {
  it('refuses a gate as the subgraph exit, naming the exit', () => {
    const graph = dataGraph()
    graph.nodes.end = {
      _type: 'GateNode', id: 'end', evaluator_type: 'fn', evaluator_command: 'true',
      reads: [], writes: [],
    }
    graph.edges = [...graph.edges, { source: 'end', target: 'after' }]
    expect(validateGraph(graph)).toContainEqual({
      node: 'data',
      message: "data node subgraph_exit 'end' is a gate; a pass ends by completing its exit",
    })
  })

  it('refuses a subgraph entry that cannot reach its exit', () => {
    const graph = dataGraph()
    graph.edges = graph.edges.filter(edge => edge.source !== 'work')
    expect(validateGraph(graph)).toContainEqual({
      node: 'data',
      message: "data node subgraph_entry 'work' cannot reach subgraph_exit 'end'",
    })
  })

  it("refuses an explicit edge into the data node's own subgraph", () => {
    const graph = dataGraph()
    graph.edges = [...graph.edges, { source: 'data', target: 'work' }]
    expect(validateGraph(graph)).toContainEqual({
      node: 'data',
      message: "data node has an edge into its own subgraph ('work'), which would run it twice per item",
    })
  })

  it('reaches the subgraph through the data node, not through an edge', () => {
    // Without following `subgraph_entry`, the whole subgraph reads as unreachable.
    expect(validateGraph(dataGraph())).toEqual([])
  })

  it('names a subgraph node that does not exist', () => {
    const graph = dataGraph()
    graph.nodes.data = { ...(graph.nodes.data as WorkflowNode), subgraph_entry: 'nowhere' }
    expect(validateGraph(graph)).toContainEqual({
      node: 'data',
      message: "data node subgraph_entry 'nowhere' is not a node",
    })
  })
})

/** A shell that answers each loader verb with the JSON a real loader prints. */
function loaderShell(items: Array<Record<string, unknown>>, log: string[]): object {
  return {
    resolve: (request: { command: string; workdir?: string; env?: Record<string, string> }) => ({
      command: request.command,
      workdir: request.workdir ?? process.cwd(),
      timeoutMs: 10_000,
      stdoutMaxBytes: 1_000_000,
      env: request.env,
    }),
    run: async (spec: { command: string }) => {
      log.push(spec.command)
      // `python -m refactory_dsh.items <verb> --node '<id>'`
      const verb = spec.command.split(' ')[3] ?? ''
      const index = Number(spec.command.match(/--index (\d+)/)?.[1] ?? 0)
      const answers: Record<string, unknown> = {
        list: { count: items.length, ids: items.map(item => item.id) },
        begin: items[index] ?? {},
        end: { id: items[index]?.id, passed: true, score: 1 },
      }
      // A deterministic node of the subgraph is not a loader command.
      const text = verb in answers ? JSON.stringify(answers[verb]) : ''
      return {
        exitCode: 0, signal: null, timedOut: false, aborted: false, timeoutMs: 10_000,
        stdout: { text, truncated: false },
        stderr: { text: '', truncated: false },
      }
    },
  }
}

function dataConfig(overrides: Partial<Config> = {}): Config {
  return {
    graph: dataGraph(),
    toolPrefix: 'wf',
    maxIterations: 3,
    executeCommands: true,
    commandEnv: {},
    commandTimeoutMs: 10_000,
    commandSandboxMode: 'danger-full-access',
    dataCommand: 'python -m refactory_dsh.items',
    workdir: scratch(),
    ceoDeny: [],
    ...overrides,
  }
}

/** A context with the two injected services, a loader shell, and the plugin. */
async function mount(shell: object, overrides: Partial<Config> = {}): Promise<Context> {
  const root = new Context()
  await root.plugin(SystemPrompt)
  await root.plugin(ToolRuntime)
  root.provide('shell', shell)
  await root.plugin(spinePlugin, dataConfig(overrides))
  return root
}

async function call(ctx: Context, name: string, args: unknown = {}): Promise<unknown> {
  const definition = ctx.tools.get(name)
  if (definition === undefined) throw new Error(`tool ${name} is not registered`)
  return definition.execute(args, {} as never)
}

describe('driving the loader commands around each pass', () => {
  it('selects the items, opens a pass, and hands the subgraph over', async () => {
    const log: string[] = []
    const root = await mount(loaderShell([{ id: 'a' }, { id: 'b' }], log))

    const first = await call(root, 'wf_next') as { nodes: Array<{ id: string }>; item?: unknown }
    expect(log).toEqual([
      "python -m refactory_dsh.items list --node 'data'",
      "python -m refactory_dsh.items begin --node 'data' --index 0",
      // The pass's own deterministic node ran before the agent node was handed over.
      'true',
    ])
    expect(first.item).toEqual({ id: 'a' })
    // The deterministic node of the pass ran; the agent node is what is handed over.
    expect(first.nodes.map(node => node.id)).toEqual(['end'])

    await call(root, 'wf_done', { node: 'end' })
    const second = await call(root, 'wf_next') as { nodes: Array<{ id: string }>; item?: unknown }
    expect(log[3]).toBe("python -m refactory_dsh.items end --node 'data' --index 0")
    expect(log[4]).toBe("python -m refactory_dsh.items begin --node 'data' --index 1")
    expect(log[5]).toBe('true')
    expect(second.item).toEqual({ id: 'b' })
    expect(second.nodes.map(node => node.id)).toEqual(['end'])

    await call(root, 'wf_done', { node: 'end' })
    const last = await call(root, 'wf_next') as { status: string }
    expect(log[6]).toBe("python -m refactory_dsh.items end --node 'data' --index 1")
    expect(last.status).toBe('done')
  })

  it('completes an empty selection instead of hanging on it', async () => {
    const log: string[] = []
    const root = await mount(loaderShell([], log))
    const state = await call(root, 'wf_next') as { status: string }
    expect(log).toEqual([
      "python -m refactory_dsh.items list --node 'data'",
      // Nothing was iterated, and the run continued past the data node.
      'true',
    ])
    expect(state.status).toBe('done')
  })

  it('reports a loader that failed, with its stderr', async () => {
    const failing = {
      resolve: (request: { command: string }) => ({
        command: request.command, workdir: process.cwd(), timeoutMs: 1,
        stdoutMaxBytes: 10, env: {},
      }),
      run: async () => ({
        exitCode: 1, signal: null, timedOut: false, aborted: false, timeoutMs: 1,
        stdout: { text: '', truncated: false },
        stderr: { text: 'TaskError: no Task named probe', truncated: false },
      }),
    }
    const root = await mount(failing)
    const state = await call(root, 'wf_next') as { status: string; stderr: string }
    expect(state.status).toBe('failed')
    expect(state.stderr).toContain('no Task named probe')
  })

  it('refuses a data graph at load when the composition mounts no loader', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    root.provide('shell', loaderShell([], []))
    await expect(root.plugin(spinePlugin, dataConfig({ dataCommand: '' })))
      .rejects.toThrow(/no item loader is configured/)

    const other = new Context()
    await other.plugin(SystemPrompt)
    await other.plugin(ToolRuntime)
    other.provide('shell', loaderShell([], []))
    await expect(other.plugin(spinePlugin, dataConfig({ executeCommands: false })))
      .rejects.toThrow(/node commands are disabled/)
  })
})

describe('the data loader command line', () => {
  it('names the node and the item, and quotes the node id', () => {
    const node = { id: 'data', _type: 'DataNode' }
    expect(dataCommandOf(node, 'list', { dataCommand: 'loader' })).toBe("loader list --node 'data'")
    expect(dataCommandOf(node, 'begin', { dataCommand: 'loader' }, 3))
      .toBe("loader begin --node 'data' --index 3")
    expect(dataCommandOf({ id: "it's", _type: 'DataNode' }, 'list', { dataCommand: 'loader' }))
      .toBe(String.raw`loader list --node 'it'\''s'`)
  })
})
