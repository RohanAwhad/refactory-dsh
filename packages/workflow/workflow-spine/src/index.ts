/**
 * The interactive workflow runner: a per-session {@link Spine} plus the
 * `wf_*` tools the driving agent uses to walk the graph step by step. The
 * spine owns "what is next", gate routing, iteration caps, fork/join barriers,
 * director expansion, and graph edits — so the agent never chooses the next
 * step and cannot drift. The agent only fills content at each node.
 * @module @deepseek-ai/dsh-workflow-spine
 */

import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { JsonValue } from '@deepseek-ai/dsh-util-values'
import { Spine } from './spine.ts'
import type { WorkflowGraph } from './types.ts'

export const name = 'workflow-spine'
export const inject = ['tools', 'systemPrompt']

/** Config: the workflow graph plus tool naming and iteration bounds. */
export interface Config {
  /** The workflow graph (refactory's `Workflow.to_dict()` output). */
  graph: WorkflowGraph
  /** Tool-name prefix (default `wf`, yielding `wf_next`, `wf_done`, …). */
  toolPrefix: string
  /** Maximum reloop iterations before a gate halts. */
  maxIterations: number
}

export const Config = z.object({
  graph: z.any(),
  toolPrefix: z.string().default('wf'),
  maxIterations: z.natural().default(3),
})

/** One model-visible step, rendered as compact JSON text. */
function text(value: JsonValue): { type: 'text'; text: string } {
  return { type: 'text', text: JSON.stringify(value, null, 2) }
}

/** Cast a spine result to the JSON the tool boundary requires. */
function json(value: unknown): JsonValue {
  return value as JsonValue
}

/** Wrap a synchronous spine result in the Promise the tool contract requires. */
function sync<T>(value: T): Promise<T> {
  return Promise.resolve(value)
}

export function apply(ctx: Context, config: Config): void {
  const { toolPrefix, maxIterations } = config
  const spine = new Spine(config.graph, { maxIterations })

  // The "force the loop" guidance: advance only through the tools. The spine
  // enforces this structurally (it hands over the next node only after the
  // current one is reported complete), and this section tells the agent to
  // cooperate rather than improvise.
  ctx.systemPrompt.section({
    name: `tool:${toolPrefix}-spine`,
    order: ctx.systemPrompt.getSectionOrder('TOOL_WORKFLOW'),
    text: `You are driving a workflow graph. Advance only through its tools: call ${toolPrefix}_next to learn the next node(s), perform exactly that node (spawn a subagent for an AgentNode, run the command for a FnNode), then report it with ${toolPrefix}_done. Route a gate with ${toolPrefix}_verdict (proceed/reloop/halt), fan out a director's plan with ${toolPrefix}_expand, and apply user steering ("skip the tests") with ${toolPrefix}_edit. Never skip, reorder, or bypass a node — the runner refuses to advance until you report the current node complete.`,
  })

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_next`,
    description: 'Get the next node(s) the workflow graph says are due now. Returns `{ status, nodes }`: for `ready`, a list of node views (agent task / fn / gate / fork) with the fields you need; for `done`, the run is complete. Always call this first and act only on what it returns — never skip or reorder.',
    parameters: {},
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute() {
      return sync(json(spine.next()))
    },
  }))

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_done`,
    description: 'Mark one non-gate node complete and advance. Call this after you finish the node `wf_next` returned (e.g. after the subagent for an AgentNode finishes, or after a deterministic FnNode ran).',
    parameters: {
      node: { type: 'string', required: true, description: 'The node id to mark complete.' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute(args) {
      return sync(json(spine.complete(args.node)))
    },
  }))

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_verdict`,
    description: 'Route a gate node. `proceed` advances along the proceed edge; `reloop` rewinds to the gate\'s reloop target with `feedback` (bounded by the iteration cap); `halt` stops or follows the halt edge.',
    parameters: {
      gate: { type: 'string', required: true, description: 'The gate node id.' },
      verdict: { type: 'string', required: true, enum: ['proceed', 'reloop', 'halt'], description: 'The verdict.' },
      feedback: { type: 'string', description: 'Feedback to attach to the reloop target (reloop only).' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute(args) {
      return sync(json(spine.verdict(args.gate, args.verdict, args.feedback ?? '')))
    },
  }))

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_edit`,
    description: 'Edit the live graph: `skip` hot-removes a node (and, for a fork, its targets); `unskip` restores it. This is how a user steering instruction ("skip the tests") becomes a structured change the runner enforces.',
    parameters: {
      node: { type: 'string', required: true, description: 'The node id to edit.' },
      action: { type: 'string', required: true, enum: ['skip', 'unskip'], description: 'The edit.' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute(args) {
      return sync(json(args.action === 'skip' ? spine.skip(args.node) : spine.unskip(args.node)))
    },
  }))

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_expand`,
    description: 'Hot-plug a director\'s N child agents from its plan entries, then return those children as the next nodes. Use this after a director agent produced its plan: pass each `{ slug, prompt }` entry so the spine fans out and runs them.',
    parameters: {
      director: { type: 'string', required: true, description: 'The director node id.' },
      entries: { type: 'array', required: true, items: { type: 'object', additionalProperties: true }, description: 'The plan entries (each with slug + prompt fields).' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute(args) {
      const entries = args.entries as Array<Record<string, unknown>>
      return sync(json(spine.expand(args.director, entries)))
    },
  }))

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_state`,
    description: 'Return the full live state: cursor, completed, skipped, gate verdicts, iteration counts, feedback, and the run log.',
    parameters: {},
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute() {
      return sync(json(spine.state()))
    },
  }))
}
