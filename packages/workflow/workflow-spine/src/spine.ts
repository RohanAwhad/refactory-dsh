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
  /** For an `fn` gate: the command whose last stdout line is the verdict. */
  evaluator_command?: string
  gate_prompt?: string
  /**
   * For a gate: the verdict labels it accepts, derived from its outgoing
   * conditional edges. A gate with a `proceed` label may also fall through its
   * single unconditional edge.
   */
  verdicts?: string[]
  /** For a gate: this gate's own reloop cap, overriding the runner's default. */
  max_iterations?: number
  reads?: string[]
  writes?: string[]
  /** The task handed to the driving agent, for `AgentNode` and `LLMNode`. */
  prompt_template?: string
  /** For a one-shot `LLMNode`: the model and sampling it was declared with. */
  model?: string
  temperature?: number
  content?: string
  command?: string
  targets?: string[]
  sources?: string[]
  expand?: DirectorExpansion
  /** For a `DataNode`: its resolved data source and the subgraph it iterates. */
  task_ref?: string
  source_path?: string
  source_format?: string
  inline_items?: Array<Record<string, unknown>>
  subgraph_entry?: string
  subgraph_exit?: string
  limit?: number
  /** For a `SelectionNode`: the selection strategy. */
  strategy?: string
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
const EXECUTED_TYPES = new Set([
  'AgentNode', 'LLMNode', 'FnNode', 'Study', 'GateNode', 'ForkNode', 'JoinNode',
  'DataNode', 'SelectionNode',
])

/** Verdict labels the spine treats as control flow rather than a forward branch. */
const RELOOP = 'reloop'
const HALT = 'halt'
const PROCEED = 'proceed'

/** Normalize a verdict/condition label. Gates and evaluators name outcomes freely. */
function label(value: string): string {
  return value.trim().toLowerCase()
}

/** One structural defect found by {@link validateGraph}. */
export interface GraphIssue {
  /** The node the issue is attached to, when it is node-local. */
  node?: string
  message: string
}

/**
 * Check a graph against the invariants the spine's cursor relies on. Returns an
 * empty array for a walkable graph. These are structural properties the walk
 * cannot recover from at runtime, so a caller that accepts an external
 * `graph.json` should reject the graph before starting a run.
 * @param graph - the workflow graph, as produced by refactory's `Workflow.to_dict()`.
 * @returns every issue found, in node order.
 */
export function validateGraph(graph: WorkflowGraph): GraphIssue[] {
  const issues: GraphIssue[] = []
  const ids = new Set(Object.keys(graph.nodes))
  if (!ids.has(graph.start_node)) {
    issues.push({ message: `start_node '${graph.start_node}' is not a node` })
  }
  const outgoing = new Map<string, WorkflowEdge[]>()
  for (const edge of graph.edges) {
    if (!ids.has(edge.source)) issues.push({ message: `edge source '${edge.source}' is not a node` })
    if (!ids.has(edge.target)) issues.push({ message: `edge target '${edge.target}' is not a node` })
    const list = outgoing.get(edge.source)
    if (list === undefined) outgoing.set(edge.source, [edge])
    else list.push(edge)
  }

  for (const [id, node] of Object.entries(graph.nodes)) {
    if (node.id !== id) {
      issues.push({ node: id, message: `node declares id '${node.id}' but is keyed as '${id}'` })
    }
    if (!EXECUTED_TYPES.has(node._type)) {
      issues.push({ node: id, message: `node type '${node._type}' is not executable` })
    }
    const edges = outgoing.get(id) ?? []
    const unconditional = edges.filter(edge => edge.condition == null)
    const conditional = edges.filter(edge => edge.condition != null)

    if (node._type === 'GateNode') {
      if (edges.length === 0) issues.push({ node: id, message: 'gate has no outgoing edge' })
      const seen = new Set<string>()
      for (const edge of conditional) {
        const key = label(edge.condition as string)
        if (seen.has(key)) issues.push({ node: id, message: `two edges carry the verdict '${key}'` })
        seen.add(key)
      }
      if (unconditional.length > 1) {
        issues.push({ node: id, message: 'gate has more than one unconditional edge' })
      }
    } else if (unconditional.length > 1) {
      issues.push({ node: id, message: 'node has more than one unconditional edge' })
    }

    if (node._type === 'ForkNode') {
      for (const target of node.targets ?? []) {
        if (!ids.has(target)) issues.push({ node: id, message: `fork target '${target}' is not a node` })
      }
    }
    if (node._type === 'JoinNode') {
      for (const source of node.sources ?? []) {
        if (!ids.has(source)) issues.push({ node: id, message: `join source '${source}' is not a node` })
      }
    }
  }

  for (const issue of unreachableNodes(graph, ids, outgoing)) {
    issues.push({ node: issue, message: 'unreachable from start_node' })
  }
  return issues
}

