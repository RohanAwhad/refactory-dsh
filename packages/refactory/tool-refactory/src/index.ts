/**
 * The model-facing `refactory` tool: drive the outer loop from the Web GUI.
 *
 * The loop is a Python program and stays one. This tool is the whole DSH-side
 * surface over it: one verb per console call, each returning the console's JSON
 * unchanged, so the client card renders the same contract an operator would read
 * in a shell. Starting a run spawns a process that outlives the call; watching it
 * tails a progress file. Nothing here reimplements the loop.
 *
 * The tool is registered by name (`refactory`) and the client card claims that
 * wire name through the `tool.call.toolview` slot, so the call renders as a run
 * view rather than a generic row.
 * @module @deepseek-ai/dsh-tool-refactory
 */

import { resolve } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { ContentBlock } from '@deepseek-ai/dsh-llm'
import type { ToolCallView, ToolResultView } from '@deepseek-ai/dsh-tools'
import type { JsonValue } from '@deepseek-ai/dsh-util-values'
import { Console, isRecord, type ConsoleResult } from './console.ts'

export const name = 'tool-refactory'
export const inject = ['tools', 'subprocess', 'systemPrompt']

/** Deployment settings: where the checkout is and how long a call may take. */
export interface Config {
  /** Model-facing tool name (default `refactory`). */
  toolName?: string
  /** Repository root holding `refactory/` and `.venv/` (default: the process cwd). */
  repoRoot?: string
  /** Directory of the interpreter with refactory_dsh installed (default `<repoRoot>/.venv/bin`). */
  pythonBin?: string
  /** Wall-clock ceiling for one console call (default 10 minutes). */
  timeoutMs?: number
  /** Per-stream collection cap in bytes (default 4 MiB). */
  collectBytes?: number
}

export const Config: z<Config> = z.object({
  toolName: z.string().default('refactory'),
  repoRoot: z.string().default(process.cwd()),
  pythonBin: z.string().default(''),
  timeoutMs: z.natural().min(1).max(60 * 60 * 1000).default(10 * 60 * 1000),
  collectBytes: z.natural().min(1024).default(4 * 1024 * 1024),
})

/** Resolved config: `pythonBin` defaults against the resolved repo root, not the schema. */
type ResolvedConfig = Omit<Required<Config>, 'pythonBin'> & { pythonBin: string }

/** The verbs the tool exposes, and the one thing each needs to be addressed. */
const VERBS = {
  modes: 'List the modes a run can be started from, with their parameters.',
  runs: 'List runs under the run root, newest first.',
  start: 'Start a run in its own process; returns the run id to watch it with.',
  status: 'Where a run is, and the events since an offset.',
  artifacts: 'What a run left on disk, optionally with contents.',
  leaderboard: 'The run\'s candidates by score, each with its diff against its parent.',
  candidate: 'One candidate, with what it changed against the parent it came from.',
  stop: 'Ask a run to stop.',
} as const

/** A console verb name. */
type Verb = keyof typeof VERBS

/** The verbs that address an existing run. */
const RUN_SCOPED: readonly Verb[] = ['status', 'artifacts', 'leaderboard', 'candidate', 'stop']

const DESCRIPTION = [
  'Drive and read a refactory outer-loop run: the optimizer loop that proposes edits to a mode,',
  'evaluates them, and keeps what improves the score. Use it to start a run, watch it while it is',
  'in flight, and read what it produced.',
  '',
  `Actions: ${Object.entries(VERBS).map(([verb, help]) => `${verb} (${help})`).join(' ')}`,
  '',
  'The loop runs in its own process, so a run continues after the call returns and after the',
  'session ends; watch it with action "status" and an "after" offset. Prefer this tool over',
  'running the loop in a shell: it is the same console the Web view renders, and a run started',
  'in a shell will not appear in the view unless it lives under the run root.',
].join('\n')

/** Input the tool accepts, after the registry's schema check. */
interface Args {
  action: Verb
  mode?: string
  run?: string
  runId?: string
  candidate?: string
  after?: number
  contents?: boolean
  limit?: number
  runsRoot?: string
  settings?: Record<string, string | number>
}

/** One run's setting, as the console's `--<flag> value` pair. */
function settingsOf(args: Args): Record<string, string | number | undefined> {
  const settings = args.settings ?? {}
  return Object.fromEntries(Object.entries(settings))
}

/**
 * The console invocation one call maps to. Every argument is passed through as
 * an option or positional; nothing is interpreted here, so a new console flag is
 * usable from the tool the moment the console declares it.
 * @param args - the model's arguments, already schema-checked.
 * @returns the verb, positionals, and options for one console call.
 */
