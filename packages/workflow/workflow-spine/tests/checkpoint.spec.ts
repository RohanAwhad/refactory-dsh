import { describe, expect, it } from 'vitest'
import { Spine } from '../src/spine.ts'
import type { WorkflowGraph } from '../src/types.ts'

const graph = (): WorkflowGraph => ({
  name: 'checkpoint', start_node: 'a', nodes: {
    a: { id: 'a', _type: 'AgentNode', prompt_template: 'first' },
    b: { id: 'b', _type: 'AgentNode', prompt_template: 'second' },
  }, edges: [{ source: 'a', target: 'b' }],
})

describe('durable graph checkpoints', () => {
  it('retains completed work, prompt correction and armed pause on explicit recovery', () => {
    const first = new Spine(graph())
    first.complete('a')
    first.amendPrompt('b', 'correction', 'append')
    first.pauseBefore('b', true)
    const saved = JSON.parse(JSON.stringify(first.checkpoint()))
    const second = new Spine(graph())
    second.restore(saved)
    expect(second.state()).toEqual(first.state())
    expect(second.isPaused()).toBe(true)
    second.pauseBefore('b', false)
    expect(second.next()).toMatchObject({ nodes: [{ id: 'b', prompt_template: 'second\n\ncorrection' }] })
    second.complete('b')
    expect(second.next().status).toBe('done')
  })
  it('refuses implicit recovery after source changes', () => {
    const source = graph()
    const saved = new Spine(source).checkpoint()
    source.nodes.a!.prompt_template = 'different objective'
    expect(() => new Spine(source).restore(saved)).toThrow(/source graph changed/)
  })
  it('provides invalidation plan without mutating completed results', () => {
    const spine = new Spine(graph())
    spine.complete('a')
    expect(spine.checkpoint()).toMatchObject({ redo_plans: { a: { nodes: ['a', 'b'] } } })
    expect(spine.state().completed).toEqual(['a'])
    spine.stopAtBoundary()
    expect(spine.next()).toMatchObject({ status: 'done', halted: { reason: 'cancelled' } })
    expect(spine.state().completed).toEqual(['a'])
  })
})
