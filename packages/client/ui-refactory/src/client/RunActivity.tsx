import type { ReactNode } from 'react'
import type { RefactoryActivity } from '@deepseek-ai/dsh-refactory-console/types'
import css from './RefactoryBody.module.css'

const shown = (value: unknown): string => value == null ? 'unavailable' : typeof value === 'string' ? value : JSON.stringify(value)

/** Runtime receipts are evidence of dispatch/delivery, never proof of compliance. */
export function RunActivity({ activity }: { readonly activity: RefactoryActivity | null | undefined }): ReactNode {
  if (activity == null) return null
  return <section className={css.status} aria-label="Live graph activity" data-refactory-activity>
    <h3 className={css.heading}>Live graph activity</h3>
    <p>Last event: {activity.last_event_at ?? 'no event recorded'}</p>
    <p>Waiting: {activity.wait_reason ?? 'no wait recorded'}</p>
    <p>Current nodes: {activity.nodes.join(', ') || 'none'}</p>
    <p>Completed: {activity.completed.join(', ') || 'none recorded'}</p>
    <p className={css.note}>Progress has no estimated percentage. Reopening this view only reads the saved run.</p>
    {activity.resources == null ? <p>Resource usage: unavailable</p>
      : <details><summary>Resource usage and limits</summary><pre>{JSON.stringify(activity.resources, null, 2)}</pre></details>}
    {activity.workers.map((worker, index) => <article key={String(worker.worker_id ?? worker.id ?? index)} data-refactory-worker>
      <p>{shown(worker.node_id || worker.node || worker.role)} · {shown(worker.state ?? worker.status)}</p>
      <p>Actual route: {shown(worker.actual_route)} · Runtime: {shown(worker.runtime ?? worker.version)}</p>
      <p>Usage: {shown(worker.usage)}</p>
      <details><summary>Worker capabilities and requested route</summary><pre>{JSON.stringify(worker, null, 2)}</pre></details>
    </article>)}
    {activity.deliveries.length > 0 && <details open><summary>Instruction delivery</summary>
      <p className={css.note}>A supplied context is not proof of dispatch or of output compliance.</p>
      <ul>{activity.deliveries.map((delivery, index) => <li key={String(delivery.worker_id ?? index)}>
        {shown(delivery.node_id)} · {shown(delivery.state)} · revision {shown(delivery.context_revision)}
      </li>)}</ul>
    </details>}
    {activity.control != null && <p role="status">Control: {shown(activity.control)}</p>}
    <details><summary>Outputs and validation</summary><pre>{JSON.stringify(activity.outputs, null, 2)}</pre></details>
    <details><summary>Activity history ({activity.cursor})</summary><ol>
      {activity.events.map((event, index) => <li key={index}>{shown(event.at)} · {shown(event.node_id ?? event.kind)} · {shown(event.state ?? event.status)} {event.summary == null ? '' : shown(event.summary)}</li>)}
    </ol></details>
  </section>
}
