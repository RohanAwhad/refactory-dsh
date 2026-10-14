import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import UserQuestionService from '@deepseek-ai/dsh-user-questions'
import * as workflowSpine from '../src/index.ts'
import type { WorkflowGraph } from '../src/types.ts'

/**
 * Minimal graph: FnNode \u2192 USER gate (proceed/reloop) \u2192 done.
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

describe('user gate free-text answers', () => {
  type Asked = { question: string; detail?: string; options?: Array<{ label: string }> }

  function setup(personAnswers: Array<{ selected: string[]; custom: string }>, asked: Asked[] = []) {
    const ctx = new Context()
    const pluginReady = (async () => {
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
    })()
    return { ctx, pluginReady }
  }

  async function advanceToGate(ctx: Context): Promise<void> {
    const next = await run(ctx, 'wf_next', {})
    if (ids(next).includes('init')) {
      await run(ctx, 'wf_done', { node: 'init' })
      await run(ctx, 'wf_next', {})
    }
  }

  it('routes case-insensitive exact label match', async () => {
    const { ctx, pluginReady } = setup([
      { selected: [], custom: 'Proceed' },
    ])
    await pluginReady
    await advanceToGate(ctx)
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['gate'])
    const result = await run(ctx, 'wf_verdict', { gate: 'gate' })
    expect(result).toMatchObject({ status: 'routed', verdict: 'proceed', answered_by: 'user' })
  })

  it('parses "reloop: feedback" prefix and routes with feedback', async () => {
    const { ctx, pluginReady } = setup([
      { selected: [], custom: 'reloop: make it simpler' },
    ])
    await pluginReady
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

  it('returns replied when the person passes their words to the CEO, gate stays due', async () => {
    const { ctx, pluginReady } = setup([
      { selected: [], custom: 'show me the strategy' },
      { selected: ['ask the CEO'], custom: '' },
    ])
    await pluginReady
    await advanceToGate(ctx)
    expect(ids(await run(ctx, 'wf_next', {}))).toEqual(['gate'])
    const result = await run(ctx, 'wf_verdict', { gate: 'gate' })
    expect(result).toMatchObject({ status: 'replied', gate: 'gate', text: 'show me the strategy' })
    const state = await run(ctx, 'wf_state', {}) as { cursor: string[] }
    expect(state.cursor).toContain('gate')
  })

  it('confirms "stop" as halt before stopping the run', async () => {
    const asked: Asked[] = []
    const { ctx, pluginReady } = setup([
      { selected: [], custom: 'stop' },
      { selected: ['halt'], custom: '' },
    ], asked)
    await pluginReady
    await advanceToGate(ctx)
    expect(await run(ctx, 'wf_verdict', { gate: 'gate' }))
      .toMatchObject({ status: 'routed', verdict: 'halt', answered_by: 'user', halted: true })
    expect(asked[1]!.question).toBe('You answered "stop". Stop the run here?')
    expect(asked[1]!.options!.map(option => option.label)).toEqual(['halt', 'proceed', 'reloop', 'ask the CEO', 'go back'])
  })

  it('confirms plain approval as proceed', async () => {
    const { ctx, pluginReady } = setup([
      { selected: [], custom: 'Looks good!' },
      { selected: ['proceed'], custom: '' },
    ])
    await pluginReady
    await advanceToGate(ctx)
    expect(await run(ctx, 'wf_verdict', { gate: 'gate' }))
      .toMatchObject({ status: 'routed', verdict: 'proceed', answered_by: 'user' })
  })

  it('offers a remark back as reloop feedback, routed only once confirmed', async () => {
    const asked: Asked[] = []
    const { ctx, pluginReady } = setup([
      { selected: [], custom: 'also add a --keep-last flag' },
      { selected: ['reloop'], custom: '' },
    ], asked)
    await pluginReady
    await advanceToGate(ctx)
    expect(await run(ctx, 'wf_verdict', { gate: 'gate' })).toMatchObject({
      status: 'routed', verdict: 'reloop', answered_by: 'user', feedback: 'also add a --keep-last flag',
    })
    expect(asked[1]!.detail).toBe('Your note: also add a --keep-last flag')
    expect(asked[1]!.options![0]!.label).toBe('reloop')
  })

  it('does not carry the note when the person picks a different outcome than suggested', async () => {
    const { ctx, pluginReady } = setup([
      { selected: [], custom: 'also add a --keep-last flag' },
      { selected: ['proceed'], custom: '' },
    ])
    await pluginReady
    await advanceToGate(ctx)
    const routed = await run(ctx, 'wf_verdict', { gate: 'gate' }) as Record<string, unknown>
    expect(routed).toMatchObject({ status: 'routed', verdict: 'proceed' })
    expect(routed.feedback).toBeUndefined()
  })

  it('returns to the original question on "go back"', async () => {
    const asked: Asked[] = []
    const { ctx, pluginReady } = setup([
      { selected: [], custom: 'stop' },
      { selected: ['go back'], custom: '' },
      { selected: ['proceed'], custom: '' },
    ], asked)
    await pluginReady
    await advanceToGate(ctx)
    expect(await run(ctx, 'wf_verdict', { gate: 'gate' })).toMatchObject({ status: 'routed', verdict: 'proceed' })
    expect(asked.map(question => question.question)).toEqual([asked[0]!.question, 'You answered "stop". Stop the run here?', asked[0]!.question])
  })

  it('hands a question straight to the CEO without a follow-up', async () => {
    const asked: Asked[] = []
    const { ctx, pluginReady } = setup([{ selected: [], custom: 'what does section 3 mean?' }], asked)
    await pluginReady
    await advanceToGate(ctx)
    expect(await run(ctx, 'wf_verdict', { gate: 'gate' }))
      .toMatchObject({ status: 'replied', text: 'what does section 3 mean?' })
    expect(asked).toHaveLength(1)
  })
})
