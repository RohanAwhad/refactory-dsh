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

import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { ContentBlock } from '@deepseek-ai/dsh-llm'
import type { ToolCallView, ToolResultView } from '@deepseek-ai/dsh-tools'
import type { JsonValue } from '@deepseek-ai/dsh-util-values'
import {
  CONSOLE_DEFAULTS, Console, isRecord, resolveConsoleSettings,
  type ConsoleResult,
} from './console.ts'

import { CHAT, PARAMETERS, OPERATING_GUIDE, BUILD_ID } from './generated-help.ts'

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
  timeoutMs: z.natural().min(1).max(120 * 60 * 1000).default(CONSOLE_DEFAULTS.timeoutMs),
  collectBytes: z.natural().min(1024).default(CONSOLE_DEFAULTS.collectBytes),
})

/** Generated from the console parser and reviewed interface metadata. */
type Verb = keyof typeof CHAT
const DESCRIPTION = 'Discover and use Refactory. Begin with action help, then help topic=<mode or operation> for full live argument schemas and supported interfaces. Action docs searches/reads current-build product documentation without shell or source access. Run uses a mode; start optimizes a scored mode. For interactive user gates use wf_open.'

/** Input the tool accepts, after the registry's schema check. */
interface Args {
  action: Verb
  topic?: string
  page?: string
  query?: string
  section?: string
  offset?: number
  mode?: string
  run?: string
  runId?: string
  candidate?: string
  after?: number
  contents?: boolean
  limit?: number
  runsRoot?: string
  settings?: Record<string, string | number | boolean | Record<string, unknown> | string[]>
  intent?: string
  note?: string
  kind?: string
  state?: boolean
  as?: string
  packages?: string
  target?: string
  args?: string
  name?: string
  brief?: string
  extends?: string
  check?: string
  allowRegression?: boolean
  approve?: string[]
  headless?: boolean
  inputs?: string[]
  resume?: string
  sample?: string[]
  knobs?: string[]
  provider?: string
  model?: string
  attempts?: number
  dataNode?: boolean
  task?: string
  singleShot?: boolean
}

