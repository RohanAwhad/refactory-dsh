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
import type { ClientRemote, RemoteFailure, RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type { RefactoryPackageChoice } from '@deepseek-ai/dsh-api-remotes/client'
import type { BoundActions } from '@deepseek-ai/dsh-client-store'
import type { TabId } from '@deepseek-ai/dsh-client-ui-dockkit'
import type { SessionId } from '@deepseek-ai/dsh-session/types'
import type {
  RefactoryKnobSetting,
  RefactorySettingName,
  RefactorySettings,
  createRefactoryStore,
} from './store.ts'

/** The slice of the Client Remote face this package calls: the `refactoryConsole` namespace. */
export type RefactoryRemote = {
  readonly refactoryConsole: Pick<
    ClientRemote['refactoryConsole'],
    'modes' | 'strategies' | 'packages' | 'start' | 'run' | 'status' | 'stop'
      | 'setPackageEnabled' | 'removePackage' | 'importPackage'
  >
}

/**
 * The knob values a gesture sends, as the console's repeatable `--knob NAME=VALUE`.
 * @param values - one value per declared knob, in declaration order.
 * @returns one `NAME=VALUE` argument per knob.
 */
function knobArguments(values: readonly RefactoryKnobSetting[]): readonly string[] {
  return values.map(({ name, value }) => `${name}=${value}`)
}

/** The picker's injected business face, as the body receives it. */
export interface RefactoryInjected {
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
  ) => void
  /**
   * Read one status frame for a run.
   * @param tabId - the tab being drawn.
   * @param run - the run id or directory.
   * @param after - how many progress lines this reader has already seen.
   * @param signal - the tab record's lifetime.
   */
  readonly refresh: (tabId: TabId, run: string, after: number, signal: AbortSignal) => void
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
    /** Per tab: the read generation a settlement must match; the latest request wins. */
    const generations = new Map<TabId, number>()
    const nextGeneration = (tabId: TabId): number => {
      const generation = (generations.get(tabId) ?? 0) + 1
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
      const generation = nextGeneration(tabId)
      actions.frameLoading(tabId)
      settle(tabId, generation, remote.refactoryConsole.status(run, after, signal), (frame) => {
        actions.framed(tabId, frame)
      }, undefined, (failure) => { actions.frameFailed(tabId, failure.message) })
    }
    /** Take the packages a package gesture answered with. */
    const takePackages = (
      tabId: TabId,
      call: Promise<RemoteResult<readonly RefactoryPackageChoice[]>>,
    ): void => {
      const generation = nextGeneration(tabId)
      actions.busy(tabId, true)
      settle(tabId, generation, call, (packages) => {
        actions.packages(tabId, packages)
      }, () => { actions.busy(tabId, false) })
    }
    const console = remote.refactoryConsole
    /**
     * One read of the three catalogues, retired by the next read of this tab.
     * Both callers have already refused an ended record; this only reads.
     */
    const read = (tabId: TabId, signal: AbortSignal): void => {
      const generation = nextGeneration(tabId)
      void Promise.all([
        console.modes(signal),
        console.strategies(signal),
        console.packages(signal),
      ]).then(([modes, strategies, packages]) => {
        if (generations.get(tabId) !== generation) return
        if (!modes.ok) { actions.failed(tabId, modes.error.message); return }
        if (!strategies.ok) { actions.failed(tabId, strategies.error.message); return }
        if (!packages.ok) { actions.failed(tabId, packages.error.message); return }
        actions.loaded(tabId, { modes: modes.value, strategies: strategies.value, packages: packages.value })
      })
    }
    return {
      load(tabId, signal) {
        if (signal.aborted) return
        actions.start(tabId)
        read(tabId, signal)
      },
      reload(tabId, signal) {
        if (signal.aborted) return
        actions.loading(tabId)
        read(tabId, signal)
      },
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
        if (signal.aborted) return
        // Only the names that were chosen travel; an empty seam takes the
        // console's own default.
        const chosen: Record<string, string> = {}
        if (settings.strategy !== '') chosen.strategy = settings.strategy
        if (settings.optimizer !== '') chosen.optimizer = settings.optimizer
        if (settings.evaluator !== '') chosen.evaluator = settings.evaluator
        const generation = nextGeneration(tabId)
        settle(tabId, generation, console.start(
          { mode, settings: chosen, knobs: knobArguments(knobs) },
          signal,
        ), (receipt) => {
          actions.watching(tabId, receipt.root)
          readStatus(tabId, receipt.root, 0, signal)
        })
      },
      run(tabId, mode, knobs, signal) {
        if (signal.aborted) return
        const generation = nextGeneration(tabId)
        actions.runLoading(tabId)
        settle(tabId, generation, console.run({ mode, knobs: knobArguments(knobs) }, signal), (run) => {
          actions.ran(tabId, run)
        }, undefined, (failure) => {
          // A refused gesture is the notice, and the tab is no longer waiting on a
          // run that is never going to arrive.
          actions.noticed(tabId, { code: failure.code, message: failure.message })
          actions.runFailed(tabId)
        })
      },
      refresh(tabId, run, after, signal) {
        readStatus(tabId, run, after, signal)
      },
      stop(tabId, run, signal) {
        if (signal.aborted) return
        const generation = nextGeneration(tabId)
        settle(tabId, generation, console.stop(run, signal), () => {
          readStatus(tabId, run, 0, signal)
        })
      },
      setEnabled(tabId, name, enabled, signal) {
        if (signal.aborted) return
        takePackages(tabId, console.setPackageEnabled(name, enabled, signal))
      },
      remove(tabId, name, signal) {
        if (signal.aborted) return
        takePackages(tabId, console.removePackage(name, signal))
      },
      importPackage(tabId, source, name, signal) {
        if (signal.aborted) return
        takePackages(tabId, console.importPackage(source, name, signal))
      },
    }
  }
}
