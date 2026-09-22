/**
 * The picker's view state: what was read, what a person chose, and what the run
 * being read last reported.
 *
 * The choices are the Host's — a mode catalogue, three level-two registries and
 * the package list — and so is every status frame. What this store keeps is the
 * mount's own: which of the described choices is selected, the run's id, and the
 * last answer about it. Bucketed by tab id, because two picker tabs in one
 * session choose independently.
 *
 * Writers run between `start` and `forget`: the owner's `signal` ends a bucket's
 * life, and the face stops dispatching once it aborts.
 */
import { defineStore, type EngineStoreHandle } from '@deepseek-ai/dsh-client-store'
import type {
  RefactoryModeChoice,
  RefactoryPackageChoice,
  RefactoryRunStatus,
  RefactoryStrategies,
} from '@deepseek-ai/dsh-api-remotes/client'
import type { TabId } from '@deepseek-ai/dsh-client-ui-dockkit'

/** One read that either landed, is in flight, or failed with the Host's reason. */
export type Loadable<T> =
  | { readonly kind: 'loading' }
  | { readonly kind: 'ready'; readonly value: T }
  | { readonly kind: 'failed'; readonly message: string }

/** Everything a picker offers to choose between, read as one. */
export interface RefactoryChoices {
  /** The modes a run can be started from, each with the verb that runs it. */
  readonly modes: readonly RefactoryModeChoice[]
  /** The named outer loops, optimizers and evaluators. */
  readonly strategies: RefactoryStrategies
  /** The packages the registry knows. */
  readonly packages: readonly RefactoryPackageChoice[]
}

/** The three level-two names a run is started with; empty means the default. */
export interface RefactorySettings {
  /** The outer loop, from the `strategies` answer. */
  strategy: string
  /** The optimizer, from the `strategies` answer. */
  optimizer: string
  /** The evaluator, from the `strategies` answer. */
  evaluator: string
}

/** Why a gesture was refused: the Host's code, and the Host's own message. */
export interface RefactoryNotice {
  /** The Remote failure's code, e.g. `refactory/authoring-mode`. */
  readonly code: string
  /** The Host's message, shown as data when the code names nothing more precise. */
  readonly message: string
}

/** One tab's picker: what it read, what it chose, and where the run got to. */
export interface RefactoryTabState {
  /** The choices, as last read. */
  choices: Loadable<RefactoryChoices>
  /** The chosen mode's catalogue id, or `null` before anything is chosen. */
  chosen: string | null
  /** The chosen level-two names. */
  settings: RefactorySettings
  /** The run being read, or `null` when nothing is being watched. */
  watching: string | null
  /** The last status frame for `watching`; `null` before the first read. */
  frame: Loadable<RefactoryRunStatus> | null
  /** What the last refused gesture said, or `null`. */
  notice: RefactoryNotice | null
  /** Whether a package change is in flight. */
  busy: boolean
}

/** Every tab's picker, keyed by tab id. */
export interface RefactoryState {
  byTab: Record<TabId, RefactoryTabState>
}

/** The three names a run setting can be; the level-two seams. */
export type RefactorySettingName = keyof RefactorySettings

/**
 * One tab's bucket, which every writer after `start` relies on: the face only
 * dispatches while the record's signal is live, and `forget` runs on its abort.
 * @param state - the draft.
 * @param tabId - the tab being written.
 * @returns the tab's picker.
 */
function bucket(state: RefactoryState, tabId: TabId): RefactoryTabState {
  const tab = state.byTab[tabId]
  if (tab === undefined) throw new Error(`ui-refactory: no picker for tab "${tabId}"`)
  return tab
}

/** The picker store's write set; every action names the tab it writes. */
type RefactoryActions = {
  start: (draft: RefactoryState, tabId: TabId) => void
  loading: (draft: RefactoryState, tabId: TabId) => void
  loaded: (draft: RefactoryState, tabId: TabId, choices: RefactoryChoices) => void
  failed: (draft: RefactoryState, tabId: TabId, message: string) => void
  chose: (draft: RefactoryState, tabId: TabId, id: string | null) => void
  setting: (draft: RefactoryState, tabId: TabId, name: RefactorySettingName, value: string) => void
  watching: (draft: RefactoryState, tabId: TabId, run: string) => void
  frameLoading: (draft: RefactoryState, tabId: TabId) => void
  framed: (draft: RefactoryState, tabId: TabId, frame: RefactoryRunStatus) => void
  frameFailed: (draft: RefactoryState, tabId: TabId, message: string) => void
  packages: (draft: RefactoryState, tabId: TabId, packages: readonly RefactoryPackageChoice[]) => void
  noticed: (draft: RefactoryState, tabId: TabId, notice: RefactoryNotice | null) => void
  busy: (draft: RefactoryState, tabId: TabId, busy: boolean) => void
  forget: (draft: RefactoryState, tabId: TabId) => void
}

/** The level-two names honestly absent, so a run takes the console's defaults for them. */
export const NO_SETTINGS: RefactorySettings = { strategy: '', optimizer: '', evaluator: '' }

/**
 * Declare the picker's store.
 *
 * A factory rather than a shared handle: the registration declares it as an
 * exclusive store, so the framework mints one instance per session.
 * @returns the store handle to declare on the registration.
 */