export function invocationFor(args: Args): {
  verb: Verb
  positional: string[]
  options: Record<string, string | number | boolean | undefined>
} {
  const options: Record<string, string | number | boolean | undefined> = {
    run: args.run,
    'run-id': args.runId,
    after: args.after,
    contents: args.contents,
    limit: args.limit,
    'runs-root': args.runsRoot,
    ...settingsOf(args),
  }
  if (args.action === 'candidate' && args.candidate === undefined) {
    throw new Error('action "candidate" needs `candidate`: the candidate id to read')
  }
  if (RUN_SCOPED.includes(args.action) && args.run === undefined) {
    throw new Error(`action "${args.action}" needs \`run\`: the run id or directory to read`)
  }
  if (args.action === 'start' && args.mode === undefined) {
    throw new Error('action "start" needs `mode`: the <package>/<name> to run')
  }
  // The console takes the mode as a positional and the candidate id as a
  // positional; every other argument is an option.
  const positional = [
    ...args.action === 'start' && args.mode !== undefined ? [args.mode] : [],
    ...args.action === 'candidate' && args.candidate !== undefined ? [args.candidate] : [],
  ]
  return { verb: args.action, positional, options }
}

/**
 * A payload value as text. The payload is JSON, so a value that is not a scalar
 * is rendered as JSON rather than as `[object Object]`.
 * @param value - any value from the console's payload.
 * @returns its text.
 */
function scalar(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (value === null || value === undefined) return ''
  return JSON.stringify(value)
}

/** `best 1.5`, or a stated absence, as the console reports a score. */
function score(value: unknown): string {
  return typeof value === 'number' ? `best score ${String(value)}` : 'no score yet'
}

/**
 * What the model is told after a call: one line per verb, plus the detail that
 * verb's answer is about. The structured value travels beside it for the browser
 * card, so neither has to parse the other.
 * @param args - the call's arguments.
 * @param value - the console's JSON, or undefined when the call failed.
 * @returns the model-facing content blocks.
 */
export function renderResult(args: Args, value: unknown): ContentBlock[] {
  const data = isRecord(value) ? value : {}
  const lines: string[] = []
  switch (args.action) {
    case 'modes': {
      const modes = Array.isArray(value) ? value : []
      lines.push(`${modes.length} mode(s) can be started:`)
      for (const mode of modes) {
        if (!isRecord(mode)) continue
        lines.push(`- ${scalar(mode.id)}${mode.startable === true ? '' : ' (not startable)'}`)
      }
      break
    }
    case 'runs': {
      const runs = Array.isArray(value) ? value : []
      lines.push(`${runs.length} run(s):`)
      for (const run of runs) {
        if (!isRecord(run)) continue
        const state = run.finished === true ? 'finished' : 'in flight'
        lines.push(`- ${scalar(run.run_id)} · ${scalar(run.mode)} · ${score(run.best_score)} · ${state}`)
      }
      break
    }
    case 'start':
      lines.push(`Started run ${scalar(data.run_id)} of ${scalar(data.mode)} in its own process.`)
      lines.push(`Watch it with action "status" and run "${scalar(data.run_id)}".`)
      break
    case 'status': {
      lines.push(`Run ${scalar(data.run_id)} is ${scalar(data.state)}; ${score(data.best_score)}.`)
      const counters = isRecord(data.counters) ? data.counters : {}
      lines.push(
        `epoch ${scalar(counters.epoch) || '0'}, generation ${scalar(counters.generation) || '0'}, `
        + `${scalar(counters.evaluations) || '0'} evaluation(s).`,
      )
      break
    }
    case 'artifacts': {
      const artifacts = Array.isArray(data.artifacts) ? data.artifacts : []
      const present = artifacts.filter(entry => isRecord(entry) && entry.exists === true)
      lines.push(`${present.length} artifact(s) present:`)
      for (const entry of present) {
        if (!isRecord(entry)) continue
        lines.push(`- ${scalar(entry.name)} (${scalar(entry.media_type)})`)
      }
      break
    }
    case 'leaderboard': {
      const rows = Array.isArray(value) ? value : []
      lines.push(`${rows.length} candidate(s), best first:`)
      for (const row of rows) {
        if (!isRecord(row)) continue
        lines.push(`- ${scalar(row.id)} scored ${typeof row.score === 'number' ? String(row.score) : 'nothing'}`)
      }
      break
    }
    case 'candidate': {
      const id = scalar(data.candidate) || args.candidate || ''
      const parent = scalar(data.parent) || 'no parent'
      lines.push(`Candidate ${id} against ${parent}:`)
      lines.push(scalar(data.rendered))
      break
    }
    default:
      lines.push(data.stopped === true
        ? 'The run was asked to stop.'
        : `Nothing was stopped: ${scalar(data.reason) || 'unknown'}.`)
      break
  }
  return [{ type: 'text', text: lines.join('\n') }]
}

