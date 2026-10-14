import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import * as workflowSpine from '../src/index.ts'
import { presentCall, presentResult, presentationMeta } from '../src/present.ts'
import type { SpineTool } from '../src/present.ts'
import type { WorkflowGraph } from '../src/types.ts'

const graph: WorkflowGraph = {
  name: 't',
  start_node: 'a',
  nodes: {
    a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', writes: [], reads: [] },
    b: { _type: 'AgentNode', id: 'b', role: 'builder', prompt_template: 'B', writes: [], reads: [] },
  },
  edges: [{ source: 'a', target: 'b' }],
}

/** The title a call shows before it returns. */
function callTitle(tool: SpineTool, args: Record<string, unknown>): string {
  const view = presentCall(tool, args)
  return view.card === 'generic' ? view.title : ''
}

/** The title a call shows once it returns `value`, or undefined when it keeps its call title. */
function resultTitle(tool: SpineTool, args: Record<string, unknown>, value: unknown): string | undefined {
  const view = presentResult(tool, args, { isError: false, meta: presentationMeta(tool, value) })
  return view?.card === 'generic' ? view.title : undefined
}

describe('spine tool presentation', () => {
  it('says what each call does in plain words', () => {
    expect(callTitle('next', {})).toBe('Run the next workflow step')
    expect(callTitle('edit', { node: 'emit', action: 'append-prompt', text: 'x' })).toBe("Add to emit's prompt")
    expect(callTitle('edit', { node: 'check', action: 'skip' })).toBe('Skip check')
    expect(callTitle('redo', { node: 'emit' })).toBe('Re-run emit')
    expect(callTitle('verdict', { gate: 'gate_strategy' })).toBe('Ask you at gate_strategy')
    expect(callTitle('verdict', { gate: 'gate_qa', verdict: 'reloop' })).toBe('Route gate_qa: reloop')
    expect(callTitle('open', { mode: 'default_pack/design' })).toBe('Open default_pack/design')
    expect(callTitle('steer', { note: 'keep it small', kind: 'constraint' })).toBe('Record a constraint: "keep it small"')
    expect(callTitle('steer', {})).toBe('Read the notes on this run')
  })

  it('names what a step ran and what it waits on next', () => {
    expect(resultTitle('next', {}, {
      status: 'ready', ran: { node: 'emit', type: 'AgentNode' }, nodes: [{ id: 'materialise', _type: 'FnNode' }],
    })).toBe('Ran emit → next: materialise')
    expect(resultTitle('next', {}, {
      status: 'ready', nodes: [{ id: 'gate_strategy', _type: 'GateNode', evaluator_type: 'user' }],
    })).toBe('Waiting for your answer at gate_strategy')
    expect(resultTitle('next', {}, {
      status: 'failed', node: 'python -m refactory_dsh.run x', error: 'materialise exited with code 1',
    })).toBe('Failed: materialise exited with code 1')
    expect(resultTitle('next', {}, { status: 'done', halted: { gate: 'gate_qa', reason: 'verdict' } }))
      .toBe('Workflow stopped at gate_qa')
    expect(resultTitle('next', {}, { status: 'done' })).toBe('Workflow finished')
  })

  it('says who answered a gate and how', () => {
    expect(resultTitle('verdict', { gate: 'g' }, { status: 'routed', gate: 'g', verdict: 'proceed', answered_by: 'user' }))
      .toBe('You chose proceed')
    expect(resultTitle('verdict', { gate: 'g' }, { status: 'replied', gate: 'g', text: 'why?' }))
      .toBe('Your message went to the CEO')
    expect(resultTitle('verdict', { gate: 'g' }, {
      status: 'routed', gate: 'g', verdict: 'halt', answered_by: 'user', halted: true,
    })).toBe('You stopped the run')
  })

  it('keeps the call title when the call was refused', () => {
    expect(presentResult('redo', { node: 'emit' }, { isError: true, meta: {} })).toBeUndefined()
  })

  it('persists only what the titles need, not the run facts', () => {
    const meta = presentationMeta('next', {
      status: 'ready', nodes: [{ id: 'b', _type: 'AgentNode', prompt_template: 'long' }],
      ran: { node: 'a', type: 'AgentNode', facts: { brief: 'x'.repeat(10_000) } },
    })
    expect(meta).toEqual({ status: 'ready', ran: 'a', next: ['b'] })
  })

  it('registers the presenters on every spine tool', async () => {
    const ctx = new Context()
    await ctx.plugin(SystemPrompt)
    await ctx.plugin(ToolRuntime)
    await ctx.plugin(workflowSpine, { graph, toolPrefix: 'wf', maxIterations: 3 })
    for (const name of ['wf_next', 'wf_done', 'wf_verdict', 'wf_edit', 'wf_redo', 'wf_expand', 'wf_state']) {
      const definition = ctx.tools.get(name)
      expect(definition?.presentCall, name).toBeTypeOf('function')
      expect(definition?.presentResult, name).toBeTypeOf('function')
    }
    expect(ctx.tools.get('wf_redo')?.presentCall?.({ node: 'a' })).toMatchObject({ title: 'Re-run a' })
  })
})
