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
  modes: 'List the modes a run can be started from, with their parameters.',
  strategies: 'List the named outer loops, optimizers and evaluators, each with what it is. Choose settings.optimizer/strategy/evaluator from these names.',
  runs: 'List runs under the run root, newest first.',
  start: 'Optimize a mode: start an outer-loop run in its own process and return the run id to watch it with.',
  run: 'Use a mode once: run it on DSH and report the result and where its artifacts are. This is the CLI\'s "run", not the optimizer.',
  status: 'Where a run is, and the events since an offset.',
  artifacts: 'What a run left on disk, optionally with contents.',
  leaderboard: 'The run\'s candidates by score, each with its diff against its parent.',
  candidate: 'One candidate, with what it changed against the parent it came from.',
  steer: 'Read a run\'s intent ledger, or add what the person said to it.',
  best: 'The winner of a run, with the state that scored it.',
  adopt: 'Write a run\'s winner as a new package, leaving the run and its source untouched.',
  registry: 'List packages of modes, or verify, smoke, add, import, remove, enable or disable one.',
  create: 'Create a package of modes from a brief (model-driven); it runs for many minutes and needs the strategy gate answered through "approve".',
  edit: 'Revise an existing mode from a brief (model-driven), refusing a regression; it runs for many minutes and needs the strategy gate answered through "approve".',
  design: 'Design, emit and check a software artifact from a brief (model-driven); it runs for many minutes and needs the strategy gate answered through "approve".',
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
  'Which action to use: "modes" answers with each mode\'s id and the action that runs it (a scored mode is run by "start"; an authoring mode by its own verb, "create", "edit" or "design"), so read that field rather than trying "start" and reading the refusal. "strategies" names every outer loop, optimizer and evaluator before you choose settings. Start with "modes", then "start" for a scored mode or the named authoring verb, then "status" until it finishes, then "leaderboard", "candidate", "artifacts" and "best".',
  '',
  'The authoring verbs behave differently from "start": they run for many minutes inside the call, they cannot pause to ask the user anything, and they stop at the strategy gate unless the answer is passed as "approve". So ask the user for that approval first, pass it with the call, and watch the run meanwhile with action "status" and the run directory (an authoring run reports its own node-by-node progress there).',
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
  inputs?: string[]
  provider?: string
  model?: string
  attempts?: number
  dataNode?: boolean
  task?: string
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
          'data-node': args.dataNode, task: args.task, verdict: args.approve,
          input: args.inputs, provider: args.provider, model: args.model,
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
          verdict: args.approve, provider: args.provider, model: args.model,
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
          verdict: args.approve, provider: args.provider, model: args.model,
        },
      }
    }
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
      limit: { type: 'integer', description: 'For action "leaderboard": how many rows to return.' },
      runsRoot: { type: 'string', description: 'Directory holding the runs (default: <repoRoot>/.factory/loop).' },
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
        description: 'For "create"/"edit"/"design": a person\'s answers to the gates the run pauses at, as "GATE=VALUE" (e.g. "gate_strategy=proceed"). A headless authoring run refuses a gate it was not told the answer to, so ask the user first — this call cannot pause to ask them itself.',
      },
      attempts: { type: 'integer', description: 'For "create"/"edit"/"design": how many times the model may retry before the run fails.' },
      dataNode: { type: 'boolean', description: 'For action "create": build the mode as a DataNode that runs its subgraph once per item (a dataset mode). The run is refused if the produced mode does not declare one.' },
      task: { type: 'string', description: 'For action "create": a Task class (module:Class or a registered name) the produced mode must satisfy — it supplies the per-item prompts and judges each pass. The run is refused if the produced mode is not bound to it.' },
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
      const invocation = invocationFor(typed)
      const result: ConsoleResult = await console.run(invocation, exec.signal)
      if (!result.ok) {
        // A refusal carries the console's own reason. Surface it as a tool error
        // so the model corrects the call rather than reading a silent success.
        throw new Error(`${resolved.toolName} ${typed.action} failed: ${result.error}`)
      }
      return { action: typed.action, data: result.value as JsonValue }
    },
    presentCall: args => presentCall(args as unknown as Args),
    presentResult: (args, result) => presentResult(args as unknown as Args, result),
  }))
}

export { Console } from './console.ts'
export type { ConsoleInvocation, ConsoleResult, ConsoleSettings } from './console.ts'
