/**
 * The interactive workflow spine: a deterministic cursor/gate/mutation state
 * machine over a workflow graph. It owns the position ("what is next"),
 * gate routing, iteration caps, fork/join barriers, director expansion, and
 * graph edits — so the driving agent never chooses the next step and cannot
 * drift. Pure logic, no DSH imports.
 * @module @deepseek-ai/dsh-workflow-spine/spine
 */

import type { DirectorExpansion, WorkflowEdge, WorkflowGraph, WorkflowNode } from './types.ts'

/** The model-facing view of one node returned by {@link Spine.next}. */
export interface NodeView {
  id: string
  _type: string
  role?: string
  evaluator_type?: string
  gate_prompt?: string
  reads?: string[]
  writes?: string[]
  prompt_template?: string
  content?: string
  command?: string
  targets?: string[]
  sources?: string[]
  expand?: DirectorExpansion
  unsupported?: boolean
}

/** The result of {@link Spine.next}. */
export interface SpineNext {
  status: 'ready' | 'done'
  nodes?: NodeView[]
  feedback?: Record<string, string>
  completed?: number
  skipped?: string[]
}

const DEFAULT_MAX_ITERATIONS = 3

/** Node types the spine advances through; others are marked unsupported. */
const EXECUTED_TYPES = new Set(['AgentNode', 'FnNode', 'Study', 'GateNode', 'ForkNode', 'JoinNode'])

export class Spine {
  private readonly nodes: Map<string, WorkflowNode>
  private edges: WorkflowEdge[]
  private readonly workflow: string
  private readonly projectDir: string
  private readonly maxIterations: number
  private cursor: string[]
  private afters: string[]
  private completed: string[]
  private skipped: string[]
  private readonly gateVerdicts: Map<string, string>
  private readonly iterations: Record<string, number>
  private readonly feedback: Record<string, string>
  private readonly successors: Record<string, string>
  private readonly log: string[]

  constructor(graph: WorkflowGraph, options?: { maxIterations?: number }) {
    this.nodes = new Map(Object.entries(graph.nodes))
    this.edges = JSON.parse(JSON.stringify(graph.edges)) as WorkflowEdge[]
    this.workflow = graph.name
    this.projectDir = graph.project_dir ?? ''
    this.maxIterations = options?.maxIterations ?? DEFAULT_MAX_ITERATIONS
    this.cursor = [graph.start_node]
    this.afters = []
    this.completed = []
    this.skipped = []
    this.gateVerdicts = new Map()
    this.iterations = {}
    this.feedback = {}
    this.successors = {}
    this.log = []
  }

  private outgoing(nid: string): WorkflowEdge[] {
    return this.edges.filter(edge => edge.source === nid)
  }

  private unconditionalTarget(nid: string): string | undefined {
    return this.outgoing(nid).find(edge => edge.condition == null)?.target
  }

  private conditionalTarget(nid: string, condition: string): string | undefined {
    return this.outgoing(nid).find(edge => edge.condition === condition)?.target
  }

  /** Un-complete `target` and its unconditional downstream up to `gate`. */
  private uncompleteBetween(target: string, gate: string): void {
    const seen = new Set<string>()
    const queue: string[] = [target]
    while (queue.length > 0) {
      const node = queue.shift()
      if (node === undefined || seen.has(node) || node === gate) continue
      seen.add(node)
      for (const edge of this.outgoing(node)) {
        if (edge.condition == null && edge.target !== gate && !seen.has(edge.target)) {
          queue.push(edge.target)
        }
      }
      const body = this.nodes.get(node)
      if (body?._type === 'ForkNode') {
        for (const child of body.targets ?? []) {
          if (child !== gate && !seen.has(child)) queue.push(child)
        }
      }
    }
    for (const node of seen) {
      this.completed = this.completed.filter(entry => entry !== node)
      this.gateVerdicts.delete(node)
    }
  }

