import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { RefactoryRunSummary } from '@deepseek-ai/dsh-refactory-console/types'
import type { RefactoryInjected } from './face.ts'
import css from './RefactoryBody.module.css'

export function GraphControls({ run, signal, control }: {
  readonly run: string; readonly signal: AbortSignal; readonly control: NonNullable<RefactoryInjected['graphControl']>
}): ReactNode {
  const [node, setNode] = useState('')
  const [correction, setCorrection] = useState('')
  const [receipt, setReceipt] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState('')
  const act = async (action: string): Promise<void> => {
    setError('')
    try { setReceipt(await control({ run, action, node, text: correction,
      ...action === 'rerun' && typeof receipt?.revision === 'string' ? { revision: receipt.revision } : {},
    }, signal)) } catch (error) { setError(error instanceof Error ? error.message : String(error)) }
  }
  return <section className={css.status} aria-label="Graph controls">
    <p className={css.note}>Controls apply at the next graph boundary. Continue in the owning session after resume. In-flight worker steering and termination are only available when the backend advertises them.</p>
    <div className={css.gestures}>{['pause', 'resume', 'stop'].map(action => <button type="button" className={css.action} key={action} onClick={() => { void act(action) }}>{action}</button>)}</div>
    <label>Completed node to correct<input className={css.input} value={node} onChange={event => { setNode(event.target.value); setReceipt(null) }} /></label>
    <label>Prompt correction<textarea className={css.input} value={correction} onChange={event => { setCorrection(event.target.value); setReceipt(null) }} /></label>
    <button type="button" className={css.action} disabled={!node.trim()} onClick={() => { void act('preview') }}>Preview affected results</button>
    {receipt?.status === 'preview' && <>
      <p>Results that become stale: {Array.isArray(receipt.invalidated) ? receipt.invalidated.join(', ') : 'unavailable'}</p>
      <button type="button" className={css.action} onClick={() => { void act('rerun') }}>Confirm correction and rerun</button>
    </>}
    {receipt !== null && <p role="status">{String(receipt.status)}: {String(receipt.message)}</p>}
    {error !== '' && <p role="alert">{error}</p>}
  </section>
}

export function RunList({ signal, read, watch }: {
  readonly signal: AbortSignal; readonly read: NonNullable<RefactoryInjected['listRuns']>; readonly watch: (run: string) => void
}): ReactNode {
  const [runs, setRuns] = useState<readonly RefactoryRunSummary[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const update = async (): Promise<void> => {
      try { const result = await read(signal); if (!disposed) { setRuns(result); setError('') } }
      catch (error) { if (!disposed) setError(error instanceof Error ? error.message : String(error)) }
      if (!disposed && !signal.aborted) timer = setTimeout(() => { void update() }, 3000)
    }
    void update()
    return () => { disposed = true; clearTimeout(timer) }
  }, [read, signal])
  return <details><summary>Runs and pending questions</summary>
    {error !== '' && <p role="alert">{error}</p>}
    {runs.length === 0 && <p>No saved runs in this project.</p>}
    <ul>{runs.map(run => <li key={run.root}>
      <button type="button" className={css.action} onClick={() => { watch(run.root) }}>{run.mode ?? run.run_id}</button>
      {' '} {run.state ?? (run.finished ? 'finished' : 'in progress')} · {run.pending_questions ?? 0} pending questions
    </li>)}</ul>
  </details>
}
