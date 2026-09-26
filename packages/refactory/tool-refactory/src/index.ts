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
  CONSOLE_DEFAULTS, Console, isRecord, registryInvocation, resolveConsoleSettings, startInvocation,
  type ConsoleResult,
} from './console.ts'

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

/** The verbs the tool exposes, and the one thing each needs to be addressed. */
const VERBS = {
  runtime: 'Read current session model selection, host DSH home and nested local credential readiness before wf_open. Uses inherited settings, never changes them or makes a model request; configured does not mean authenticated.',
  modes: 'List the modes a run can be started from: the verb that runs each, that wf_open can drive it node by node, its gates with a person\'s marked, and its parameters.',
  strategies: 'List the named outer loops, optimizers and evaluators, each with what it is. Choose settings.optimizer/strategy/evaluator from these names.',
  runs: 'List runs under the run root, newest first.',
  start: 'Optimize a mode: start an outer-loop run in its own process and return the run id to watch it with.',
  run: 'Use a mode once: run it on DSH and report the result and where its artifacts are. This is the CLI\'s "run", not the optimizer.',
  status: 'Where a run is, and the events since an offset.',
  artifacts: 'What a run left on disk, optionally with contents.',
  leaderboard: 'The run\'s candidates by score, each with its diff against its parent.',
  candidate: 'One candidate, with what it changed against the parent it came from.',
  steer: 'Read an existing run\'s intent ledger, or add what the person said to it. Use the absolute run directory returned by start, or repeat its runsRoot.',
  best: 'The winner of a run, with the state that scored it.',
  adopt: 'Write a run\'s winner as a new package, leaving the run and its source untouched.',
  registry: 'List packages of modes, or verify, smoke, add, import, remove, enable or disable one.',
  create: 'Create a package of modes from a brief (model-driven); runs for many minutes, and answers its gates through "approve" with "headless" (a person present is asked through wf_open instead).',
  edit: 'Revise an existing mode from a brief (model-driven), refusing a regression; runs for many minutes, and answers its gates through "approve" with "headless" (a person present is asked through wf_open instead).',
  design: 'Design, emit and check a software artifact from a brief (model-driven); runs for many minutes, and answers its gates through "approve" with "headless" (a person present is asked through wf_open instead).',
  stop: 'Ask a run to stop.',
} as const

/** A console verb name. */
type Verb = keyof typeof VERBS

/** The verbs that address an existing run. */
const RUN_SCOPED: readonly Verb[] = [
  'status', 'artifacts', 'leaderboard', 'candidate', 'steer', 'best', 'adopt', 'stop',
]

