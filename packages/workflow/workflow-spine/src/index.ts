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
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import z from '@deepseek-ai/schemastery'
import { scopeChainOf, scopeOf } from '@deepseek-ai/dsh-scope'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { UserQuestionService } from '@deepseek-ai/dsh-user-questions'
import type { JsonValue } from '@deepseek-ai/dsh-util-values'
import { Spine, validateGraph } from './spine.ts'
import type { NodeView, SpineNext } from './spine.ts'
import { isDeterministic, runDataCommand, runDeterministic } from './execute.ts'
import type { CommandFailure } from './execute.ts'
import type { WorkflowGraph } from './types.ts'

export { Spine, validateGraph } from './spine.ts'
export { commandOf, dataCommandOf, isDeterministic, runDataCommand, verdictOf, NODE_ENV_KEY } from './execute.ts'
export type { CommandFailure, CommandOutcome, CommandRunnerConfig, NodeManifest } from './execute.ts'
export type { DataPass, GraphIssue, NodeView, SpineNext } from './spine.ts'
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
  /**
   * The command that selects a data node's items and opens and closes one pass,
   * run as `<dataCommand> <list|begin|end> --node <id> [--index <n>]`. Required
   * when the graph has a `DataNode` and `executeCommands` is on: the items live
   * outside the graph, so a composition with no loader cannot iterate one.
   */
  dataCommand: string
  /** Foreground timeout for one node command. */
  commandTimeoutMs: number
  /** Sandbox mode for node commands; graph commands are trusted ops, so full access. */
  commandSandboxMode: string
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
  dataCommand: z.string().default(''),
  commandTimeoutMs: z.natural().default(600_000),
  commandSandboxMode: z.string().default('danger-full-access'),
  ceoDeny: z.array(z.string()).default(DEFAULT_CEO_DENY),
})

