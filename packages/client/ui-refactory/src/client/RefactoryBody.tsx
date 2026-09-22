/**
 * The picker's body: one described choice per seam, and the run they start.
 *
 * Everything the picker keeps lives in its store, keyed by tab; everything it
 * asks for goes through its injected face. The body only decides what to draw
 * for each read and what a gesture means: choosing a mode, choosing a level-two
 * name, starting the chosen mode, refreshing or stopping the run being read, and
 * enabling, disabling, removing or importing a package.
 *
 * Nothing here is free text except the two import fields, which the registry
 * itself requires: a mode, a strategy and a package are always chosen from what
 * the Host listed, never typed. A mode the loop cannot search is offered but not
 * startable: the row says which action runs it, and the Host refuses what the
 * loop cannot search.
 */
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { RefactoryRunStatus, RefactoryStrategyChoice } from '@deepseek-ai/dsh-api-remotes/client'
import type { PropsLocale, PropsRuntime, PropsStore, TranslateNS } from '@deepseek-ai/dsh-client-ui-slots'
import {
  IconDatabaseOutline16, IconPlayOutline16, IconRefreshOutline16, IconStopFill16,
} from '@deepseek-ai/dsh-client-ui-primitives'
import type {} from './locales.ts'
import type { RefactoryInjected } from './face.ts'
import type { Loadable, RefactoryNotice, RefactorySettingName, createRefactoryStore } from './store.ts'
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
        {status.best_score === null ? t('status.noScore') : t('status.best', { score: status.best_score })}
      </p>
      <p className={css.statusLine} data-refactory-evaluations>
        {t('status.evaluations', { count: status.counters.evaluations })}
      </p>
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
        {!status.finished && (
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

/** The picker's body: the described choices, and the run they start. */
export function RefactoryBody({
  useTabInfo, useStore, choose, setSetting, start, load, reload, refresh, stop,
  setEnabled, remove, importPackage, t,
}: RefactoryBodyProps): ReactNode {
  const { tab } = useTabInfo()
  const { signal } = tab
  const state = useStore(store => store.byTab[tab.id])
  const [source, setSource] = useState('')
  const [name, setName] = useState('')
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
  const selected = modes.find(mode => mode.id === state.chosen)
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
        {selected !== undefined && selected.action !== 'start' && (
          <p className={css.note} data-refactory-authoring>
            {t('mode.authoring', { action: selected.action })}
          </p>
        )}
        {selected !== undefined && selected.action === 'start' && !selected.enabled && (
          <p className={css.note} data-refactory-disabled>{t('mode.disabled')}</p>
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
          <button
            type="button"
            className={css.action}
            data-refactory-start
            onClick={() => { start(tab.id, selected.id, state.settings, signal) }}
          >
            <IconPlayOutline16 />
            {t('start')}
          </button>
        )}
      </section>

      <section className={css.section}>
        <h2 className={css.heading}>{t('status.heading')}</h2>
        {run !== null && (
          // Which run the gesture produced: the id is what `console status --run`
          // and `wf_state` take, so a person can follow it outside the panel.
          <p className={css.note} data-refactory-started>{t('started', { run })}</p>
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
