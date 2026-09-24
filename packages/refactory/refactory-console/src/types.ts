/**
 * What a picker chooses between, as `refactory_dsh.console` describes it.
 *
 * Every field here is the console's own: the catalogue's `action` field decides
 * which verb runs a mode, `enabled` is the registry's answer, and the strategy
 * names are the registries'. Nothing in this file re-derives a fact the console
 * already reports, so a picker that renders these choices and a model that reads
 * the same JSON cannot disagree about what is on offer.
 *
 * The console's answer carries more than the picker reads — a mode's nodes and
 * ports stay on the wire untouched — so these are the reported choices, not a
 * second answer to the console's question.
 * @module @deepseek-ai/dsh-refactory-console/types
 */

/** The knob kinds `refactory_dsh.nn.knob` declares. */
export type RefactoryKnobKind = 'int' | 'float' | 'bool' | 'str' | 'enum'

/** One declared knob of a mode, as the console's catalogue describes it. */
export interface RefactoryKnob {
  /** The value the mode runs with until one is chosen for it, as text. */
  readonly value: string
  /** The value kind the knob accepts. */
  readonly kind: RefactoryKnobKind
  /**
   * The finite set the knob accepts, as text: an `enum`'s members, or the values
   * a `str` knob declares. Empty means the set is open (a `str` knob) or bound by
   * `range` instead (a number).
   */
  readonly values: readonly string[]
  /** The numeric bounds `[low, high]`, either of which may be absent. */
  readonly range: readonly [number | null, number | null]
  /**
   * Whether the search may not move it. A person running the mode may still say
   * what it is, which is how an input-taking mode is used at all.
   */
  readonly frozen: boolean
  /** What the knob is for, from its own declaration. */
  readonly description: string
}

/** One mode the console's catalogue offers. */
export interface RefactoryModeChoice {
  /** `<package>/<mode>`, the identifier every console verb accepts. */
  readonly id: string
  /** Package the mode is declared in. */
  readonly package: string
  /** The mode's own name inside its package. */
  readonly mode: string
  /** The mode's one-line description. */
  readonly description: string
  /** Whether a run can be started from it right now. */
  readonly startable: boolean
  /** Whether its package is enabled. */
  readonly enabled: boolean
  /** Whether the mode declares a score, so the loop has something to search. */
  readonly scored: boolean
  /** The verb that runs this mode: `start`, or the authoring verb `create`, `edit` or `design`. */
  readonly action: string
  /** Every knob the mode declares, by name, as its own declaration describes it. */
  readonly parameters: Readonly<Record<string, RefactoryKnob>>
  /** The package's trust level, when the catalogue resolved the mode. */
  readonly trust?: string
  /** Why the mode could not be resolved, for a mode whose package fails to import. */
  readonly reason?: string
}

/** One named choice in a level-two registry. */
export interface RefactoryStrategyChoice {
  /** The name a run setting accepts. */
  readonly name: string
  /** What the implementation says it is, from its own first docstring line. */
  readonly description: string
}

/** The three level-two seams, each a list of described choices. */
export interface RefactoryStrategies {
  /** The named outer loops. */
  readonly outer_loops: readonly RefactoryStrategyChoice[]
  /** The named optimizers. */
  readonly optimizers: readonly RefactoryStrategyChoice[]
  /** The named evaluators. */
  readonly evaluators: readonly RefactoryStrategyChoice[]
}

/** One run under the run root, as the console lists it. */
export interface RefactoryRunSummary {
  /** The run directory's name. */
  readonly run_id: string
  /** Absolute path of the run directory. */
  readonly root: string
  /** The mode the run was started from; absent for a run with no recorded mode. */
  readonly mode?: string | null
  /** Best score so far, or absent when nothing scored yet. */
  readonly best_score?: number | null
  /** Whether the run wrote its report. */
  readonly finished: boolean
}

/** One progress line the loop recorded, as much of it as a reader reads. */
export interface RefactoryRunEvent {
  /** The epoch the step belongs to. */
  readonly epoch?: number
  /** The generation within the epoch. */
  readonly generation?: number
  /** Evaluations completed when the step was written. */
  readonly evaluations?: number
  /** The score the step recorded. */
  readonly score?: number
}

/** The tail counters a run's progress reports. */
export interface RefactoryRunCounters {
  /** Latest epoch in the events read. */
  readonly epoch: number
  /** Latest generation in the events read. */
  readonly generation: number
  /** Latest evaluation count in the events read. */
  readonly evaluations: number
  /** How many events were read in this frame. */
  readonly steps: number
}

/** Where one run is, and what is new since the reader last looked. */
export interface RefactoryRunStatus {
  /** The run directory's name. */
  readonly run_id: string
  /** Absolute path of the run directory. */
  readonly root: string
  /** The mode the run was started from. */
  readonly mode?: string | null
  /** One of `starting`, `running`, `finished`, `died`. */
  readonly state: string
  /** Progress lines after the requested offset. */
  readonly events: readonly RefactoryRunEvent[]
  /** Offset to pass as `after` on the next look. */
  readonly cursor: number
  /** Counters as of the last event in this frame. */
  readonly counters: RefactoryRunCounters
  /** Best score so far, or `null` when nothing scored yet. */
  readonly best_score: number | null
  /** Whether the run wrote its report. */
  readonly finished: boolean
  /** Whether the run's process is alive. */
  readonly running: boolean
}