/** How much of a failed node's stderr the status file keeps. */
const STATUS_DETAIL = 2_000

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
  // A data node's items live outside the graph, so a composition that mounts no
  // loader hands the node to the driving agent, which cannot iterate a subgraph the
  // cursor never enters. Refused here, at load, because both facts are config.
  const dataNodes = Object.values(config.graph.nodes)
    .filter(node => node._type === 'DataNode')
    .map(node => node.id)
  if (dataNodes.length > 0) {
    const reason = !config.executeCommands
      ? 'node commands are disabled (`executeCommands: false`)'
      : config.dataCommand === ''
        ? 'no item loader is configured (`dataCommand`)'
        : ''
    if (reason !== '') {
      throw new Error(
        `this graph iterates data in ${dataNodes.join(', ')}, and ${reason}; a data node's `
        + 'items are selected by a command outside the graph, so a composition without one '
        + 'cannot run it',
      )
    }
  }
  const spine = new Spine(config.graph, { maxIterations })

  // Who answers a `user` gate. Resolved here, once, so routing one is a runtime act
  // rather than a claim the model makes: a composition without an answerer (a
  // headless run) cannot route a person's gate at all, and a model cannot route one
  // on their behalf.
  const userQuestions: UserQuestionService | undefined = ctx.get('userQuestions')

  /**
   * The cursor's own status on disk, when a work directory is configured.
   *
   * The runner cannot see the spine, so a run that stopped at a gate it could not
   * route used to look like a run that simply scored 0. The status file is what makes
   * "the graph finished" and "the graph refused" different facts to a reader.
   * @param status - `done`, `ready`, `failed`, or `refused`.
   * @param detail - the error, for a failure or refusal.
   */
  function recordStatus(status: string, detail?: string): void {
    if (config.workdir === undefined) return
    try {
      const dir = join(config.workdir, '.refactory')
      mkdirSync(dir, { recursive: true })
      // The cursor travels with the status: a reader of a finished run wants the
      // score, and a reader of an abandoned one wants to know which nodes never ran.
      const state = spine.state()
      writeFileSync(
        join(dir, 'spine-status.json'),
        JSON.stringify(
          {
            status,
            ...(detail === undefined ? {} : { error: detail }),
            ...(status === 'ready' ? { nodes: state.cursor as string[], completed: state.completed as string[] } : {}),
          },
          null,
          2,
        ),
      )
    } catch {
      // The work directory belongs to the caller; a status write must never be the
      // thing that stops the run it is describing.
    }
  }

  /**
   * Run every deterministic node the cursor is sitting on, so `wf_next` reports
   * only work a model must do. A node that fails leaves the cursor where it is
   * and is reported instead: the graph must not advance past a step that did
   * not happen.
   *
   * A data node is iterated here too. The cursor cannot enter a subgraph on its
   * own, so the loop selects the node's items, opens a pass, walks the subgraph the
   * same way it walks the rest of the graph, and closes the pass when its exit
   * completes — which is what makes one pass land per item on this runtime, the way
   * the walking executor gives it.
   */
  async function drain(): Promise<{ next: SpineNext; failure?: CommandFailure; item?: unknown }> {
    if (!config.executeCommands) return { next: spine.next() }
    const shell = ctx.get('shell')
    if (shell === undefined) {
      // Without an executor nothing deterministic can run. That is only a problem
      // when something is waiting to be run: a data node handed to the driving
      // agent would be a node the cursor never enters and never iterates.
      const state = spine.next()
      const waiting = state.status === 'ready'
        ? state.nodes.find(node => node._type === 'DataNode')
        : undefined
      if (waiting === undefined) return { next: state }
      return {
        next: state,
        failure: {
          ok: false,
          command: waiting.id,
          exitCode: null,
          stdout: '',
          stderr: '',
          error: `${waiting.id} iterates data and this composition mounts no shell executor to load its items`,
        },
      }
    }
    const runner = {
      env: config.commandEnv,
      timeoutMs: config.commandTimeoutMs,
      dataCommand: config.dataCommand,
      sandboxPolicy: { mode: config.commandSandboxMode as 'danger-full-access', workspaceRoot: config.workdir ?? process.cwd() },
      ...(config.workdir === undefined ? {} : { workdir: config.workdir }),
    }
    /** `<node>:<index>` of the item already materialised on disk. */
    let begun: string | undefined
    let item: unknown

    /** Select a data node's items and open its first pass. */
    const openPass = async (data: NodeView): Promise<{ failure?: CommandFailure }> => {
      const listed = await runDataCommand(shell, data, 'list', runner)
      if (!listed.ok) return { failure: listed }
      const count = Number(listed.value.count ?? 0)
      // An empty selection is a fact, not a pass: the node completes and the run
      // continues, exactly as the walking executor continues past it.
      if (count === 0) {
        spine.complete(data.id)
        return {}
      }
      spine.startPass(data.id, count)
      return {}
    }

    /** Materialise the running pass's item, once per item. */
    const beginItem = async (): Promise<{ failure?: CommandFailure }> => {
      const pass = spine.passOf()
      if (pass === undefined || pass.finished) return {}
      const key = `${pass.node}:${pass.index}`
      if (begun === key) return {}
      const data = spine.view(pass.node)
      const started = await runDataCommand(shell, data, 'begin', runner, pass.index)
      if (!started.ok) return { failure: started }
      begun = key
      item = started.value
      return {}
    }

    /** Close a finished pass: judge it, then move to the next item or finish. */
    const close = async (): Promise<{ failure?: CommandFailure }> => {
      const pass = spine.passOf()
      if (pass === undefined || !pass.finished) return {}
      const data = spine.view(pass.node)
      const ended = await runDataCommand(shell, data, 'end', runner, pass.index)
      if (!ended.ok) return { failure: ended }
      begun = undefined
      item = undefined
      spine.endPass()
      return {}
    }

    for (;;) {
      const closed = await close()
      if (closed.failure !== undefined) return { next: spine.next(), failure: closed.failure }
      const state = spine.next()
      if (state.status === 'done') return { next: state, item }
      const started = await beginItem()
      if (started.failure !== undefined) return { next: state, failure: started.failure }
      const data = state.nodes.find(node => node._type === 'DataNode')
      if (data !== undefined) {
        const opened = await openPass(data)
        if (opened.failure !== undefined) return { next: state, failure: opened.failure }
        continue
      }
      const automatic = state.nodes.filter(isDeterministic)
      if (automatic.length === 0) return { next: state, item }
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
    const { next, failure, item } = await drain()
    if (failure !== undefined) {
      // The node's own stderr is why it failed. The exit code alone sends a reader
      // back to re-run the node to find out, so the reason is recorded with it.
      const reason = failure.stderr.trim()
      recordStatus('failed', reason === '' ? failure.error : `${failure.error}: ${reason.slice(-STATUS_DETAIL)}`)
      return json({
        status: 'failed',
        node: failure.command,
        exit_code: failure.exitCode,
        error: failure.error,
        stdout: failure.stdout,
        stderr: failure.stderr,
      })
    }
    recordStatus(next.status === 'done' ? 'done' : 'ready')
    // The item being iterated travels with the node views, so the subagent for a
    // pass is handed the item rather than being sent to find it on disk.
    return json(item === undefined ? next : { ...next, item })
  }

  /**
   * The verdict a person gives at a `user` gate, asked by the runtime.
   *
   * Asked here rather than left to the model's own `ask_user_question`, because a
   * model that can route a gate without asking can invent the answer, and an invented
   * verdict is indistinguishable from one a person gave. The gate's own outcome labels
   * are the options, so the answer is always one the graph accepts.
   * @param gate - the gate node id.
   * @param view - the gate's view, whose `verdicts` are the offered options.
   * @param exec - the tool execution, for the calling agent and its abort signal.
   * @returns the verdict the person chose.
   */
  async function askPerson(
    gate: string,
    view: NodeView,
    exec: { agent?: Agent; signal?: AbortSignal },
  ): Promise<string> {
    if (userQuestions === undefined) {
      const message = `gate ${gate} is answered by a person and this composition mounts no user-questions `
        + 'channel, so it cannot be routed here; run it where a person can be asked'
      recordStatus('refused', message)
      throw new Error(message)
    }
    const labels = spine.acceptedVerdicts(gate)
    let answer
    try {
      answer = await userQuestions.ask({
        questions: [{
          id: gate,
          header: 'Workflow gate',
          question: view.gate_prompt || `Gate ${gate}`,
          ...(labels.length === 0 ? {} : { options: labels.map(verdict => ({ label: verdict })) }),
        }],
        ...(exec.agent === undefined ? {} : { agent: exec.agent }),
        ...(exec.signal === undefined ? {} : { signal: exec.signal }),
      })
    } catch (error: unknown) {
      const detail = error instanceof Error ? error.message : String(error)
      const message = `gate ${gate} is answered by a person and could not be asked: ${detail}`
      recordStatus('refused', message)
      throw new Error(message)
    }
    const chosen = answer.answers.find(entry => entry.id === gate)
    const verdict = (chosen?.selected[0] ?? chosen?.custom ?? '').trim()
    if (verdict === '') {
      throw new Error(`gate ${gate} was answered without a verdict`)
    }
    return verdict
  }

  // The driving contract. The spine enforces "what is next" structurally (it
  // hands over the next node only after the current one is reported complete),
  // and the deny-guard below removes the CEO's coding tools. This section tells
  // the CEO how to operate within those two constraints: advance with wf_next,
  // delegate each node's work to a subagent, report with wf_done, route gates.
  ctx.systemPrompt.section({
    name: `tool:${toolPrefix}-spine`,
    order: ctx.systemPrompt.getSectionOrder('TOOL_WORKFLOW'),
    text: `You are the CEO of a workflow run, and you are DRIVING A WORKFLOW GRAPH. Your coding tools (bash, write, edit, web, skill) are disabled — you cannot implement, edit files, or browse yourself. Advance only through the wf tools. ${toolPrefix}_next is the ONLY way to learn what is due now: call it first, and call it again after every ${toolPrefix}_done / ${toolPrefix}_verdict / ${toolPrefix}_edit / ${toolPrefix}_expand — those tools only confirm the mutation, they do not tell you what is next. For an AgentNode or LLMNode, spawn a subagent (the subagent tool) whose task is the node's prompt_template, and explicitly tell the subagent to read the node's reads files and write its result to the node's writes file (the filename in the node's writes field, e.g. candidate.py) — wait for it, then report the node with ${toolPrefix}_done. For a FnNode, follow its content and report ${toolPrefix}_done. Route a gate with ${toolPrefix}_verdict, answering with one of the verdicts the gate view lists (proceed/reloop/halt, or the gate's own named outcomes). For a user gate the runtime asks the person itself and routes the answer they give: call ${toolPrefix}_verdict with the gate id and never answer for them. Fan out a director's plan with ${toolPrefix}_expand, and apply user steering ("skip the tests") with ${toolPrefix}_edit. A DataNode is iterated for you: the runtime selects its items and hands you the subgraph's nodes one pass at a time, with the item being processed in the payload's item field and on disk as the node's current item file. Do not try to iterate it yourself, and do not report the data node — or the pass's last node — before its work is done. Never skip, reorder, or bypass a node — the runner refuses to advance until you report the current node complete.`,
  })

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_next`,
    description: 'Get the next node(s) the workflow graph says are due now. Deterministic nodes (a command an `fn` gate or an `FnNode` runs) and a data node\'s item passes are executed for you and do not appear. Returns `{ status, nodes }`: for `ready`, the node views you must act on (agent task / one-shot LLM / user gate / fork) with the fields you need, plus `item` while a data node is iterating; for `done`, the run is complete; for `failed`, a deterministic node failed and the cursor has not moved. Always call this first and act only on what it returns — never skip or reorder.',
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
    description: 'Route a gate node. Pass one of the `verdicts` the gate view from `wf_next` lists: `proceed` advances along the proceed edge, `reloop` rewinds to the gate\'s reloop target with `feedback` (bounded by the iteration cap), `halt` stops or follows the halt edge, and any other label is that gate\'s own forward outcome. A `user` gate is not yours to decide: the runtime asks the person and routes the answer they give, and refuses when nobody can be asked. Returns only a confirmation — call `wf_next` to learn the next node.',
    parameters: {
      gate: { type: 'string', required: true, description: 'The gate node id.' },
      verdict: { type: 'string', required: true, description: 'The verdict: one of the gate\'s listed `verdicts`. Ignored for a `user` gate, whose verdict comes from the person.' },
      feedback: { type: 'string', description: 'Feedback to attach to the reloop target (reloop only).' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    async execute(args, exec) {
      const view = spine.view(args.gate)
      if (view.evaluator_type === 'user') {
        const verdict = await askPerson(args.gate, view, exec)
        spine.verdict(args.gate, verdict, args.feedback ?? '')
        return json({ status: 'routed', gate: args.gate, verdict, answered_by: 'user' })
      }
      spine.verdict(args.gate, args.verdict, args.feedback ?? '')
      return json({ status: 'routed', gate: args.gate, verdict: args.verdict })
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
