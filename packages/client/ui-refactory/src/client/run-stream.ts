/** The one explicit Remote contribution needed by the panel's launch stream. */
import type { TypertRemoteContribution } from '@deepseek-ai/dsh-typert-protocol'
import type { RefactorySingleEvent, RefactorySingleRunRequest, RefactoryWatchFrame } from '@deepseek-ai/dsh-refactory-console/types'

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error('Expected a run stream object')
  return value as Record<string, unknown>
}
function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === 'string')
}
function textFields(value: Record<string, unknown>, fields: string[]): boolean {
  return fields.every(field => typeof value[field] === 'string')
}
function request(value: unknown): RefactorySingleRunRequest {
  const entry = record(value)
  if (typeof entry.mode !== 'string' || (entry.knobs !== undefined && !strings(entry.knobs))) {
    throw new Error('Invalid Run once request')
  }
  return entry as unknown as RefactorySingleRunRequest
}
/** Validate every delivered frame, including the complete ordinary result. */
export function singleEvent(value: unknown): RefactorySingleEvent {
  const entry = record(value)
  if (entry.kind === 'launch') {
    const launch = record(entry.launch)
    if (!textFields(launch, ['mode', 'root']) || !['starting', 'prepared'].includes(String(launch.phase))
      || (launch.phase === 'prepared' && typeof launch.invocation !== 'string')) throw new Error('Invalid run launch identity')
  } else if (entry.kind === 'result') {
    const run = record(entry.run)
    if (!textFields(run, ['run_id', 'root', 'state']) || (run.mode !== null && typeof run.mode !== 'string')
      || typeof run.finished !== 'boolean' || (run.best_score !== null && typeof run.best_score !== 'number')
      || !Object.values(record(run.facts)).every(item => typeof item === 'string')) throw new Error('Invalid final run result')
    if (run.authoring !== null) {
      const authoring = record(run.authoring)
      if (!textFields(authoring, ['spine', 'error']) || !strings(authoring.nodes_due)) throw new Error('Invalid graph progress')
    }
    if (run.item_results != null) {
      const counts = record(run.item_results)
      if (!['count', 'passed', 'failed', 'unclassified'].every(key => typeof counts[key] === 'number')) throw new Error('Invalid item results')
    }
  } else throw new Error('Unknown run stream frame')
  return entry as unknown as RefactorySingleEvent
}

/**
 * The lean assembly has no generated namespace for this repository's service.
 * Mount the stream through the published Gateway so browser WebSocket framing,
 * cancellation and errors retain their existing owners. Unary calls still use
 * Connection RPC. The descriptor and these boundary validators live together.
 */
export const runStreamContribution: TypertRemoteContribution = {
  package: '@deepseek-ai/dsh-refactory-console',
  descriptors: [{
    id: '@deepseek-ai/dsh-refactory-console#refactoryConsole/runStream',
    service: 'refactoryConsole', namespace: 'refactoryConsole', method: 'runStream', mode: 'stream',
    invocation: { kind: 'direct' },
    parameters: [{ name: 'request', wire: 'request', source: 'json', codec: {
      mode: 'strict', typeSymbol: 'RefactorySingleRunRequest', schema: { parse: request },
    } }],
    cancellation: { parameter: 'signal' },
    result: { mode: 'strict', typeSymbol: 'RefactorySingleEvent', schema: { parse: singleEvent } },
  }],
}

/** Reconnectable, read-only observation of a durable run. */
export function watchFrame(value: unknown): RefactoryWatchFrame {
  const frame = record(value)
  const status = record(frame.status)
  const steering = record(frame.steering)
  if (!textFields(status, ['run_id', 'root', 'state']) || typeof status.cursor !== 'number'
    || !textFields(steering, ['run']) || !Array.isArray(steering.gates)) throw new Error('Invalid durable run frame')
  return frame as unknown as RefactoryWatchFrame
}
export const watchStreamContribution: TypertRemoteContribution = {
  package: '@deepseek-ai/dsh-refactory-console',
  descriptors: [{
    id: '@deepseek-ai/dsh-refactory-console#refactoryConsole/watchStream',
    service: 'refactoryConsole', namespace: 'refactoryConsole', method: 'watchStream', mode: 'stream',
    invocation: { kind: 'direct' },
    parameters: [
      { name: 'run', wire: 'run', source: 'json', codec: { mode: 'strict', typeSymbol: 'string', schema: { parse(value: unknown) {
        if (typeof value !== 'string' || value.trim() === '') throw new Error('A run is required')
        return value
      } } } },
      { name: 'after', wire: 'after', source: 'json', codec: { mode: 'strict', typeSymbol: 'number', schema: { parse(value: unknown) {
        if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error('Invalid cursor')
        return value
      } } } },
    ],
    cancellation: { parameter: 'signal' },
    result: { mode: 'strict', typeSymbol: 'RefactoryWatchFrame', schema: { parse: watchFrame } },
  }],
}
