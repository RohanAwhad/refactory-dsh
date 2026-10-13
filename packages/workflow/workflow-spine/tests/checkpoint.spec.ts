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
  it('preserves an amended prompt on a dispatched AgentNode across checkpoint restore', () => {
    const dispatchedGraph = (): WorkflowGraph => ({
      name: 'checkpoint', start_node: 'a', nodes: {
        a: {
          id: 'a', _type: 'AgentNode', role: 'builder',
          prompt_template: 'build it',
          dispatch_command: 'python -m worker build',
          reads: [], writes: [],
        },
        b: { id: 'b', _type: 'AgentNode', prompt_template: 'review it' },
      }, edges: [{ source: 'a', target: 'b' }],
    })
    const first = new Spine(dispatchedGraph())
    first.amendPrompt('a', 'also handle edge cases', 'append')
    const saved = JSON.parse(JSON.stringify(first.checkpoint()))
    const second = new Spine(dispatchedGraph())
    second.restore(saved)
    expect(second.state()).toEqual(first.state())
    // The amended prompt survives the round-trip.
    expect(second.view('a').prompt_template).toBe('build it\n\nalso handle edge cases')
    // The dispatch command is still there.
    expect(second.view('a').command).toBe('python -m worker build')
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
