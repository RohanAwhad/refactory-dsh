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
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
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
export type { DataPass, GraphIssue, NodeView, PromptAmendmentMode, SpineNext } from './spine.ts'
export type { WorkflowEdge, WorkflowGraph, WorkflowNode } from './types.ts'

export const name = 'workflow-spine'
export const inject = ['tools', 'systemPrompt']

/** Config: the workflow graph, tool naming, iteration bounds, and the CEO deny-list. */
export interface Config {
  /**
   * The workflow graph (refactory's `Workflow.to_dict()` output). Required when
   * {@link interactiveOpen} is false: the composition's one graph, validated at
   * mount. Omitted when {@link interactiveOpen} is true, where a graph arrives
   * later through `<toolPrefix>_open`.
   */
  graph?: WorkflowGraph
  /** Tool-name prefix (default `wf`, yielding `wf_next`, `wf_done`, …). */
  toolPrefix: string
  /** Maximum reloop iterations before a gate halts. */
  maxIterations: number
  /**
   * Open a mode's graph after boot, owned by the session that opens it, instead
   * of configuring one graph at mount.
   *
   * This is what a long-lived web chat session needs: it is one process serving
   * many sessions, so the graph cannot be a mount-time constant. When true the
   * spine starts with no graph, `graph` may be omitted, nothing is validated at
   * mount, and `<toolPrefix>_open` is registered for a session to open and claim
   * a run. When false (the default) the composition behaves exactly as before:
   * `graph` is required, validated at mount, and one spine serves the process.
   */
  interactiveOpen: boolean
  /**
   * The command that lowers a mode and prints its JSON payload, called as
   * `<openCommand> <openArgs...> <mode>` (with `--workdir <dir>` and one
   * `--knob NAME=VALUE` per knob appended). The payload names the run directory
   * (`run`), the graph name (`name`), the absolute path of the lowered graph
   * JSON (`graph`), and the run's declarations (`declarations`). Used by
   * `<toolPrefix>_open`; the console's `open` verb is that command.
   */
  openCommand: string
  /** Arguments for {@link openCommand}, before the mode. */
  openArgs: string[]
  /**
   * Run deterministic nodes (`FnNode`/`Study` commands and `fn` gate evaluators)
   * in-process instead of handing them to the driving agent. Requires a shell
   * executor; with none mounted the nodes are handed over as before.
   */
  executeCommands: boolean
  /** Working directory for node commands; absent uses the shell executor's own. */
  workdir?: string
  /**
   * Fallback environment entries for node commands.
   *
   * The values are per **run** — the work directory and the mode's knobs — so a
   * run opened at run time carries its own in the `<toolPrefix>_open` payload and
   * this is only what it falls back to when the payload omits one. A configured
   * graph has no payload, so this is the whole environment there and that path is
   * unchanged.
   */
  commandEnv: Record<string, string>
  /**
   * The command that selects a data node's items and opens and closes one pass,
   * run as `<dataCommand> <list|begin|end> --node <id> [--index <n>]`. Required
   * when the graph has a `DataNode` and `executeCommands` is on: the items live
   * outside the graph, so a composition with no loader cannot iterate one. A run
   * opened at run time may name its own in the `<toolPrefix>_open` payload; this
   * is the fallback when it does not.
   */
  dataCommand: string
  /**
   * The command that reads the run's steering ledger, run as
   * `<steerCommand> <steerArgs...> <workdir>`. When non-empty (and `workdir` is
   * set) the plugin registers `<toolPrefix>_steer`, so a person's mid-run
   * correction reaches the driving agent. The command's owner is the ledger
   * format's owner: the spine learns only a command to run and returns the JSON
   * it prints, so it stays free of any knowledge of how a ledger is stored.
   */
  steerCommand: string
  /** Arguments for {@link steerCommand}; the run's `workdir` is appended last. */
  steerArgs: string[]
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
  interactiveOpen: z.boolean().default(false),
  openCommand: z.string().default(''),
  openArgs: z.array(z.string()).default([]),
  executeCommands: z.boolean().default(false),
  workdir: z.string(),
  commandEnv: z.dict(z.string()).default({}),
  dataCommand: z.string().default(''),
  steerCommand: z.string().default(''),
  steerArgs: z.array(z.string()).default([]),
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

/** The last non-empty line of a command's output, for an error a reader can act on. */
function lastLine(text: string | null | undefined): string {
  const lines = (text ?? '').split('\n').map(line => line.trim()).filter(line => line !== '')
  return lines[lines.length - 1] ?? ''
}

/**
 * What one deterministic node left in its run directory, for the CEO to report.
 *
 * A node's outputs are the files it declared it writes and that now exist; a
 * declaration that names a file the command did not produce is not an output,
 * and saying otherwise would let the CEO report work that did not happen. The
 * facts are the run's own `facts.json`, which the node command rewrites and
 * which is empty when no command has run yet.
 * @param node - the node that just ran.
 * @param workdir - the run directory its command ran in.
 * @returns the node's id and type, plus the outputs and facts that exist.
 */
function recordedStep(node: NodeView, workdir: string | undefined): RanStep {
  const step: RanStep = { node: node.id, type: node._type }
  if (workdir === undefined) return step
  const outputs = (node.writes ?? []).filter(rel => rel !== '' && existsSync(resolve(workdir, rel)))
  if (outputs.length > 0) step.outputs = outputs
  try {
    // The runner writes one JSON object; anything else is not facts a reader can
    // use, and is reported as absent rather than as a malformed value.
    const parsed: unknown = JSON.parse(readFileSync(join(workdir, 'facts.json'), 'utf8'))
    if (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) {
      step.facts = parsed as JsonValue
    }
  } catch {
    // No facts file yet, or one a command left mid-write: the run directory
    // simply carries no facts for this step.
  }
  return step
}

/**
 * The run directory a session gets when the person names none.
 *
 * A chat has no terminal to name a directory, and the console's `open` verb
 * refuses without one, so the spine derives a directory from the two facts it
 * has at the call: the calling session and the mode. It is
 * `<process.cwd()>/.factory/web/<session id>/<package>-<mode>`, where the mode's
 * `/` becomes `-` so the leaf reads `<package>-<mode>`, and it is created here
 * because the open command will lower into it.
 * @param session - the id of the session opening the run.
 * @param mode - the mode, as `<package>/<name>`.
 * @returns the absolute run directory, created.
 */
function derivedWorkdir(session: string, mode: string): string {
  const dir = join(process.cwd(), '.factory', 'web', session, mode.split('/').join('-'))
  mkdirSync(dir, { recursive: true })
  return dir
}

/** The per-call execution facts a spine tool resolves its session from. */
interface ToolExec {
  agent?: Agent
  signal?: AbortSignal
}

/**
 * One deterministic node a `wf_next` (or `wf_open`) ran for the CEO.
 *
 * With `interactiveOpen` a call runs exactly one deterministic node, so the
 * result can name the node that just ran and what it left in the run directory
 * alongside the nodes still due. Neither field is invented when the run
 * directory does not carry it: a node that declares no artifact has no
 * `outputs`, and a run whose commands have not written `facts.json` has no
 * `facts`.
 */
interface RanStep {
  /** The node that just ran. */
  node: string
  /** Its node type, so a reader can tell an op from a gate. */
  type: string
  /** The node's declared writes that now exist in the run directory, relative to it. */
  outputs?: string[]
  /** The run's `facts.json` as the node's command left it. */
  facts?: JsonValue
}

/** The item a data pass already materialised on disk, so it is begun once per item. */
interface Materialised {
  /** `<node>:<index>` of the pass whose item is on disk. */
  key: string
  /** The item `begin` printed for that pass. */
  item: unknown
}

/** A graph opened at run time, and the session that owns it. */
interface OpenRun {
  /** The id of the session that opened the run. */
  session: string
  /** The opened graph's spine. */
  spine: Spine
  /** The run directory the mode was lowered into: where node commands run and status is written. */
  workdir: string
  /** The opened graph's name, for status and reporting. */
  name: string
  /** The run's own environment for node commands, from the open payload. */
  commandEnv: Record<string, string>
  /** The run's own item loader command, from the open payload. */
  dataCommand: string
}

/** The JSON `<openCommand>` prints: where it lowered the mode. */
interface OpenPayload {
  /** The run directory the mode was lowered into. */
  run?: unknown
  /** The lowered graph's name. */
  name?: unknown
  /** Absolute path of the lowered graph JSON the spine loads. */
  graph?: unknown
  /** Absolute path of the run's node declarations. */
  declarations?: unknown
  /**
   * The run's own environment for node commands: the work directory, the project,
   * the knobs and the interpreter-leading `PATH`. Per run, so it travels with the
   * payload rather than being a mount-time constant.
   */
  commandEnv?: unknown
  /** The run's own data-node item loader command. */
  dataCommand?: unknown
}

/** The environment a payload names, or `undefined` when it is absent or malformed. */
function payloadEnv(value: unknown): Record<string, string> | undefined {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return undefined
  const out: Record<string, string> = {}
  for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
    // A non-string value is not an environment a process can be given, so the
    // whole payload is treated as absent and the config fallback is used.
    if (typeof entry !== 'string') return undefined
    out[key] = entry
  }
  return out
}

