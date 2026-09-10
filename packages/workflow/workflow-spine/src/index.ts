/**
 * The interactive workflow runner: a per-session {@link Spine} plus the
 * `wf_*` tools the CEO uses to walk the graph step by step, and the
 * drift-free guard that makes the graph the ONLY thing the CEO can do.
 *
 * The spine owns "what is next", gate routing, iteration caps, fork/join
 * barriers, director expansion, and graph edits — so the CEO never chooses
 * the next step and cannot drift. On top of that, at `agent/created` the
 * plugin denies the CEO's coding/external tools (bash, write, edit, web,
 * skill, …) for the CEO's own scope: the CEO is left with exactly the `wf_*`
 * tools, delegation, ask-user, and read-only tools. Node work therefore
 * happens inside the subagents the CEO spawns, which join the full `standard`
 * composition and keep every tool. Removing the coding tools from the CEO is
 * what turns "please follow the graph" into "there is no other path".
 * @module @deepseek-ai/dsh-workflow-spine
 */

import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { scopeChainOf, scopeOf } from '@deepseek-ai/dsh-scope'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { JsonValue } from '@deepseek-ai/dsh-util-values'
import { Spine, validateGraph } from './spine.ts'
import type { SpineNext } from './spine.ts'
import { isDeterministic, runDeterministic } from './execute.ts'
import type { CommandOutcome } from './execute.ts'
import type { WorkflowGraph } from './types.ts'

export { Spine, validateGraph } from './spine.ts'
export { commandOf, isDeterministic, verdictOf, NODE_ENV_KEY } from './execute.ts'
export type { CommandOutcome, CommandRunnerConfig, NodeManifest } from './execute.ts'
export type { GraphIssue, NodeView, SpineNext } from './spine.ts'
export type { WorkflowEdge, WorkflowGraph, WorkflowNode } from './types.ts'

export const name = 'workflow-spine'
export const inject = ['tools', 'systemPrompt']

/** Config: the workflow graph, tool naming, iteration bounds, and the CEO deny-list. */
export interface Config {
  /** The workflow graph (refactory's `Workflow.to_dict()` output). */
  graph: WorkflowGraph
  /** Tool-name prefix (default `wf`, yielding `wf_next`, `wf_done`, …). */
  toolPrefix: string
  /** Maximum reloop iterations before a gate halts. */
  maxIterations: number
  /**
   * Run deterministic nodes (`FnNode`/`Study` commands and `fn` gate evaluators)
   * in-process instead of handing them to the driving agent. Requires a shell
   * executor; with none mounted the nodes are handed over as before.
   */
  executeCommands: boolean
  /** Working directory for node commands; absent uses the shell executor's own. */
  workdir?: string
  /** Extra environment entries for node commands. */
  commandEnv: Record<string, string>
  /** Foreground timeout for one node command. */
  commandTimeoutMs: number
  /**
   * Global tools the CEO is not allowed to call. The guard denies these on the
   * CEO's own scope at `agent/created`, so subagents (which join the full
   * `standard` composition as siblings) keep them. Names absent from the CEO's
   * inherited surface are skipped, so the list can name platform-gated tools
   * (e.g. `pwsh`) that are not present.
   */
  ceoDeny: string[]
}

/** Coding/external tools the CEO is not allowed to call, by default. */
const DEFAULT_CEO_DENY = [
  'bash',
  'pwsh',
  'write',
  'edit',
  'str_replace_editor',
  'web_search',
  'web_fetch',
  'skill',
  'ralph',
  'workflow',
  'read_image',
]

