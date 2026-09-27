import type { ReactNode } from 'react'
import type { RefactoryActivity } from '@deepseek-ai/dsh-refactory-console/types'
import css from './RefactoryBody.module.css'
import { RunGraph } from './RunGraph.tsx'

const shown = (value: unknown): string => value == null ? 'unavailable' : typeof value === 'string' ? value : JSON.stringify(value)

/** Runtime receipts are evidence of dispatch/delivery, never proof of compliance. */
export function RunActivity({ activity, state = '' }: { readonly state?: string; readonly activity: RefactoryActivity | null | undefined }): ReactNode {
  if (activity == null) return null
  return <section className={css.status} aria-label="Live graph activity" data-refactory-activity>
    <h3 className={css.heading}>Live graph activity</h3>
    <RunGraph activity={activity} state={state} />
    {!activity.graph && <ul className={css.nodeTrack} aria-label="Recorded nodes">
      {activity.completed.map(node => <li className={css.node} data-state="complete" key={'done-' + node}><span aria-hidden="true">✓</span>{node}<span className={css.note}>complete</span></li>)}
      {activity.nodes.filter(node => !activity.completed.includes(node)).map(node => <li className={css.node} data-state="current" key={node}><span aria-hidden="true">◇</span>{node}<span className={css.note}>current</span></li>)}
    </ul>}
    <dl className={css.activityMeta}>
      <dt>Last event</dt><dd>{activity.last_event_at ?? 'no event recorded'}</dd>
      <dt>Waiting</dt><dd>{activity.wait_reason ?? 'no wait recorded'}</dd>
      <dt>Current nodes</dt><dd>{activity.nodes.join(', ') || 'none'}</dd>
      <dt>Completed</dt><dd>{activity.completed.length ? activity.completed.length + ' recorded' : 'none recorded'}</dd>
    </dl>
    <p className={css.note}>Recorded activity, without an estimated percentage. Opening this view only reads the saved run.</p>
    {activity.resources == null ? <p className={css.note}>Resource usage: unavailable</p>
      : <details className={css.disclosure}><summary>Resource usage and limits</summary><pre>{JSON.stringify(activity.resources, null, 2)}</pre></details>}
    {activity.workers.map((worker, index) => <article className={css.package} key={String(worker.worker_id ?? worker.id ?? index)} data-refactory-worker>
      <p className={css.name}>{shown(worker.node_id || worker.node || worker.role)} · {shown(worker.state ?? worker.status)}</p>
      <p className={css.note}>Actual route: {shown(worker.actual_route)} · Runtime: {shown(worker.runtime ?? worker.version)}</p>
      <p className={css.note}>Usage: {shown(worker.usage)}</p>
      <details><summary>Worker capabilities and requested route</summary><pre>{JSON.stringify(worker, null, 2)}</pre></details>
    </article>)}
    {activity.deliveries.length > 0 && <details><summary>Instruction delivery</summary>
      <p className={css.note}>A supplied context is not proof of dispatch or of output compliance.</p>
      <ul className={css.recorded}>{activity.deliveries.map((delivery, index) => <li key={String(delivery.worker_id ?? index)}>
        {shown(delivery.node_id)} · {shown(delivery.state)} · revision {shown(delivery.context_revision)}
      </li>)}</ul>
    </details>}
    {activity.control != null && <details><summary>Control receipt · {shown(activity.control.status)}</summary><pre>{JSON.stringify(activity.control, null, 2)}</pre></details>}
    <details><summary>Outputs and validation</summary><pre>{JSON.stringify(activity.outputs, null, 2)}</pre></details>
    <details><summary>Activity history ({activity.cursor})</summary><ol className={css.recorded}>
      {activity.events.map((event, index) => <li key={index}>{shown(event.at)} · {shown(event.node_id ?? event.kind)} · {shown(event.state ?? event.status)} {event.summary == null ? '' : shown(event.summary)}</li>)}
    </ol></details>
  </section>
}
