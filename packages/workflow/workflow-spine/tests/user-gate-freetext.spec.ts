import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import UserQuestionService from '@deepseek-ai/dsh-user-questions'
import * as workflowSpine from '../src/index.ts'
import type { WorkflowGraph } from '../src/types.ts'

/**
 * Minimal graph: FnNode → USER gate (proceed/reloop) → done.
 * The gate has two labelled edges so `acceptedVerdicts` returns ['proceed', 'reloop'].
 */
const graph: WorkflowGraph = {
  name: 'freetext-gate-test',
  start_node: 'init',
  nodes: {
    init: { _type: 'FnNode', id: 'init', content: 'setup', writes: ['intent.md'] },
    gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'user', reads: [] },
    done: { _type: 'FnNode', id: 'done', writes: [], command: '' },
  },
  edges: [
    { source: 'init', target: 'gate' },
    { source: 'gate', target: 'done', condition: 'proceed' },
    { source: 'gate', target: 'init', condition: 'reloop' },
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

/** One question as the user-questions channel received it. */
interface AskedQuestion {
  question: string
  detail?: string
  options?: Array<{ label: string }>
}

/**
 * A composition whose person gives `personAnswers` in turn, one per question asked.
 * @param personAnswers - the answers, consumed in order.
 * @param asked - collects each question as it is asked.
 * @returns the composition, ready to drive.
 */
async function setup(
  personAnswers: Array<{ selected: string[]; custom: string }>, asked: AskedQuestion[] = [],
): Promise<Context> {
  const ctx = new Context()
  await ctx.plugin(SystemPrompt)
  await ctx.plugin(ToolRuntime)
  await ctx.plugin(UserQuestionService)
  ctx.on('user-questions/request', request => Promise.resolve({
    answers: request.questions.map(question => {
      asked.push(question)
      const answer = personAnswers.shift()!
      return { id: question.id, selected: answer.selected, custom: answer.custom }
    }),
  }))
  await ctx.plugin(workflowSpine, { graph, toolPrefix: 'wf', maxIterations: 3 })
  return ctx
}

/** Complete the start node so the user gate is due. */
async function advanceToGate(ctx: Context): Promise<void> {
  const next = await run(ctx, 'wf_next', {})
  if (ids(next).includes('init')) {
    await run(ctx, 'wf_done', { node: 'init' })
    await run(ctx, 'wf_next', {})
  }
}

describe('user gate free-text answers', () => {
  it('routes case-insensitive exact label match', async () => {
    const ctx = await setup([
      { selected: [], custom: 'Proceed' },
    ])
    await advanceToGate(ctx)
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['gate'])
    const result = await run(ctx, 'wf_verdict', { gate: 'gate' })
    expect(result).toMatchObject({ status: 'routed', verdict: 'proceed', answered_by: 'user' })
  })

  it('parses "reloop: feedback" prefix and routes with feedback', async () => {
    const ctx = await setup([
      { selected: [], custom: 'reloop: make it simpler' },
    ])
    await advanceToGate(ctx)
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['gate'])
    const result = await run(ctx, 'wf_verdict', { gate: 'gate' })
    expect(result).toMatchObject({
      status: 'routed',
      verdict: 'reloop',
      answered_by: 'user',
      feedback: 'make it simpler',
    })
  })

  it('hands typed text that is no outcome to the CEO, and the gate stays due', async () => {
    const ctx = await setup([
      { selected: [], custom: 'show me the strategy' },
    ])
    await advanceToGate(ctx)
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['gate'])
    const result = await run(ctx, 'wf_verdict', { gate: 'gate' })
    expect(result).toMatchObject({ status: 'replied', gate: 'gate', text: 'show me the strategy' })
    const state = await run(ctx, 'wf_state', {}) as { cursor: string[] }
    expect(state.cursor).toContain('gate')
  })

  it('asks nothing further before handing text to the CEO', async () => {
    const asked: AskedQuestion[] = []
    const ctx = await setup([{ selected: [], custom: 'i want the unicode' }], asked)
    await advanceToGate(ctx)
    expect(await run(ctx, 'wf_verdict', { gate: 'gate' }))
      .toMatchObject({ status: 'replied', text: 'i want the unicode' })
    expect(asked).toHaveLength(1)
  })
})