/** Nodes the walk can never reach, following edges plus fork fan-out. */
function unreachableNodes(
  graph: WorkflowGraph,
  ids: Set<string>,
  outgoing: Map<string, WorkflowEdge[]>,
): string[] {
  if (!ids.has(graph.start_node)) return []
  const seen = new Set<string>([graph.start_node])
  const queue = [graph.start_node]
  while (queue.length > 0) {
    const id = queue.shift() as string
    const next = (outgoing.get(id) ?? []).map(edge => edge.target)
    const node = graph.nodes[id]
    if (node?._type === 'ForkNode') next.push(...(node.targets ?? []))
    for (const target of next) {
      if (ids.has(target) && !seen.has(target)) {
        seen.add(target)
        queue.push(target)
      }
    }
  }
  return [...ids].filter(id => !seen.has(id))
}

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
    const wanted = label(condition)
    return this.outgoing(nid).find(
      edge => edge.condition != null && label(edge.condition) === wanted,
    )?.target
  }

  /** A gate's own reloop cap when it declares one, else the runner's default. */
  private reloopCap(gate: string): number {
    const declared = this.nodes.get(gate)?.max_iterations
    return typeof declared === 'number' ? declared : this.maxIterations
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
      if (node.evaluator_command !== undefined) view.evaluator_command = node.evaluator_command
      view.gate_prompt = node.gate_prompt ?? ''
      view.reads = node.reads ?? []
      view.verdicts = this.outgoing(nid)
        .filter(edge => edge.condition != null)
        .map(edge => label(edge.condition as string))
      if (typeof node.max_iterations === 'number') view.max_iterations = node.max_iterations
    }
    if (node._type === 'AgentNode') {
      view.writes = node.writes ?? []
      view.reads = node.reads ?? []
      view.prompt_template = node.prompt_template ?? ''
      if (node.expand !== undefined) view.expand = node.expand
    }
    if (node._type === 'LLMNode') {
      // A one-shot model call is delivered the same way an agent node is: the
      // driving agent runs it and reports the node complete.
      view.writes = node.writes ?? []
      view.reads = node.reads ?? []
      view.prompt_template = node.system_prompt ?? node.instance_prompt ?? ''
      view.model = node.model ?? ''
      view.temperature = node.temperature ?? 0
    }
    if (node._type === 'ForkNode') view.targets = node.targets ?? []
    if (node._type === 'JoinNode') view.sources = node.sources ?? []
    if (node._type === 'FnNode' || node._type === 'Study') {
      view.writes = node.writes ?? []
      view.command = node.command ?? ''
      if (node.content !== undefined) view.content = node.content
    }
    if (node._type === 'DataNode') {
      // A DataNode loads data items and drives per-item execution of its
      // declared subgraph (subgraph_entry → subgraph_exit). The driving agent
      // materializes the items and iterates the subgraph once per item.
      view.writes = node.writes ?? []
      view.reads = node.reads ?? []
      view.task_ref = node.task_ref ?? ''
      view.source_path = node.source_path ?? ''
      view.source_format = node.source_format ?? ''
      view.inline_items = node.inline_items ?? []
      view.subgraph_entry = node.subgraph_entry ?? ''
      view.subgraph_exit = node.subgraph_exit ?? ''
      if (node.limit !== undefined) view.limit = node.limit
    }
    if (node._type === 'SelectionNode') {
      view.strategy = node.strategy ?? 'best_score'
      view.reads = node.reads ?? []
      view.writes = node.writes ?? []
    }
    if (!EXECUTED_TYPES.has(node._type)) view.unsupported = true
    return view
  }

  /**
   * One node's view, due or not.
   *
   * The plugin needs this to decide who answers a gate: an `fn` gate carries a
   * command and is run for the CEO, while a `user` gate names no command and must be
   * put to a person. Deciding that from the view is what keeps the runtime, rather
   * than the model's word, the thing that answers it.
   * @param nodeId - the node to describe.
   * @returns the node view.
   */
  view(nodeId: string): NodeView {
    return this.describe(nodeId)
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

    this.advance(node)
    if (this.cursor.length === 0 && this.afters.length > 0) {
      this.cursor = [this.afters.pop() as string]
    }
    return this.next()
  }

  /**
   * Replace `node` with its successor, leaving any concurrent position alone.
   * A fork's branches each advance on their own; the join is reached only when
   * every branch has drained.
   */
  private advance(node: string): void {
    const target = this.unconditionalTarget(node)
    const rest = this.cursor.filter(entry => entry !== node)
    const replacement = target === undefined ? [] : [target]
    this.cursor = [...new Set([...rest, ...replacement])]
  }

  /**
   * Route a gate. `verdict` is `proceed`, `reloop`, `halt`, or any forward
   * outcome the gate names on its outgoing edges — a gate is a switch, not only
   * a binary decision. A `proceed` gate without a proceed edge falls through its
   * unconditional edge.
   * @param gate - the gate node id.
   * @param verdict - the outcome label; matched case-insensitively.
   * @param feedback - feedback attached to the reloop target (reloop only).
   * @returns the spine's next state.
   */
  verdict(gate: string, verdict: string, feedback = ''): SpineNext {
    if (this.nodes.get(gate)?._type !== 'GateNode') throw new Error(`${gate} is not a gate`)
    const outcome = label(verdict)
    if (outcome === '') throw new Error(`empty verdict for gate ${gate}`)
    if (!this.completed.includes(gate)) this.completed.push(gate)
    this.gateVerdicts.set(gate, outcome)

    if (outcome === RELOOP) {
      const target = this.conditionalTarget(gate, RELOOP)
      if (target === undefined) throw new Error(`${gate} has no reloop edge`)
      const cap = this.reloopCap(gate)
      const count = (this.iterations[gate] ?? 0) + 1
      this.iterations[gate] = count
      if (count > cap) {
        this.gateVerdicts.set(gate, HALT)
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
    } else if (outcome === HALT) {
      const target = this.conditionalTarget(gate, HALT)
      this.cursor = target === undefined ? [] : [target]
      this.afters = []
      this.log.push(`gate ${gate}: halt`)
    } else {
      const target = this.conditionalTarget(gate, outcome)
        ?? (outcome === PROCEED ? this.unconditionalTarget(gate) : undefined)
      if (target === undefined) {
        const allowed = this.describe(gate).verdicts ?? []
        throw new Error(`${gate} has no '${outcome}' edge (accepts ${allowed.join(', ')})`)
      }
      this.cursor = [target]
      this.afters = []
      this.log.push(`gate ${gate}: ${outcome} -> ${target}`)
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
      // A successor is reached through the `afters` barrier, so the children
      // carry no edge to it: with several children, one edge would schedule the
      // successor as soon as the first child finished.
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