  private describe(nid: string): NodeView {
    const node = this.nodes.get(nid)
    if (node === undefined) throw new Error(`unknown node ${nid}`)
    const view: NodeView = { id: nid, _type: node._type }
    if (node.role !== undefined) view.role = node.role
    if (node._type === 'GateNode') {
      if (node.evaluator_type !== undefined) view.evaluator_type = node.evaluator_type
      view.gate_prompt = node.gate_prompt ?? ''
      view.reads = node.reads ?? []
    }
    if (node._type === 'AgentNode') {
      view.writes = node.writes ?? []
      view.reads = node.reads ?? []
      view.prompt_template = node.prompt_template ?? ''
      if (node.expand !== undefined) view.expand = node.expand
    }
    if (node._type === 'ForkNode') view.targets = node.targets ?? []
    if (node._type === 'JoinNode') view.sources = node.sources ?? []
    if (node._type === 'FnNode' || node._type === 'Study') {
      view.writes = node.writes ?? []
      view.command = node.command ?? ''
      if (node.content !== undefined) view.content = node.content
    }
    if (!EXECUTED_TYPES.has(node._type)) view.unsupported = true
    return view
  }

  /** The node(s) due now, or `done`. */
  next(): SpineNext {
    if (this.cursor.length === 0) {
      return { status: 'done', completed: this.completed.length, skipped: [...this.skipped] }
    }
    return { status: 'ready', nodes: this.cursor.map(nid => this.describe(nid)), feedback: { ...this.feedback } }
  }

  /** Mark a non-gate node complete and advance. */
  complete(node: string): SpineNext {
    const body = this.nodes.get(node)
    if (body === undefined) throw new Error(`unknown node ${node}`)
    if (body._type === 'GateNode') throw new Error(`${node} is a gate — use verdict()`)
    if (this.skipped.includes(node)) return this.next()
    if (!this.completed.includes(node)) {
      this.completed.push(node)
      this.log.push(`completed ${node}`)
    }

    if (body._type === 'ForkNode') {
      const join = this.unconditionalTarget(node)
      if (join !== undefined) this.afters.push(join)
      this.cursor = (body.targets ?? []).filter(target => !this.skipped.includes(target))
      return this.next()
    }

    this.cursor = this.cursor.filter(entry => entry !== node)
    if (this.cursor.length === 0) {
      if (this.afters.length > 0) {
        this.cursor = [this.afters.pop() as string]
      } else {
        const target = this.unconditionalTarget(node)
        this.cursor = target === undefined ? [] : [target]
      }
    }
    return this.next()
  }

  /** Route a gate. `verdict` is proceed | reloop | halt. */
  verdict(gate: string, verdict: string, feedback = ''): SpineNext {
    if (this.nodes.get(gate)?._type !== 'GateNode') throw new Error(`${gate} is not a gate`)
    if (verdict !== 'proceed' && verdict !== 'reloop' && verdict !== 'halt') {
      throw new Error(`bad verdict ${verdict}`)
    }
    if (!this.completed.includes(gate)) this.completed.push(gate)
    this.gateVerdicts.set(gate, verdict)

    if (verdict === 'reloop') {
      const target = this.conditionalTarget(gate, 'reloop')
      if (target === undefined) throw new Error(`${gate} has no reloop edge`)
      const count = (this.iterations[gate] ?? 0) + 1
      this.iterations[gate] = count
      if (count > this.maxIterations) {
        this.gateVerdicts.set(gate, 'halt')
        this.cursor = []
        this.afters = []
        this.log.push(`gate ${gate}: max iterations -> halt`)
        return this.next()
      }
      this.feedback[target] = feedback
      this.uncompleteBetween(target, gate)
      this.cursor = [target]
      this.afters = []
      this.log.push(`gate ${gate}: reloop -> ${target} (${count})`)
    } else if (verdict === 'halt') {
      const target = this.conditionalTarget(gate, 'halt')
      this.cursor = target === undefined ? [] : [target]
      this.afters = []
      this.log.push(`gate ${gate}: halt`)
    } else {
      const target = this.conditionalTarget(gate, 'proceed') ?? this.unconditionalTarget(gate)
      this.cursor = target === undefined ? [] : [target]
      this.afters = []
      this.log.push(`gate ${gate}: proceed -> ${String(target)}`)
    }
    return this.next()
  }

