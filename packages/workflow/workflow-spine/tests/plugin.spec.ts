import { describe, expect, it } from 'vitest'
import type { Context } from '@deepseek-ai/cordis'
import { apply } from '../src/index.ts'
import type { WorkflowGraph } from '../src/types.ts'

/** A minimal fake context capturing the tools `apply` registers. */
interface Registered {
  name: string
  execute(args: unknown, exec: unknown): Promise<unknown>
}

function capture(): { ctx: Context; registered: Registered[] } {
  const registered: Registered[] = []
  const ctx = {
    tools: {
      register(definition: { name: string; execute: (args: unknown, exec: unknown) => Promise<unknown> }): void {
        registered.push(definition)
      },
    },
  } as unknown as Context
  return { ctx, registered }
}

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

describe('workflow-spine plugin', () => {
  it('registers the six wf_* tools and walks a graph through them', async () => {
    const { ctx, registered } = capture()
    apply(ctx, { graph, toolPrefix: 'wf', maxIterations: 3 })
    const names = registered.map(tool => tool.name)
    expect(names).toEqual(['wf_next', 'wf_done', 'wf_verdict', 'wf_edit', 'wf_expand', 'wf_state'])

    const byName = (name: string): Registered => registered.find(tool => tool.name === name) as Registered

    const next = await byName('wf_next').execute({}, {})
    expect(next).toMatchObject({ status: 'ready', nodes: [{ id: 'a' }] })

    await byName('wf_done').execute({ node: 'a' }, {})
    const atGate = await byName('wf_next').execute({}, {})
    expect(atGate).toMatchObject({ status: 'ready', nodes: [{ id: 'gate' }] })

    await byName('wf_verdict').execute({ gate: 'gate', verdict: 'proceed' }, {})
    const atB = await byName('wf_next').execute({}, {})
    expect(atB).toMatchObject({ status: 'ready', nodes: [{ id: 'b' }] })

    await byName('wf_done').execute({ node: 'b' }, {})
    const done = await byName('wf_next').execute({}, {})
    expect(done).toMatchObject({ status: 'done' })
  })
})