/**
 * The structured payload the browser card renders. The card cannot rebuild the
 * console's answer from the summary text, so the answer travels beside it —
 * which is what makes the view a view of the run rather than of a sentence
 * about the run. Persisted with the session log, so a replayed call renders the
 * same card.
 * @param value - the tool's canonical output.
 * @returns the action and the console's payload.
 */
export function presentationMeta(value: { action: string; data: JsonValue }): JsonValue {
  return { action: value.action, data: value.data }
}

/**
 * The pending-call card: which action is about to run, and on what.
 * @param args - the call's arguments.
 * @returns the generic card intent.
 */
export function presentCall(args: Args): ToolCallView {
  const subject = args.mode ?? args.run ?? args.candidate ?? ''
  return {
    card: 'generic',
    title: subject === '' ? `refactory ${args.action}` : `refactory ${args.action} ${subject}`,
    kind: 'other',
  }
}

/**
 * The completed-call card: the same action title, with the summary the model saw.
 * @param args - the call's arguments.
 * @param result - the tool's canonical output.
 * @returns the generic result intent.
 */
export function presentResult(args: Args, result: unknown): ToolResultView {
  return {
    card: 'generic',
    title: `refactory ${args.action}`,
    content: renderResult(args, isRecord(result) ? result.data : undefined),
  }
}

export function apply(ctx: Context, config: Config): void {
  const resolved: ResolvedConfig = {
    ...config as Required<Config>,
    pythonBin: (config as Required<Config>).pythonBin || resolve((config as Required<Config>).repoRoot, '.venv', 'bin'),
  }
  const console = new Console(ctx.subprocess, {
    repoRoot: resolve(resolved.repoRoot),
    pythonBin: resolve(resolved.pythonBin),
    timeoutMs: resolved.timeoutMs,
    collectBytes: resolved.collectBytes,
  })

  // Usage policy ships with the tool: the loop is not something to reach for
  // unprompted, and the model needs to know a run outlives the call.
  ctx.systemPrompt.section({
    name: `tool:${resolved.toolName}`,
    order: ctx.systemPrompt.getSectionOrder('TOOL_REFACTORY'),
    text: `Use the ${resolved.toolName} tool to run and read a refactory outer-loop run. A run starts in its own process and continues after the call returns, so start it, then watch it with the "status" action (pass an "after" offset to read only what is new) instead of blocking on it.`,
  })

  ctx.tools.register(defineTool({
    name: resolved.toolName,
    description: DESCRIPTION,
    parameters: {
      action: {
        type: 'string',
        required: true,
        enum: Object.keys(VERBS),
        description: `The verb. ${Object.entries(VERBS).map(([verb, help]) => `${verb}: ${help}`).join(' ')}`,
      },
      mode: { type: 'string', description: 'Mode to run, as <package>/<name>. Required by action "start".' },
      run: {
        type: 'string',
        description: 'The run id or absolute run directory. Required by status, artifacts, leaderboard, candidate, stop.',
      },
      runId: { type: 'string', description: 'Run id to claim, for action "start" (default: derived from the mode and the time).' },
      candidate: { type: 'string', description: 'Candidate id to read. Required by action "candidate".' },
      after: { type: 'integer', description: 'For action "status": only return events after this many progress lines, so a poll reads what is new.' },
      contents: { type: 'boolean', description: 'For action "artifacts": include each artifact\'s contents.' },
      limit: { type: 'integer', description: 'For action "runs"/"leaderboard": how many rows to return.' },
      runsRoot: { type: 'string', description: 'Directory holding the runs (default: <repoRoot>/.factory/loop).' },
      settings: {
        type: 'object',
        additionalProperties: true,
        description: 'Run settings for action "start": epochs, batch_size, optimizer, strategy, evaluator, seed, elite, attempts, preset, max_evaluations. Passed through as console flags.',
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          action: { type: 'string', required: true },
          data: { type: 'json', required: true },
        },
      },
      render: (args, value) => renderResult(args as unknown as Args, value.data),
      presentationMeta: (_args, value) => presentationMeta(value),
    },
    async execute(args, exec) {
      const typed = args as unknown as Args
      const invocation = invocationFor(typed)
      const result: ConsoleResult = await console.run(invocation, exec.signal)
      if (!result.ok) {
        // A refusal carries the console's own reason. Surface it as a tool error
        // so the model corrects the call rather than reading a silent success.
        throw new Error(`${resolved.toolName} ${typed.action} failed: ${result.error ?? 'no reason reported'}`)
      }
      return { action: typed.action, data: (result.value ?? null) as JsonValue }
    },
    presentCall: args => presentCall(args as unknown as Args),
    presentResult: (args, result) => presentResult(args as unknown as Args, result),
  }))
}

export { Console } from './console.ts'
export type { ConsoleInvocation, ConsoleResult, ConsoleSettings } from './console.ts'
