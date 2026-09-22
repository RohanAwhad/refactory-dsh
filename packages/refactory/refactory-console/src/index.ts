/**
 * The Web picker's Host service: the console, over Typert Remote.
 *
 * The loop is Python and stays there; the tool and the Web view are its two
 * DSH-side readers. This service is the second one: it holds the same `Console`
 * the tool holds, mounts the same deployment schema, and runs the same
 * `python -m refactory_dsh.console` invocations, so a choice a person makes in
 * the panel is the choice the model makes through the tool.
 *
 * What this service adds over a bare call is the picker's two rules:
 *
 * 1. Every choice is described. Modes, strategies and packages come from the
 *    console's own catalogues, so the panel never asks a person to type a name
 *    it could have offered.
 * 2. A refusal says what to do instead. `start` reads the catalogue's `action`
 *    field — the console's, not a second derivation — and names the authoring
 *    verb for a mode the loop cannot search, before shelling out.
 *
 * Failures are one `RemoteError` per reason: `gateway/bad-request` for an
 * address the picker must supply, `refactory/unknown-mode`,
 * `refactory/authoring-mode` and `refactory/package-disabled` for the three
 * described-choice refusals, and `refactory/console-failed` for a console
 * refusal, carrying the console's own message and the argv to reproduce it.
 * @module @deepseek-ai/dsh-refactory-console
 */