export function createRefactoryStore(): EngineStoreHandle<RefactoryState, RefactoryActions> {
  return defineStore({
    init: (): RefactoryState => ({ byTab: {} }),
    actions: {
      /**
       * Seed one tab's picker, with the choices already being read.
       * @param d - draft state.
       * @param tabId - the tab being drawn.
       */
      start: (d, tabId: TabId) => {
        d.byTab[tabId] = {
          choices: { kind: 'loading' },
          chosen: null,
          settings: { ...NO_SETTINGS },
          watching: null,
          frame: null,
          notice: null,
          busy: false,
        }
      },
      /**
       * Mark the choices as being read again, keeping what a person chose.
       * @param d - draft state.
       * @param tabId - the tab being drawn.
       */
      loading: (d, tabId: TabId) => {
        bucket(d, tabId).choices = { kind: 'loading' }
      },
      /**
       * Record the choices the Host answered with.
       * @param d - draft state.
       * @param tabId - the tab being drawn.
       * @param choices - the modes, strategies and packages.
       */
      loaded: (d, tabId: TabId, choices: RefactoryChoices) => {
        bucket(d, tabId).choices = { kind: 'ready', value: choices }
      },
      /**
       * Record why the choices could not be read, and drop a selection they may
       * no longer contain.
       * @param d - draft state.
       * @param tabId - the tab being drawn.
       * @param message - the Host's reason.
       */
      failed: (d, tabId: TabId, message: string) => {
        const tab = bucket(d, tabId)
        tab.choices = { kind: 'failed', message }
        tab.chosen = null
      },
      /**
       * Choose one of the described modes, or nothing.
       * @param d - draft state.
       * @param tabId - the tab being drawn.
       * @param id - the chosen mode's catalogue id, or `null`.
       */
      chose: (d, tabId: TabId, id: string | null) => {
        bucket(d, tabId).chosen = id
      },
      /**
       * Choose one of the described level-two names.
       * @param d - draft state.
       * @param tabId - the tab being drawn.
       * @param name - which seam the choice is for.
       * @param value - the chosen name, or the empty string for the default.
       */
      setting: (d, tabId: TabId, name: RefactorySettingName, value: string) => {
        bucket(d, tabId).settings[name] = value
      },
      /**
       * Begin reading one run's status.
       * @param d - draft state.
       * @param tabId - the tab being drawn.
       * @param run - the run id or directory the Host reported.
       */
      watching: (d, tabId: TabId, run: string) => {
        const tab = bucket(d, tabId)
        tab.watching = run
        tab.frame = { kind: 'loading' }
        tab.notice = null
      },
      /**
       * Mark the watched run's status as being read.
       * @param d - draft state.
       * @param tabId - the tab being drawn.
       */
      frameLoading: (d, tabId: TabId) => {
        bucket(d, tabId).frame = { kind: 'loading' }
      },
      /**
       * Record one status frame.
       * @param d - draft state.
       * @param tabId - the tab being drawn.
       * @param frame - where the run is.
       */
      framed: (d, tabId: TabId, frame: RefactoryRunStatus) => {
        bucket(d, tabId).frame = { kind: 'ready', value: frame }
      },
      /**
       * Replace the packages with the catalogue a package gesture answered with.
       *
       * A package change cannot alter a mode's own description or the level-two
       * registries, so the modes and strategies already read stay as they are; a
       * gesture before the first read lands is dropped, because that read is
       * already fetching the packages it would replace.
       * @param d - draft state.
       * @param tabId - the tab being drawn.
       * @param packages - the packages the Host answered with.
       */
      packages: (d, tabId: TabId, packages: readonly RefactoryPackageChoice[]) => {
        const tab = bucket(d, tabId)
        const choices = tab.choices
        if (choices.kind !== 'ready') return
        tab.choices = { kind: 'ready', value: { ...choices.value, packages } }
      },
      /**
       * Record why the watched run's status could not be read.
       *
       * The failure belongs to the status the reader is looking at, so it is
       * written where that status is rather than as a notice about a gesture.
       * @param d - draft state.
       * @param tabId - the tab being drawn.
       * @param message - the Host's reason.
       */
      frameFailed: (d, tabId: TabId, message: string) => {
        bucket(d, tabId).frame = { kind: 'failed', message }
      },
      /**
       * Say what the last refused gesture was.
       * @param d - draft state.
       * @param tabId - the tab being drawn.
       * @param notice - the failure to show, or `null` to clear it.
       */
      noticed: (d, tabId: TabId, notice: RefactoryNotice | null) => {
        bucket(d, tabId).notice = notice
      },
      /**
       * Record whether a package change is in flight.
       * @param d - draft state.
       * @param tabId - the tab being drawn.
       * @param busy - whether the change is still running.
       */
      busy: (d, tabId: TabId, busy: boolean) => {
        bucket(d, tabId).busy = busy
      },
      /**
       * Forget one tab's picker, for a tab record that is gone.
       * @param d - draft state.
       * @param tabId - the tab that went away.
       */
      forget: (d, tabId: TabId) => {
        d.byTab = Object.fromEntries(Object.entries(d.byTab).filter(([id]) => id !== tabId))
      },
    },
  })
}
