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
  content?: string
  command?: string
  evaluator_type?: 'fn' | 'user' | 'agent'
  gate_prompt?: string
  targets?: string[]
  sources?: string[]
  expand?: DirectorExpansion
  [key: string]: unknown
}

/** One directed edge with an optional gate condition. */
export interface WorkflowEdge {
  source: string
  target: string
  condition?: 'proceed' | 'reloop' | 'halt' | null
}

/** A workflow graph — the exact shape of refactory's `Workflow.to_dict()`. */
export interface WorkflowGraph {
  name: string
  start_node: string
  project_dir?: string
  nodes: Record<string, WorkflowNode>
  edges: WorkflowEdge[]
}
