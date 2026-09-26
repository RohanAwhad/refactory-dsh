/**
 * The picker's body: one described choice per seam, and the run they start.
 *
 * Everything the picker keeps lives in its store, keyed by tab; everything it
 * asks for goes through its injected face. The body only decides what to draw
 * for each read and what a gesture means: choosing a mode, setting one of the
 * knobs that mode declares, choosing a level-two name, starting the chosen mode,
 * using it once, refreshing or stopping the run being read, and enabling,
 * disabling, removing or importing a package.
 *
 * Nothing here is free text except the two import fields and the knobs a mode
 * declares without a set of accepted values, which the registry and the knob
 * itself require: a mode, a strategy and a package are always chosen from what
 * the Host listed, never typed. A mode the loop cannot search is offered but not
 * started and not used once: the row says which action runs it, and the Host
 * refuses what the loop cannot search.
 */
import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type {
  RefactoryCandidateRow,
  RefactoryKnob,
  RefactoryNoteKind,
  RefactoryRunStatus,
  RefactorySteering,
  RefactoryStrategyChoice,
} from '@deepseek-ai/dsh-refactory-console/types'
import type { PropsLocale, PropsRuntime, PropsStore, TranslateNS } from '@deepseek-ai/dsh-client-ui-slots'
import {
  IconDatabaseOutline16, IconPlayOutline16, IconRefreshOutline16, IconStopFill16,
} from '@deepseek-ai/dsh-client-ui-primitives'
import type {} from './locales.ts'
import type { RefactoryInjected } from './face.ts'
import type { Loadable, RefactoryNotice, RefactorySettingName, RefactorySingleState, createRefactoryStore } from './store.ts'
import { knobValues } from './store.ts'
import css from './RefactoryBody.module.css'

/** The body's composed props: the tab it draws, its store, its face, and its copy. */
export type RefactoryBodyProps =
  & PropsRuntime<'sidebar.right.pane.tab'>
  & PropsStore<ReturnType<typeof createRefactoryStore>>
  & RefactoryInjected
  & PropsLocale<'sidebarRefactory'>

/**
 * Say why a gesture was refused, in terms of what the reader did.
 *
 * The three refusals the Host names precisely have copy of their own; anything
 * else is the Host's own message, which only the console knows how to phrase.
 * @param t - namespace-bound translate.
 * @param notice - the settled Remote failure.
 * @returns the line to show above the picker.
 */
export function failureLine(t: TranslateNS<'sidebarRefactory'>, notice: RefactoryNotice): string {
  switch (notice.code) {
    case 'refactory/unknown-mode': return t('error.unknownMode')
    case 'refactory/authoring-mode': return t('error.authoringMode')
    case 'refactory/package-disabled': return t('error.packageDisabled')
    // Carrier and unclassified Host failures reach the reader as themselves:
    // the picker knows nothing useful to add to a console's own message.
    default: return t('error.failed', { message: notice.message })
  }
}

/**
 * What the chosen level-two name says it is, or the default line when none is chosen.
 * @param t - namespace-bound translate.
 * @param chosen - the chosen name, empty for the console's default.
 * @param names - the described choices for this seam.
 * @returns the line under the select.
 */
function descriptionOf(
  t: TranslateNS<'sidebarRefactory'>,
  chosen: string,
  names: readonly RefactoryStrategyChoice[],
): string {
  if (chosen === '') return t('setting.default')
  return names.find(entry => entry.name === chosen)?.description ?? t('setting.default')
}

/** One level-two seam: the named choices, and what the chosen one is. */
function Seam({
  label, id, value, names, t, onChoose,
}: {
  readonly label: string
  readonly id: RefactorySettingName
  readonly value: string
  readonly names: readonly RefactoryStrategyChoice[]
  readonly t: TranslateNS<'sidebarRefactory'>
  readonly onChoose: (value: string) => void
}): ReactNode {
  return (
    <div className={css.field}>
      <label className={css.label} htmlFor={`refactory-${id}`}>{label}</label>
      <select
        id={`refactory-${id}`}
        className={css.control}
        value={value}
        data-refactory-setting={id}
        onChange={(event) => { onChoose(event.target.value) }}
      >
        <option value="">{t('setting.default')}</option>
        {names.map(entry => <option key={entry.name} value={entry.name}>{entry.name}</option>)}
      </select>
      <p className={css.note} data-refactory-setting-note={id}>{descriptionOf(t, value, names)}</p>
    </div>
  )
}

