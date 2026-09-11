/**
 * Graph vocabulary for the workflow spine. Field names deliberately match
 * refactory's `Workflow.to_dict()` output (snake_case) — that JSON is the wire
 * format this package consumes.
 * @module @deepseek-ai/dsh-workflow-spine/types
 */

/** A director annotation on an AgentNode: hot-plug N child agents from a plan. */
export interface DirectorExpansion {
  /** Artifact path the director writes (the plan). */
  plan: string
  /** Child agent role. */
  role: string
  /** Plan-entry field holding the child slug. */
  slug_field: string
  /** Plan-entry field holding the child prompt. */
  prompt_field: string
  /** Child writes template, with `{slug}` substituted. */
  writes_template: string
}

/** One workflow node. Loose on purpose: it mirrors an external JSON format. */
export interface WorkflowNode {
  _type: string
  id: string
  role?: string
  writes?: string[]
  reads?: string[]
  prompt_template?: string
  /** `LLMNode`: the system prompt for the one-shot call. */
  system_prompt?: string
  /** `LLMNode`: an alternative per-instance prompt when no system prompt is set. */
  instance_prompt?: string
  /** `AgentNode` / `LLMNode`: the model the node was declared with. */
  model?: string
  /** `LLMNode`: sampling temperature. */
  temperature?: number
  /** `LLMNode`: the node's own turn cap. */
  max_turns?: number
  /** `GateNode`: the cap for this gate's reloop edge. */
  max_iterations?: number
  content?: string
  command?: string
  evaluator_type?: 'fn' | 'user' | 'agent'
  evaluator_command?: string
  gate_prompt?: string
  targets?: string[]
  sources?: string[]
  expand?: DirectorExpansion
  /** `DataNode`: the resolved data source and the subgraph it iterates. */
  task_ref?: string
  source_path?: string
  source_format?: string
  inline_items?: Array<Record<string, unknown>>
  subgraph_entry?: string
  subgraph_exit?: string
  limit?: number
  /** `SelectionNode`: the selection strategy. */
  strategy?: string
  [key: string]: unknown
}

/** One directed edge with an optional gate condition label. */
export interface WorkflowEdge {
  source: string
  target: string
  /**
   * The verdict label that routes along this edge, or null/absent for the
   * unconditional edge. `proceed` / `reloop` / `halt` carry their usual
   * meaning; any other label is a gate-named forward outcome (a switch), e.g.
   * `draft` / `improve` / `debug`. Labels are compared in lowercase.
   */
  condition?: string | null
}

/** A workflow graph — the exact shape of refactory's `Workflow.to_dict()`. */
export interface WorkflowGraph {
  name: string
  start_node: string
  project_dir?: string
  nodes: Record<string, WorkflowNode>
  edges: WorkflowEdge[]
}
