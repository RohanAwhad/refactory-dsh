/**
 * Correcting a run in flight: reopening a completed node with `redo`, amending an
 * agent/llm node's prompt with `amendPrompt`, and the `wf_redo` / `wf_edit` tools
 * that carry a person's correction from the CEO to the spine.
 *
 * The spine specs drive the state machine directly; the tool specs mount the
 * plugin the way `composition.spec.ts` does, because a correction only counts
 * once it reaches the spine through the tool boundary. Each test builds its own
 * graph: the spine holds the node bodies it was given, so an amendment mutates
 * the graph object itself.
 */
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import { describe, expect, it } from 'vitest'
import * as spinePlugin from '../src/index.ts'
import { Spine } from '../src/spine.ts'
import type { Config } from '../src/index.ts'
import type { WorkflowGraph } from '../src/types.ts'

function ids(spine: Spine): string[] {
  const next = spine.next()
  return next.nodes?.map(node => node.id) ?? []
}

/** A graph with every node kind `redo` has an opinion about. */
function redoGraph(): WorkflowGraph {
  return {
    name: 't',
    start_node: 'a',
    nodes: {
      a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', reads: [], writes: [] },
      b: { _type: 'AgentNode', id: 'b', role: 'builder', prompt_template: 'B', reads: [], writes: [] },
      gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'fn', evaluator_command: 'true', reads: [] },
      data: { _type: 'DataNode', id: 'data', subgraph_entry: 'sub', subgraph_exit: 'subend', reads: [], writes: [] },
      sub: { _type: 'AgentNode', id: 'sub', role: 'builder', prompt_template: 'S', reads: [], writes: [] },
      subend: { _type: 'AgentNode', id: 'subend', role: 'builder', prompt_template: 'SE', reads: [], writes: [] },
    },
    edges: [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'gate' },
      { source: 'gate', target: 'data', condition: 'proceed' },
      { source: 'gate', target: 'a', condition: 'reloop' },
      { source: 'data', target: 'sub' },
      { source: 'sub', target: 'subend' },
    ],
  }
}

describe('redo', () => {
  it('reopens a completed node, moving the cursor back to it and clearing completion', () => {
    const spine = new Spine(redoGraph())
    spine.complete('a')
    expect(ids(spine)).toEqual(['b'])
    const next = spine.redo('a')
    expect(next.status).toBe('ready')
    expect(ids(spine)).toEqual(['a'])
    expect(spine.state().completed).not.toContain('a')
    // The log records the redo as a line naming where it went and what it invalidated.
    expect(spine.state().log).toEqual(expect.arrayContaining([
      expect.stringMatching(/^redid a at \S+; invalidated \S/),
    ]))
  })

  it('clears a skip as well as the completion', () => {
    const spine = new Spine(redoGraph())
    spine.complete('a')
    // A completed node cannot be skipped now, so the skipped work is a due
    // descendant; reopening `a` must clear both histories it invalidates.
    spine.skip('b')
    expect(spine.state().skipped).toContain('b')
    spine.redo('a')
    expect(ids(spine)).toEqual(['a'])
    expect(spine.state().skipped).not.toContain('b')
    expect(spine.state().completed).not.toContain('a')
  })

  it('refuses an unknown node', () => {
    expect(() => new Spine(redoGraph()).redo('ghost')).toThrow(/unknown node ghost/)
  })

  it('refuses a node that has not completed', () => {
    expect(() => new Spine(redoGraph()).redo('b'))
      .toThrow(/node b has not completed, so there is nothing to redo/)
  })

  it('refuses a gate, naming the verdict route instead', () => {
    expect(() => new Spine(redoGraph()).redo('gate'))
      .toThrow(/node gate is a gate; redo a completed upstream agent, then run the gate when due/)
  })

  it('refuses a data node, whose passes are the runner\'s', () => {
    expect(() => new Spine(redoGraph()).redo('data'))
      .toThrow(/node data iterates its items; redo a completed node in its body or upstream/)
  })
})

