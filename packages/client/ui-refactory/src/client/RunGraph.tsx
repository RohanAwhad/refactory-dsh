import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { RefactoryActivity, RefactoryRunGraph } from '@deepseek-ai/dsh-refactory-console/types'
import css from './RefactoryBody.module.css'

type Point = { x: number; y: number }

/** Stable breadth-first layers; back edges remain explicit, including cycles. */
export function layoutGraph(graph: RefactoryRunGraph): { points: Map<string, Point>; width: number; height: number } {
  const ids = new Set(graph.nodes.map(node => node.id))
  const outgoing = new Map<string, string[]>()
  const incoming = new Set<string>()
  for (const edge of graph.edges) {
    if (!ids.has(edge.source) || !ids.has(edge.target)) continue
    outgoing.set(edge.source, [...outgoing.get(edge.source) ?? [], edge.target])
    incoming.add(edge.target)
  }
  const levels = new Map<string, number>()
  const visit = (roots: string[], base: number): void => {
    const queue = roots.map(id => ({ id, level: base }))
    for (let i = 0; i < queue.length; i++) {
      const item = queue[i]!
      if (levels.has(item.id)) continue
      levels.set(item.id, item.level)
      for (const id of outgoing.get(item.id) ?? []) if (!levels.has(id)) queue.push({ id, level: item.level + 1 })
    }
  }
  visit(graph.nodes.filter(node => !incoming.has(node.id)).map(node => node.id), 0)
  for (const node of graph.nodes) if (!levels.has(node.id)) visit([node.id], levels.size ? Math.max(...levels.values()) + 1 : 0)
  const rows = new Map<number, string[]>()
  for (const node of graph.nodes) {
    const level = levels.get(node.id) ?? 0
    rows.set(level, [...rows.get(level) ?? [], node.id])
  }
  const columns = Math.max(1, ...[...rows.values()].map(row => row.length))
  const width = columns * 198 + 66
  const points = new Map<string, Point>()
  for (const [level, row] of rows) row.forEach((id, index) => {
    points.set(id, { x: 32 + (columns - row.length) * 99 + index * 198, y: 24 + level * 106 })
  })
  return { points, width, height: Math.max(120, rows.size * 106 + 8) }
}

export function graphNodeState(id: string, activity: RefactoryActivity, state: string): string {
  if (activity.nodes.includes(id)) {
    if (/failed|refused|died|error|exhaust/i.test(state)) return 'failed'
    if (/paus|halt|cancel|stop/i.test(state)) return 'paused'
    if (/wait|gate/i.test(state) || /waiting for|awaiting|user gate/i.test(activity.wait_reason ?? '')) return 'waiting'
    return /running|executing/i.test(state) ? 'running' : 'current'
  }
  if (activity.graph?.skipped?.includes(id)) return 'skipped'
  return activity.completed.includes(id) ? 'completed' : 'pending'
}

/** Observation only: the graph is supplied by the run, never synthesized from event order. */
export function RunGraph({ activity, state }: { activity: RefactoryActivity; state: string }) {
  const graph = activity.graph
  const id = useId().replace(/:/g, '')
  const viewport = useRef<HTMLDivElement>(null)
  const [follow, setFollow] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)
  const layout = useMemo(() => graph ? layoutGraph(graph) : null, [graph])
  const current = activity.nodes[0]
  const point = current ? layout?.points.get(current) : undefined
  useEffect(() => {
    if (!follow || !point || !viewport.current) return
    const view = viewport.current
    view.scrollTo?.({ top: Math.max(0, point.y - 100), left: Math.max(0, point.x - (view.clientWidth - 176) / 2), behavior: 'auto' })
  }, [follow, current, point?.x, point?.y])
  if (!graph || !layout) return null
  return <section className={css.graph} aria-label="Workflow map" data-refactory-graph>
    <div className={css.graphHeader}><h4 className={css.heading}>Workflow map</h4>
      <button className={css.graphFollow} type="button" aria-pressed={follow} onClick={() => { setFollow(!follow) }}>Follow active</button>
    </div>
    <div className={css.graphViewport} ref={viewport} tabIndex={0} aria-label="Scrollable workflow graph">
      <svg width={layout.width} height={layout.height} viewBox={`0 0 ${layout.width} ${layout.height}`} role="group" aria-labelledby={`${id}-title`}>
        <title id={`${id}-title`}>Recorded workflow connections. Select a node to inspect its state.</title>
        <defs><marker id={`${id}-arrow`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M1 1l6 3-6 3" fill="none" stroke="currentColor" strokeWidth="1.4" /></marker></defs>
        {graph.edges.map((edge, index) => {
          const from = layout.points.get(edge.source), to = layout.points.get(edge.target)
          if (!from || !to) return null
          const back = to.y <= from.y
          const x1 = from.x + 88, y1 = from.y + 58, x2 = to.x + 88, y2 = to.y
          const lane = Math.max(from.x, to.x) + 190 + (index % 3) * 9
          const path = back ? `M${from.x + 176} ${from.y + 29}H${lane}V${to.y + 29}H${to.x + 176}`
            : `M${x1} ${y1}C${x1} ${y1 + 24},${x2} ${y2 - 24},${x2} ${y2}`
          return <g className={css.graphEdge} key={`${edge.source}-${edge.target}-${index}`}>
            <title>{edge.source} → {edge.target}{edge.label ? ` (${edge.label})` : ''}</title>
            <path d={path} fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray={back ? '4 4' : undefined} markerEnd={`url(#${id}-arrow)`} />
            {edge.label && !back && <text x={(x1 + x2) / 2 + 8} y={(y1 + y2) / 2 + 4} className={css.graphEdgeLabel}>{edge.label.length > 16 ? edge.label.slice(0, 15) + '…' : edge.label}</text>}
          </g>
        })}
        {graph.nodes.map(node => {
          const at = layout.points.get(node.id)!
          const status = graphNodeState(node.id, activity, state)
          return <g key={node.id} transform={`translate(${at.x},${at.y})`} className={css.graphNode} data-state={status}
            role="button" tabIndex={0} aria-label={`${node.id}: ${status}`} aria-pressed={selected === node.id}
            onClick={() => { setSelected(node.id) }} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelected(node.id) } }}>
            <title>{node.id} · {node.kind} · {status}</title>
            <rect className={css.graphNodeBox} width="176" height="58" rx="10" />
            <circle className={css.graphSignal} cx="15" cy="18" r="3.5" />
            <text x="27" y="22" className={css.graphNodeTitle}>{node.id.length > 20 ? node.id.slice(0, 19) + '…' : node.id}</text>
            <text x="14" y="43" className={css.graphNodeMeta}>{status === 'completed' ? '✓ completed' : status} · {node.kind.replace(/Node$/, '').toLowerCase()}</text>
          </g>
        })}
      </svg>
    </div>
    {selected && graph.nodes.some(node => node.id === selected) && <p className={css.graphSelection} role="status"><strong>{selected}</strong> · {graphNodeState(selected, activity, state)}</p>}
    <p className={css.note}>Actual connections · completion records execution, not validation. Dashed lines return to an earlier step.</p>
  </section>
}
