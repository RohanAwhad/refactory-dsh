import { useEffect, useId, useRef, useState } from 'react'
import type { RefactorySteering } from '@deepseek-ai/dsh-refactory-console/types'
import type { TranslateNS } from '@deepseek-ai/dsh-client-ui-slots'
import type { Loadable } from './store.ts'
import css from './RefactoryBody.module.css'

/** All recorded text stays readable while live updates or a manual refresh arrive. */
export function SteeringLedger({ steering, refresh, t }: {
  steering: Loadable<RefactorySteering> | null
  refresh: () => void
  t: TranslateNS<'sidebarRefactory'>
}) {
  const title = useId(), hint = useId(), searchId = useId()
  const dialog = useRef<HTMLDialogElement>(null), trigger = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false), [query, setQuery] = useState('')
  const [previous, setPrevious] = useState<RefactorySteering | null>(null)
  const current = steering?.kind === 'ready' ? steering.value : null
  useEffect(() => { if (current) setPrevious(current) }, [current])
  useEffect(() => { if (open) dialog.current?.showModal() }, [open])
  const ledger = current ?? previous
  const entries = ledger?.entries ?? []
  const needle = query.trim().toLocaleLowerCase()
  const matches = entries.map((entry, index) => ({ entry, index })).filter(({ entry }) =>
    !needle || `${entry.kind}\n${entry.at}\n${entry.text}`.toLocaleLowerCase().includes(needle))
  const close = (): void => { dialog.current?.close(); setOpen(false); trigger.current?.focus() }
  const reading = steering === null || steering.kind === 'loading'
  const latest = entries.at(-1)
  return <>
    <button ref={trigger} type="button" className={css.ledgerPreview} data-refactory-ledger-open aria-haspopup="dialog"
      aria-label={`${t('ledger.open')} · ${t('ledger.count', { count: entries.length })}`}
      onClick={() => { setQuery(''); setOpen(true) }}>
      <span className={css.ledgerPreviewTitle}>{t('ledger.open')} <span aria-hidden="true">↗</span></span>
      <span className={css.note}>{t('ledger.count', { count: entries.length })}</span>
      {latest && <span className={css.briefText}>{latest.text}</span>}
      {!reading && steering?.kind !== 'failed' && !latest && <span data-refactory-ledger="empty">{t('steering.empty')}</span>}
    </button>
    <dialog ref={dialog} className={`${css.briefDialog} ${css.ledgerDialog}`} aria-labelledby={title} aria-describedby={hint}
      onCancel={event => { event.preventDefault(); close() }}>
      <header className={css.briefHeader}><h2 id={title}>{t('ledger.title')}</h2><p id={hint}>{t('ledger.hint')}</p></header>
      {ledger && <p className={css.ledgerPath}>{ledger.ledger}</p>}
      <div className={css.ledgerToolbar}>
        <label className={css.srOnly} htmlFor={searchId}>{t('ledger.search')}</label>
        <input id={searchId} type="search" autoFocus className={css.input} value={query} placeholder={t('ledger.search')}
          onChange={event => { setQuery(event.target.value) }} />
        <button type="button" className={css.action} disabled={reading} onClick={refresh}>{t('steering.refresh')}</button>
      </div>
      <p className={css.note} role="status">{needle ? t('ledger.matches', { count: matches.length, total: entries.length }) : t('ledger.count', { count: entries.length })}
        {reading && <> · {t('steering.reading')}</>}</p>
      {steering?.kind === 'failed' && <p className={css.notice} role="alert">{t('error.failed', { message: steering.message })}
        {ledger && <> {t('ledger.saved')}</>}</p>}
      <div className={css.ledgerScroll}>
        {open && (ledger === null ? (reading ? <p className={css.note}>{t('steering.reading')}</p> : null)
          : entries.length === 0 ? <p className={css.note}>{t('steering.empty')}</p>
          : matches.length === 0 ? <p className={css.note}>{t('ledger.noMatches')}</p>
            : <ol className={css.ledgerEntries} data-refactory-ledger="entries">
              {matches.map(({ entry, index }) => <li key={`${index}-${entry.at}`} data-refactory-entry={entry.kind}>
                <div className={css.ledgerMeta}><span className={css.ledgerKind}>{entry.kind}</span>
                  <time dateTime={entry.at}>{entry.at}</time><span>#{index + 1}</span></div>
                <p className={css.ledgerText}>{entry.text}</p>
              </li>)}
            </ol>)}
      </div>
      <footer className={css.ledgerFooter}><button type="button" className={css.action} onClick={close}>{t('ledger.close')}</button></footer>
    </dialog>
  </>
}