/** Shared generated mappings are the actual invocation adapter, not a second help list. */
export function invocationFor(args: Args): {
  verb: Verb
  positional: readonly string[]
  options: Readonly<Record<string, string | number | boolean | readonly string[] | undefined>>
} {
  type Spec = { flags: readonly string[]; positional: boolean; type: string; position?: number }
  type Mapping = { verb: Verb; order: readonly string[]; fields: Readonly<Record<string, string>>; required: readonly string[];
    arguments: Readonly<Record<string, Spec>>; defaults?: Readonly<Record<string, unknown>>; settings?: boolean }
  const mapping = CHAT[args.action] as Mapping
  const values = { ...mapping.defaults, ...args } as Record<string, unknown>
  if (mapping.required.some(key => values[key] === undefined)) {
    const names = mapping.required.map(key => '`' + key + '`')
    const required = names.length > 1 ? names.slice(0, -1).join(', ') + ' and ' + names.at(-1) : names[0]
    throw new Error(`action "${args.action}" needs ${required}`)
  }
  if ((args.approve?.length ?? 0) > 0 && args.headless !== true) {
    throw new Error('`approve` answers a gate on the person’s behalf and needs `headless: true`. When a person is here use wf_open so the runtime asks their user gate.')
  }
  const positional: string[] = []
  const options: Record<string, string | number | boolean | readonly string[] | undefined> = {}
  for (const alias of mapping.order) {
    const spec = mapping.arguments[alias]!
    const value = values[alias]
    if (spec.positional) {
      if (value !== undefined) positional.push(String(value))
    } else {
      const flag = spec.flags[0]!.replace(/^--/, '')
      options[flag] = spec.type === 'object' && value !== undefined ? JSON.stringify(value)
        : value as string | number | boolean | readonly string[] | undefined
    }
  }
  if (mapping.settings) {
    for (const [key, value] of Object.entries(args.settings ?? {})) {
      options[key] = typeof value === 'object' ? JSON.stringify(value) : value
    }
  }
  return { verb: mapping.verb, positional, options }
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
 * One mode as a line a caller can choose a route from.
 *
 * Everything here is the console's own answer: the verb that runs the mode
 * (`action`), the second route the workflow spine offers (`interactive`, driven one
 * node at a time with `wf_open`), where the run stops for a person (`gates`, a
 * `user` gate meaning the runtime asks them and no verdict may be passed for it),
 * and the knobs it declares. The two routes are named together on purpose: a caller
 * shown only the headless verb picks it, and a headless authoring run pre-answers
 * the very gate the person would have been asked at.
 * @param mode - one catalogue entry.
 * @returns the summary that follows the mode's id.
 */
function modeSummary(mode: Readonly<Record<string, unknown>>): string {
  const parts: string[] = []
  const action = scalar(mode.action)
  parts.push(`run: ${action === '' ? 'unknown' : action}`)
  if (mode.interactive === true) parts.push('or wf_open to drive it node by node')
  const gates = (Array.isArray(mode.gates) ? mode.gates : [])
    .filter(isRecord)
    .map(gate => `${scalar(gate.id)}${gate.user === true ? ' (user)' : ''}`)
    .filter(gate => gate !== '')
  if (gates.length > 0) parts.push(`gates: ${gates.join(', ')}`)
  const knobs = isRecord(mode.parameters) ? Object.keys(mode.parameters) : []
  if (knobs.length > 0) parts.push(`knobs: ${knobs.join(', ')}`)
  if (mode.startable !== true) parts.push('not startable')
  return parts.join('; ')
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
    case 'runtime': {
      lines.push(JSON.stringify(data, null, 2))
      break
    }
    case 'help':
    case 'docs':
    case 'workers':
    case 'profiles':
    case 'profile-resolve':
    case 'comparison':
      lines.push(JSON.stringify(value, null, 2))
      break
    case 'modes': {
      const modes = Array.isArray(value) ? value : []
      lines.push(`${modes.length} mode(s):`)
      for (const mode of modes) {
        if (!isRecord(mode)) continue
        lines.push(`- ${scalar(mode.id)} — ${modeSummary(mode)}`)
        lines.push(JSON.stringify(mode))
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
    case 'strategies': {
      const seams = isRecord(value) ? value : {}
      const groups: Array<[string, string]> = [
        ['outer_loops', 'outer loop'],
        ['optimizers', 'optimizer'],
        ['evaluators', 'evaluator'],
      ]
      for (const [key, label] of groups) {
        const entries = Array.isArray(seams[key]) ? seams[key] : []
        lines.push(`${entries.length} ${label}(s):`)
        for (const entry of entries) {
          if (!isRecord(entry)) continue
          lines.push(`- ${scalar(entry.name)}: ${scalar(entry.description)}`)
        }
      }
      break
    }
    case 'start':
      lines.push(`Started run ${scalar(data.run_id)} of ${scalar(data.mode)} in its own process.`)
      lines.push(`Watch it with action "status" and run "${scalar(data.run_id)}".`)
      break
    case 'status': {
      // An authoring run is driven by the graph spine and writes no progress lines, so
      // its own status file is what says where it is.
      const authoring = isRecord(data.authoring) ? data.authoring : undefined
      if (authoring !== undefined) {
        const due = Array.isArray(authoring.nodes_due) ? authoring.nodes_due.map(String) : []
        const done = Array.isArray(authoring.completed) ? authoring.completed.length : 0
        lines.push(`Authoring run ${scalar(authoring.name)} is ${scalar(authoring.spine)}: ${done} node(s) done${due.length === 0 ? '' : `, ${due.length} due (${due.join(', ')})`}.`)
        if (typeof authoring.error === 'string' && authoring.error !== '') lines.push(`error: ${authoring.error}`)
        if (typeof authoring.validation === 'string' && authoring.validation !== '') lines.push(`validation: ${authoring.validation}`)
      }
      lines.push(`Run ${scalar(data.run_id)} is ${scalar(data.state)}; ${score(data.best_score)}.`)
      const counters = isRecord(data.counters) ? data.counters : {}
      lines.push(
        `epoch ${scalar(counters.epoch) || '0'}, generation ${scalar(counters.generation) || '0'}, `
        + `${scalar(counters.evaluations) || '0'} evaluation(s).`,
      )
      break
    }
    case 'artifacts': {
      const artifacts: unknown[] = Array.isArray(data.artifacts) ? data.artifacts : []
      const present = artifacts.filter((entry): entry is Record<string, unknown> =>
        isRecord(entry) && entry.exists === true)
      lines.push(`${present.length} artifact(s) present:`)
      for (const entry of present) {
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
    case 'run': {
      // A single run is the CLI's `run`. The runner prints its report from the child
      // process — the terminal's stdout, not the console's buffer — so the console
      // reads the run directory back and reports that instead.
      lines.push(`Ran ${scalar(args.mode)}${data.ok === true ? '' : ' and it did not finish'}.`)
      const single = isRecord(data.run) ? data.run : {}
      if (typeof single.workdir === 'string') {
        lines.push(`Run directory: ${single.workdir}. Read it with action "status", or "artifacts" for what it left.`)
      }
      if (typeof single.state === 'string') lines.push(`State: ${single.state}.`)
      const facts = isRecord(single.facts) ? single.facts : {}
      const recorded = Object.entries(facts)
        .filter(([, value]) => value !== null && value !== '' && value !== undefined)
        .slice(0, 8)
        .map(([key, value]) => `${key}: ${scalar(value)}`)
      if (recorded.length > 0) lines.push(`What the run recorded — ${recorded.join(', ')}.`)
      break
    }
    case 'candidate': {
      const id = scalar(data.candidate) || args.candidate || ''
      const parent = scalar(data.parent) || 'no parent'
      lines.push(`Candidate ${id} against ${parent}:`)
      lines.push(scalar(data.rendered))
      break
    }
    case 'steer': {
      const entries = Array.isArray(data.entries) ? data.entries : []
      lines.push(`Run directory: ${scalar(data.run)}.`)
      lines.push(`Intent ledger: ${scalar(data.ledger)}.`)
      lines.push(
        `The intent ledger is ${data.exists === true ? 'present' : 'missing'} `
        + `with ${entries.length} entr${entries.length === 1 ? 'y' : 'ies'}:`,
      )
      for (const entry of entries) {
        if (isRecord(entry)) lines.push(`- [${scalar(entry.kind)}] ${scalar(entry.text)}`)
      }
      const verdicts = isRecord(data.verdicts) ? data.verdicts : {}
      const gates = Object.keys(verdicts)
      if (gates.length > 0) {
        lines.push(`Gate verdicts: ${gates.map(gate => `${gate}=${scalar(verdicts[gate])}`).join(', ')}.`)
      }
      for (const problem of Array.isArray(data.problems) ? data.problems : []) {
        lines.push(`problem: ${scalar(problem)}`)
      }
      break
    }
    case 'best':
      lines.push(`The winner is ${scalar(data.candidate)}, scoring ${typeof data.score === 'number' ? String(data.score) : 'nothing'}.`)
      lines.push('Adopt it with action "adopt" and an "as" package name.')
      break
    case 'adopt':
      lines.push(`Wrote ${scalar(data.mode)} from ${scalar(data.adopted_from)} at ${scalar(data.package_root)}.`)
      lines.push(`The adopted mode scored ${typeof data.score === 'number' ? String(data.score) : 'nothing'}; the source is untouched.`)
      break
    case 'registry':
    case 'create':
    case 'edit':
    case 'design': {
      // These forward to another entry point; its own JSON is the answer, and on a
      // failure the reason it wrote — stderr included — is the only useful part.
      lines.push(`${args.action} ${data.ok === true ? 'succeeded' : 'failed'} (exit ${scalar(data.exit_code)}).`)
      const inner = isRecord(data.result) ? data.result : {}
      const reason = typeof inner.error === 'string' && inner.error !== ''
        ? inner.error
        : typeof data.stderr === 'string' && data.stderr !== ''
          ? data.stderr
          : scalar(inner.output) || JSON.stringify(inner)
      lines.push(reason)
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
  const resolved = config as Required<Config>
  const console = new Console(ctx.subprocess, resolveConsoleSettings(resolved))

  // Usage policy ships with the tool: the loop is not something to reach for
  // unprompted, and the model needs to know a run outlives the call. The
  // published harness owns the central section-order table and has no
  // refactory-specific slot, so this outer-loop tool takes the workflow slot
  // beside the workflow and ralph tools.
  ctx.systemPrompt.section({
    name: `tool:${resolved.toolName}`,
    order: ctx.systemPrompt.getSectionOrder('TOOL_WORKFLOW'),
    text: `${OPERATING_GUIDE}\nTool: ${resolved.toolName}. Tool build identity: ${BUILD_ID}.`,
  })

  ctx.tools.register(defineTool({
    name: resolved.toolName,
    description: DESCRIPTION,
    parameters: PARAMETERS,
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
      const base = invocationFor(typed)
      const header = exec.agent?.session.requestHeader()?.config
      const selection = header ?? exec.agent?.options
      const current = selection === undefined ? null : {
        provider: selection.provider ?? null,
        model: selection.model ?? null,
        reasoning_effort: selection.reasoningEffort ?? null,
        source: header === undefined ? 'agent-options' : 'request-header',
      }
      // Only the diagnostic uses the owning selection. Never let a caller's
      // optional provider/model fields relabel the session being inspected.
      const invocation = typed.action === 'runtime'
        ? { ...base, options: { provider: selection?.provider, model: selection?.model } }
        : base
      const result: ConsoleResult = await console.run(invocation, exec.signal)
      if (!result.ok) {
        // A refusal carries the console's own reason. Surface it as a tool error
        // so the model corrects the call rather than reading a silent success.
        throw new Error(`${resolved.toolName} ${typed.action} failed: ${result.error}`)
      }
      const data = typed.action === 'runtime' && isRecord(result.value)
        ? { ...result.value, current_session: current }
        : result.value
      return { action: typed.action, data: data as JsonValue }
    },
    presentCall: args => presentCall(args as unknown as Args),
    presentResult: (args, result) => presentResult(args as unknown as Args, result),
  }))
}

export { Console } from './console.ts'
export type { ConsoleInvocation, ConsoleResult, ConsoleSettings } from './console.ts'
