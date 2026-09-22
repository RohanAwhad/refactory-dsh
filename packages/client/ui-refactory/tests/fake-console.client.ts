/**
 * A scripted `refactoryConsole` Remote face, and the described choices a spec
 * feeds it.
 *
 * Every call resolves from the spec's own value, so a spec states what the Host
 * answered with and reads what the picker did with it. A refusal is built from
 * a code and a message: the picker reads exactly those two fields, and the
 * carrier's error class carries more than a picker test needs.
 */
import { vi } from 'vitest'
import type { Mock } from 'vitest'
import type { RemoteFailure, RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type {
  RefactoryModeChoice,
  RefactoryPackageChoice,
  RefactoryRunStatus,
  RefactoryStartReceipt,
  RefactoryStrategies,
} from '@deepseek-ai/dsh-api-remotes/client'
import type { RefactoryRemote } from '../src/client/face.ts'

/** The three registries with nothing in them. */
export const NO_STRATEGIES: RefactoryStrategies = { outer_loops: [], optimizers: [], evaluators: [] }

/** One scored mode, as the catalogue describes it. */
export const MODE: RefactoryModeChoice = {
  id: 'p/adaptive',
  package: 'p',
  mode: 'adaptive',
  description: 'search the modes',
  startable: true,
  enabled: true,
  scored: true,
  action: 'start',
  trust: 'trusted',
}

/** One authoring mode, whose `action` is the verb that runs it. */
export const AUTHORING: RefactoryModeChoice = {
  ...MODE,
  id: 'default_pack/create',
  package: 'default_pack',
  mode: 'create',
  description: 'create a package of modes',
  scored: false,
  action: 'create',
}

/** One mode whose package is disabled, so the loop cannot run it here. */
export const DISABLED: RefactoryModeChoice = {
  ...MODE,
  id: 'off/adaptive',
  package: 'off',
  enabled: false,
  startable: false,
}

/** One package with one mode. */
export const PACKAGE: RefactoryPackageChoice = {
  name: 'p',
  enabled: true,
  where: '/repo/.factory/packages/p',
  modes: [{ name: 'adaptive', description: 'search the modes', trust: 'trusted' }],
}

/** The three registries, each naming one implementation. */
export const STRATEGIES: RefactoryStrategies = {
  outer_loops: [{ name: 'beam', description: 'keep the best few' }],
  optimizers: [{ name: 'random', description: 'sample uniformly' }],
  evaluators: [{ name: 'score', description: 'the mode\'s own score' }],
}

/** One status frame, mid-run. */
export const FRAME: RefactoryRunStatus = {
  run_id: 'live',
  root: '/runs/live',
  mode: 'p/adaptive',
  state: 'running',
  events: [],
  cursor: 3,
  counters: { epoch: 0, generation: 1, evaluations: 2, steps: 1 },
  best_score: 1.25,
  finished: false,
  running: true,
}

/** What the console recorded about a start. */
export const RECEIPT: RefactoryStartReceipt = {
  run_id: 'live',
  mode: 'p/adaptive',
  root: '/runs/live',
  started: 1.5,
  config: { epochs: '1' },
  log: 'live.log',
  pid: 7,
}

/** A landed Remote call. */
export function ok<T>(value: T): RemoteResult<T> {
  return { ok: true, value }
}

/** A refused Remote call, carrying the code and message the picker reads. */
export function failure<T>(code: string, message: string): RemoteResult<T> {
  return { ok: false, error: { code, message } as unknown as RemoteFailure }
}

/** The scripted Remote face and the mocks behind it. */
export interface FakeConsole {
  readonly remote: RefactoryRemote
  readonly modes: Mock
  readonly strategies: Mock
  readonly packages: Mock
  readonly start: Mock
  readonly status: Mock
  readonly stop: Mock
  readonly setPackageEnabled: Mock
  readonly removePackage: Mock
  readonly importPackage: Mock
}

/**
 * Build a Remote face whose every method resolves from a settable answer.
 * @returns the face and its mocks.
 */
export function fakeConsole(): FakeConsole {
  const methods = {
    modes: vi.fn(async () => ok<readonly RefactoryModeChoice[]>([])),
    strategies: vi.fn(async () => ok(NO_STRATEGIES)),
    packages: vi.fn(async () => ok<readonly RefactoryPackageChoice[]>([])),
    start: vi.fn(async () => ok(RECEIPT)),
    status: vi.fn(async () => ok(FRAME)),
    stop: vi.fn(async () => ok({ stopped: true })),
    setPackageEnabled: vi.fn(async () => ok<readonly RefactoryPackageChoice[]>([])),
    removePackage: vi.fn(async () => ok<readonly RefactoryPackageChoice[]>([])),
    importPackage: vi.fn(async () => ok<readonly RefactoryPackageChoice[]>([])),
  }
  return { remote: { refactoryConsole: methods }, ...methods }
}

/** Let every settled Remote promise's `.then` run. */
export async function settle(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
}