/** How one declared knob is entered: from a declared set, or as free text of a kind. */
type KnobControl =
  | { readonly kind: 'choice'; readonly values: readonly string[] }
  | { readonly kind: 'number'; readonly step: string; readonly min?: number; readonly max?: number }
  | { readonly kind: 'text' }

/**
 * The control one declared knob needs.
 *
 * A knob that declares values is a choice whatever its kind — an `enum`'s members
 * and a `str` knob's values are both finite sets — and a `bool` is the same kind of
 * choice over the two words the console reads. Everything else is a number, bounded
 * when the declaration gives bounds, or open text.
 * @param knob - the declaration, as the catalogue reports it.
 * @returns which control to draw for it.
 */
function controlOf(knob: RefactoryKnob): KnobControl {
  if (knob.values.length > 0) return { kind: 'choice', values: knob.values }
  if (knob.kind === 'bool') return { kind: 'choice', values: ['true', 'false'] }
  if (knob.kind === 'int' || knob.kind === 'float') {
    const [low, high] = knob.range
    return {
      kind: 'number',
      step: knob.kind === 'int' ? '1' : 'any',
      ...low === null ? {} : { min: low },
      ...high === null ? {} : { max: high },
    }
  }
  return { kind: 'text' }
}

/** One declared knob: its name, the value this run uses, and what it is for. */
function KnobField({
  name, knob, value, t, onChange,
}: {
  readonly name: string
  readonly knob: RefactoryKnob
  readonly value: string
  readonly t: TranslateNS<'sidebarRefactory'>
  readonly onChange: (value: string) => void
}): ReactNode {
  const control = controlOf(knob)
  const id = `refactory-knob-${name}`
  return (
    <div className={css.field}>
      <label className={css.label} htmlFor={id}>{name}</label>
      {control.kind === 'choice'
        ? (
          <select
            id={id}
            className={css.control}
            value={value}
            data-refactory-knob-input={name}
            onChange={(event) => { onChange(event.target.value) }}
          >
            {control.values.map(entry => <option key={entry} value={entry}>{entry}</option>)}
          </select>
        )
        : control.kind === 'number'
          ? (
            <input
              id={id}
              className={css.input}
              type="number"
              step={control.step}
              min={control.min}
              max={control.max}
              value={value}
              data-refactory-knob-input={name}
              onChange={(event) => { onChange(event.target.value) }}
            />
          )
          : (
            <input
              id={id}
              className={css.input}
              type="text"
              value={value}
              data-refactory-knob-input={name}
              onChange={(event) => { onChange(event.target.value) }}
            />
          )}
      {knob.description !== '' && (
        <p className={css.note} data-refactory-knob-description={name}>{knob.description}</p>
      )}
      {knob.frozen && <p className={css.note} data-refactory-knob-frozen={name}>{t('knob.frozen')}</p>}
    </div>
  )
}

