/**
 * How the `refactory` tool presents its calls in a host UI.
 *
 * Presentation only: a title saying what a call does, and the completed card's
 * content. The model-facing result is untouched. Kept apart from the tool logic
 * so the host-facing layer can move without it.
 * @module @deepseek-ai/dsh-tool-refactory/present
 */

import type { ContentBlock } from '@deepseek-ai/dsh-llm'
import type { ToolCallView, ToolResultView } from '@deepseek-ai/dsh-tools'
import type { JsonValue } from '@deepseek-ai/dsh-util-values'

/** The arguments a title reads; every other argument is the tool's own business. */
export interface PresentArgs {
  action: string
  mode?: string
  run?: string
  candidate?: string
  name?: string
  as?: string
  topic?: string
  query?: string
  page?: string
  section?: string
  note?: string
  kind?: string
}

/** The longest quoted text a title carries before it is cut. */
const TITLE_TEXT = 60

/** Actions that only read, so a UI may treat them as lookups rather than changes. */
const READ_ACTIONS = new Set([
  'runtime', 'help', 'docs', 'workers', 'profiles', 'profile-resolve', 'comparison', 'modes', 'runs',
  'strategies', 'status', 'artifacts', 'leaderboard', 'candidate', 'best',
])

/**
 * The pending-call card: what this call does, in plain words.
 * @param args - the call's arguments.
 * @returns the generic card intent.
 */
export function presentCall(args: PresentArgs): ToolCallView {
  return { card: 'generic', title: callTitle(args), kind: READ_ACTIONS.has(args.action) ? 'read' : 'other' }
}

/**
 * The structured payload the browser card renders. The card cannot rebuild the
 * console's answer from the summary text, so the answer travels beside it —
 * which is what makes the view a view of the run rather than of a sentence
 * about the run. Persisted with the session log, so a replayed call renders the
 * same card.
 * @param value - the tool's canonical output.
 * @returns the action and the console's payload.
 */
export function presentationMeta(value: { action: string; data: JsonValue }): JsonValue {
  return { action: value.action, data: value.data }
}

/**
 * The completed-call card: the call's title, with the summary rendered from the
 * console's payload as `presentationMeta` persisted it.
 * @param args - the call's arguments.
 * @param result - the completed call, carrying the payload as `meta`.
 * @param render - renders a payload the way the model saw it.
 * @returns the generic result intent, or undefined for a refused call, whose error text is shown.
 */
export function presentResult(
  args: PresentArgs, result: { isError: boolean; meta?: JsonValue },
  render: (data: unknown) => ContentBlock[],
): ToolResultView | undefined {
  if (result.isError) return undefined
  const meta = isRecord(result.meta) ? result.meta : {}
  return { card: 'generic', title: callTitle(args), content: render(meta.data) }
}

/** What a call does, by action, naming its subject when it has one. */
function callTitle(args: PresentArgs): string {
  const run = runLabel(args.run)
  const mode = args.mode ?? 'a mode'
  switch (args.action) {
    case 'runtime': return 'Check model credentials and this session'
    case 'help': return args.topic === undefined ? 'Look up what refactory can do' : `Look up ${args.topic}`
    case 'docs':
      if (args.query !== undefined) return `Search the docs for "${clip(args.query)}"`
      return args.page === undefined ? 'Open the docs index'
        : `Read the docs: ${args.page}${args.section === undefined ? '' : ` § ${args.section}`}`
    case 'workers': return 'Check worker routing'
    case 'profiles': return 'List saved optimization profiles'
    case 'profile-resolve': return `Preview optimization settings for ${mode}`
    case 'comparison': return `Compare every trial in ${run}`
    case 'modes': return 'List the available modes'
    case 'runs': return 'List runs'
    case 'strategies': return 'List optimizers and evaluators'
    case 'start': return `Start optimizing ${mode}`
    case 'status': return `Check ${run}`
    case 'artifacts': return `List the artifacts of ${run}`
    case 'leaderboard': return `Show the leaderboard for ${run}`
    case 'run': return `Run ${mode}`
    case 'candidate': return `Read candidate ${args.candidate ?? ''} of ${run}`
    case 'steer': return args.note === undefined
      ? `Read the notes on ${run}`
      : `Add a ${args.kind ?? 'note'} to ${run}: "${clip(args.note)}"`
    case 'best': return `Read the best result of ${run}`
    case 'adopt': return `Adopt the winner of ${run} as ${args.as ?? 'a new mode'}`
    case 'registry': return 'Manage mode packages'
    case 'create': return `Create the mode ${args.name ?? ''}`.trimEnd()
    case 'edit': return `Edit the mode ${mode}`
    case 'design': return `Design and build ${args.name ?? 'an artifact'}`
    case 'stop': return `Stop ${run}`
    default: return `refactory ${args.action}`
  }
}

/** A run named by its last path segment, since a run is often given as a full path. */
function runLabel(run: string | undefined): string {
  if (run === undefined || run === '') return 'the run'
  const segments = run.split('/').filter(segment => segment !== '')
  return `run ${segments[segments.length - 1] ?? run}`
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