import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import type {} from '@deepseek-ai/dsh-subprocess'
import {
  CONSOLE_DEFAULTS, Console, isRecord, registryInvocation, resolveConsoleSettings, startInvocation,
} from '@deepseek-ai/dsh-tool-refactory/console'
import type { ConsoleConfig as ConsoleSettings, ConsoleInvocation } from '@deepseek-ai/dsh-tool-refactory/console'
import { Remote, RemoteError, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import type {
  RefactoryModeChoice,
  RefactoryPackageChoice,
  RefactoryPackageMode,
  RefactoryRunCounters,
  RefactoryRunEvent,
  RefactoryRunStatus,
  RefactoryRunSummary,
  RefactoryStartReceipt,
  RefactoryStartRequest,
  RefactoryStopReceipt,
  RefactoryStrategies,
  RefactoryStrategyChoice,
} from './types.ts'

export type * from './types.ts'

declare module '@deepseek-ai/cordis' {
  interface Context {
    /** Host owner of the `refactoryConsole` Remote namespace. */
    refactoryConsole: RefactoryConsole
  }
}

/**
 * Deployment settings: where the checkout is and how long a call may take.
 *
 * The same four settings the model-facing tool mounts, from one schema, so a
 * deployment that points the tool at a checkout points this service there too.
 */
export type Config = ConsoleSettings

/** A JSON object, as a console answer's members decode. */
type Json = Record<string, unknown>

/**
 * Require one console answer member to be a list of JSON objects.
 *
 * The console writes JSON on stdout, which is a process boundary, so its shape
 * is checked here rather than trusted: a console whose answer changed shape must
 * fail loudly at this seam instead of rendering an empty panel.
 * @param value - the console's decoded answer.
 * @param what - the member's name, for the message.
 * @returns the member as a list of objects.
 */
function objectList(value: unknown, what: string): Json[] {
  if (!Array.isArray(value)) throw new Error(`the console's ${what} is not a list`)
  for (const item of value) {
    if (!isRecord(item)) throw new Error(`the console's ${what} holds a member that is not an object`)
  }
  return value as Json[]
}

/**
 * Require one console answer member to be a JSON object.
 * @param value - the console's decoded answer.
 * @param what - the member's name, for the message.
 * @returns the member as an object.
 */
function objectOf(value: unknown, what: string): Json {
  if (!isRecord(value)) throw new Error(`the console's ${what} is not an object`)
  return value
}

/** A string member of a console answer, or a stated absence. */
function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

/** A number member of a console answer, or a stated absence. */
function count(value: unknown): number {
  return typeof value === 'number' ? value : 0
}

/** Host Remote service over the refactory console. */
export class RefactoryConsole extends TypertRemoteService {
  static inject = ['subprocess', 'typert']

  static Config: z<Config> = z.object({
    repoRoot: z.string().default(process.cwd()),
    pythonBin: z.string().default(''),
    timeoutMs: z.natural().min(1).max(60 * 60 * 1000).default(CONSOLE_DEFAULTS.timeoutMs),
    collectBytes: z.natural().min(1024).default(CONSOLE_DEFAULTS.collectBytes),
  })

  private readonly console: Console

  /**
   * @param ctx - Host context carrying the subprocess capability.
   * @param config - where the checkout is and how long one console call may take.
   */
  constructor(ctx: Context, config: Config) {
    super(ctx, 'refactoryConsole')
    this.console = new Console(ctx.subprocess, resolveConsoleSettings(config as Required<Config>))
  }

  /**
   * The modes a run can be started from, each with the verb that runs it.
   * @param signal - caller cancellation.
   * @returns the console's catalogue, as the choices it describes.
   */
  @Remote
  async modes(signal: AbortSignal): Promise<readonly RefactoryModeChoice[]> {
    const answer = await this.call({ verb: 'modes' }, signal)
    return objectList(answer, 'mode catalogue').map(entry => ({
      id: text(entry.id),
      package: text(entry.package),
      mode: text(entry.mode),
      description: text(entry.description),
      startable: entry.startable === true,
      enabled: entry.enabled === true,
      scored: entry.scored === true,
      action: text(entry.action),
      ...entry.trust === undefined ? {} : { trust: text(entry.trust) },
      ...entry.reason === undefined ? {} : { reason: text(entry.reason) },
    }))
  }

  /**
   * The named outer loops, optimizers and evaluators a run's settings choose between.
   * @param signal - caller cancellation.
   * @returns the three registries, each a list of named choices.
   */
  @Remote
  async strategies(signal: AbortSignal): Promise<RefactoryStrategies> {
    const answer = objectOf(await this.call({ verb: 'strategies' }, signal), 'strategy answer')
    return {
      outer_loops: choices(answer.outer_loops, 'outer loops'),
      optimizers: choices(answer.optimizers, 'optimizers'),
      evaluators: choices(answer.evaluators, 'evaluators'),
    }
  }

  /**
   * Every run under the run root, newest first.
   * @param signal - caller cancellation.
   * @returns the runs, each with where it got to.
   */
  @Remote
  async runs(signal: AbortSignal): Promise<readonly RefactoryRunSummary[]> {
    const answer = await this.call({ verb: 'runs' }, signal)
    return objectList(answer, 'run list').map(entry => ({
      run_id: text(entry.run_id),
      root: text(entry.root),
      mode: typeof entry.mode === 'string' ? entry.mode : null,
      best_score: typeof entry.best_score === 'number' ? entry.best_score : null,
      finished: entry.finished === true,
    }))
  }

  /**
   * The packages the registry knows, with the modes each declares.
   * @param signal - caller cancellation.
   * @returns the packages as described choices.
   */
  @Remote
  async packages(signal: AbortSignal): Promise<readonly RefactoryPackageChoice[]> {
    return this.catalogue(signal)
  }

  /**
   * Start a run from one described mode.
   *
   * The mode must be one the catalogue offers and one whose `action` is `start`;
   * an authoring mode is refused by name, with the verb that runs it, because a
   * person who picked it needs the action rather than a failed process.
   * @param request - the mode, run addressing, and settings.
   * @param signal - caller cancellation.
   * @returns what the console recorded about the start.
   */
  @Remote
  async start(request: RefactoryStartRequest, signal: AbortSignal): Promise<RefactoryStartReceipt> {
    const mode = request.mode.trim()
    if (mode.length === 0) {
      throw new RemoteError('gateway/bad-request', 'mode is required: the <package>/<name> to run', {})
    }
    const choices = await this.modes(signal)
    const choice = choices.find(entry => entry.id === mode)
    if (choice === undefined) {
      throw new RemoteError(
        'refactory/unknown-mode',
        `"${mode}" is not in the mode catalogue; ask for the modes and choose one of them`,
        { mode },
      )
    }
    if (choice.action !== 'start') {
      throw new RemoteError(
        'refactory/authoring-mode',
        `"${mode}" is an authoring mode, not a run to search; use the "${choice.action}" action`,
        { mode, action: choice.action },
      )
    }
    if (!choice.enabled) {
      throw new RemoteError(
        'refactory/package-disabled',
        `"${choice.package}" is disabled, so "${mode}" cannot run; enable the package first`,
        { mode, package: choice.package },
      )
    }
    const invocation = startInvocation({
      mode,
      runId: request.runId,
      runsRoot: request.runsRoot,
      settings: request.settings,
    })
    return receipt(await this.call(invocation, signal))
  }

  /**
   * Where one run is, and what is new since the reader last looked.
   * @param run - the run id or absolute run directory.
   * @param after - how many progress lines the caller has already read.
   * @param signal - caller cancellation.
   * @returns the run's state, the new events, and the offset for the next look.
   */
  @Remote
  async status(run: string, after: number, signal: AbortSignal): Promise<RefactoryRunStatus> {
    const addressed = this.runAddress(run)
    const answer = objectOf(
      await this.call({ verb: 'status', options: { run: addressed, after } }, signal),
      'run status',
    )
    return {
      run_id: text(answer.run_id),
      root: text(answer.root),
      mode: typeof answer.mode === 'string' ? answer.mode : null,
      state: text(answer.state),
      events: events(answer.events),
      cursor: count(answer.cursor),
      counters: counters(answer.counters),
      best_score: typeof answer.best_score === 'number' ? answer.best_score : null,
      finished: answer.finished === true,
      running: answer.running === true,
    }
  }

  /**
   * Ask one run to stop.
   * @param run - the run id or absolute run directory.
   * @param signal - caller cancellation.
   * @returns whether the run's process is gone.
   */
  @Remote
  async stop(run: string, signal: AbortSignal): Promise<RefactoryStopReceipt> {
    const addressed = this.runAddress(run)
    const answer = objectOf(
      await this.call({ verb: 'stop', options: { run: addressed } }, signal),
      'stop answer',
    )
    return { stopped: answer.stopped === true }
  }

  /**
   * Enable or disable one package, and answer with the catalogue as it now stands.
   * @param name - the package name, as the catalogue lists it.
   * @param enabled - the state to set.
   * @param signal - caller cancellation.
   * @returns the packages as described choices, after the change.
   */
  @Remote
  async setPackageEnabled(
    name: string,
    enabled: boolean,
    signal: AbortSignal,
  ): Promise<readonly RefactoryPackageChoice[]> {
    const target = this.packageName(name)
    await this.registry(enabled ? 'enable' : 'disable', target, '', signal)
    return this.catalogue(signal)
  }

  /**
   * Remove one workspace package, and answer with the catalogue as it now stands.
   * @param name - the package name, as the catalogue lists it.
   * @param signal - caller cancellation.
   * @returns the packages as described choices, after the change.
   */
  @Remote
  async removePackage(name: string, signal: AbortSignal): Promise<readonly RefactoryPackageChoice[]> {
    const target = this.packageName(name)
    await this.registry('remove', target, '--force', signal)
    return this.catalogue(signal)
  }

  /**
   * Bring a package in from a directory or git URL, and answer with the catalogue
   * as it now stands.
   * @param source - the directory or git URL to import from.
   * @param name - the name to import it as; empty keeps the package's own name.
   * @param signal - caller cancellation.
   * @returns the packages as described choices, after the import.
   */
  @Remote
  async importPackage(
    source: string,
    name: string,
    signal: AbortSignal,
  ): Promise<readonly RefactoryPackageChoice[]> {
    const from = source.trim()
    if (from.length === 0) {
      throw new RemoteError('gateway/bad-request', 'source is required: a directory or git URL to import from', {})
    }
    const as = name.trim()
    await this.registry('import', from, as.length === 0 ? '' : `--as ${JSON.stringify(as)}`, signal)
    return this.catalogue(signal)
  }

  /**
   * Run one registry verb through the console.
   * @param action - the registry verb.
   * @param target - the package or source it acts on.
   * @param args - extra registry flags, as a shell-quoted string.
   * @param signal - caller cancellation.
   * @returns the forwarded entry point's answer.
   */
  private async registry(action: string, target: string, args: string, signal: AbortSignal): Promise<Json> {
    const invocation = registryInvocation(action, target, args)
    return objectOf(await this.call(invocation, signal), 'registry answer')
  }

  /**
   * The registry's catalogue, as described choices.
   *
   * The registry lists modes grouped by package and reports the package's
   * enabled state and location on each mode, so a package that declares no modes
   * reports no state: its `enabled` is `null` rather than a guess.
   * @param signal - caller cancellation.
   * @returns the packages, each with the modes it declares.
   */
  private async catalogue(signal: AbortSignal): Promise<readonly RefactoryPackageChoice[]> {
    const answer = await this.registry('list', '', '', signal)
    if (answer.ok !== true) {
      const failure = objectOf(answer.result, 'registry failure')
      throw new RemoteError(
        'refactory/console-failed',
        text(failure.error) || text(failure.output) || 'the registry could not list packages',
        {},
      )
    }
    const listed = objectOf(answer.result, 'registry catalogue')
    return Object.entries(listed)
      .map(([name, modes]): RefactoryPackageChoice => {
        const declared = objectList(modes, `catalogue for "${name}"`)
        const first = declared[0]
        return {
          name,
          enabled: first === undefined ? null : first.package_enabled === true,
          where: text(first?.where),
          modes: declared.map((entry): RefactoryPackageMode => ({
            name: text(entry.name),
            description: text(entry.description),
            trust: text(entry.trust),
          })),
        }
      })
      .sort((left, right) => left.name.localeCompare(right.name))
  }

  /**
   * One console call, with its refusal turned into a Remote error.
   * @param invocation - the verb, positionals, and options.
   * @param signal - caller cancellation.
   * @returns the console's decoded JSON.
   */
  private async call(invocation: ConsoleInvocation, signal: AbortSignal): Promise<unknown> {
    if (signal.aborted) throw new RemoteError('gateway/cancelled', 'the refactory call was cancelled', {})
    const result = await this.console.run(invocation, signal)
    if (!result.ok) {
      throw new RemoteError('refactory/console-failed', result.error, { argv: result.argv })
    }
    return result.value
  }

  /**
   * The run address one run-scoped verb needs.
   * @param run - the run id or absolute run directory.
   * @returns the address to pass to the console.
   */
  private runAddress(run: string): string {
    const addressed = run.trim()
    if (addressed.length === 0) {
      throw new RemoteError('gateway/bad-request', 'run is required: the run id or its directory', {})
    }
    return addressed
  }

  /**
   * The package name one registry verb needs.
   * @param name - the package name, as the catalogue lists it.
   * @returns the name to pass to the console.
   */
  private packageName(name: string): string {
    const named = name.trim()
    if (named.length === 0) {
      throw new RemoteError('gateway/bad-request', 'name is required: the package to act on', {})
    }
    return named
  }
}

/** The strategy choices one level-two registry answered with. */
function choices(value: unknown, what: string): readonly RefactoryStrategyChoice[] {
  return objectList(value, what).map(entry => ({
    name: text(entry.name),
    description: text(entry.description),
  }))
}

/** The progress events of one status answer. */
function events(value: unknown): readonly RefactoryRunEvent[] {
  return objectList(value, 'run events').map(entry => ({
    ...typeof entry.epoch === 'number' ? { epoch: entry.epoch } : {},
    ...typeof entry.generation === 'number' ? { generation: entry.generation } : {},
    ...typeof entry.evaluations === 'number' ? { evaluations: entry.evaluations } : {},
    ...typeof entry.score === 'number' ? { score: entry.score } : {},
  }))
}

/** The counters of one status answer. */
function counters(value: unknown): RefactoryRunCounters {
  const entry = objectOf(value, 'run counters')
  return {
    epoch: count(entry.epoch),
    generation: count(entry.generation),
    evaluations: count(entry.evaluations),
    steps: count(entry.steps),
  }
}

/** What the console recorded about a start. */
function receipt(value: unknown): RefactoryStartReceipt {
  const entry = objectOf(value, 'start answer')
  const config = isRecord(entry.config) ? entry.config : {}
  return {
    run_id: text(entry.run_id),
    mode: text(entry.mode),
    root: text(entry.root),
    started: count(entry.started),
    config: Object.fromEntries(Object.entries(config).map(([key, setting]) => [key, text(setting)])),
    log: text(entry.log),
    pid: count(entry.pid),
  }
}

export default RefactoryConsole