export function apply(ctx: Context, config: Config): void {
  const { toolPrefix, maxIterations, ceoDeny, interactiveOpen } = config

  // The composition's one spine, when the graph was a mount-time constant. When
  // `interactiveOpen` is true there is none yet: a session opens a mode through
  // `<toolPrefix>_open`, and only then is a graph validated and a spine built.
  let configuredSpine: Spine | undefined
  if (!interactiveOpen) {
    if (config.graph === undefined) {
      throw new Error(
        'no workflow graph is configured; set `graph`, or set `interactiveOpen: true` so a '
        + `session can open one with ${toolPrefix}_open`,
      )
    }
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
    configuredSpine = new Spine(config.graph, { maxIterations })
  }

  // One in-session driven run at a time is the accepted v1: the `wf_*` tools are
  // shared by the composition, so two sessions driving at once would each see the
  // other's cursor. A run opened at run time is therefore claimed by its session,
  // and every tool call resolves that claim before touching a spine.
  let opened: OpenRun | undefined

  // The item a pass already materialised, kept between calls rather than inside
  // one `drain`. With `interactiveOpen` a call returns after a single
  // deterministic node, so a pass's item outlives the call that began it and
  // re-running `begin` for it would re-run the Task's setup once per node.
  let materialised: Materialised | undefined

  /** The id of the session an execution belongs to, when it has an agent. */
  function sessionIdOf(exec: ToolExec): string | undefined {
    const session = exec.agent?.session
    return session === undefined ? undefined : String(session.id)
  }

  /**
   * The spine a tool call works on, and the per-run settings it belongs to.
   *
   * With a configured graph that is the composition's one spine and its static
   * configuration. With `interactiveOpen` it is the run the calling session
   * opened — its own work directory, environment and item loader, read from the
   * open payload — and a call from any other session, or before any open, is
   * refused rather than served against an unknown run.
   */
  function requireRun(exec: ToolExec): {
    spine: Spine
    workdir: string | undefined
    commandEnv: Record<string, string>
    dataCommand: string
  } {
    if (configuredSpine !== undefined) {
      return {
        spine: configuredSpine,
        workdir: config.workdir,
        commandEnv: config.commandEnv,
        dataCommand: config.dataCommand,
      }
    }
    if (opened === undefined) {
      throw new Error(`no mode is open in this session; call ${toolPrefix}_open with a mode first`)
    }
    if (sessionIdOf(exec) !== opened.session) {
      throw new Error('a run is already open in another session')
    }
    return {
      spine: opened.spine,
      workdir: opened.workdir,
      commandEnv: opened.commandEnv,
      dataCommand: opened.dataCommand,
    }
  }

  // Who answers a `user` gate. Resolved here, once, so routing one is a runtime act
  // rather than a claim the model makes: a composition without an answerer (a
  // headless run) cannot route a person's gate at all, and a model cannot route one
  // on their behalf.
  const userQuestions: UserQuestionService | undefined = ctx.get('userQuestions')

  /**
   * The cursor's own status on disk, when a work directory is known.
   *
   * The runner cannot see the spine, so a run that stopped at a gate it could not
   * route used to look like a run that simply scored 0. The status file is what makes
   * "the graph finished" and "the graph refused" different facts to a reader.
   * @param spine - the run's spine, whose cursor the status records.
   * @param workdir - where to write: the configured run directory, or the opened
   * run's own directory when a session opened the mode at run time.
   * @param status - `done`, `ready`, `failed`, or `refused`.
   * @param detail - the error, for a failure or refusal.
   */
  function recordStatus(spine: Spine, workdir: string | undefined, status: string, detail?: string): void {
    if (workdir === undefined) return
    try {
      const dir = join(workdir, '.refactory')
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
   * Run the deterministic nodes the cursor is sitting on, so `wf_next` reports
   * only work a model must do. A node that fails leaves the cursor where it is
   * and is reported instead: the graph must not advance past a step that did
   * not happen.
   *
   * A data node is iterated here too. The cursor cannot enter a subgraph on its
   * own, so the loop selects the node's items, opens a pass, walks the subgraph the
   * same way it walks the rest of the graph, and closes the pass when its exit
   * completes — which is what makes one pass land per item on this runtime, the way
   * the walking executor gives it.
   *
   * With `interactiveOpen` the loop stops after **one** deterministic node and
   * returns, so each `wf_next` is one node the CEO can watch and steer between.
   * Opening and closing a data pass are loop bookkeeping, not nodes, so they do
   * not consume that one-node budget and a pass that begins always continues on
   * the calls that follow. A configured graph (`interactiveOpen: false`) keeps
   * draining every deterministic node in one call.
   * @param spine - the run to advance.
   * @param workdir - the run directory node commands run in.
   * @param commandEnv - the run's own environment for node commands.
   * @param dataCommand - the run's own item loader command.
   */
  async function drain(
    spine: Spine,
    workdir: string | undefined,
    commandEnv: Record<string, string>,
    dataCommand: string,
  ): Promise<{ next: SpineNext; failure?: CommandFailure; item?: unknown; ran?: RanStep | undefined }> {
    if (!config.executeCommands) return { next: spine.next() }
    // A configured graph drains a whole pass within one call, so its materialised
    // item is per-call state exactly as before; only the one-node interactive
    // drain has to carry it across calls.
    if (!interactiveOpen) materialised = undefined
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
      env: commandEnv,
      timeoutMs: config.commandTimeoutMs,
      dataCommand,
      sandboxPolicy: { mode: config.commandSandboxMode as 'danger-full-access', workspaceRoot: workdir ?? process.cwd() },
      ...(workdir === undefined ? {} : { workdir }),
    }
    // The node this call ran, when the composition stops after one. A configured
    // graph runs many per call and reports none of them individually.
    let ran: RanStep | undefined
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

    /** Materialise the running pass's item, once per item across calls. */
    const beginItem = async (): Promise<{ failure?: CommandFailure }> => {
      const pass = spine.passOf()
      if (pass === undefined || pass.finished) return {}
      const key = `${pass.node}:${pass.index}`
      if (materialised?.key === key) {
        item = materialised.item
        return {}
      }
      const data = spine.view(pass.node)
      const started = await runDataCommand(shell, data, 'begin', runner, pass.index)
      if (!started.ok) return { failure: started }
      materialised = { key, item: started.value }
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
      materialised = undefined
      item = undefined
      spine.endPass()
      return {}
    }

    for (;;) {
      const closed = await close()
      if (closed.failure !== undefined) return { next: spine.next(), failure: closed.failure, ran }
      const state = spine.next()
      if (state.status === 'done') return { next: state, item, ran }
      const started = await beginItem()
      if (started.failure !== undefined) return { next: state, failure: started.failure, ran }
      const data = state.nodes.find(node => node._type === 'DataNode')
      if (data !== undefined) {
        const openedPass = await openPass(data)
        if (openedPass.failure !== undefined) return { next: state, failure: openedPass.failure, ran }
        continue
      }
      const automatic = state.nodes.filter(isDeterministic)
      if (automatic.length === 0) return { next: state, item, ran }
      // One node per call is the interactive contract; the configured graph keeps
      // walking every automatic node the cursor is sitting on.
      const due = interactiveOpen ? automatic.slice(0, 1) : automatic
      for (const node of due) {
        const outcome = await runDeterministic(shell, node, runner)
        if (!outcome.ok) return { next: state, failure: outcome, ran }
        if (outcome.verdict === undefined) spine.complete(node.id)
        else spine.verdict(node.id, outcome.verdict)
        if (interactiveOpen) ran = recordedStep(node, workdir)
      }
      if (interactiveOpen) return { next: spine.next(), item, ran }
    }
  }

  /**
   * The `wf_next` payload: the due nodes, or the node that failed to run.
   * @param spine - the run to advance.
   * @param workdir - the run directory its status is written to.
   * @param commandEnv - the run's own environment for node commands.
   * @param dataCommand - the run's own item loader command.
   */
  async function report(
    spine: Spine,
    workdir: string | undefined,
    commandEnv: Record<string, string>,
    dataCommand: string,
  ): Promise<JsonValue> {
    const { next, failure, item, ran } = await drain(spine, workdir, commandEnv, dataCommand)
    if (failure !== undefined) {
      // The node's own stderr is why it failed. The exit code alone sends a reader
      // back to re-run the node to find out, so the reason is recorded with it.
      const reason = failure.stderr.trim()
      recordStatus(spine, workdir, 'failed', reason === '' ? failure.error : `${failure.error}: ${reason.slice(-STATUS_DETAIL)}`)
      return json({
        status: 'failed',
        node: failure.command,
        exit_code: failure.exitCode,
        error: failure.error,
        stdout: failure.stdout,
        stderr: failure.stderr,
      })
    }
    recordStatus(spine, workdir, next.status === 'done' ? 'done' : 'ready')
    // The item being iterated travels with the node views, so the subagent for a
    // pass is handed the item rather than being sent to find it on disk. Under
    // `interactiveOpen` the node this call ran travels the same way, so the CEO
    // can say which one it was and what it left behind.
    const view = item === undefined ? next : { ...next, item }
    return json(ran === undefined ? view : { ...view, ran })
  }

  /**
   * The verdict a person gives at a `user` gate, asked by the runtime.
   *
   * Asked here rather than left to the model's own `ask_user_question`, because a
   * model that can route a gate without asking can invent the answer, and an invented
   * verdict is indistinguishable from one a person gave. The gate's own outcome labels
   * are the options, so the answer is always one the graph accepts.
   * @param gate - the gate node id.
   * @param spine - the run whose gate is being routed.
   * @param view - the gate's view, whose `verdicts` are the offered options.
   * @param exec - the tool execution, for the calling agent and its abort signal.
   * @param workdir - the run directory a refusal is recorded in.
   * @returns the verdict the person chose.
   */
  async function askPerson(
    gate: string,
    spine: Spine,
    view: NodeView,
    exec: ToolExec,
    workdir: string | undefined,
  ): Promise<string> {
    if (userQuestions === undefined) {
      const message = `gate ${gate} is answered by a person and this composition mounts no user-questions `
        + 'channel, so it cannot be routed here; run it where a person can be asked'
      recordStatus(spine, workdir, 'refused', message)
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
      recordStatus(spine, workdir, 'refused', message)
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
  // A node's `reads`/`writes` are relative to the run directory, and a subagent's
  // working directory is *not* that directory — it is wherever the harness was
  // launched. Naming the run directory here, and requiring absolute paths in every
  // brief, is what keeps a node's work inside its run.
  const runDirectory = config.workdir === undefined
    ? 'the run directory the composition gave you'
    : `the run directory ${config.workdir}`
  // Only the run-time-open composition can be asked to start a mode, so only it is
  // told how. A composition with a configured graph has no `wf_open` to call.
  const openInstruction = interactiveOpen
    ? ` When the person asks you to run a mode and see each step, open it with ${toolPrefix}_open `
      + '(pass the mode as <package>/<name>), then drive it with '
      + `${toolPrefix}_next, reporting each node as it completes: each call runs exactly one `
      + 'deterministic node and names it under `ran` with the outputs and facts it left in the run '
      + `directory, so say what it produced before acting on what is due next. A FnNode, Study or `
      + '`fn` gate listed in `nodes` is deterministic — do NOT run its command and do NOT call '
      + `${toolPrefix}_done for it; call ${toolPrefix}_next again and the runtime runs it. `
      + `${toolPrefix}_done is only for a node whose work you or a subagent did.`
    : ''
  ctx.systemPrompt.section({
    name: `tool:${toolPrefix}-spine`,
    order: ctx.systemPrompt.getSectionOrder('TOOL_WORKFLOW'),
    text: `You are the CEO of a workflow run, and you are DRIVING A WORKFLOW GRAPH. Your coding tools (bash, write, edit, web, skill) are disabled — you cannot implement, edit files, or browse yourself. Advance only through the wf tools.${openInstruction} ${toolPrefix}_next is the ONLY way to learn what is due now: call it first, and call it again after every ${toolPrefix}_done / ${toolPrefix}_verdict / ${toolPrefix}_edit / ${toolPrefix}_expand — those tools only confirm the mutation, they do not tell you what is next. When ${toolPrefix}_steer is available, read it before each ${toolPrefix}_next: it is what the person said about the run since you last looked. A correction from the person is applied to that node's prompt with ${toolPrefix}_edit (\`set-prompt\`/\`append-prompt\`) and the node is re-run with ${toolPrefix}_redo. For an AgentNode or LLMNode, spawn a subagent (the subagent tool) whose task is the node's prompt_template, and explicitly tell the subagent to read the node's reads files and write its result to the node's writes file (the filename in the node's writes field, e.g. candidate.py) — wait for it, then report the node with ${toolPrefix}_done. Every path in a node's reads and writes is relative to ${runDirectory}, and the subagent's own working directory is NOT that directory: give the subagent the absolute path (${runDirectory}/<the path>) for each file it must read or write, and tell it to write nowhere else. For a FnNode, follow its content and report ${toolPrefix}_done. Route a gate with ${toolPrefix}_verdict, answering with one of the verdicts the gate view lists (proceed/reloop/halt, or the gate's own named outcomes). For a user gate the runtime asks the person itself and routes the answer they give: call ${toolPrefix}_verdict with the gate id and no verdict, and never answer for them — a verdict passed for their gate is refused. Fan out a director's plan with ${toolPrefix}_expand, and apply user steering ("skip the tests") with ${toolPrefix}_edit. When a person says a step is wrong, read the run's ledger if one is configured, then use ${toolPrefix}_edit with \`set-prompt\` or \`append-prompt\` to correct that node's prompt and ${toolPrefix}_redo to run the node again with the correction. A DataNode is iterated for you: the runtime selects its items and hands you the subgraph's nodes one pass at a time, with the item being processed in the payload's item field and on disk as the node's current item file. Do not try to iterate it yourself, and do not report the data node — or the pass's last node — before its work is done. Never skip, reorder, or bypass a node — the runner refuses to advance until you report the current node complete.`,
  })

  // Open a mode's graph after boot, for a caller that will drive it node by node.
  // Registered only when `interactiveOpen` is set: a composition with a configured
  // graph has nothing to open, and a tool that cannot run must not be advertised.
  if (interactiveOpen) {
    ctx.tools.register(defineTool({
      name: `${toolPrefix}_open`,
      description: `Open a mode's graph and claim it for this session, so it can be driven node by node. Pass the mode as \`<package>/<name>\`; the open command lowers it and this reads the graph it wrote. Returns the post-drain view, so a deterministic start node has already run and is reported under \`ran\` rather than left due. One driven run at a time is open in a session: a second open is refused until that run finishes. All the other \`${toolPrefix}_*\` tools work on the opened run, and refuse when this session has not opened one.`,
      parameters: {
        mode: { type: 'string', required: true, description: 'The mode to run, as `<package>/<name>`.' },
        knobs: { type: 'array', items: { type: 'string' }, description: 'Knob overrides for this run, each as `NAME=VALUE` (repeatable).' },
        workdir: { type: 'string', description: 'The run directory to lower into. When absent the spine derives one from this session and the mode — `<cwd>/.factory/web/<session>/<package>-<mode>` — creates it, and passes it as `--workdir`; the console\'s `open` verb requires a workdir, so one is always sent.' },
      },
      output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
      execute(args, exec) {
        const session = exec.agent?.session
        if (session === undefined) {
          throw new Error(`${toolPrefix}_open requires an owning agent session`)
        }
        // One in-session driven run at a time is the accepted v1; the claim is the
        // session's, so a second open is refused rather than silently replacing it.
        const owner = String(session.id)
        if (opened !== undefined) {
          throw new Error(opened.session === owner
            ? 'a run is already open in this session'
            : 'a run is already open in another session')
        }
        if (config.openCommand === '') {
          throw new Error(
            `${toolPrefix}_open needs an open command; set \`openCommand\` (for example `
            + '`python -m refactory_dsh.console open`)',
          )
        }
        const argv = [...config.openArgs, args.mode]
        // The console refuses without a workdir, so an absent one is derived from
        // the session and the mode rather than dropped. The payload's `run` is
        // still the authority on where the run actually landed.
        const requested = args.workdir !== undefined && args.workdir !== ''
          ? args.workdir
          : derivedWorkdir(owner, args.mode)
        argv.push('--workdir', requested)
        for (const knob of args.knobs ?? []) argv.push('--knob', knob)
        const result = spawnSync(config.openCommand, argv, { encoding: 'utf8' })
        if (result.error !== undefined) {
          throw new Error(`${toolPrefix}_open could not run the open command: ${result.error.message}`)
        }
        if (result.status !== 0) {
          const reason = lastLine(result.stderr) || lastLine(result.stdout)
          throw new Error(
            `${toolPrefix}_open could not lower ${args.mode}: `
            + (reason === '' ? `the open command exited with ${String(result.status)}` : reason),
          )
        }
        let payload: OpenPayload
        try {
          payload = JSON.parse(result.stdout) as OpenPayload
        } catch {
          const said = lastLine(result.stdout)
          throw new Error(
            `${toolPrefix}_open: the open command printed no JSON payload (${said === '' ? 'no output' : said})`,
          )
        }
        // The command's payload is the authority on where the run landed; the
        // tool's own workdir is only the request. A command that answers with
        // neither leaves the run with nowhere to write its status, so it is
        // refused rather than opened half-addressed.
        const workdir = typeof payload.run === 'string' && payload.run !== ''
          ? payload.run
          : requested
        if (workdir === '') {
          throw new Error(`${toolPrefix}_open: the open command did not report a run directory`)
        }
        if (typeof payload.graph !== 'string' || payload.graph === '') {
          throw new Error(`${toolPrefix}_open: the open command did not report where the graph was lowered`)
        }
        let graph: WorkflowGraph
        try {
          graph = JSON.parse(readFileSync(payload.graph, 'utf8')) as WorkflowGraph
        } catch (error: unknown) {
          const detail = error instanceof Error ? error.message : String(error)
          throw new Error(`${toolPrefix}_open could not read the lowered graph at ${payload.graph}: ${detail}`)
        }
        const issues = validateGraph(graph)
        if (issues.length > 0) {
          const rendered = issues
            .map(issue => (issue.node === undefined ? issue.message : `${issue.node}: ${issue.message}`))
            .join('; ')
          throw new Error(`invalid workflow graph: ${rendered}`)
        }
        const spine = new Spine(graph, { maxIterations })
        const runEnv = payloadEnv(payload.commandEnv) ?? config.commandEnv
        const runData = typeof payload.dataCommand === 'string' && payload.dataCommand !== ''
          ? payload.dataCommand
          : config.dataCommand
        opened = {
          session: owner,
          spine,
          workdir,
          name: typeof payload.name === 'string' && payload.name !== '' ? payload.name : graph.name,
          // The run's own values when the payload carries them; the composition's
          // static ones otherwise, which is what the graph profile relies on.
          commandEnv: runEnv,
          dataCommand: runData,
        }
        // A new run owns no materialised item; a fresh claim cannot inherit one.
        materialised = undefined
        // The post-drain view, not `spine.next()`: a deterministic start node runs
        // here, exactly as the next `<toolPrefix>_next` would, so it is never
        // reported as work the model owes.
        return report(spine, workdir, runEnv, runData)
      },
    }))
  }

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_next`,
    description: 'Get the next node(s) the workflow graph says are due now. Deterministic nodes (a command an `fn` gate or an `FnNode` runs) and a data node\'s item passes are executed for you and do not appear, except that when a mode is open interactively exactly one deterministic node runs per call: that node is named under `ran` (with its id, declared outputs and the run\'s facts), and `nodes` then names what is due next — which may itself be deterministic, in which case call this tool again rather than running it or calling `wf_done`. Returns `{ status, nodes }`: for `ready`, the node views you must act on (agent task / one-shot LLM / user gate / fork) with the fields you need, plus `item` while a data node is iterating; for `done`, the run is complete; for `failed`, a deterministic node failed and the cursor has not moved. Always call this first and act only on what it returns — never skip or reorder.',
    parameters: {},
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute(_args, exec) {
      const { spine, workdir, commandEnv, dataCommand } = requireRun(exec)
      return report(spine, workdir, commandEnv, dataCommand)
    },
  }))

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_done`,
    description: 'Mark one non-gate node complete and advance the spine. Returns only a confirmation — call `wf_next` to learn the next node. Call this after the node `wf_next` returned is done (e.g. after the subagent for an AgentNode finishes). Never call this for a deterministic node (`FnNode`/`Study`, or an `fn` gate) when the composition runs commands: the runtime runs those itself on the next `wf_next`, and marking one complete here would skip its work.',
    parameters: {
      node: { type: 'string', required: true, description: 'The node id to mark complete.' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute(args, exec) {
      const { spine } = requireRun(exec)
      spine.complete(args.node)
      return sync(json({ status: 'complete', node: args.node }))
    },
  }))

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_verdict`,
    description: 'Route a gate node. For a gate with a command, pass one of the `verdicts` the gate view from `wf_next` lists: `proceed` advances along the proceed edge, `reloop` rewinds to the gate\'s reloop target with `feedback` (bounded by the iteration cap), `halt` stops or follows the halt edge, and any other label is that gate\'s own forward outcome. A `user` gate is not yours to decide: call this tool with the gate id **and no `verdict`** — the runtime asks the person, routes the answer they give, and refuses when nobody can be asked. There is deliberately no way to answer for them. Returns only a confirmation — call `wf_next` to learn the next node.',
    parameters: {
      gate: { type: 'string', required: true, description: 'The gate node id.' },
      verdict: { type: 'string', description: 'For a gate with a command: the verdict, one of the gate\'s listed `verdicts`. Omit it for a `user` gate — the runtime puts that gate to the person, and a verdict passed here is refused rather than recorded, because a person\'s answer is not the caller\'s to give.' },
      feedback: { type: 'string', description: 'Feedback to attach to the reloop target (reloop only).' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    async execute(args, exec) {
      const { spine, workdir } = requireRun(exec)
      const view = spine.view(args.gate)
      if (view.evaluator_type === 'user') {
        if (args.verdict !== undefined) {
          throw new Error(`gate ${args.gate} is a user gate: calling it with a verdict would record an answer the person never gave. Call again with the gate id alone; the runtime asks them.`)
        }
        const verdict = await askPerson(args.gate, spine, view, exec, workdir)
        spine.verdict(args.gate, verdict, args.feedback ?? '')
        return json({ status: 'routed', gate: args.gate, verdict, answered_by: 'user' })
      }
      if (args.verdict === undefined) {
        const offered = (view.verdicts ?? []).join(', ')
        throw new Error(`gate ${args.gate} routes on a verdict: pass one of ${offered || 'the verdicts the gate view lists'}`)
      }
      spine.verdict(args.gate, args.verdict, args.feedback ?? '')
      return json({ status: 'routed', gate: args.gate, verdict: args.verdict })
    },
  }))

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_edit`,
    description: 'Edit the live graph: `skip` hot-removes a node (and, for a fork, its targets); `unskip` restores it; `set-prompt` replaces an agent/llm node\'s prompt and `append-prompt` adds a paragraph to it. This is how a person\'s correction reaches the node\'s prompt — follow it with `wf_redo` to run that node again. Returns only a confirmation — call `wf_next` to learn the next node.',
    parameters: {
      node: { type: 'string', required: true, description: 'The node id to edit.' },
      action: { type: 'string', required: true, enum: ['skip', 'unskip', 'set-prompt', 'append-prompt'], description: 'The edit.' },
      text: { type: 'string', description: 'The new prompt text; required for `set-prompt` and `append-prompt`.' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute(args, exec) {
      const { spine } = requireRun(exec)
      if (args.action === 'skip') spine.skip(args.node)
      else if (args.action === 'unskip') spine.unskip(args.node)
      else {
        if (args.text === undefined || args.text.trim() === '') {
          throw new Error(`${toolPrefix}_edit action '${args.action}' needs a non-empty prompt amendment in 'text'`)
        }
        spine.amendPrompt(args.node, args.text, args.action === 'set-prompt' ? 'set' : 'append')
      }
      return sync(json({ status: 'edited', node: args.node, action: args.action }))
    },
  }))

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_redo`,
    description: 'Reopen a completed node so the next `wf_next` returns it again; an amended prompt or other knob is what the re-run uses. Use this after correcting a node to run that node once more. Returns only a confirmation — call `wf_next` to get the node back.',
    parameters: {
      node: { type: 'string', required: true, description: 'The completed node id to reopen.' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute(args, exec) {
      const { spine } = requireRun(exec)
      spine.redo(args.node)
      return sync(json({ status: 'redone', node: args.node }))
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
    execute(args, exec) {
      const { spine } = requireRun(exec)
      const entries = args.entries as Array<Record<string, unknown>>
      const result = spine.expand(args.director, entries)
      return sync(json({ status: 'expanded', director: result.expanded, children: result.children }))
    },
  }))

  ctx.tools.register(defineTool({
    name: `${toolPrefix}_state`,
    description: 'Return the full live state: cursor, completed, skipped, gate verdicts, iteration counts, feedback, amended prompts, and the run log.',
    parameters: {},
    output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
    execute(_args, exec) {
      const { spine } = requireRun(exec)
      return sync(json(spine.state()))
    },
  }))

  // The live steering channel: a person's mid-run correction reaching the driver,
  // not only the next agent node. The ledger's owner is the configured command, so
  // the spine learns only a command to run and returns the JSON it prints
  // unchanged — it never parses the ledger itself. Registered when a command is
  // configured and there will be a run directory to read: the configured one, or
  // the one a session opens at run time. Without either there is nothing to read,
  // and a tool that cannot run must not be advertised as if it could.
  if (config.steerCommand !== '' && (config.workdir !== undefined || interactiveOpen)) {
    const command = config.steerCommand
    const steerArgs = [...config.steerArgs]
    ctx.tools.register(defineTool({
      name: `${toolPrefix}_steer`,
      description: `Read what the person said about this run since you last looked, before deciding anything. Runs the run's steering command and returns its ledger payload unchanged: the origin, every entry (a note, a correction, a constraint), recorded gate verdicts, and the fidelity problems found. Read it before each \`${toolPrefix}_next\`; when it carries a correction, apply it to that node's prompt with \`${toolPrefix}_edit\` (\`set-prompt\`/\`append-prompt\`) and re-run the node with \`${toolPrefix}_redo\`. An unreadable or unavailable ledger is reported as \`status: 'unavailable'\`, never thrown, so it cannot stop the run.`,
      parameters: {},
      output: { schema: { type: 'json' }, render: (_args, value) => [text(value)] },
      execute(_args, exec) {
        // The ledger lives in the run directory, which for an opened run is the
        // one its `wf_open` payload named — not the composition's configured one.
        const run = requireRun(exec)
        if (run.workdir === undefined) {
          return sync(json({ status: 'unavailable', error: 'this run has no work directory to read' }))
        }
        const result = spawnSync(command, [...steerArgs, run.workdir], { encoding: 'utf8' })
        if (result.status === 0) {
          try {
            return sync(json(JSON.parse(result.stdout)))
          } catch {
            // Exit 0 with unparseable output is as unreadable as a failure: fall
            // through to the unavailable payload instead of throwing.
          }
        }
        const reason = lastLine(result.stderr) || lastLine(result.stdout) || 'no output'
        return sync(json({ status: 'unavailable', error: reason }))
      },
    }))
  }

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
