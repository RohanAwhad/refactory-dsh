// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render } from '@testing-library/react'
import type { RefactoryActivity, RefactoryRunGraph } from '@deepseek-ai/dsh-refactory-console/types'
import { graphNodeState, layoutGraph, RunGraph } from '../src/client/RunGraph.tsx'

const graph: RefactoryRunGraph = { nodes: ['start', 'left', 'right', 'review'].map(id => ({ id, kind: id === 'review' ? 'GateNode' : 'FnNode' })), edges: [
  { source: 'start', target: 'left', label: '' }, { source: 'start', target: 'right', label: '' },
  { source: 'left', target: 'review', label: '' }, { source: 'right', target: 'review', label: '' },
  { source: 'review', target: 'start', label: 'reloop' },
] }
const activity: RefactoryActivity = { schema_version: 1, run_id: '/run', invocation: 'a', cursor: 0,
  last_event_at: null, started_at: null, nodes: ['review'], completed: ['start', 'left', 'right'],
  wait_reason: null, workers: [], deliveries: [], events: [], outputs: {}, resources: null, control: null, graph }
afterEach(cleanup)

describe('recorded workflow map', () => {
  it('lays out branching cycles without dropping nodes or overlapping a layer', () => {
    const layout = layoutGraph(graph)
    expect(layout.points.size).toBe(4)
    expect(new Set([...layout.points.values()].map(point => JSON.stringify(point))).size).toBe(4)
    expect(layout.points.get('left')!.y).toBe(layout.points.get('right')!.y)
    expect(layout.points.get('review')!.y).toBeGreaterThan(layout.points.get('start')!.y)
    for (const point of layout.points.values()) {
      expect(point.x + 176).toBeLessThan(layout.width)
      expect(point.y + 58).toBeLessThan(layout.height)
    }
  })

  it('distinguishes running, waiting, failed and completed execution', () => {
    expect(graphNodeState('review', activity, 'running')).toBe('running')
    expect(graphNodeState('review', activity, 'waiting_user')).toBe('waiting')
    expect(graphNodeState('review', activity, 'paused')).toBe('paused')
    expect(graphNodeState('review', activity, 'failed')).toBe('failed')
    expect(graphNodeState('start', activity, 'failed')).toBe('completed')
    expect(graphNodeState('unknown', activity, 'running')).toBe('pending')
    expect(graphNodeState('left', { ...activity, graph: { ...graph, skipped: ['left'] } }, 'finished')).toBe('skipped')
  })

  it('renders only recorded edges and lets keyboard users inspect a node', () => {
    const view = render(<RunGraph activity={activity} state="waiting_user" />)
    expect(view.getByRole('button', { name: 'review: waiting' })).toBeDefined()
    expect(view.container.querySelectorAll('path[marker-end]')).toHaveLength(5)
    expect(view.container.querySelector('[data-state="running"]')).toBeNull()
    fireEvent.keyDown(view.getByRole('button', { name: 'review: waiting' }), { key: 'Enter' })
    expect(view.getByRole('status').textContent).toBe('review · waiting')
    expect(view.getByRole('button', { name: 'Follow active' }).getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(view.getByRole('button', { name: 'Follow active' }))
    expect(view.getByRole('button', { name: 'Follow active' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('does not invent topology for an old saved run', () => {
    const view = render(<RunGraph activity={{ ...activity, graph: null }} state="finished" />)
    expect(view.container.querySelector('svg')).toBeNull()
  })
})