/** A graph with the prompt-bearing node kinds and one that carries no prompt. */
function amendGraph(): WorkflowGraph {
  return {
    name: 't',
    start_node: 'a',
    nodes: {
      a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', reads: [], writes: [] },
      blank: { _type: 'AgentNode', id: 'blank', role: 'builder', reads: [], writes: [] },
      llm: { _type: 'LLMNode', id: 'llm', system_prompt: 'S', model: 'flash', reads: [], writes: [] },
      alt: { _type: 'LLMNode', id: 'alt', instance_prompt: 'I', model: 'flash', reads: [], writes: [] },
      fn: { _type: 'FnNode', id: 'fn', command: 'true', reads: [], writes: [] },
    },
    edges: [],
  }
}

describe('amendPrompt', () => {
  it('appends to an existing prompt on its own paragraph, and the view carries it', () => {
    const spine = new Spine(amendGraph())
    spine.amendPrompt('a', 'extra', 'append')
    expect(spine.view('a').prompt_template).toBe('A\n\nextra')
    expect(spine.state().prompts).toEqual({ a: 'A\n\nextra' })
    expect(spine.state().log).toContain('prompt a append')
  })

  it('sets a prompt in place of the existing one', () => {
    const spine = new Spine(amendGraph())
    spine.amendPrompt('a', 'replaced', 'set')
    expect(spine.view('a').prompt_template).toBe('replaced')
    expect(spine.state().prompts).toEqual({ a: 'replaced' })
  })

  it('sets when there is no existing prompt', () => {
    const spine = new Spine(amendGraph())
    spine.amendPrompt('blank', 'first', 'append')
    expect(spine.view('blank').prompt_template).toBe('first')
  })

  it('reports only the nodes whose prompt was amended', () => {
    const spine = new Spine(amendGraph())
    spine.amendPrompt('a', 'x', 'set')
    expect(spine.state().prompts).toEqual({ a: 'x' })
    expect(spine.state().prompts).not.toHaveProperty('blank')
  })

  it('amends an LLM node\'s system prompt, which is what its view reports', () => {
    const spine = new Spine(amendGraph())
    spine.amendPrompt('llm', 'more', 'append')
    expect(spine.view('llm').prompt_template).toBe('S\n\nmore')
    expect(spine.state().prompts).toEqual({ llm: 'S\n\nmore' })
  })

  it('amends an LLM node that declared only an instance prompt', () => {
    const spine = new Spine(amendGraph())
    spine.amendPrompt('alt', 'more', 'append')
    expect(spine.view('alt').prompt_template).toBe('I\n\nmore')
  })

  it('refuses an unknown node', () => {
    expect(() => new Spine(amendGraph()).amendPrompt('ghost', 'x', 'set')).toThrow(/unknown node ghost/)
  })

  it('refuses a node type that carries no prompt, naming the type', () => {
    expect(() => new Spine(amendGraph()).amendPrompt('fn', 'x', 'set'))
      .toThrow(/node fn is a FnNode and carries no prompt/)
  })

  it('refuses empty text', () => {
    expect(() => new Spine(amendGraph()).amendPrompt('a', '   ', 'append'))
      .toThrow('prompt amendment text is required')
  })

  it('amends the prompt of an AgentNode that has a dispatch command', () => {
    const graph: WorkflowGraph = {
      name: 't',
      start_node: 'a',
      nodes: {
        a: {
          _type: 'AgentNode', id: 'a', role: 'builder',
          prompt_template: 'original task',
          dispatch_command: 'python -m worker build',
          reads: [], writes: [],
        },
      },
      edges: [],
    }
    const spine = new Spine(graph)
    spine.amendPrompt('a', 'also handle edge cases', 'append')
    expect(spine.view('a').prompt_template).toBe('original task\n\nalso handle edge cases')
    expect(spine.view('a').command).toBe('python -m worker build')
    expect(spine.state().prompts).toEqual({ a: 'original task\n\nalso handle edge cases' })
  })
})

