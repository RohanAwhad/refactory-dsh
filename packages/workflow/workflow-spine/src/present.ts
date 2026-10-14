/**
 * How the spine's `<prefix>_*` tools present their calls in a host UI.
 *
 * Presentation only: a title saying what a call does, and once it returns, what
 * came of it. The model-facing result is untouched. Kept apart from the tool
 * logic so the host-facing layer can move without it.
 * @module @deepseek-ai/dsh-workflow-spine/present
 */

import type { ToolCallView, ToolResultView } from '@deepseek-ai/dsh-tools'
import type { JsonValue } from '@deepseek-ai/dsh-util-values'

/** A spine tool, named without its configurable prefix. */
export type SpineTool = 'open' | 'next' | 'done' | 'verdict' | 'edit' | 'redo' | 'expand' | 'state' | 'steer'

/** The longest quoted text or error a title carries before it is cut. */
const TITLE_TEXT = 80

/** What `presentationMeta` keeps of a result: only what a result title needs. */
interface ResultSummary {
  status?: string
  node?: string
  gate?: string
  verdict?: string
  answeredBy?: string
  ran?: string
  next?: string[]
  userGate?: string
  halted?: string
  error?: string
}

/**
 * What one call does, in the words a person reading the conversation would use.
 * @param tool - the spine tool called.
 * @param args - the call's arguments.
 * @returns the call's card.
 */
export function presentCall(tool: SpineTool, args: Record<string, unknown>): ToolCallView {
  return { card: 'generic', title: callTitle(tool, args), kind: 'other' }
}

/**
 * The part of a result a result title reads, persisted with the call. A summary
 * rather than the result itself: `next` carries the run's whole `facts.json`.
 * @param tool - the spine tool called.
 * @param value - the tool's result.
 * @returns the summary.
 */
export function presentationMeta(tool: SpineTool, value: unknown): JsonValue {
  const result = isRecord(value) ? value : {}
  const summary: ResultSummary = {}
  if (typeof result.status === 'string') summary.status = result.status
  if (typeof result.node === 'string') summary.node = result.node
  if (typeof result.gate === 'string') summary.gate = result.gate
  if (typeof result.verdict === 'string') summary.verdict = result.verdict
  if (typeof result.answered_by === 'string') summary.answeredBy = result.answered_by
  if (typeof result.error === 'string') summary.error = clip(result.error)
  if (result.halted === true) summary.halted = typeof result.gate === 'string' ? result.gate : 'gate'
  if (isRecord(result.halted) && typeof result.halted.gate === 'string') summary.halted = result.halted.gate
  if (tool === 'next' || tool === 'open') {
    if (isRecord(result.ran) && typeof result.ran.node === 'string') summary.ran = result.ran.node
    const nodes = Array.isArray(result.nodes) ? result.nodes.filter(isRecord) : []
    summary.next = nodes.map(node => String(node.id))
    const gate = nodes.find(node => node._type === 'GateNode' && node.evaluator_type === 'user')
    if (gate !== undefined) summary.userGate = String(gate.id)
  }
  return summary as JsonValue
}

/**
 * What came of one call, from its persisted summary.
 * @param tool - the spine tool called.
 * @param args - the call's arguments.
 * @param result - the completed call, carrying the summary as `meta` and whether it failed.
 * @returns the completed card, or undefined to keep the call's title.
 */
export function presentResult(
  tool: SpineTool, args: Record<string, unknown>, result: { isError: boolean; meta?: JsonValue },
): ToolResultView | undefined {
  // A refused call keeps its title; the refusal text is the result shown.
  if (result.isError || !isRecord(result.meta)) return undefined
  const title = resultTitle(tool, args, result.meta as ResultSummary)
  return title === undefined ? undefined : { card: 'generic', title }
}

/** The title for a call before it returns. */
function callTitle(tool: SpineTool, args: Record<string, unknown>): string {
  const node = String(args.node ?? '')
  switch (tool) {
    case 'open': return `Open ${String(args.mode ?? 'a mode')}`
    case 'next': return 'Run the next workflow step'
    case 'done': return `Mark ${node} done`
    case 'verdict': return args.verdict === undefined
      ? `Ask you at ${String(args.gate)}`
      : `Route ${String(args.gate)}: ${String(args.verdict)}`
    case 'edit': return editTitle(String(args.action), node)
    case 'redo': return `Re-run ${node}`
    case 'expand': {
      const count = Array.isArray(args.entries) ? args.entries.length : 0
      return `Plan ${count} ${count === 1 ? 'step' : 'steps'} under ${String(args.director)}`
    }
    case 'state': return 'Check workflow state'
    case 'steer': return typeof args.note === 'string'
      ? `Record a ${String(args.kind ?? 'note')}: "${clip(args.note)}"`
      : 'Read the notes on this run'
  }
}

/** The title for a `<prefix>_edit` call, by its action. */
function editTitle(action: string, node: string): string {
  switch (action) {
    case 'skip': return `Skip ${node}`
    case 'unskip': return `Stop skipping ${node}`
    case 'set-prompt': return `Replace ${node}'s prompt`
    case 'append-prompt': return `Add to ${node}'s prompt`
    case 'pause': return `Pause before ${node}`
    case 'unpause': return `Resume at ${node}`
    default: return `Edit ${node}`
  }
}

/** The title for a returned call, or undefined when its call title still says it. */
function resultTitle(tool: SpineTool, args: Record<string, unknown>, summary: ResultSummary): string | undefined {
  switch (tool) {
    case 'open':
    case 'next': return stepTitle(summary)
    case 'verdict': return verdictTitle(summary)
    case 'redo': return `Reset ${String(args.node)}; it runs on the next step`
    default: return undefined
  }
}

/** What a step of the workflow came to: what ran, and what it waits on now. */
function stepTitle(summary: ResultSummary): string {
  if (summary.status === 'failed') return `Failed: ${summary.error ?? 'a step did not complete'}`
  if (summary.halted !== undefined) return `Workflow stopped at ${summary.halted}`
  if (summary.status === 'done') return 'Workflow finished'
  if (summary.status === 'paused') return 'Workflow paused'
  const ran = summary.ran === undefined ? '' : `Ran ${summary.ran} → `
  if (summary.userGate !== undefined) return `${ran}${ran === '' ? 'Waiting' : 'waiting'} for your answer at ${summary.userGate}`
  const next = summary.next ?? []
  return `${ran}${ran === '' ? 'Next' : 'next'}: ${next.length === 0 ? 'nothing due' : next.join(', ')}`
}

/** What a gate call came to. */
function verdictTitle(summary: ResultSummary): string | undefined {
  if (summary.halted !== undefined) return summary.answeredBy === 'user' ? 'You stopped the run' : 'Run stopped'
  if (summary.status === 'replied') return 'Your message went to the CEO'
  if (summary.status === 'cancelled') return `Cancelled ${summary.gate ?? 'the gate'}`
  if (summary.status === 'routed') {
    return summary.answeredBy === 'user'
      ? `You chose ${summary.verdict ?? ''}`
      : `Routed ${summary.gate ?? ''}: ${summary.verdict ?? ''}`
  }
  if (summary.status === 'paused' || summary.status === 'halted' || summary.status === 'ready') {
    return `Question closed: the run is ${summary.status}`
  }
  return undefined
}

/** Cut text a title quotes to one readable line. */
function clip(text: string): string {
  const line = text.replace(/\s+/gu, ' ').trim()
  return line.length > TITLE_TEXT ? `${line.slice(0, TITLE_TEXT - 1)}…` : line
}

/** Whether a value is a plain JSON object. */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