export const Config = z.object({
  graph: z.any(),
  toolPrefix: z.string().default('wf'),
  maxIterations: z.natural().default(3),
  executeCommands: z.boolean().default(false),
  workdir: z.string(),
  commandEnv: z.dict(z.string()).default({}),
  commandTimeoutMs: z.natural().default(600_000),
  ceoDeny: z.array(z.string()).default(DEFAULT_CEO_DENY),
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
  const { toolPrefix, maxIterations, ceoDeny } = config
  // A graph accepted from configuration is a parser boundary: reject it at load
  // rather than discovering a dead cursor mid-run.
  const issues = validateGraph(config.graph)
  if (issues.length > 0) {
    const rendered = issues
      .map(issue => (issue.node === undefined ? issue.message : `${issue.node}: ${issue.message}`))
      .join('; ')
    throw new Error(`invalid workflow graph: ${rendered}`)
  }
  const spine = new Spine(config.graph, { maxIterations })

  /**
   * Run every deterministic node the cursor is sitting on, so `wf_next` reports
   * only work a model must do. A node that fails leaves the cursor where it is
   * and is reported instead: the graph must not advance past a step that did
   * not happen.
   */
  async function drain(): Promise<{ next: SpineNext; failure?: CommandOutcome }> {
    if (!config.executeCommands) return { next: spine.next() }
    const shell = ctx.get('shell')
    if (shell === undefined) return { next: spine.next() }
    const runner = {
      env: config.commandEnv,
      timeoutMs: config.commandTimeoutMs,
      ...(config.workdir === undefined ? {} : { workdir: config.workdir }),
    }
    for (;;) {
      const state = spine.next()
      if (state.status === 'done') return { next: state }
      const automatic = (state.nodes ?? []).filter(isDeterministic)
      if (automatic.length === 0) return { next: state }
      for (const node of automatic) {
        const outcome = await runDeterministic(shell, node, runner)
        if (!outcome.ok) return { next: state, failure: outcome }
        if (outcome.verdict === undefined) spine.complete(node.id)
        else spine.verdict(node.id, outcome.verdict)
      }
    }
  }

  /** The `wf_next` payload: the due nodes, or the node that failed to run. */
  async function report(): Promise<JsonValue> {
    const { next, failure } = await drain()
    if (failure !== undefined) {
      return json({
        status: 'failed',
        node: failure.command,
        exit_code: failure.exitCode,
        error: failure.error ?? 'command failed',
        stdout: failure.stdout,
        stderr: failure.stderr,
      })
    }
    return json(next)
  }

  // The driving contract. The spine enforces "what is next" structurally (it
  // hands over the next node only after the current one is reported complete),
  // and the deny-guard below removes the CEO's coding tools. This section tells
  // the CEO how to operate within those two constraints: advance with wf_next,
  // delegate each node's work to a subagent, report with wf_done, route gates.
  ctx.systemPrompt.section({
    name: `tool:${toolPrefix}-spine`,
    order: ctx.systemPrompt.getSectionOrder('TOOL_WORKFLOW'),
    text: `You are the CEO of a workflow run, and you are DRIVING A WORKFLOW GRAPH. Your coding tools (bash, write, edit, web, skill) are disabled — you cannot implement, edit files, or browse yourself. Advance only through the wf tools. ${toolPrefix}_next is the ONLY way to learn what is due now: call it first, and call it again after every ${toolPrefix}_done / ${toolPrefix}_verdict / ${toolPrefix}_edit / ${toolPrefix}_expand — those tools only confirm the mutation, they do not tell you what is next. For an AgentNode, spawn a subagent (the subagent tool) with that node's task and wait for it, then report the node with ${toolPrefix}_done. For a FnNode, follow its content and report ${toolPrefix}_done. Route a gate with ${toolPrefix}_verdict, answering with one of the verdicts the gate view lists (proceed/reloop/halt, or the gate's own named outcomes) — for a user gate, first present the gate to the user with ask_user_question and route the verdict they give. Fan out a director's plan with ${toolPrefix}_expand, and apply user steering ("skip the tests") with ${toolPrefix}_edit. Never skip, reorder, or bypass a node — the runner refuses to advance until you report the current node complete.`,
  })

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_next`,
    description: 'Get the next node(s) the workflow graph says are due now. Deterministic nodes (a command an `fn` gate or an `FnNode` runs) are executed for you and do not appear. Returns `{ status, nodes }`: for `ready`, the node views you must act on (agent task / one-shot LLM / user gate / fork) with the fields you need; for `done`, the run is complete; for `failed`, a deterministic node failed and the cursor has not moved. Always call this first and act only on what it returns — never skip or reorder.',
    parameters: {},
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute() {
      return report()
    },
  }))

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_done`,
    description: 'Mark one non-gate node complete and advance the spine. Returns only a confirmation — call `wf_next` to learn the next node. Call this after the node `wf_next` returned is done (e.g. after the subagent for an AgentNode finishes, or a deterministic FnNode is satisfied).',
    parameters: {
      node: { type: 'string', required: true, description: 'The node id to mark complete.' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute(args) {
      spine.complete(args.node)
      return sync(json({ status: 'complete', node: args.node }))
    },
  }))

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_verdict`,
    description: 'Route a gate node. Pass one of the `verdicts` the gate view from `wf_next` lists: `proceed` advances along the proceed edge, `reloop` rewinds to the gate\'s reloop target with `feedback` (bounded by the iteration cap), `halt` stops or follows the halt edge, and any other label is that gate\'s own forward outcome. Returns only a confirmation — call `wf_next` to learn the next node.',
    parameters: {
      gate: { type: 'string', required: true, description: 'The gate node id.' },
      verdict: { type: 'string', required: true, description: 'The verdict: one of the gate\'s listed `verdicts`.' },
      feedback: { type: 'string', description: 'Feedback to attach to the reloop target (reloop only).' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute(args) {
      spine.verdict(args.gate, args.verdict, args.feedback ?? '')
      return sync(json({ status: 'routed', gate: args.gate, verdict: args.verdict }))
    },
  }))

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_edit`,
    description: 'Edit the live graph: `skip` hot-removes a node (and, for a fork, its targets); `unskip` restores it. This is how a user steering instruction ("skip the tests") becomes a structured change the runner enforces. Returns only a confirmation — call `wf_next` to learn the next node.',
    parameters: {
      node: { type: 'string', required: true, description: 'The node id to edit.' },
      action: { type: 'string', required: true, enum: ['skip', 'unskip'], description: 'The edit.' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute(args) {
      if (args.action === 'skip') spine.skip(args.node)
      else spine.unskip(args.node)
      return sync(json({ status: 'edited', node: args.node, action: args.action }))
    },
  }))

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_expand`,
    description: 'Hot-plug a director\'s N child agents from its plan entries. Returns the child ids it created. Call `wf_next` afterward to learn the next node(s) due. Use this after a director agent produced its plan: pass each `{ slug, prompt }` entry so the spine fans out and runs them.',
    parameters: {
      director: { type: 'string', required: true, description: 'The director node id.' },
      entries: { type: 'array', required: true, items: { type: 'object', additionalProperties: true }, description: 'The plan entries (each with slug + prompt fields).' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute(args) {
      const entries = args.entries as Array<Record<string, unknown>>
      const result = spine.expand(args.director, entries)
      return sync(json({ status: 'expanded', director: result.expanded, children: result.children }))
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

  // The drift-free guard. At `agent/created` the CEO (a top-level agent, never
  // a subagent) of THIS composition loses the coding/external tools on its own
  // scope only. Content subagents are siblings of the CEO under the same
  // standing mount, so this restriction does not reach them.
  const compositionScope = scopeOf(ctx)
  const restrictions = new WeakMap<Agent, () => void>()
  ctx.on('agent/created', ({ agent }) => {
    if (agent.session.header.origin === 'subagent') return
    if (compositionScope === undefined) return
    const agentScope = scopeOf(agent.ctx)
    if (agentScope === undefined) return
    if (!scopeChainOf(agentScope).includes(compositionScope)) return
    const visible = new Set(ctx.tools.schemas(agentScope).map(schema => schema.name))
    const deny = ceoDeny.filter(name => visible.has(name))
    if (deny.length === 0) return
    restrictions.set(agent, agent.ctx.tools.restrict({ deny }))
  })
  ctx.on('agent/disposed', ({ agent }) => {
    const dispose = restrictions.get(agent)
    if (dispose === undefined) return
    dispose()
    restrictions.delete(agent)
  })
}