/** What using the chosen mode once left, or that the run is still going. */
function Single({
  run, t,
}: {
  readonly run: RefactorySingleState
  readonly t: TranslateNS<'sidebarRefactory'>
}): ReactNode {
  if (run.kind === 'running') {
    return <p className={css.note} data-refactory-single="running">{t('single.running')}</p>
  }
  const single = run.value
  const authoring = single.authoring
  return (
    <div className={css.status} data-refactory-single={single.state}>
      <p className={css.statusLine} data-refactory-single-run>{t('single.ran', { mode: single.mode ?? single.run_id, run: single.root })}</p>
      <p className={css.statusLine}>{t('status.state')}: {single.state}</p>
      <p className={css.statusLine} data-refactory-single-score>
        {single.best_score === null
          ? t(single.finished || ['finished', 'failed', 'died', 'halted'].includes(single.state) ? 'single.noAggregate' : 'status.noScore')
          : t('status.best', { score: single.best_score })}
      </p>
      {single.item_results != null && (
        <p className={css.statusLine} data-refactory-item-results>
          {t('single.itemResults', { ...single.item_results })}
        </p>
      )}
      {authoring !== null && (
        <p className={css.statusLine} data-refactory-single-spine={authoring.spine}>
          {t('single.spine')}: {authoring.spine}
        </p>
      )}
      {authoring !== null && authoring.nodes_due.length > 0 && (
        <p className={css.statusLine} data-refactory-single-due>
          {t('single.due', { nodes: authoring.nodes_due.join(', ') })}
        </p>
      )}
      {authoring !== null && (authoring.spine === 'ready' || authoring.spine === 'refused') && (
        <p className={css.note} data-refactory-single-gate>{t('single.gate')}</p>
      )}
      {authoring !== null && authoring.error !== '' && (
        <p className={css.note} data-refactory-single-error>
          {t('error.failed', { message: authoring.error })}
        </p>
      )}
      {Object.keys(single.facts).length > 0 && (
        <div className={css.status}>
          <p className={css.note}>{t('single.facts')}</p>
          <ul className={css.recorded}>
            {Object.entries(single.facts).map(([name, written]) => (
              <li key={name} data-refactory-fact={name}>{name}: {written}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

/** The run's status, once there is a run to read. */
function Status({
  tabId, frame, run, signal, t, refresh, stop,
}: {
  readonly tabId: Parameters<RefactoryInjected['refresh']>[0]
  readonly frame: Loadable<RefactoryRunStatus> | null
  readonly run: string
  readonly signal: AbortSignal
  readonly t: TranslateNS<'sidebarRefactory'>
  readonly refresh: RefactoryInjected['refresh']
  readonly stop: RefactoryInjected['stop']
}): ReactNode {
  if (frame === null || frame.kind === 'loading') {
    return <p className={css.note} data-refactory-status="reading">{t('status.reading')}</p>
  }
  if (frame.kind === 'failed') {
    return (
      <p className={css.note} data-refactory-status="failed">
        {t('error.failed', { message: frame.message })}
      </p>
    )
  }
  const status = frame.value
  return (
    <div className={css.status} data-refactory-status={status.state}>
      <p className={css.statusLine} data-refactory-run>{run}</p>
      <p className={css.statusLine}>{t('status.state')}: {status.state}</p>
      <p className={css.statusLine} data-refactory-score>
        {status.best_score === null
          ? t(status.finished ? 'single.noAggregate' : 'status.noScore')
          : t('status.best', { score: status.best_score })}
      </p>
      {status.authoring != null
        ? <p className={css.statusLine} data-refactory-watched-spine>{t('single.spine')}: {status.authoring.spine}</p>
        : <p className={css.statusLine} data-refactory-evaluations>{t('status.evaluations', { count: status.counters.evaluations })}</p>}
      {status.item_results != null && (
        <p className={css.statusLine} data-refactory-watched-item-results>{t('single.itemResults', { ...status.item_results })}</p>
      )}
      <div className={css.packageActions}>
        <button
          type="button"
          className={css.action}
          data-refactory-refresh
          onClick={() => { refresh(tabId, run, status.cursor, signal) }}
        >
          <IconRefreshOutline16 />
          {t('status.refresh')}
        </button>
        {!status.finished && status.kind === 'optimizer' && (
          <button
            type="button"
            className={css.action}
            data-refactory-stop
            onClick={() => { stop(tabId, run, signal) }}
          >
            <IconStopFill16 />
            {t('status.stop')}
          </button>
        )}
      </div>
    </div>
  )
}

/** One note kind, and the copy the kind selector shows for it. */
const NOTE_KINDS: readonly RefactoryNoteKind[] = ['info', 'correction', 'constraint']

/**
 * The watched run's candidates, and what a person can do to one.
 *
 * Read on demand rather than on every tick: a leaderboard is a console call of its
 * own, and a search that is not being steered right now does not need its table
 * fetched every few seconds. Every act refreshes it, so the flags a click changes
 * are the flags the person then sees.
 */
function Candidates({
  tabId, run, candidates, finished, signal, t, readCandidates, controlRun,
}: {
  readonly tabId: Parameters<RefactoryInjected['refresh']>[0]
  readonly run: string
  readonly candidates: Loadable<readonly RefactoryCandidateRow[]> | null
  readonly finished: boolean
  readonly signal: AbortSignal
  readonly t: TranslateNS<'sidebarRefactory'>
  readonly readCandidates: RefactoryInjected['readCandidates']
  readonly controlRun: RefactoryInjected['controlRun']
}): ReactNode {
  return (
    <div className={css.status} data-refactory-candidates={candidates?.kind ?? 'none'}>
      <div className={css.packageActions}>
        <button
          type="button"
          className={css.action}
          data-refactory-candidates-read
          onClick={() => { readCandidates(tabId, run, signal) }}
        >
          <IconRefreshOutline16 />
          {t('candidates.read')}
        </button>
        <button
          type="button"
          className={css.action}
          data-refactory-control="adopt"
          onClick={() => { controlRun(tabId, run, { control: 'adopt' }, signal) }}
        >
          {t('control.adopt')}
        </button>
      </div>
      {candidates?.kind === 'loading' && (
        <p className={css.note}>{t('candidates.reading')}</p>
      )}
      {candidates?.kind === 'failed' && (
        <p className={css.note}>{t('error.failed', { message: candidates.message })}</p>
      )}
      {candidates?.kind === 'ready' && (candidates.value.length === 0
        ? <p className={css.note} data-refactory-candidates-empty>{t('candidates.empty')}</p>
        : (
          <ul className={css.recorded} data-refactory-candidates-rows>
            {candidates.value.map(row => (
              <li
                key={row.id}
                className={css.package}
                data-refactory-candidate={row.id}
                data-refactory-kept={String(row.kept)}
                data-refactory-rejected={String(row.rejected)}
              >
                <span className={css.name}>{row.id}</span>{' '}
                {row.score === null ? t('candidates.unscored') : String(row.score)}
                {row.kept && <span className={css.note}> · {t('candidate.kept')}</span>}
                {row.rejected && <span className={css.note}> · {t('candidate.rejected')}</span>}
                <div className={css.gestures}>
                  <button
                    type="button"
                    className={css.action}
                    disabled={finished || row.kept}
                    data-refactory-candidate-keep={row.id}
                    onClick={() => {
                      controlRun(tabId, run, { control: 'keep', target: row.id }, signal)
                    }}
                  >
                    {t('candidate.keep')}
                  </button>
                  <button
                    type="button"
                    className={css.action}
                    disabled={finished || row.rejected}
                    data-refactory-candidate-reject={row.id}
                    onClick={() => {
                      controlRun(tabId, run, { control: 'reject', target: row.id }, signal)
                    }}
                  >
                    {t('candidate.reject')}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        ))}
    </div>
  )
}

/** The watched run's intent ledger, and the gates it is stopped at. */
function Steering({
  tabId, run, steering, candidates, optimizer, signal, t, readSteering, readCandidates, addNote,
  answerGate, controlRun, statusFinished,
}: {
  readonly tabId: Parameters<RefactoryInjected['refresh']>[0]
  readonly run: string
  readonly steering: Loadable<RefactorySteering> | null
  readonly candidates: Loadable<readonly RefactoryCandidateRow[]> | null
  readonly optimizer: boolean | null
  readonly signal: AbortSignal
  readonly t: TranslateNS<'sidebarRefactory'>
  readonly readSteering: RefactoryInjected['readSteering']
  readonly readCandidates: RefactoryInjected['readCandidates']
  readonly addNote: RefactoryInjected['addNote']
  readonly answerGate: RefactoryInjected['answerGate']
  readonly controlRun: RefactoryInjected['controlRun']
  readonly statusFinished: boolean | undefined
}): ReactNode {
  const [said, setSaid] = useState('')
  const [kind, setKind] = useState<RefactoryNoteKind>('info')
  if (steering === null || steering.kind === 'loading') {
    return <p className={css.note} data-refactory-steering="reading">{t('steering.reading')}</p>
  }
  if (steering.kind === 'failed') {
    return (
      <p className={css.note} data-refactory-steering="failed">
        {t('error.failed', { message: steering.message })}
      </p>
    )
  }
  const ledger = steering.value
  const finished = statusFinished === true || ledger.finished === true
  return (
    <div className={css.status} data-refactory-steering="ready">
      {optimizer && finished && <p className={css.note} data-refactory-controls-finished>{t('control.finished')}</p>}
      {ledger.control != null && (
        <p className={css.note} data-refactory-control-receipt={ledger.control.status}>
          {t(`control.${ledger.control.status}`, { action: ledger.control.action })}
          {ledger.control.target && <> {ledger.control.target}</>}
          {ledger.control.as && <> · {ledger.control.as}</>}
          {ledger.control.recovered && <> {t('control.recovered')}</>}
          {ledger.control.recovered_control && (
            <> {t('control.recovered')} {ledger.control.recovered_control.action} {ledger.control.recovered_control.target}</>
          )}
          {ledger.control.candidate && <> {ledger.control.candidate}</>}
          {ledger.control.package_root && <> · {ledger.control.package_root}</>}
          {ledger.control.message && <> · {ledger.control.message}</>}
          {ledger.control.error && <> · {ledger.control.error}</>}
        </p>
      )}
      <p className={css.note} data-refactory-steering-run>{ledger.run}</p>
      <div className={css.packageActions}>
        <button
          type="button"
          className={css.action}
          data-refactory-steering-refresh
          onClick={() => { readSteering(tabId, run, signal) }}
        >
          <IconRefreshOutline16 />
          {t('steering.refresh')}
        </button>
        {optimizer && <button
          type="button"
          className={css.action}
          data-refactory-control="pause"
          disabled={finished}
          onClick={() => { controlRun(tabId, run, { control: 'pause' }, signal) }}
        >
          {t('control.pause')}
        </button>}
        {optimizer && <button
          type="button"
          className={css.action}
          data-refactory-control="resume"
          disabled={finished}
          onClick={() => { controlRun(tabId, run, { control: 'resume' }, signal) }}
        >
          {t('control.resume')}
        </button>}
        {optimizer && <button
          type="button"
          className={css.action}
          data-refactory-control="stop"
          disabled={finished}
          onClick={() => { controlRun(tabId, run, { control: 'stop' }, signal) }}
        >
          {t('control.stop')}
        </button>}
      </div>
      {ledger.entries.length === 0
        ? <p className={css.note} data-refactory-ledger="empty">{t('steering.empty')}</p>
        : (
          <ul className={css.recorded} data-refactory-ledger="entries">
            {ledger.entries.map((entry, index) => (
              <li key={`${entry.at}-${String(index)}`} data-refactory-entry={entry.kind}>
                <span className={css.name}>{entry.kind}</span> {entry.text}
              </li>
            ))}
          </ul>
        )}
      {optimizer && <Candidates
        tabId={tabId}
        run={run}
        candidates={candidates}
        finished={finished}
        signal={signal}
        t={t}
        readCandidates={readCandidates}
        controlRun={controlRun}
      />}
      {optimizer === false && <p className={css.note}>{t('steering.graphControls')}</p>}
      {optimizer === null && <p className={css.note}>{t('steering.unknownControls')}</p>}
      {ledger.problems.length > 0 && (
        <ul className={css.recorded} data-refactory-ledger="problems">
          {ledger.problems.map((problem, index) => (
            <li key={String(index)} className={css.note} data-refactory-problem>{problem}</li>
          ))}
        </ul>
      )}
      {optimizer !== null && <form
        className={css.import}
        onSubmit={(event) => {
          event.preventDefault()
          const note = said.trim()
          if (note === '') return
          addNote(tabId, run, note, kind, signal)
          setSaid('')
        }}
      >
        <input
          className={css.input}
          value={said}
          aria-label={t('steering.note')}
          placeholder={t('steering.note')}
          data-refactory-note
          onChange={(event) => { setSaid(event.target.value) }}
        />
        <select
          className={css.control}
          value={kind}
          aria-label={t('steering.kind')}
          data-refactory-note-kind
          onChange={(event) => { setKind(event.target.value as RefactoryNoteKind) }}
        >
          {NOTE_KINDS.map(option => (
            <option key={option} value={option}>{t(`kind.${option}`)}</option>
          ))}
        </select>
        <button
          type="submit"
          className={css.action}
          disabled={said.trim() === ''}
          data-refactory-note-add
        >
          {t('steering.add')}
        </button>
      </form>}
      {optimizer !== null && ledger.gates.length > 0 && (
        <div className={css.status} data-refactory-gates="waiting">
          {ledger.gates.map(gate => (
            <div key={gate.id} data-refactory-gate={gate.id}>
              <p className={css.statusLine} data-refactory-gate-prompt>
                {t('steering.gate', { gate: gate.id })}
              </p>
              {gate.prompt !== '' && <p className={css.note}>{gate.prompt}</p>}
              <div className={css.gestures}>
                {gate.verdicts.map(option => (
                  <button
                    key={option}
                    type="button"
                    className={css.action}
                    data-refactory-verdict={option}
                    onClick={() => { answerGate(tabId, run, gate.id, option, signal) }}
                  >
                    {option}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** The picker's body: the described choices, and the run they start. */
export function RefactoryBody({
  useTabInfo, useStore, choose, setKnob, setSetting, start, run: runOnce, load, reload, refresh, watch, stop,
  readSteering, readCandidates, addNote, answerGate, controlRun, setEnabled, remove,
  importPackage, t,
}: RefactoryBodyProps): ReactNode {
  const { tab } = useTabInfo()
  const { signal } = tab
  const state = useStore(store => store.byTab[tab.id])
  const [source, setSource] = useState('')
  const [name, setName] = useState('')
  const [watchedPath, setWatchedPath] = useState('')
  useEffect(() => { setWatchedPath(state?.watching ?? '') }, [state?.watching])
  const chosen = state?.choices.kind === 'ready'
    ? state.choices.value.modes.find(mode => mode.id === state.chosen)
    : undefined
  // What both gestures send: every declared knob, with the declaration's own value
  // under anything a person has not changed.
  const knobs = useMemo(() => knobValues(chosen, state?.knobs), [chosen, state?.knobs])
  useEffect(() => {
    // A bucket gone because the record aborted must not be re-seeded by a
    // component that has not unmounted yet.
    if (state !== undefined || signal.aborted) return
    load(tab.id, signal)
  }, [state, tab.id, signal, load])

  if (state === undefined) return null
  const choices = state.choices
  if (choices.kind !== 'ready') {
    return (
      <div className={css.status} data-refactory-state={choices.kind}>
        <p className={css.statusLine}>
          {choices.kind === 'loading' ? t('choices.loading') : t('error.failed', { message: choices.message })}
        </p>
      </div>
    )
  }
  const { modes, strategies, packages } = choices.value
  const selected = chosen
  const run = state.watching
  return (
    <div className={css.root} data-refactory-state="ready">
      <div className={css.header}>
        <IconDatabaseOutline16 className={css.icon} />
        <span className={css.name}>{t('type.label')}</span>
        <button
          type="button"
          className={css.tool}
          aria-label={t('reload')}
          title={t('reload')}
          data-refactory-reload
          onClick={() => { reload(tab.id, signal) }}
        >
          <IconRefreshOutline16 />
        </button>
      </div>
      {state.notice !== null && (
        <p className={css.notice} data-refactory-notice={state.notice.code}>{failureLine(t, state.notice)}</p>
      )}

      <section className={css.section}>
        <h2 className={css.heading}>{t('run.heading')}</h2>
        <div className={css.field}>
          <label className={css.label} htmlFor="refactory-mode">{t('mode.label')}</label>
          <select
            id="refactory-mode"
            className={css.control}
            value={state.chosen ?? ''}
            data-refactory-mode
            onChange={(event) => { choose(tab.id, event.target.value === '' ? null : event.target.value) }}
          >
            <option value="">{t('mode.choose')}</option>
            {modes.map(mode => (
              <option key={mode.id} value={mode.id}>{mode.id}</option>
            ))}
          </select>
          {selected !== undefined && (
            <p className={css.note} data-refactory-mode-note={selected.action}>
              {selected.description}
            </p>
          )}
        </div>
        {knobs.map(({ name: knob, knob: declaration, value }) => (
          <KnobField
            key={knob}
            name={knob}
            knob={declaration}
            value={value}
            t={t}
            onChange={(changed) => { setKnob(tab.id, knob, changed) }}
          />
        ))}
        {selected !== undefined && selected.enabled && selected.action !== 'start' && (
          <p className={css.note} data-refactory-authoring>
            {t('mode.authoring', { action: selected.action })}
          </p>
        )}
        {selected !== undefined && !selected.enabled && (
          <div>
            <p className={css.note} data-refactory-disabled>{t('mode.disabled')}</p>
            <button
              type="button"
              className={css.action}
              disabled={state.busy}
              data-refactory-enable-selected
              onClick={() => { setEnabled(tab.id, selected.package, true, signal) }}
            >
              {t('packages.enable')}
            </button>
          </div>
        )}
        <Seam
          id="strategy"
          label={t('strategy.label')}
          value={state.settings.strategy}
          names={strategies.outer_loops}
          t={t}
          onChoose={(value) => { setSetting(tab.id, 'strategy', value) }}
        />
        <Seam
          id="optimizer"
          label={t('optimizer.label')}
          value={state.settings.optimizer}
          names={strategies.optimizers}
          t={t}
          onChoose={(value) => { setSetting(tab.id, 'optimizer', value) }}
        />
        <Seam
          id="evaluator"
          label={t('evaluator.label')}
          value={state.settings.evaluator}
          names={strategies.evaluators}
          t={t}
          onChoose={(value) => { setSetting(tab.id, 'evaluator', value) }}
        />
        {selected !== undefined && (
          <div className={css.gestures}>
            <button
              type="button"
              className={css.action}
              disabled={!selected.enabled || selected.action !== 'start'}
              data-refactory-start
              onClick={() => { start(tab.id, selected.id, knobs, state.settings, signal) }}
            >
              <IconPlayOutline16 />
              {t('start')}
            </button>
            {selected.action === 'start' && (
              <button
                type="button"
                className={css.action}
                disabled={!selected.enabled || state.single?.kind === 'running'}
                data-refactory-run-once
                onClick={() => { runOnce(tab.id, selected.id, knobs, signal) }}
              >
                <IconPlayOutline16 />
                {t('runOnce')}
              </button>
            )}
          </div>
        )}
        {state.single !== null && <Single run={state.single} t={t} />}
      </section>

      <section className={css.section}>
        <h2 className={css.heading}>{t('status.heading')}</h2>
        <form className={css.import} onSubmit={(event) => {
          event.preventDefault()
          watch(tab.id, watchedPath, signal)
        }}>
          <input
            className={css.input}
            value={watchedPath}
            aria-label={t('status.runPath')}
            placeholder={t('status.runPath')}
            data-refactory-watch-path
            onChange={(event) => { setWatchedPath(event.target.value) }}
          />
          <button type="submit" className={css.action} disabled={watchedPath.trim() === ''} data-refactory-watch>
            {t('status.watch')}
          </button>
        </form>
        <p className={css.note}>{t('status.watchHelp')}</p>
        {run !== null && (
          // Which run the gesture produced: the id is what `console status --run`
          // and `wf_state` take, so a person can follow it outside the panel.
          <p className={css.note} data-refactory-started>{t('watching', { run })}</p>
        )}
        {run === null
          ? <p className={css.note} data-refactory-status="none">{t('status.none')}</p>
          : (
            <Status
              tabId={tab.id}
              frame={state.frame}
              run={run}
              signal={signal}
              t={t}
              refresh={refresh}
              stop={stop}
            />
          )}
      </section>

      <section className={css.section}>
        <h2 className={css.heading}>{t('steering.heading')}</h2>
        {run === null
          ? <p className={css.note} data-refactory-steering="none">{t('steering.none')}</p>
          : (
            <Steering
              key={run}
              tabId={tab.id}
              run={run}
              steering={state.steering}
              candidates={state.candidates}
              statusFinished={state.frame?.kind === 'ready' ? state.frame.value.finished : undefined}
              optimizer={state.frame?.kind === 'ready' && state.frame.value.kind !== 'unknown'
                ? state.frame.value.kind === 'optimizer' : null}
              signal={signal}
              t={t}
              readSteering={readSteering}
              readCandidates={readCandidates}
              addNote={addNote}
              answerGate={answerGate}
              controlRun={controlRun}
            />
          )}
      </section>

      <section className={css.section}>
        <h2 className={css.heading}>{t('packages.heading')}</h2>
        <ul className={css.packages}>
          {packages.map(pkg => (
            <li
              key={pkg.name}
              className={css.package}
              data-refactory-package={pkg.name}
              data-refactory-enabled={pkg.enabled === null ? 'unknown' : String(pkg.enabled)}
            >
              <div className={css.packageHead}>
                <span className={css.name}>{pkg.name}</span>
                <span className={css.count}>{t('packages.count', { count: pkg.modes.length })}</span>
              </div>
              <span className={css.state}>
                {pkg.enabled === null
                  ? t('packages.unknown')
                  : pkg.enabled ? t('packages.enabled') : t('packages.disabled')}
              </span>
              <div className={css.packageActions}>
                {pkg.enabled !== null && (
                  <button
                    type="button"
                    className={css.action}
                    disabled={state.busy}
                    data-refactory-toggle={pkg.name}
                    onClick={() => { setEnabled(tab.id, pkg.name, !pkg.enabled, signal) }}
                  >
                    {pkg.enabled ? t('packages.disable') : t('packages.enable')}
                  </button>
                )}
                <button
                  type="button"
                  className={css.action}
                  disabled={state.busy}
                  data-refactory-remove={pkg.name}
                  onClick={() => { remove(tab.id, pkg.name, signal) }}
                >
                  {t('packages.remove')}
                </button>
              </div>
              <ul className={css.packageModes}>
                {pkg.modes.map(mode => <li key={mode.name} data-refactory-package-mode={mode.name}>{mode.name}</li>)}
              </ul>
            </li>
          ))}
        </ul>
        <form
          className={css.import}
          onSubmit={(event) => {
            event.preventDefault()
            importPackage(tab.id, source, name, signal)
            setSource('')
            setName('')
          }}
        >
          <input
            className={css.input}
            value={source}
            aria-label={t('packages.importSource')}
            placeholder={t('packages.importSource')}
            data-refactory-import-source
            onChange={(event) => { setSource(event.target.value) }}
          />
          <input
            className={css.input}
            value={name}
            aria-label={t('packages.importName')}
            placeholder={t('packages.importName')}
            data-refactory-import-name
            onChange={(event) => { setName(event.target.value) }}
          />
          <button
            type="submit"
            className={css.action}
            disabled={state.busy || source.trim() === ''}
            data-refactory-import
          >
            {t('packages.import')}
          </button>
        </form>
      </section>
    </div>
  )
}