  /** Hot-plug a director's N child agents (a graph edit). */
  expand(director: string, entries: Array<Record<string, unknown>>): { expanded: string; children: string[]; next: SpineNext } {
    const node = this.nodes.get(director)
    const expansion = node?.expand
    if (expansion === undefined) throw new Error(`${director} has no expand annotation`)
    const slugField = expansion.slug_field
    const promptField = expansion.prompt_field
    const writesTemplate = expansion.writes_template

    let successor = this.successors[director]
    if (successor === undefined) {
      const next = this.unconditionalTarget(director)
      if (next !== undefined) {
        successor = next
        this.successors[director] = next
      }
    }

    // Revert the director's previous expansion: drop old children and their edges.
    const prefix = `${director}__`
    for (const child of [...this.nodes.keys()]) {
      if (child.startsWith(prefix)) {
        this.nodes.delete(child)
        this.completed = this.completed.filter(entry => entry !== child)
      }
    }
    this.edges = this.edges.filter(edge =>
      !(edge.source === director && edge.condition == null)
      && !(edge.source.startsWith(prefix) || edge.target.startsWith(prefix)),
    )
    for (const body of this.nodes.values()) {
      if (body._type === 'JoinNode' && (body.sources ?? []).some(source => source.startsWith(prefix))) {
        body.sources = (body.sources ?? []).filter(source => !source.startsWith(prefix))
      }
    }

    const children: string[] = []
    entries.forEach((entry, index) => {
      const rawSlug = typeof entry[slugField] === 'string' ? entry[slugField] : `child${index}`
      const slug = rawSlug.trim().replace(/\//g, '-').replace(/ /g, '-')
      const prompt = typeof entry[promptField] === 'string' ? entry[promptField] : ''
      const writes = writesTemplate === '' ? [] : [writesTemplate.replace('{slug}', slug)]
      const childId = `${director}__${slug}`
      this.nodes.set(childId, { _type: 'AgentNode', id: childId, role: expansion.role, prompt_template: prompt, writes, reads: [] })
      children.push(childId)
    })

    for (const child of children) {
      this.edges.push({ source: director, target: child })
      if (successor !== undefined) this.edges.push({ source: child, target: successor })
    }
    if (successor === undefined) {
      for (const body of this.nodes.values()) {
        if (body._type === 'JoinNode' && (body.sources ?? []).includes(director)) {
          body.sources = (body.sources ?? []).filter(source => source !== director).concat(children)
        }
      }
    }

    if (!this.completed.includes(director)) this.completed.push(director)
    if (successor !== undefined) this.afters.push(successor)
    this.cursor = children
    this.log.push(`expanded ${director} -> ${children.join(',')}`)
    return { expanded: director, children, next: this.next() }
  }

  /** Graph mutation: skip a node (and, for a fork, its targets). */
  skip(node: string): SpineNext {
    const body = this.nodes.get(node)
    if (body === undefined) throw new Error(`unknown node ${node}`)
    if (!this.skipped.includes(node)) {
      this.skipped.push(node)
      this.log.push(`skipped ${node}`)
    }
    if (body._type === 'ForkNode') {
      for (const target of body.targets ?? []) {
        if (!this.skipped.includes(target)) {
          this.skipped.push(target)
          this.log.push(`skipped ${target}`)
        }
      }
      const join = this.unconditionalTarget(node)
      this.cursor = join === undefined ? [] : [join]
    } else {
      this.cursor = this.cursor.filter(entry => entry !== node)
      if (this.cursor.length === 0 && this.afters.length > 0) {
        this.cursor = [this.afters.pop() as string]
      }
    }
    return this.next()
  }

  /** Undo a skip. */
  unskip(node: string): SpineNext {
    if (this.skipped.includes(node)) {
      this.skipped = this.skipped.filter(entry => entry !== node)
      this.log.push(`unskipped ${node}`)
    }
    return this.next()
  }

  /** Full mutable state (for `wf_state`). */
  state(): Record<string, unknown> {
    return {
      workflow: this.workflow,
      project_dir: this.projectDir,
      cursor: [...this.cursor],
      completed: [...this.completed],
      skipped: [...this.skipped],
      gate_verdicts: Object.fromEntries(this.gateVerdicts),
      iterations: { ...this.iterations },
      feedback: { ...this.feedback },
      log: [...this.log],
    }
  }
}
