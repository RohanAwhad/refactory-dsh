/**
 * The picker's asynchronous half: reading the described choices and the run's
 * status into the store, and dispatching the gestures that change the Host.
 *
 * The component never awaits anything. It calls `load` / `choose` / `setKnob` /
 * `setSetting` / `start` / `run` / `refresh` / `stop` and the package gestures, and
 * this face performs the Remote call and writes the outcome through the store's own
 * actions — the Slot-standard `inject` face, so the write set stays the store's and
 * the body only decides what to draw. `start` and `run` differ in one thing: `start`
 * dispatches the outer loop and then reads its frames, while `run` uses the mode
 * once and takes the single answer the console returns.
 *
 * One read has one settlement in force: asking again — the reload gesture, a
 * refresh while one is in flight — retires the read still outstanding for that
 * tab, whose settlement then writes nothing. Cleanup rides the owner's `signal`:
 * no request is made for a record that already ended, and when the record goes
 * away the bucket and this tab's bookkeeping are forgotten, so no later
 * settlement writes to it.
 *
 * A Remote call does not reject: the result carries the failure, which is stored
 * as the Host's code and message and turned into copy where the copy lives.
 */
import type { RemoteFailure, RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { ClientConnectionRpc } from '@deepseek-ai/dsh-client-connection/client'
import type { RefactoryConsoleRemote, RefactoryNoteKind, RefactoryPackageChoice } from '@deepseek-ai/dsh-refactory-console/types'
import type { BoundActions } from '@deepseek-ai/dsh-client-store'
import type { TabId } from '@deepseek-ai/dsh-client-ui-dockkit'
import type { SessionId } from '@deepseek-ai/dsh-session/types'
import type {
  RefactoryKnobSetting,
  RefactorySettingName,
  RefactorySettings,
  createRefactoryStore,
} from './store.ts'
import { searchBudget } from './store.ts'

/** The slice of the Client Remote face this package calls: the `refactoryConsole` namespace. */
export type RefactoryRemote = {
  readonly refactoryConsole: Pick<
    RefactoryConsoleRemote,
    'project' | 'modes' | 'strategies' | 'packages' | 'start' | 'run' | 'runStream' | 'status' | 'leaderboard'
      | 'runs' | 'graphControl' | 'watchStream' | 'steer' | 'stop' | 'setPackageEnabled' | 'removePackage' | 'importPackage'
      | 'profiles' | 'profileSave' | 'profileResolve' | 'comparison'
      | 'steer' | 'stop' | 'setPackageEnabled' | 'removePackage' | 'importPackage'
  >
}

/**
 * Bind the `refactoryConsole` namespace to the Connection's generic RPC.
 *
 * The Host service is discovered by the gateway from its `@Remote` markers, and
 * the gateway reads each argument by the method's declared parameter name. The
 * pinned Client Remote assembly is a prebuilt bundle carrying the harness's own
 * namespaces, so it mounts no `remote.refactoryConsole` service for this fork's
 * namespace; the same `/api` channel answers `refactoryConsole/<method>`, so this
 * sends the named arguments the SRC descriptor reads.
 * @param rpc - the Client Connection's generic unary RPC caller.
 * @param runStream - the mounted Gateway stream; absent retains the unary adapter contract.
 * @returns the namespace face the picker calls.
 */
export function refactoryConsoleOverConnection(
  rpc: ClientConnectionRpc,
  runStream?: RefactoryConsoleRemote['runStream'],
  watchStream?: RefactoryConsoleRemote['watchStream'],
): RefactoryRemote {
  const call = <T>(
    method: string,
    args: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<RemoteResult<T>> =>
    rpc.call('/api', `refactoryConsole/${method}`, { args }, signal) as Promise<RemoteResult<T>>
  return {
    refactoryConsole: {
      ...watchStream === undefined ? {} : { watchStream },
      runs: (signal) => call('runs', {}, signal),
      graphControl: (request, signal) => call('graphControl', { request }, signal),
      profiles: (signal) => call('profiles', {}, signal),
      profileSave: (name, spec, signal) => call('profileSave', { name, spec }, signal),
      profileResolve: (request, signal) => call('profileResolve', { request }, signal),
      comparison: (run, signal) => call('comparison', { run }, signal),
      project: (signal) => call('project', {}, signal),
      modes: (signal) => call('modes', {}, signal),
      strategies: (signal) => call('strategies', {}, signal),
      packages: (signal) => call('packages', {}, signal),
      start: (request, signal) => call('start', { request }, signal),
      run: (request, signal) => call('run', { request }, signal),
      runStream: runStream ?? (async function* (request, signal) {
        const result = await call<import('@deepseek-ai/dsh-refactory-console/types').RefactorySingleRun>('run', { request }, signal)
        if (!result.ok) throw new Error(result.error.message)
        yield { kind: 'result' as const, run: result.value }
      }),
      status: (run, after, signal) => call('status', { run, after }, signal),
      leaderboard: (run, signal) => call('leaderboard', { run }, signal),
      steer: (request, signal) => call('steer', { request }, signal),
      stop: (run, signal) => call('stop', { run }, signal),
      setPackageEnabled: (name, enabled, signal) => call('setPackageEnabled', { name, enabled }, signal),
      removePackage: (name, signal) => call('removePackage', { name }, signal),
      importPackage: (source, name, signal) => call('importPackage', { source, name }, signal),
    },
  }
}

/**
 * The knob values a gesture sends, as the console's repeatable `--knob NAME=VALUE`.
 * @param values - one value per declared knob, in declaration order.
 * @returns one `NAME=VALUE` argument per knob.
 */
function knobArguments(values: readonly RefactoryKnobSetting[]): readonly string[] {
  return values.map(({ name, value }) => `${name}=${value}`)
}

/**
 * One control act as the body asks for it: the verb, and the candidate or package
 * it names.
 *
 * The act travels as one value rather than as four arguments because the console
 * refuses a request that names nothing, and a shape that cannot express "keep,
 * with no candidate" is one the body cannot ask for by accident.
 */
export interface RefactoryControlAct {
  /** The verb the loop honors at its next generation boundary. */
  readonly control: 'pause' | 'resume' | 'stop' | 'keep' | 'reject' | 'adopt'
  /** The candidate `keep` and `reject` name, as the leaderboard lists it. */
  readonly target?: string
  /** With `keep` or `reject`, act on the candidate's descendants too. */
  readonly lineage?: boolean
  /** With `adopt`, the package name to write. */
  readonly as?: string
}

/** The picker's injected business face, as the body receives it. */
export interface RefactoryInjected {
  readonly listRuns?: (signal: AbortSignal) => Promise<readonly import('@deepseek-ai/dsh-refactory-console/types').RefactoryRunSummary[]>
  readonly graphControl?: (request: { run: string; action: string; node?: string; text?: string; revision?: string }, signal: AbortSignal) => Promise<Record<string, unknown>>

  /** Direct profile operations keep the saved recipe separate from the watched run. */
  readonly profileApi?: Pick<RefactoryConsoleRemote, 'profiles' | 'profileSave' | 'profileResolve' | 'comparison'>

  /**
   * Seed this tab's picker and read the described choices into it.
   * @param tabId - the tab being drawn.
   * @param signal - the tab record's lifetime.
   */
  readonly load: (tabId: TabId, signal: AbortSignal) => void
  /**
   * Read the described choices again, keeping what this tab has chosen.
   * @param tabId - the tab being drawn.
   * @param signal - the tab record's lifetime.
   */
  readonly reload: (tabId: TabId, signal: AbortSignal) => void
  /**
   * Choose one of the described modes, or nothing.
   * @param tabId - the tab being drawn.
   * @param id - the chosen catalogue id, or `null`.
   */
  readonly choosePackage: (tabId: TabId, name: string | null) => void
  readonly chooseIntent: (tabId: TabId, intent: 'run' | 'optimize') => void
  readonly choose: (tabId: TabId, id: string | null) => void
  /**
   * Change one of the chosen mode's declared knobs for this run.
   * @param tabId - the tab being drawn.
   * @param name - the knob's declared name.
   * @param value - the value to send, as text.
   */
  readonly setKnob: (tabId: TabId, name: string, value: string) => void
  /**
   * Choose one of the described level-two names.
   * @param tabId - the tab being drawn.
   * @param name - which seam the choice is for.
   * @param value - the chosen name, or the empty string for the default.
   */
  readonly setSetting: (tabId: TabId, name: RefactorySettingName, value: string) => void
  /**
   * Start a run from the chosen mode, its knobs and its settings, then read its
   * first frame.
   * @param tabId - the tab being drawn.
   * @param mode - the chosen mode's catalogue id.
   * @param knobs - the value of every declared knob, in declaration order.
   * @param settings - the chosen level-two names.
   * @param signal - the tab record's lifetime.
   */
  readonly start: (
    tabId: TabId,
    mode: string,
    knobs: readonly RefactoryKnobSetting[],
    settings: RefactorySettings,
    signal: AbortSignal,
  ) => void
  /**
   * Use the chosen mode once, with its knobs, and take the result it left.
   * @param tabId - the tab being drawn.
   * @param mode - the chosen mode's catalogue id.
   * @param knobs - the value of every declared knob, in declaration order.
   * @param signal - the tab record's lifetime.
   */
  readonly run: (
    tabId: TabId,
    mode: string,
    knobs: readonly RefactoryKnobSetting[],
    signal: AbortSignal,
    settings?: RefactorySettings,
  ) => void
  /**
   * Read one status frame for a run.
   * @param tabId - the tab being drawn.
   * @param run - the run id or directory.
   * @param after - how many progress lines this reader has already seen.
   * @param signal - the tab record's lifetime.
   */
  readonly refresh: (tabId: TabId, run: string, after: number, signal: AbortSignal) => void
  /** Attach an existing run by the exact directory the CEO or terminal reported. */
  readonly watch: (tabId: TabId, run: string, signal: AbortSignal) => void
  /**
   * Read the watched run's intent ledger and the gates it is stopped at.
   * @param tabId - the tab being drawn.
   * @param run - the run id or directory.
   * @param signal - the tab record's lifetime.
   */
  readonly readSteering: (tabId: TabId, run: string, signal: AbortSignal) => void
  /**
   * Read the watched run's candidates, with what a person has done to each.
   * @param tabId - the tab being drawn.
   * @param run - the run id or directory.
   * @param signal - the tab record's lifetime.
   */
  readonly readCandidates: (tabId: TabId, run: string, signal: AbortSignal) => void
  /**
   * Control the run: hold it, release it, end it, follow a candidate up, dismiss
   * one, or write its current winner as a package.
   * @param tabId - the tab being drawn.
   * @param run - the run id or directory.
   * @param act - the verb the loop honors at its next generation boundary, and
   * what it names.
   * @param signal - the tab record's lifetime.
   */
  readonly controlRun: (
    tabId: TabId,
    run: string,
    act: RefactoryControlAct,
    signal: AbortSignal,
  ) => void
  /**
   * Append one note to the watched run's ledger, and take the ledger it answers with.
   * @param tabId - the tab being drawn.
   * @param run - the run id or directory.
   * @param note - what the person said.
   * @param kind - what the note is.
   * @param signal - the tab record's lifetime.
   */
  readonly addNote: (tabId: TabId, run: string, note: string, kind: RefactoryNoteKind, signal: AbortSignal) => void
  /**
   * Record a verdict for a gate the watched run is stopped at, and take the ledger
   * it answers with.
   * @param tabId - the tab being drawn.
   * @param run - the run id or directory.
   * @param gate - the gate node the verdict answers.
   * @param verdict - the outcome label the gate routes on.
   * @param signal - the tab record's lifetime.
   */
  readonly answerGate: (tabId: TabId, run: string, gate: string, verdict: string, signal: AbortSignal) => void
  /**
   * Ask a run to stop, then read it again.
   * @param tabId - the tab being drawn.
   * @param run - the run id or directory.
   * @param signal - the tab record's lifetime.
   */
  readonly stop: (tabId: TabId, run: string, signal: AbortSignal) => void
  /**
   * Enable or disable one package, then take the catalogue the Host answers with.
   * @param tabId - the tab being drawn.
   * @param name - the package name.
   * @param enabled - the state to set.
   * @param signal - the tab record's lifetime.
   */
  readonly setEnabled: (tabId: TabId, name: string, enabled: boolean, signal: AbortSignal) => void
  /**
   * Remove one workspace package, then take the catalogue the Host answers with.
   * @param tabId - the tab being drawn.
   * @param name - the package name.
   * @param signal - the tab record's lifetime.
   */
  readonly remove: (tabId: TabId, name: string, signal: AbortSignal) => void
  /**
   * Import a package from a directory or git URL, then take the catalogue the
   * Host answers with.
   * @param tabId - the tab being drawn.
   * @param source - the directory or git URL.
   * @param name - the name to import it as; empty keeps its own.
   * @param signal - the tab record's lifetime.
   */
  readonly importPackage: (tabId: TabId, source: string, name: string, signal: AbortSignal) => void
}

/**
 * Bind the picker's face to one Client Remote face.
 * @param remote - the Client Remote face carrying the `refactoryConsole` namespace.
 * @returns the Slot `inject` factory: session and bound actions in, face out.
 */
export function refactoryFace(
  remote: RefactoryRemote,
): (sessionId: SessionId, actions: BoundActions<ReturnType<typeof createRefactoryStore>>) => RefactoryInjected {
  return (
    _sessionId: SessionId,
    actions: BoundActions<ReturnType<typeof createRefactoryStore>>,
  ): RefactoryInjected => {
    // Only a newer request for the same operation may retire its predecessor.
    // In particular, catalogue/status reads cannot retire a long-running use.
    const catalogueGenerations = new Map<TabId, number>()
    const statusGenerations = new Map<TabId, number>()
    const startGenerations = new Map<TabId, number>()
    const runGenerations = new Map<TabId, number>()
    const packageGenerations = new Map<TabId, number>()
    const stopGenerations = new Map<TabId, number>()
    const watchGenerations = new Map<TabId, number>()
    const starting = new Set<TabId>()
    const running = new Set<TabId>()
    const watchers = new Map<TabId, AbortController>()
    /**
     * The same, for the ledger read: a status refresh and a ledger refresh are two
     * independent reads of one run, and retiring one must not drop the other.
     */
    const steeringGenerations = new Map<TabId, number>()
    // A candidate read and a ledger read are two reads of the same tab and must not
    // retire each other; one control act refreshes both.
    const candidateGenerations = new Map<TabId, number>()
    // Monotonic across bucket lifetimes: a late response from a closed tab must
    // not match the first request when that id is used again.
    let sequence = 0
    const nextGeneration = (generations: Map<TabId, number>, tabId: TabId): number => {
      const generation = ++sequence
      generations.set(tabId, generation)
      return generation
    }
    /**
     * Write one call's outcome, unless a newer read retired it. A refusal becomes
     * the notice the body renders, or the read's own failure line where the read
     * is the thing the reader is looking at; `done` runs for a gesture that must
     * clear its own in-flight state, and `failed` for one that must clear it and
     * still keep the Host's code.
     */
    const settle = <T>(
      generations: Map<TabId, number>,
      tabId: TabId,
      generation: number,
      call: Promise<RemoteResult<T>>,
      landed: (value: T) => void,
      done?: () => void,
      failed?: (failure: RemoteFailure) => void,
    ): void => {
      void call.then((result) => {
        if (generations.get(tabId) !== generation) return
        if (result.ok) landed(result.value)
        else if (failed === undefined) {
          actions.noticed(tabId, { code: result.error.code, message: result.error.message })
        } else failed(result.error)
        done?.()
      })
    }
    /** Read one run's status into the store. */
    const readStatus = (tabId: TabId, run: string, after: number, signal: AbortSignal): void => {
      if (signal.aborted) return
      const generation = nextGeneration(statusGenerations, tabId)
      actions.frameLoading(tabId)
      settle(statusGenerations, tabId, generation, remote.refactoryConsole.status(run, after, signal), (frame) => {
        actions.framed(tabId, frame)
      }, undefined, (failure) => { actions.frameFailed(tabId, failure.message) })
    }
    /** Read one run's intent ledger and gates into the store. */
    const loadSteering = (tabId: TabId, run: string, signal: AbortSignal): void => {
      if (signal.aborted) return
      const generation = nextGeneration(steeringGenerations, tabId)
      actions.steeringLoading(tabId)
      settle(steeringGenerations, tabId, generation, console.steer({ run }, signal), (steering) => {
        actions.steered(tabId, steering)
      }, undefined, (failure) => { actions.steeringFailed(tabId, failure.message) })
    }
    /** Read one run's candidates into the store. */
    const readCandidates = (tabId: TabId, run: string, signal: AbortSignal): void => {
      if (signal.aborted) return
      const generation = nextGeneration(candidateGenerations, tabId)
      actions.candidatesLoading(tabId)
      settle(candidateGenerations, tabId, generation, console.leaderboard(run, signal), (candidates) => {
        actions.candidatesRead(tabId, candidates)
      }, undefined, (failure) => { actions.candidatesFailed(tabId, failure.message) })
    }
    /** Change target without allowing the previous target's late replies to land. */
    const watchRun = (tabId: TabId, run: string, signal: AbortSignal): void => {
      if (signal.aborted || run.trim() === '') return
      nextGeneration(watchGenerations, tabId)
      nextGeneration(candidateGenerations, tabId)
      nextGeneration(stopGenerations, tabId)
      watchers.get(tabId)?.abort()
      actions.watching(tabId, run.trim())
      readStatus(tabId, run.trim(), 0, signal)
      loadSteering(tabId, run.trim(), signal)
      if (console.watchStream !== undefined) {
        const controller = new AbortController()
        watchers.set(tabId, controller)
        const abort = (): void => controller.abort()
        signal.addEventListener('abort', abort, { once: true })
        void (async () => {
          try {
            for await (const frame of console.watchStream!(run.trim(), 0, controller.signal)) {
              if (controller.signal.aborted || watchers.get(tabId) !== controller) return
              // Retire any older unary frame before accepting streamed state.
              nextGeneration(statusGenerations, tabId)
              nextGeneration(steeringGenerations, tabId)
              actions.framed(tabId, frame.status)
              actions.steered(tabId, frame.steering)
            }
          } catch (error) {
            if (!controller.signal.aborted) actions.noticed(tabId, {
              code: 'refactory/watch-disconnected', message: `Live view disconnected; Watch reconnects without restarting work. ${error instanceof Error ? error.message : String(error)}`,
            })
          } finally { signal.removeEventListener('abort', abort) }
        })()
      }
    }
    /** Take the packages a package gesture answered with. */
    const takePackages = (
      tabId: TabId,
      call: Promise<RemoteResult<readonly RefactoryPackageChoice[]>>,
      signal: AbortSignal,
    ): void => {
      const generation = nextGeneration(packageGenerations, tabId)
      actions.busy(tabId, true)
      settle(packageGenerations, tabId, generation, call, (packages) => {
        actions.packages(tabId, packages)
        read(tabId, signal)
      }, () => { actions.busy(tabId, false) })
    }
    const console = remote.refactoryConsole
    /**
     * One read of the project identity and catalogues, retired by the next read of this tab.
     * Both callers have already refused an ended record; this only reads.
     */
    const read = (tabId: TabId, signal: AbortSignal): void => {
      const generation = nextGeneration(catalogueGenerations, tabId)
      void Promise.all([
        console.project(signal),
        console.modes(signal),
        console.strategies(signal),
        console.packages(signal),
      ]).then(([project, modes, strategies, packages]) => {
        if (catalogueGenerations.get(tabId) !== generation) return
        if (!project.ok) { actions.failed(tabId, project.error.message); return }
        if (!modes.ok) { actions.failed(tabId, modes.error.message); return }
        if (!strategies.ok) { actions.failed(tabId, strategies.error.message); return }
        if (!packages.ok) { actions.failed(tabId, packages.error.message); return }
        actions.loaded(tabId, { project: project.value, modes: modes.value, strategies: strategies.value, packages: packages.value })
      })
    }
    return {
      async listRuns(signal) {
        const result = await console.runs(signal)
        if (!result.ok) throw new Error(result.error.message)
        return result.value
      },
      async graphControl(request, signal) {
        if (console.graphControl === undefined) throw new Error('Graph controls unavailable in this runtime')
        const result = await console.graphControl(request, signal)
        if (!result.ok) throw new Error(result.error.message)
        return result.value
      },
      profileApi: console,
      load(tabId, signal) {
        if (signal.aborted) return
        actions.start(tabId)
        signal.addEventListener('abort', () => {
          for (const generations of [
            catalogueGenerations, statusGenerations, startGenerations, runGenerations,
            packageGenerations, stopGenerations, steeringGenerations, candidateGenerations, watchGenerations,
          ]) generations.delete(tabId)
          starting.delete(tabId)
          running.delete(tabId)
          watchers.get(tabId)?.abort()
          watchers.delete(tabId)
          actions.forget(tabId)
        }, { once: true })
        read(tabId, signal)
      },
      reload(tabId, signal) {
        if (signal.aborted) return
        actions.loading(tabId)
        read(tabId, signal)
      },
      choosePackage(tabId, name) { actions.chosePackage(tabId, name) },
      chooseIntent(tabId, intent) { actions.setupIntent(tabId, intent) },
      choose(tabId, id) {
        actions.chose(tabId, id)
      },
      setKnob(tabId, name, value) {
        actions.knob(tabId, name, value)
      },
      setSetting(tabId, name, value) {
        actions.setting(tabId, name, value)
      },
      start(tabId, mode, knobs, settings, signal) {
        if (signal.aborted || starting.has(tabId)) return
        const budget = searchBudget(settings)
        if (budget === null) {
          actions.noticed(tabId, { code: 'refactory/invalid-search-budget', message: 'Search limits must be positive whole numbers.' })
          return
        }
        // Only the names that were chosen travel; an empty seam takes the
        // console's own default.
        const chosen: Record<string, string | number> = { ...budget }
        if (settings.strategy !== '') chosen.strategy = settings.strategy
        if (settings.optimizer !== '') chosen.optimizer = settings.optimizer
        if (settings.evaluator !== '') chosen.evaluator = settings.evaluator
        if (settings.configuration) chosen.settings_json = settings.configuration
        if (settings.profile) chosen.profile = settings.profile
        if (settings.profile_version) chosen.profile_version = Number(settings.profile_version)
        starting.add(tabId)
        actions.starting(tabId, true)
        const generation = nextGeneration(startGenerations, tabId)
        const watchGeneration = watchGenerations.get(tabId)
        settle(startGenerations, tabId, generation, console.start(
          { mode, settings: chosen, knobs: knobArguments(knobs) },
          signal,
        ), (receipt) => {
          if (watchGenerations.get(tabId) === watchGeneration) watchRun(tabId, receipt.root, signal)
        }, () => { starting.delete(tabId); actions.starting(tabId, false) })
      },
      run(tabId, mode, knobs, signal, settings) {
        if (signal.aborted || running.has(tabId)) return
        running.add(tabId)
        const generation = nextGeneration(runGenerations, tabId)
        const watchGeneration = watchGenerations.get(tabId)
        actions.runLoading(tabId)
        const current = (): boolean => !signal.aborted && runGenerations.get(tabId) === generation
        void (async () => {
          let answered = false
          try {
            for await (const event of console.runStream({ mode, knobs: knobArguments(knobs),
              ...settings?.configuration ? { settings: runtimeSettings(settings.configuration) } : {},
            }, signal)) {
              if (!current()) return
              if (event.kind === 'launch') {
                actions.runLaunched(tabId, event.launch)
                // A later explicit Watch owns the view, even while this launch
                // is still preparing. Its worker/result remain independently owned.
                if (event.launch.phase === 'prepared' && watchGenerations.get(tabId) === watchGeneration) {
                  watchRun(tabId, event.launch.root, signal)
                }
              }
              // The durable watcher owns the ledger. A completed use must not
              // put its gates under another run selected in the meantime.
              else { actions.ran(tabId, event.run); answered = true }
            }
            if (!answered && current()) throw new Error('The run stream ended without a final result; inspect its run directory before retrying.')
          } catch (error) {
            if (!current()) return
            const failure = error instanceof Error ? error.message : String(error)
            actions.noticed(tabId, { code: 'refactory/console-failed', message: failure })
            actions.runFailed(tabId)
          } finally {
            // Release this invocation even when its own signal was cancelled.
            // Record teardown deletes the generation first, so it is never revived.
            if (runGenerations.get(tabId) === generation) {
              running.delete(tabId)
              if (signal.aborted) {
                actions.noticed(tabId, { code: 'gateway/cancelled', message: 'Run once was cancelled; inspect its run directory before retrying.' })
                actions.runFailed(tabId)
              }
            }
          }
        })()
      },
      refresh(tabId, run, after, signal) {
        readStatus(tabId, run, after, signal)
        // A ledger is appended to while the run goes, so a refresh of the run is
        // also a refresh of what has been said about it.
        loadSteering(tabId, run, signal)
      },
      watch(tabId, run, signal) {
        watchRun(tabId, run, signal)
      },
      readSteering(tabId, run, signal) {
        loadSteering(tabId, run, signal)
      },
      readCandidates(tabId, run, signal) {
        readCandidates(tabId, run, signal)
      },
      controlRun(tabId, run, act, signal) {
        if (signal.aborted) return
        // Replace the previous gesture's notice before dispatch. A later failure
        // from another operation must not be erased by this control's success.
        actions.noticed(tabId, null)
        const generation = nextGeneration(steeringGenerations, tabId)
        settle(steeringGenerations, tabId, generation, console.steer(
          { run, ...act },
          signal,
        ), (steering) => {
          actions.steered(tabId, steering)
          // A keep or a reject changes the rows themselves, and the ledger read is
          // already current; so the same act refreshes the table it acted on.
          readCandidates(tabId, run, signal)
        }, undefined, (failure) => {
          actions.noticed(tabId, { code: failure.code, message: failure.message })
          // A refused adoption also has a durable receipt. Read it so the
          // previously applied control is not left looking like this result.
          loadSteering(tabId, run, signal)
        })
      },
      addNote(tabId, run, note, kind, signal) {
        if (signal.aborted) return
        const generation = nextGeneration(steeringGenerations, tabId)
        settle(steeringGenerations, tabId, generation, console.steer({ run, note, kind }, signal), (steering) => {
          actions.steered(tabId, steering)
        }, undefined, (failure) => {
          actions.noticed(tabId, { code: failure.code, message: failure.message })
        })
      },
      answerGate(tabId, run, gate, verdict, signal) {
        if (signal.aborted) return
        const generation = nextGeneration(steeringGenerations, tabId)
        settle(steeringGenerations, tabId, generation, console.steer(
          { run, verdicts: [`${gate}=${verdict}`] },
          signal,
        ), (steering) => {
          actions.steered(tabId, steering)
        }, undefined, (failure) => {
          actions.noticed(tabId, { code: failure.code, message: failure.message })
        })
      },
      stop(tabId, run, signal) {
        if (signal.aborted) return
        const generation = nextGeneration(stopGenerations, tabId)
        settle(stopGenerations, tabId, generation, console.stop(run, signal), () => {
          readStatus(tabId, run, 0, signal)
        })
      },
      setEnabled(tabId, name, enabled, signal) {
        if (signal.aborted) return
        takePackages(tabId, console.setPackageEnabled(name, enabled, signal), signal)
      },
      remove(tabId, name, signal) {
        if (signal.aborted) return
        takePackages(tabId, console.removePackage(name, signal), signal)
      },
      importPackage(tabId, source, name, signal) {
        if (signal.aborted) return
        takePackages(tabId, console.importPackage(source, name, signal), signal)
      },
    }
  }
}

/** Only worker routing and resources apply when using a mode once. */
export function runtimeSettings(configuration: string): Record<string, string | number> {
  const value = JSON.parse(configuration) as Record<string, unknown>
  const allowed = ['worker_backend', 'provider', 'model', 'effort', 'permission_mode',
    'max_elapsed_s', 'max_tokens', 'validation_reserve_s', 'validation_reserve_tokens']
  const result = Object.fromEntries(Object.entries(value).filter(([key, entry]) => allowed.includes(key)
    && (typeof entry === 'string' || typeof entry === 'number'))) as Record<string, string | number>
  for (const key of ['role_routes', 'node_routes']) if (value[key] && typeof value[key] === 'object') result[key] = JSON.stringify(value[key])
  return result
}