// One physical line per paragraph: the description reaches the tool catalog
// verbatim, and a paragraph broken across source lines is a paragraph the
// markdown wrapper has to reject.
const DESCRIPTION = [
  'Drive and read a refactory outer-loop run: the optimizer loop that proposes edits to a mode, evaluates them, and keeps what improves the score. Use it to start a run, watch it while it is in flight, and read what it produced.',
  '',
  `Actions: ${Object.entries(VERBS).map(([verb, help]) => `${verb} (${help})`).join(' ')}`,
  '',
  'The loop runs in its own process, so a run continues after the call returns and after the session ends; watch it with action "status" and an "after" offset. Prefer this tool over running the loop in a shell: it is the same console the Web view renders, and a run started in a shell will not appear in the view unless it lives under the run root.',
  '',
  'Which action to use: "modes" answers with each mode\'s id, the verb that runs it (a scored mode by "start", an authoring mode by its own verb "create", "edit" or "design"), that it can also be driven node by node with "wf_open", the gates it declares with the ones a person answers marked "(user)", and its knobs. Read those fields rather than trying "start" and reading the refusal. "strategies" names every outer loop, optimizer and evaluator before you choose settings. Start with "modes", then "start" for a scored mode or the named authoring verb, then "status" until it finishes, then "leaderboard", "candidate", "artifacts" and "best".',
  '',
  'Every mode has two routes, and "modes" names both. "start" (or the mode\'s own authoring verb, "create", "edit" or "design") runs it to completion in a process of its own: it cannot pause to ask anyone anything, so a gate it stops at is answered only if "approve" carries the answer, which is why "approve" requires "headless" — say with it that nobody is at the keyboard. The other route is the workflow spine: "wf_open <package>/<name>" lowers the same mode and hands it over one node at a time, and its "user" gates are put to the person by the runtime, who answers them. So when a person is here, use "wf_open" and let them answer; never pass "approve" for a gate "modes" marks "(user)", because that records an answer they never gave. An authoring run chooses its strategy at "gate_strategy", which is one of those person gates in the shipped authoring modes.',
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

/** One run's setting, as the console's `--<flag> value` pair. */
function settingsOf(args: Args): Readonly<Record<string, string | number>> {
  const settings = args.settings ?? {}
  return Object.fromEntries(Object.entries(settings))
}

/**
 * The console invocation one call maps to. Every argument is passed through as
 * an option or positional; nothing is interpreted here, so a new console flag is
 * usable from the tool the moment the console declares it. The authoring and
 * package actions forward to the entry points the console now carries.
 * @param args - the model's arguments, already schema-checked.
 * @returns the verb, positionals, and options for one console call.
 */
export function invocationFor(args: Args): {
  verb: Verb
  positional: readonly string[]
  options: Readonly<Record<string, string | number | boolean | readonly string[] | undefined>>
} {
  if (RUN_SCOPED.includes(args.action) && args.run === undefined) {
    throw new Error(`action "${args.action}" needs \`run\`: the run id or directory to read`)
  }
  // `approve` answers a gate on the person's behalf, and these verbs run headless:
  // the call cannot pause to ask them, so an answer passed here is the only answer
  // the run will ever see. That is right for a run nobody is sitting at and wrong
  // for one they are: the same mode can be driven node by node with `wf_open`, and
  // the runtime puts its `user` gates to the person itself. Requiring the caller to
  // say which case this is makes pre-answering a person's gate impossible by
  // accident, which is the mistake this tool used to invite (#92).
  if ((args.approve?.length ?? 0) > 0 && args.headless !== true) {
    throw new Error(
      '`approve` answers a gate for a run nobody can ask, so it needs `headless: true`. '
      + 'When a person is here, do not pre-answer their gate: open the mode with '
      + '`wf_open <package>/<name>` and drive it — the runtime puts a `user` gate to '
      + 'them. `modes` marks which gates those are.',
    )
  }
  const run: Record<string, string | number | boolean | readonly string[] | undefined> = {
    run: args.run,
    'runs-root': args.runsRoot,
  }
  switch (args.action) {
    case 'start': {
      if (args.mode === undefined) {
        throw new Error('action "start" needs `mode`: the <package>/<name> to run')
      }
      const { positional = [], options = {} } = startInvocation({
        mode: args.mode,
        runId: args.runId,
        runsRoot: args.runsRoot,
        settings: settingsOf(args),
        knobs: args.knobs,
      })
      return { verb: args.action, positional, options }
    }
    case 'run': {
      if (args.mode === undefined) {
        throw new Error('action "run" needs `mode`: the <package>/<name> to run once')
      }
      return {
        verb: args.action,
        positional: [args.mode],
        options: {
          intent: args.intent,
          verdict: args.approve,
          provider: args.provider,
          model: args.model,
        },
      }
    }
    case 'candidate': {
      if (args.candidate === undefined) {
        throw new Error('action "candidate" needs `candidate`: the candidate id to read')
      }
      return { verb: args.action, positional: [args.candidate], options: run }
    }
    case 'status':
      return { verb: args.action, positional: [], options: { ...run, after: args.after } }
    case 'artifacts':
      return { verb: args.action, positional: [], options: { ...run, contents: args.contents } }
    case 'leaderboard':
      return { verb: args.action, positional: [], options: { ...run, limit: args.limit } }
    case 'steer':
      return {
        verb: args.action,
        positional: [],
        options: { ...run, seed: args.intent, note: args.note, kind: args.kind },
      }
    case 'best':
      return { verb: args.action, positional: [], options: { ...run, state: args.state } }
    case 'adopt': {
      if (args.as === undefined) {
        throw new Error('action "adopt" needs `as`: the new package name')
      }
      return { verb: args.action, positional: [], options: { ...run, as: args.as } }
    }
    case 'registry': {
      const { positional = [], options = {} } = registryInvocation(
        args.packages ?? 'list',
        args.target ?? '',
        args.args,
      )
      return { verb: args.action, positional, options }
    }
    case 'create': {
      if (args.name === undefined || args.brief === undefined) {
        throw new Error('action "create" needs `name` and `brief`')
      }
      return {
        verb: args.action,
        positional: [],
        options: {
          name: args.name, brief: args.brief, extends: args.extends, attempts: args.attempts,
          'data-node': args.dataNode, task: args.task, single: args.singleShot,
          verdict: args.approve,
          input: args.inputs, provider: args.provider, model: args.model, resume: args.resume,
          sample: args.sample,
        },
      }
    }
    case 'edit': {
      if (args.mode === undefined || args.name === undefined || args.brief === undefined) {
        throw new Error('action "edit" needs `mode`, `name` and `brief`')
      }
      return {
        verb: args.action,
        positional: [],
        options: {
          mode: args.mode, name: args.name, brief: args.brief,
          'allow-regression': args.allowRegression, attempts: args.attempts,
          verdict: args.approve, provider: args.provider, model: args.model, resume: args.resume,
          sample: args.sample,
        },
      }
    }
    case 'design': {
      if (args.name === undefined || args.brief === undefined || args.check === undefined) {
        throw new Error('action "design" needs `name`, `brief` and `check`')
      }
      return {
        verb: args.action,
        positional: [],
        options: {
          name: args.name, brief: args.brief, check: args.check, attempts: args.attempts,
          verdict: args.approve, provider: args.provider, model: args.model, resume: args.resume,
          sample: args.sample,
        },
      }
    }
    case 'runtime':
    case 'modes':
    case 'strategies':
    case 'runs':
    case 'stop':
      return { verb: args.action, positional: [], options: run }
  }
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
    case 'modes': {
      const modes = Array.isArray(value) ? value : []
      lines.push(`${modes.length} mode(s):`)
      for (const mode of modes) {
        if (!isRecord(mode)) continue
        lines.push(`- ${scalar(mode.id)} — ${modeSummary(mode)}`)
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
    text: `Use the ${resolved.toolName} tool to run and read a refactory outer-loop run. A run starts in its own process and continues after the call returns, so start it, then watch it with the "status" action (pass an "after" offset to read only what is new) instead of blocking on it. Before asking the user to repeat model/home setup, call action "runtime": it reads the owning session route and nested configured-readiness without changing settings or testing authentication. Keep inherited values unless the user requests a change. Follow explicit Task/fixture constraints without asking for the same confirmation again.`,
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
      limit: { type: 'integer', description: 'For action "leaderboard": how many rows to return.' },
      runsRoot: { type: 'string', description: 'Directory holding the runs (default: <repoRoot>/.factory/loop).' },
      knobs: {
        type: 'array',
        items: { type: 'string' },
        description: 'For action "start": knob values the search starts from, as "NAME=VALUE" (repeatable). A mode whose inputs are frozen knobs (an input path, a command) cannot be searched without one — every candidate would run the defaults and score the same thing.',
      },
      settings: {
        type: 'object',
        additionalProperties: true,
        description: 'Run settings for action "start": epochs, batch_size, optimizer, strategy, evaluator, seed, elite, attempts, preset, max_evaluations. The optimizer, strategy and evaluator must be names from the "strategies" action, which lists each with what it is. Passed through as console flags.',
      },
      intent: { type: 'string', description: 'For actions "steer" and "run": what the run is for, in the user\'s words. For "run" it seeds the intent ledger every agent node reads.' },
      provider: { type: 'string', description: 'For actions "run", "create", "edit" and "design": the provider route the model runs on (default: the settings default).' },
      model: { type: 'string', description: 'For actions "run", "create", "edit" and "design": the model id to run on (default: the settings default).' },
      note: { type: 'string', description: 'For action "steer": something said while the run goes, appended to the ledger.' },
      kind: { type: 'string', enum: ['info', 'correction', 'constraint'], description: 'For action "steer": what the note is. A constraint must be respected, not merely known.' },
      state: { type: 'boolean', description: 'For action "best": include the winner\'s full state dict.' },
      as: { type: 'string', description: 'For action "adopt": the new package name. Required by "adopt".' },
      packages: { type: 'string', enum: ['list', 'show', 'verify', 'smoke', 'tools', 'add', 'import', 'remove', 'enable', 'disable'], description: 'For action "registry": which package verb to run (default "list").' },
      target: { type: 'string', description: 'For action "registry": the package name, <package>/<mode>, or directory the verb acts on.' },
      args: { type: 'string', description: 'For action "registry": extra flags for that verb, as a shell-quoted string (e.g. "--as renamed --force").' },
      name: { type: 'string', description: 'For "create"/"edit"/"design": the package or artifact name to write.' },
      brief: { type: 'string', description: 'For "create"/"edit"/"design": what to build or change, in the asker\'s words.' },
      extends: { type: 'string', description: 'For action "create": a <package>/<mode> to use as a reference.' },
      check: { type: 'string', description: 'For action "design": the command that decides whether the artifact works. Required by "design".' },
      allowRegression: { type: 'boolean', description: 'For action "edit": allow a revision that scores worse than the mode it came from.' },
      approve: {
        type: 'array',
        items: { type: 'string' },
        description: 'For "create"/"edit"/"design": answers to the gates the run pauses at, as "GATE=VALUE" (e.g. "gate_strategy=proceed"), for a run nobody can ask — it requires `headless: true`. These verbs run headless, so a gate the run stops at gets no answer unless one is passed; but when a person is here, their gate is theirs: open the mode with `wf_open <package>/<name>` instead and the runtime puts it to them. `modes` marks which gates belong to a person with `(user)`.',
      },
      headless: {
        type: 'boolean',
        description: 'For "create"/"edit"/"design": that nobody is at the keyboard for this run, which is the only case in which `approve` may answer a gate. Omit it for a run a person can answer, and drive the mode with `wf_open` so they are asked.',
      },
      attempts: { type: 'integer', description: 'For "create"/"edit"/"design": how many times the model may retry before the run fails.' },
      dataNode: { type: 'boolean', description: 'For action "create": require that the produced mode declares a DataNode that runs its subgraph once per item. A requirement on top of `task` rather than a directive of its own: `task` already implies the data node, and the run is refused if the produced mode does not declare one.' },
      task: { type: 'string', description: 'For action "create": the class of work, as a Task class (module:Class or a registered name). The produced package binds a data node to it, runs its subgraph once per item, and is judged per item; the run is refused if the produced mode is not bound to it. Naming neither `task` nor `singleShot` is refused, because a harness built for one hardcoded example is the special case.' },
      singleShot: { type: 'boolean', description: 'For action "create": build a harness for one run of one thing, with no Task binding. One of `task` or `singleShot` is required.' },
      sample: {
        type: 'array',
        items: { type: 'string' },
        description: 'For "create"/"edit"/"design": knob values the validation run should use, as "NAME=VALUE" (repeatable). Give the input the brief is about so the smoke exercises that path instead of the built-in fixture; the run is also refused when the artifact it produces is not the format it claims (a .whl without METADATA and WHEEL beside its RECORD, an unreadable .zip).',
      },
      resume: { type: 'string', description: 'For "create"/"edit"/"design": a run directory to continue instead of starting a new run. Every node that already finished is kept, and the run continues at the one that failed — use it after a transport failure or a killed call rather than paying for the finished work again.' },
      inputs: {
        type: 'array',
        items: { type: 'string' },
        description: 'For action "create": the inputs the mode must accept, each named the way a knob or a file would be (e.g. "package", "source_tree"). The run is refused when the produced mode has no way to receive one — no knob named after it and no read under that name — so state them when the brief is about operating on something the caller supplies.',
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