/** What the console recorded when it started a run. */
export interface RefactoryStartReceipt {
  /** The run directory's name. */
  readonly run_id: string
  /** The mode the run was started from. */
  readonly mode: string
  /** Absolute path of the run directory. */
  readonly root: string
  /** Unix time the start was recorded. */
  readonly started: number
  /** The run settings, as the console recorded them. */
  readonly config: Readonly<Record<string, string>>
  /** The run's log file name inside its directory. */
  readonly log: string
  /** The run process's id. */
  readonly pid: number
}

/** What asking a run to stop produced. */
export interface RefactoryStopReceipt {
  /** Whether the run's process is gone. */
  readonly stopped: boolean
}

/** One mode a package declares, as the registry's catalogue lists it. */
export interface RefactoryPackageMode {
  /** The mode's name inside its package. */
  readonly name: string
  /** The mode's one-line description. */
  readonly description: string
  /** The package's trust level. */
  readonly trust: string
}

/** One package the registry knows, with the choices it declares. */
export interface RefactoryPackageChoice {
  /** The package name every registry verb accepts. */
  readonly name: string
  /**
   * Whether the package is enabled, or `null` when the registry reported no
   * modes for it: the state travels on each mode, so an empty package names none.
   */
  readonly enabled: boolean | null
  /** Where the package lives; empty when the registry cannot place it. */
  readonly where: string
  /** The modes the package declares. */
  readonly modes: readonly RefactoryPackageMode[]
}

/** What a graph spine last recorded about a run, as the console reports it. */
export interface RefactoryAuthoringProgress {
  /** The spine's own word: `done`, `ready`, `failed`, or `refused`. */
  readonly spine: string
  /** The nodes the spine left due, when it stopped with work outstanding. */
  readonly nodes_due: readonly string[]
  /** Why the spine refused or failed, as the spine wrote it. */
  readonly error: string
}

/** One mode used once: where it ran, how it ended, and what it recorded. */
export interface RefactorySingleRun {
  /** The run directory's name. */
  readonly run_id: string
  /** Absolute path of the run directory. */
  readonly root: string
  /** The mode the run was started from. */
  readonly mode: string | null
  /** One of `starting`, `running`, `finished`, `died`. */
  readonly state: string
  /** Whether the run wrote its report. */
  readonly finished: boolean
  /** Best score so far, or `null` when nothing scored yet. */
  readonly best_score: number | null
  /**
   * What the graph spine recorded, for a run the spine drove — a run that stopped
   * at a gate a person owns says so here — or `null` for a search run.
   */
  readonly authoring: RefactoryAuthoringProgress | null
  /** What the run wrote to `facts.json`, each value as text. */
  readonly facts: Readonly<Record<string, string>>
}

/** One run to start: the mode, where the run lives, and its settings. */
export interface RefactoryStartRequest {
  /** `<package>/<mode>`, as the catalogue lists it. */
  readonly mode: string
  /** Run id to claim; the console derives one when absent. */
  readonly runId?: string
  /** Directory holding the runs; the console default when absent. */
  readonly runsRoot?: string
  /** Run settings, passed through as the console's `--<name> <value>` flags. */
  readonly settings?: Readonly<Record<string, string | number | boolean>>
  /**
   * Knob values the search starts from, as `NAME=VALUE`, one `--knob` per element.
   * A mode whose inputs are frozen knobs (an input path, a command) needs one, or
   * every candidate runs the defaults and scores the same.
   */
  readonly knobs?: readonly string[]
}

/** One mode to use once: the mode, and the knob values it uses. */
export interface RefactorySingleRunRequest {
  /** `<package>/<mode>`, as the catalogue lists it. */
  readonly mode: string
  /** Knob values this run uses, as `NAME=VALUE`; the console's default when absent. */
  readonly knobs?: readonly string[]
}

declare module '@deepseek-ai/dsh-typert-protocol' {
  interface RemoteErrorDetailsMap {
    /** `start` was asked for a mode the catalogue does not offer. */
    'refactory/unknown-mode': { readonly mode: string }
    /** `start` or `run` was asked for a mode whose own action is an authoring verb. */
    'refactory/authoring-mode': { readonly mode: string; readonly action: string }
    /** `start` or `run` was asked for a mode of a package the registry reports disabled. */
    'refactory/package-disabled': { readonly mode: string; readonly package: string }
    /**
     * The console refused the call, or answered with a shape it does not own.
     * `argv` is absent for a refusal the registry forwarded inside its own answer.
     */
    'refactory/console-failed': { readonly argv?: readonly string[] }
  }
}
