import { useEffect, useId, useRef, useState } from 'react'
import type { TranslateNS } from '@deepseek-ai/dsh-client-ui-slots'
import css from './RefactoryBody.module.css'

/** A deliberate draft: Cancel never overwrites the run's saved input. */
export function BriefEditor({ id, value, description, onChange, t }: {
  id: string; value: string; description: string; onChange: (value: string) => void; t: TranslateNS<'sidebarRefactory'>
}) {
  const title = useId(), hint = useId()
  const dialog = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(value)
  useEffect(() => {
    if (open) dialog.current?.showModal()
  }, [open])
  const close = (): void => { dialog.current?.close(); setOpen(false); trigger.current?.focus() }
  const apply = (): void => { onChange(draft); close() }
  return <>
    <button ref={trigger} id={id} type="button" className={css.briefPreview} data-refactory-knob-input="brief"
      aria-haspopup="dialog" onClick={() => { setDraft(value); setOpen(true) }}>
      <span className={css.briefText}>{value || t('brief.placeholder')}</span>
      <span className={css.briefEdit}>{t('brief.expand')} ↗</span>
    </button>
    <dialog ref={dialog} className={css.briefDialog} aria-labelledby={title} aria-describedby={hint}
      onCancel={event => { event.preventDefault(); close() }}
      onKeyDown={event => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') { event.preventDefault(); apply() } }}>
      <div className={css.briefHeader}><h2 id={title}>{t('brief.title')}</h2><p id={hint}>{description || t('brief.hint')}</p></div>
      <label className={css.srOnly} htmlFor={`${id}-draft`}>{t('brief.title')}</label>
      <textarea id={`${id}-draft`} className={css.briefDraft} autoFocus value={draft}
        placeholder={t('brief.hint')} onChange={event => { setDraft(event.target.value) }} />
      <div className={css.briefFooter}><span>{t('brief.count', { count: draft.length })} · {t('brief.shortcut')}</span>
        <div className={css.gestures}><button type="button" className={css.action} onClick={close}>{t('brief.cancel')}</button>
          <button type="button" className={`${css.action} ${css.primary}`} onClick={apply}>{t('brief.apply')}</button></div>
      </div>
    </dialog>
  </>
}
