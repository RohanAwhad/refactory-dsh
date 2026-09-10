/**
 * Running a graph's deterministic nodes against a shell executor.
 *
 * An `FnNode` with a `command` and an `fn` `GateNode` with an `evaluator_command`
 * need no model: the plugin can run them itself, so `wf_next` hands the driving
 * agent only the nodes that need one. Keeping this out of `spine.ts` leaves the
 * cursor state machine free of I/O.
 * @module @deepseek-ai/dsh-workflow-spine/execute
 */

import type { ShellExecutor } from '@deepseek-ai/dsh-shell'
import type { NodeView } from './spine.ts'

/**
 * Environment key carrying the running node's declaration as JSON. A node
 * command is generic — it knows nothing about the graph that invoked it — so
 * the plugin hands it the declaration it needs (its id and the artifacts it
 * reads and writes) under one harness-owned name.
 */
export const NODE_ENV_KEY = 'DSH_WORKFLOW_NODE'

/** What a node command is told about the node it is running as. */
export interface NodeManifest {
  id: string
  type: string
  command: string
  reads: string[]
  writes: string[]
}

/** Per-run settings for node commands: where they run, and with what. */
export interface CommandRunnerConfig {
  /** Working directory for every node command; absent uses the executor's own. */
  workdir?: string
  /** Extra environment entries merged into each node command. */
  env: Record<string, string>
  /** Foreground timeout for one node command. */
  timeoutMs: number
}

/** The outcome of one deterministic node. */
export interface CommandOutcome {
  /** True when the node completed and the spine may advance. */
  ok: boolean
  /** The command that ran, for reporting. */
  command: string
  /** The process exit code; null when the process died from a signal. */
  exitCode: number | null
  /** A gate's routed outcome, read from the last non-empty stdout line. */
  verdict?: string
  /** Bounded stdout, for the failure report. */
  stdout: string
  /** Bounded stderr, for the failure report. */
  stderr: string
  /** Why the node failed, when it did. */
  error?: string
}

/** The command a node runs itself, or `undefined` when a model owns the node. */
export function commandOf(node: NodeView): string | undefined {
  if (node._type === 'FnNode' || node._type === 'Study') {
    return node.command === undefined || node.command === '' ? undefined : node.command
  }
  if (node._type === 'GateNode' && node.evaluator_type === 'fn') {
    const command = node.evaluator_command
    return command === undefined || command === '' ? undefined : command
  }
  return undefined
}

/** True when the plugin runs this node rather than handing it to the driver. */
export function isDeterministic(node: NodeView): boolean {
  return commandOf(node) !== undefined
}

/**
 * The verdict a gate's evaluator stdout declares: its last non-empty line,
 * lowercased to match how the spine compares edge conditions.
 * @param stdout - the evaluator's captured stdout.
 * @returns the verdict label, or `undefined` when the evaluator printed nothing.
 */
export function verdictOf(stdout: string): string | undefined {
  const lines = stdout.split('\n').map(line => line.trim()).filter(line => line !== '')
  const last = lines[lines.length - 1]
  return last === undefined ? undefined : last.toLowerCase()
}

/** The declaration handed to a node command. */
function manifestOf(node: NodeView, command: string): NodeManifest {
  return {
    id: node.id,
    type: node._type,
    command,
    reads: node.reads ?? [],
    writes: node.writes ?? [],
  }
}

/**
 * Run one deterministic node.
 *
 * A nonzero exit, a timeout, an abort, or (for a gate) empty stdout fails the
 * node rather than advancing the cursor: a graph step that did not happen must
 * not look like one that did.
 * @param shell - the shell executor that runs node commands.
 * @param node - the node view whose command to run.
 * @param config - work directory, environment, and timeout for the command.
 * @returns the outcome; never rejects for a command's own failure.
 */
export async function runDeterministic(
  shell: ShellExecutor,
  node: NodeView,
  config: CommandRunnerConfig,
): Promise<CommandOutcome> {
  const command = commandOf(node)
  const base: CommandOutcome = {
    ok: false,
    command: command ?? '',
    exitCode: null,
    stdout: '',
    stderr: '',
  }
  if (command === undefined) {
    return { ...base, error: `${node.id} has no command` }
  }

  const spec = shell.resolve({
    command,
    env: { ...config.env, [NODE_ENV_KEY]: JSON.stringify(manifestOf(node, command)) },
    timeoutMs: config.timeoutMs,
    ...(config.workdir === undefined ? {} : { workdir: config.workdir }),
  })
  const result = await shell.run(spec)
  const stdout = result.stdout.text.trim()
  const stderr = result.stderr.text.trim()
  const outcome: CommandOutcome = { ...base, exitCode: result.exitCode, stdout, stderr }

  if (result.timedOut) {
    return { ...outcome, error: `${node.id} timed out after ${result.timeoutMs}ms` }
  }
  if (result.aborted) return { ...outcome, error: `${node.id} was aborted` }
  if (result.exitCode !== 0) {
    return { ...outcome, error: `${node.id} exited with code ${String(result.exitCode)}` }
  }

  if (node._type === 'GateNode') {
    const verdict = verdictOf(stdout)
    if (verdict === undefined) return { ...outcome, error: `${node.id} printed no verdict` }
    return { ...outcome, ok: true, verdict }
  }
  return { ...outcome, ok: true }
}
