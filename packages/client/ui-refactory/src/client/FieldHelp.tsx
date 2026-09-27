import { useEffect, useId, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import css from './RefactoryBody.module.css'

/** Help stays out of the form's reading flow, but works with pointer, keyboard and touch. */
export function FieldHelp({ label, children }: { label: string; children: ReactNode }) {
  const id = useId()
  const button = useRef<HTMLButtonElement>(null)
  const leaveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const [position, setPosition] = useState<{ left: number; top: number; above: boolean } | null>(null)
  const show = (): void => {
    clearTimeout(leaveTimer.current)
    const rect = button.current?.getBoundingClientRect()
    if (!rect) return
    const width = Math.min(300, window.innerWidth - 32)
    setPosition({ left: Math.max(16, Math.min(rect.left, window.innerWidth - width - 16)),
      top: rect.bottom + 8, above: window.innerHeight - rect.bottom < 200 })
  }
  const hide = (): void => { leaveTimer.current = setTimeout(() => { setPosition(null) }, 180) }
  const visible = position !== null
  useEffect(() => {
    if (!visible) return
    const dismiss = (): void => { setPosition(null) }
    const escape = (event: KeyboardEvent): void => { if (event.key === 'Escape') dismiss() }
    window.addEventListener('resize', dismiss)
    window.addEventListener('scroll', dismiss, true)
    document.addEventListener('keydown', escape)
    return () => {
      window.removeEventListener('resize', dismiss)
      window.removeEventListener('scroll', dismiss, true)
      document.removeEventListener('keydown', escape)
    }
  }, [visible])
  useEffect(() => () => { clearTimeout(leaveTimer.current) }, [])
  return <span className={css.helpAnchor} onMouseEnter={show} onMouseLeave={hide}>
    <button ref={button} type="button" className={css.helpButton} aria-label={`${label} — help`}
      aria-describedby={position ? id : undefined} onFocus={show} onBlur={() => { setPosition(null) }}
      onClick={show} onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); setPosition(null) } }}>?</button>
    {position && createPortal(<span id={id} role="tooltip" className={css.tooltip}
      style={{ left: position.left, top: position.above ? undefined : position.top,
        bottom: position.above ? window.innerHeight - position.top + 38 : undefined }}
      onMouseEnter={show} onMouseLeave={hide}>{children}</span>, document.body)}
  </span>
}

export function FieldLabel({ htmlFor, children, help }: { htmlFor: string; children: string; help: string }) {
  return <div className={css.fieldLabel}><label className={css.label} htmlFor={htmlFor}>{children}</label>
    <FieldHelp label={children}>{help}</FieldHelp></div>
}
