import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import * as workflowSpine from '../src/index.ts'
import type { WorkflowGraph } from '../src/types.ts'

const graph: WorkflowGraph = {
  name: 't',
  start_node: 'a',
  nodes: {
    a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', writes: [], reads: [] },
    gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'user', gate_prompt: 'ok?', reads: [] },
    b: { _type: 'AgentNode', id: 'b', role: 'builder', prompt_template: 'B', writes: [], reads: [] },
  },
  edges: [
    { source: 'a', target: 'gate' },
    { source: 'gate', target: 'b', condition: 'proceed' },
    { source: 'gate', target: 'a', condition: 'reloop' },
  ],
}

async function setup(): Promise<Context> {
  const ctx = new Context()
  await ctx.plugin(SystemPrompt)
  await ctx.plugin(ToolRuntime)
  await ctx.plugin(workflowSpine, { graph, toolPrefix: 'wf', maxIterations: 3 })
  return ctx
}

async function run(ctx: Context, name: string, args: unknown): Promise<unknown> {
  const definition = ctx.tools.get(name)
  if (definition === undefined) throw new Error(`tool ${name} not registered`)
  return definition.execute(args, {} as never)
}

describe('workflow-spine real composition', () => {
  it('registers the six wf_* tools and walks a graph through them', async () => {
    const ctx = await setup()
    for (const name of ['wf_next', 'wf_done', 'wf_verdict', 'wf_edit', 'wf_expand', 'wf_state']) {
      expect(ctx.tools.get(name)).toBeDefined()
    }

    expect(await run(ctx, 'wf_next', {})).toMatchObject({ status: 'ready', nodes: [{ id: 'a' }] })
    await run(ctx, 'wf_done', { node: 'a' })
    expect(await run(ctx, 'wf_next', {})).toMatchObject({ status: 'ready', nodes: [{ id: 'gate' }] })
    await run(ctx, 'wf_verdict', { gate: 'gate', verdict: 'proceed' })
    expect(await run(ctx, 'wf_next', {})).toMatchObject({ status: 'ready', nodes: [{ id: 'b' }] })
    await run(ctx, 'wf_done', { node: 'b' })
    expect(await run(ctx, 'wf_next', {})).toMatchObject({ status: 'done' })
  })
})
