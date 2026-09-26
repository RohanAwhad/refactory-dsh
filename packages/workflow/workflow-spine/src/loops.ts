/** Retry ownership shared by graph admission and routing; no new graph fields. */
import type { WorkflowEdge, WorkflowNode } from './types.ts'

export function retryKey(source: string, target: string, condition?: string | null): string {
  return JSON.stringify([source, target, (condition ?? '').trim().toLowerCase()])
}

/** Cut explicit reloops, then infer only residual single-gate cyclic regions. */
export function retryEdges(nodes: Record<string, WorkflowNode>, edges: WorkflowEdge[]): Set<string> {
  const gates = new Set(Object.keys(nodes).filter(key => nodes[key]?._type === 'GateNode'))
  const loops = new Set(edges.filter(edge => gates.has(edge.source)
    && (edge.condition ?? '').trim().toLowerCase() === 'reloop')
    .map(edge => retryKey(edge.source, edge.target, edge.condition)))
  const adjacent = new Map(Object.keys(nodes).map(key => [key, [] as string[]]))
  for (const edge of edges) {
    if (!loops.has(retryKey(edge.source, edge.target, edge.condition))
      && adjacent.has(edge.source) && adjacent.has(edge.target)) {
      adjacent.get(edge.source)!.push(edge.target)
    }
  }
  for (const [key, node] of Object.entries(nodes)) {
    if (node._type === 'ForkNode') {
      adjacent.get(key)!.push(...(node.targets ?? []).filter(target => adjacent.has(target)))
    } else if (node._type === 'JoinNode') {
      for (const source of node.sources ?? []) adjacent.get(source)?.push(key)
    } else if (node._type === 'DataNode' && node.subgraph_entry !== undefined
      && adjacent.has(node.subgraph_entry)) {
      adjacent.get(key)!.push(node.subgraph_entry)
    }
  }
  // One gate per SCC is not sufficient if an internal cycle bypasses it.
  const visiting = new Set<string>()
  const finished = new Set<string>()
  const checkGuarded = (key: string): void => {
    if (gates.has(key) || finished.has(key)) return
    if (visiting.has(key)) {
      throw new Error(`cycle through ${JSON.stringify(key)} avoids every gate; route every cycle through a gate with a bounded retry transition`)
    }
    visiting.add(key)
    for (const target of adjacent.get(key)!) checkGuarded(target)
    visiting.delete(key)
    finished.add(key)
  }
  for (const key of [...adjacent.keys()].sort()) checkGuarded(key)

  const indices = new Map<string, number>()
  const low = new Map<string, number>()
  const stack: string[] = []
  const active = new Set<string>()
  const visit = (key: string): void => {
    indices.set(key, indices.size)
    low.set(key, indices.get(key)!)
    stack.push(key)
    active.add(key)
    for (const target of adjacent.get(key)!) {
      if (!indices.has(target)) {
        visit(target)
        low.set(key, Math.min(low.get(key)!, low.get(target)!))
      } else if (active.has(target)) {
        low.set(key, Math.min(low.get(key)!, indices.get(target)!))
      }
    }
    if (low.get(key) !== indices.get(key)) return
    const component = new Set<string>()
    while (true) {
      const target = stack.pop()!
      active.delete(target)
      component.add(target)
      if (target === key) break
    }
    if (component.size === 1 && !adjacent.get(key)!.includes(key)) return
    const owners = [...component].filter(id => gates.has(id))
    if (owners.length !== 1) {
      throw new Error(`cycle ${JSON.stringify([...component].sort())} has ${owners.length} gates after explicit reloop edges are removed; label the intended retry transitions 'reloop' so their max_iterations budget is unambiguous`)
    }
    for (const edge of edges) {
      if (edge.source === owners[0] && component.has(edge.target)) {
        loops.add(retryKey(edge.source, edge.target, edge.condition))
      }
    }
  }
  for (const key of [...adjacent.keys()].sort()) if (!indices.has(key)) visit(key)
  return loops
}