/** One agent node, so the cursor has something a model must do. */
function agentGraph(): WorkflowGraph {
  return {
    name: 't',
    start_node: 'a',
    nodes: { a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', reads: [], writes: [] } },
    edges: [],
  }
}

function config(graph: WorkflowGraph = agentGraph()): Config {
  return {
    graph,
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
  }
}

/** A context with the two services the plugin injects, and the plugin mounted. */
async function mount(graph: WorkflowGraph = agentGraph()): Promise<Context> {
  const root = new Context()
  await root.plugin(SystemPrompt)
  await root.plugin(ToolRuntime)
  await root.plugin(spinePlugin, config(graph))
  return root
}

async function call(ctx: Context, name: string, args: unknown = {}): Promise<unknown> {
  const definition = ctx.tools.get(name)
  if (definition === undefined) throw new Error(`tool ${name} not registered`)
  return definition.execute(args, {} as never)
}

describe('the correction tools', () => {
  it('registers wf_redo', async () => {
    expect((await mount()).tools.get('wf_redo')).toBeDefined()
  })

  it('reopens a completed node so wf_next returns it again, and it leaves completed', async () => {
    const ctx = await mount()
    await call(ctx, 'wf_done', { node: 'a' })
    expect(await call(ctx, 'wf_next')).toMatchObject({ status: 'done' })

    await call(ctx, 'wf_redo', { node: 'a' })
    expect(await call(ctx, 'wf_next')).toMatchObject({ status: 'ready', nodes: [{ id: 'a' }] })
    expect((await call(ctx, 'wf_state') as { completed: string[] }).completed).not.toContain('a')
  })

  it('sets a node\'s prompt through wf_edit, and the next view carries it', async () => {
    const ctx = await mount()
    await call(ctx, 'wf_edit', { node: 'a', action: 'set-prompt', text: 'corrected' })
    expect(await call(ctx, 'wf_next')).toMatchObject({ nodes: [{ id: 'a', prompt_template: 'corrected' }] })
    expect(await call(ctx, 'wf_state')).toMatchObject({ prompts: { a: 'corrected' } })
  })

  it('appends through wf_edit', async () => {
    const ctx = await mount()
    await call(ctx, 'wf_edit', { node: 'a', action: 'append-prompt', text: 'more' })
    expect(await call(ctx, 'wf_next')).toMatchObject({ nodes: [{ prompt_template: 'A\n\nmore' }] })
  })

  it('amends and redoes a dispatched AgentNode through the tools', async () => {
    const dispatched: WorkflowGraph = {
      name: 't',
      start_node: 'a',
      nodes: {
        a: {
          _type: 'AgentNode', id: 'a', role: 'builder',
          prompt_template: 'build it',
          dispatch_command: 'echo dispatched',
          reads: [], writes: [],
        },
      },
      edges: [],
    }
    const ctx = await mount(dispatched)
    // The dispatched node is non-deterministic here (executeCommands: false),
    // so it appears in wf_next for the CEO. But we can still edit its prompt.
    await call(ctx, 'wf_edit', { node: 'a', action: 'append-prompt', text: 'also handle errors' })
    const next = await call(ctx, 'wf_next') as { nodes: Array<{ prompt_template: string }> }
    expect(next.nodes[0]!.prompt_template).toBe('build it\n\nalso handle errors')

    // Complete and redo: the amended prompt survives.
    await call(ctx, 'wf_done', { node: 'a' })
    await call(ctx, 'wf_redo', { node: 'a' })
    const redone = await call(ctx, 'wf_next') as { nodes: Array<{ prompt_template: string }> }
    expect(redone.nodes[0]!.prompt_template).toBe('build it\n\nalso handle errors')
  })

  it('refuses a prompt action with no text', async () => {
    const ctx = await mount()
    await expect(call(ctx, 'wf_edit', { node: 'a', action: 'set-prompt' }))
      .rejects.toThrow(/needs a non-empty prompt amendment/)
  })

  it('still skips and unskips through wf_edit', async () => {
    const ctx = await mount()
    await call(ctx, 'wf_edit', { node: 'a', action: 'skip' })
    expect(await call(ctx, 'wf_state')).toMatchObject({ skipped: ['a'] })
    await call(ctx, 'wf_edit', { node: 'a', action: 'unskip' })
    expect(await call(ctx, 'wf_state')).toMatchObject({ skipped: [] })
  })
})
