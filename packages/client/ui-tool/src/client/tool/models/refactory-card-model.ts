/**
 * Pure derivation of the refactory tool row from the persisted presentation
 * metadata: the run view a person reads in the conversation. Every field comes
 * from the console payload the Host tool projected, so the card says what the
 * run actually recorded and never re-derives the loop's files.
 * @module
 */

import type { ToolCallBlock } from '@deepseek-ai/dsh-client-ui-chat/client'
import type { TranslateNS } from '@deepseek-ai/dsh-client-ui-slots'
import { parsedToolCall, singleResultText, type ParsedToolCall } from './raw-tool-call.ts'

/** The actions the tool exposes, in the order a reader meets them. */
const ACTIONS = [
  'modes', 'strategies', 'runs', 'start', 'status', 'artifacts', 'leaderboard', 'candidate',
  'steer', 'best', 'adopt', 'registry', 'create', 'edit', 'design', 'stop',
] as const

/** One action name. */
type Action = typeof ACTIONS[number]

/** The tool row the chat card renders. */
export interface RefactoryCardModel {
  /** Card title: the word for the act, then the action that ran. */
  title: string
  /** Collapsed one-line summary. */
  summary: string
  /** Expanded body: the run's own facts, one per line. */
  body: string[]
}

/** A JSON object with string keys. */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * A payload value as display text. A payload is JSON, so a value that is not a
 * scalar is rendered as JSON rather than as `[object Object]`: a nested object
 * is still information a reader wants, just not scalar information.
 * @param value - any value from the console's payload.
 * @returns its text.
 */
function scalar(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (value === null || value === undefined) return ''
  return JSON.stringify(value)
}

/** The action of a call, or null when the arguments are not this tool's. */
export function refactoryAction(block: ToolCallBlock): Action | null {
  const call: ParsedToolCall | null = parsedToolCall(block)
  if (call?.name !== 'refactory') return null
  const action = call.args.action
  return ACTIONS.find(candidate => candidate === action) ?? null
}

/** The action and payload the Host projected, or null when absent or malformed. */
export function refactoryMeta(block: ToolCallBlock): { action: Action; data: unknown } | null {
  if (!('kind' in block)) return null
  const meta: unknown = block.meta
  if (!isRecord(meta)) return null
  const action = ACTIONS.find(candidate => candidate === meta.action)
  return action === undefined ? null : { action, data: meta.data }
}

/** `best 1.5` for a score, and a stated absence for a run that has not scored. */
function score(value: unknown, t: TranslateNS<'conversation'>): string {
  return typeof value === 'number' ? t('refactory.best', { score: String(value) }) : t('refactory.noScore')
}

/** The word for a run's lifecycle state, falling back to the recorded value. */
function stateWord(value: unknown, t: TranslateNS<'conversation'>): string {
  switch (value) {
    case 'running': return t('refactory.running')
    case 'starting': return t('refactory.starting')
    case 'finished': return t('refactory.finished')
    case 'died': return t('refactory.died')
    default: return scalar(value)
  }
}

/** One `label: value` line, with an em dash for a value the run did not record. */
function field(label: string, value: unknown): string {
  const text = scalar(value)
  return `${label}: ${text === '' ? '—' : text}`
}

/** One candidate's changed parameters, nodes and edges. */
function diffLines(entry: Record<string, unknown>): string[] {
  if (!isRecord(entry.diff)) return []
  const { knobs, nodes, edges } = entry.diff
  const lines: string[] = []
  for (const [name, change] of Object.entries(isRecord(knobs) ? knobs : {})) {
    if (!isRecord(change)) continue
    lines.push(`  ${name}: ${scalar(change.before) || '—'} → ${scalar(change.after) || '—'}`)
  }
  if (isRecord(nodes)) {
    const added = Array.isArray(nodes.added) ? nodes.added.map(scalar) : []
    const removed = Array.isArray(nodes.removed) ? nodes.removed.map(scalar) : []
    if (added.length > 0) lines.push(`  + nodes: ${added.join(', ')}`)
    if (removed.length > 0) lines.push(`  - nodes: ${removed.join(', ')}`)
  }
  if (isRecord(edges)) {
    const added = Array.isArray(edges.added) ? edges.added.length : 0
    const removed = Array.isArray(edges.removed) ? edges.removed.length : 0
    if (added > 0) lines.push(`  + edges: ${added}`)
    if (removed > 0) lines.push(`  - edges: ${removed}`)
  }
  return lines
}

/** The body lines for a `modes` or `runs` listing. */
function listingLines(action: Action, data: unknown, t: TranslateNS<'conversation'>): string[] {
  const entries = Array.isArray(data) ? data : []
  return entries.flatMap((entry) => {
    if (!isRecord(entry)) return []
    if (action === 'runs') {
      const state = entry.finished === true ? t('refactory.finished') : t('refactory.inFlight')
      return [`${scalar(entry.run_id)} · ${scalar(entry.mode)} · ${score(entry.best_score, t)} · ${state}`]
    }
    const count = isRecord(entry.parameters) ? Object.keys(entry.parameters).length : 0
    const startable = entry.startable === true ? '' : ` · ${t('refactory.notStartable')}`
    return [`${scalar(entry.id)}${startable} · ${t('refactory.parameters', { count: String(count) })}`]
  })
}

