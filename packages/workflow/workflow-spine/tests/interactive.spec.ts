import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import UserQuestionService from '@deepseek-ai/dsh-user-questions'
import * as workflowSpine from '../src/index.ts'
import type { WorkflowGraph } from '../src/types.ts'

/** A mini design-v2: FnNode -> director(expand) -> synthesize -> USER gate -> builder -> AGENT gate -> fork/join. */
const graph: WorkflowGraph = {
  name: 'design-v2-mini',
  start_node: 'init',
  nodes: {
    init: { _type: 'FnNode', id: 'init', content: 'brief', writes: ['intent.md'] },
    research_director: {
      _type: 'AgentNode', id: 'research_director', role: 'ceo', prompt_template: 'plan',
      expand: { plan: 'p.json', role: 'researcher', slug_field: 'slug', prompt_field: 'prompt', writes_template: 'research-{slug}.md' },
    },
    synthesize: { _type: 'AgentNode', id: 'synthesize', role: 'strategist', prompt_template: 'synth', writes: ['plan.md'] },
    gate_plan: { _type: 'GateNode', id: 'gate_plan', evaluator_type: 'user', reads: ['plan.md'] },
    builder: { _type: 'AgentNode', id: 'builder', role: 'builder', prompt_template: 'build', writes: ['out.md'] },
    gate_build: { _type: 'GateNode', id: 'gate_build', evaluator_type: 'agent', reads: ['out.md'] },
    fork_qa: { _type: 'ForkNode', id: 'fork_qa', targets: ['qa1', 'qa2'] },
    qa1: { _type: 'AgentNode', id: 'qa1', role: 'reviewer', prompt_template: 'qa1', writes: [], reads: [] },
    qa2: { _type: 'AgentNode', id: 'qa2', role: 'reviewer', prompt_template: 'qa2', writes: [], reads: [] },
    join_qa: { _type: 'JoinNode', id: 'join_qa', sources: ['qa1', 'qa2'] },
    done: { _type: 'FnNode', id: 'done', writes: [], command: '' },
  },
  edges: [
    { source: 'init', target: 'research_director' },
    { source: 'research_director', target: 'synthesize' },
    { source: 'synthesize', target: 'gate_plan' },
    { source: 'gate_plan', target: 'builder', condition: 'proceed' },
    { source: 'gate_plan', target: 'research_director', condition: 'reloop' },
    { source: 'builder', target: 'gate_build' },
    { source: 'gate_build', target: 'fork_qa', condition: 'proceed' },
    { source: 'gate_build', target: 'builder', condition: 'reloop' },
    { source: 'fork_qa', target: 'join_qa' },
    { source: 'join_qa', target: 'done' },
  ],
}

async function run(ctx: Context, name: string, args: unknown): Promise<unknown> {
  const definition = ctx.tools.get(name)
  if (definition === undefined) throw new Error(`tool ${name} not registered`)
  return definition.execute(args, {} as never)
}

function ids(result: unknown): string[] {
  const nodes = (result as { nodes?: Array<{ id: string }> }).nodes ?? []
  return nodes.map(node => node.id)
}

describe('interactive design-v2 loop', () => {
  it('drives the graph through the tools, including reloop and skip steering', async () => {
    const ctx = new Context()
    await ctx.plugin(SystemPrompt)
    await ctx.plugin(ToolRuntime)
    await ctx.plugin(UserQuestionService)
    // The person at the `user` gate. The CEO's own verdict argument is deliberately
    // the opposite of each answer, so the test fails if the model's word routes the
    // gate instead of the person's.
    const personSays: string[] = []
    ctx.on('user-questions/request', request => Promise.resolve({
      answers: request.questions.map(question => ({
        id: question.id,
        selected: [personSays.shift() ?? 'proceed'],
      })),
    }))
    await ctx.plugin(workflowSpine, { graph, toolPrefix: 'wf', maxIterations: 3 })

    // init -> research director
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['init'])
    await run(ctx, 'wf_done', { node: 'init' })
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['research_director'])

    // director expansion -> two parallel researchers
    await run(ctx, 'wf_expand', { director: 'research_director', entries: [{ slug: 'a', prompt: 'p1' }, { slug: 'b', prompt: 'p2' }] })
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['research_director__a', 'research_director__b'])
    await run(ctx, 'wf_done', { node: 'research_director__a' })
    await run(ctx, 'wf_done', { node: 'research_director__b' })
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['synthesize'])
    await run(ctx, 'wf_done', { node: 'synthesize' })

    // USER gate: the person reloops with feedback, though the CEO asked to proceed.
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['gate_plan'])
    personSays.push('reloop')
    await run(ctx, 'wf_verdict', { gate: 'gate_plan', verdict: 'proceed', feedback: 'also research security' })
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['research_director'])
    const state = await run(ctx, 'wf_state', {})
    expect((state as { feedback: Record<string, string> }).feedback.research_director).toBe('also research security')

    // re-expand and reach the gate again: now the person proceeds, though the CEO
    // asked to reloop.
    await run(ctx, 'wf_expand', { director: 'research_director', entries: [{ slug: 'security', prompt: 'p3' }] })
    await run(ctx, 'wf_done', { node: 'research_director__security' })
    await run(ctx, 'wf_done', { node: 'synthesize' })
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['gate_plan'])
    personSays.push('proceed')
    await run(ctx, 'wf_verdict', { gate: 'gate_plan', verdict: 'reloop' })

    // build -> agent gate -> proceed to fork
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['builder'])
    await run(ctx, 'wf_done', { node: 'builder' })
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['gate_build'])
    await run(ctx, 'wf_verdict', { gate: 'gate_build', verdict: 'proceed' })
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['fork_qa'])

    // skip steering: hot-remove the QA fork
    await run(ctx, 'wf_edit', { node: 'fork_qa', action: 'skip' })
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['join_qa'])
    const final = await run(ctx, 'wf_state', {})
    expect((final as { skipped: string[] }).skipped).toEqual(['fork_qa', 'qa1', 'qa2'])

    await run(ctx, 'wf_done', { node: 'join_qa' })
    await run(ctx, 'wf_done', { node: 'done' })
    expect(await run(ctx, 'wf_next', {})).toMatchObject({ status: 'done' })
  })
})
