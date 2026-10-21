import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { Spine } from '../src/spine'

interface RoutingCase {
  name: string
  gate: string
  edges: { target: string; condition: string | null }[]
  verdict: string
  gate_kind: string
  expect: { target?: string; halt?: boolean; reason?: string; error?: boolean }
}

const casesPath = resolve(__dirname, '../../../refactory/refactory_dsh/data/verdict-routing-cases.json')
const cases: RoutingCase[] = JSON.parse(readFileSync(casesPath, 'utf-8'))

function buildSpine(gate: string, edges: RoutingCase['edges'], gateKind: string): Spine {
  // Build a minimal graph with a start node, the gate, and all targets.
  const targets = new Set(edges.map(e => e.target))
  const nodes: Record<string, any> = {
    _start: { _type: 'AgentNode', successors: [gate] },
    [gate]: {
      _type: 'GateNode',
      evaluator_type: gateKind === 'user' ? 'user' : 'fn',
      evaluator_command: gateKind === 'fn' ? 'echo proceed' : undefined,
      gate_prompt: 'test gate',
      successors: edges.map(e => e.target),
      max_iterations: 10,
    },
  }
  for (const t of targets) {
    if (!nodes[t]) {
      nodes[t] = { _type: 'AgentNode', successors: [] }
    }
  }
  const graphEdges = edges.map(e => ({
    source: gate,
    target: e.target,
    condition: e.condition ?? undefined,
  }))
  // Add edge from _start to the gate.
  graphEdges.push({ source: '_start', target: gate, condition: undefined })

  const graph = { name: 'test', nodes, edges: graphEdges, start_node: '_start' }
  return new Spine(graph as any)
}

describe('verdict routing cases', () => {
  for (const c of cases) {
    it(c.name, () => {
      const spine = buildSpine(c.gate, c.edges, c.gate_kind)
      // Advance to the gate.
      spine.complete('_start')
      if (c.expect.error) {
        expect(() => spine.verdict(c.gate, c.verdict)).toThrow()
      } else if (c.expect.halt) {
        const result = spine.verdict(c.gate, c.verdict)
        const status = result.status
        if (status === 'done') {
          expect(result.halted).toBeDefined()
          expect(result.halted!.reason).toBe(c.expect.reason)
        }
      } else {
        const result = spine.verdict(c.gate, c.verdict)
        // The next node should be the expected target.
        const nextNodes = result.status === 'ready' ? result.nodes.map(n => n.id) : []
        expect(nextNodes).toContain(c.expect.target)
      }
    })
  }
})