/** The body lines for a `status` payload: the counters, then the progress tail. */
function statusLines(data: unknown, t: TranslateNS<'conversation'>): string[] {
  const payload = isRecord(data) ? data : {}
  const counters = isRecord(payload.counters) ? payload.counters : {}
  const lines = [
    field(t('refactory.state'), stateWord(payload.state, t)),
    field(t('refactory.best'), score(payload.best_score, t)),
    t('refactory.counters', {
      epoch: scalar(counters.epoch) || '0',
      generation: scalar(counters.generation) || '0',
      evaluations: scalar(counters.evaluations) || '0',
    }),
  ]
  const events = Array.isArray(payload.events) ? payload.events : []
  for (const event of events.slice(-8)) {
    if (!isRecord(event)) continue
    const step = scalar(event.step)
    lines.push(event.candidate === undefined
      ? `  ${field(t('refactory.step'), step)}`
      : `  ${step} · ${scalar(event.candidate)} · ${score(event.score, t)}`)
  }
  return lines
}

/** The body lines for one action's payload. */
function bodyFor(action: Action, data: unknown, t: TranslateNS<'conversation'>): string[] {
  const payload = isRecord(data) ? data : {}
  switch (action) {
    case 'modes':
    case 'runs':
      return listingLines(action, data, t)
    case 'strategies': {
      // The named choice: each seam's implementations with what each says it is.
      const lines: string[] = []
      for (const key of ['outer_loops', 'optimizers', 'evaluators']) {
        const entries = Array.isArray(payload[key]) ? payload[key] as unknown[] : []
        for (const entry of entries) {
          if (isRecord(entry)) lines.push(`${scalar(entry.name)}: ${scalar(entry.description)}`)
        }
      }
      return lines
    }
    case 'start':
      return [field(t('refactory.mode'), payload.mode), field(t('refactory.run'), payload.run_id)]
    case 'status':
      return statusLines(data, t)
    case 'artifacts': {
      const artifacts = Array.isArray(payload.artifacts) ? payload.artifacts : []
      return artifacts.flatMap((entry) => {
        if (!isRecord(entry)) return []
        return [`${entry.exists === true ? '✓' : '·'} ${scalar(entry.name)} (${scalar(entry.media_type)})`]
      })
    }
    case 'leaderboard': {
      const rows = Array.isArray(data) ? data : []
      return rows.flatMap((row) => {
        if (!isRecord(row)) return []
        return [`${scalar(row.id)} · ${score(row.score, t)}`, ...diffLines(row)]
      })
    }
    case 'candidate': {
      const rendered = typeof payload.rendered === 'string' ? payload.rendered.split('\n') : []
      return [
        `${scalar(payload.candidate)} ← ${scalar(payload.parent) || t('refactory.seed')}`,
        ...rendered,
      ]
    }
    case 'steer': {
      // The ledger itself: what was asked for, what was said since, and any gate
      // verdict a person gave. Data, so it renders verbatim.
      const entries = Array.isArray(payload.entries) ? payload.entries : []
      const lines = entries.flatMap(entry => isRecord(entry)
        ? [`[${scalar(entry.kind)}] ${scalar(entry.text)}`]
        : [])
      const verdicts = isRecord(payload.verdicts) ? payload.verdicts : {}
      for (const [gate, verdict] of Object.entries(verdicts)) lines.push(`${gate}=${scalar(verdict)}`)
      for (const problem of Array.isArray(payload.problems) ? payload.problems : []) {
        lines.push(scalar(problem))
      }
      return lines
    }
    case 'best':
      return [`${scalar(payload.candidate)} · ${score(payload.score, t)}`]
    case 'adopt':
      return [
        `${scalar(payload.mode)} ← ${scalar(payload.adopted_from)}`,
        score(payload.score, t),
      ]
    case 'registry':
    case 'create':
    case 'edit':
    case 'design': {
      // These forward to another entry point, so its own output is the body.
      const inner = isRecord(payload.result) ? payload.result : {}
      const output = scalar(inner.output)
      const lines = output === '' ? [JSON.stringify(inner)] : output.split('\n')
      return [payload.ok === true ? t('refactory.finished') : t('refactory.died'), ...lines]
    }
    default:
      return payload.stopped === true
        ? [t('refactory.stopRequested')]
        : [field(t('refactory.reason'), payload.reason)]
  }
}

/**
 * Derive the row from one refactory call.
 * @param block - the running or settled Tool block.
 * @param t - the conversation namespace translator.
 * @returns the row, or null when this block is not a refactory call.
 */
export function refactoryCardModel(
  block: ToolCallBlock,
  t: TranslateNS<'conversation'>,
): RefactoryCardModel | null {
  const action = refactoryAction(block)
  if (action === null) return null
  const title = `${t('tool.title.refactory')} ${action}`
  const meta = refactoryMeta(block)
  if (meta === null) {
    // A call still running, or a result whose metadata predates this view: the
    // summary text is the only material there is, and saying so beats inventing
    // a progress bar out of nothing.
    const text = 'kind' in block ? singleResultText(block) : undefined
    return { title, summary: text?.split('\n')[0] ?? '', body: text === undefined ? [] : [text] }
  }
  const body = bodyFor(meta.action, meta.data, t)
  return { title, summary: body[0] ?? '', body }
}
