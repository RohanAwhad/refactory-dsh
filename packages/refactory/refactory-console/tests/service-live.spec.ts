/**
 * The service against the real loop: a real console process, the real registry,
 * the real checkout's modes.
 *
 * The unit spec covers the mapping and the refusals; this covers what happens
 * when the argv the service builds is executed — which is the only way to know
 * the two halves of the process boundary still agree. It reads the same console
 * a person pressing "pick a mode" in the panel reads.
 */
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import LocalSubprocessRuntime from '@deepseek-ai/dsh-subprocess-local'
import { remoteErrorOf } from '@deepseek-ai/dsh-typert-protocol'
import { RefactoryConsole } from '../src/index.ts'
import type { Config } from '../src/index.ts'

/** The checkout that holds `refactory/` and `.venv/`. */
const REPO = join(import.meta.dirname, '..', '..', '..', '..')

const CONFIG: Config = {
  repoRoot: REPO,
  pythonBin: join(REPO, '.venv', 'bin'),
  timeoutMs: 120_000,
  collectBytes: 8 * 1024 * 1024,
}

/** One context with the real subprocess provider, and the service over it. */
async function setup(): Promise<{ ctx: Context; service: RefactoryConsole }> {
  const ctx = new Context()
  await ctx.plugin(LocalSubprocessRuntime)
  // The Gateway's discovery service is not what this spec is about: the
  // service's own registration is asserted below, and nothing else reads it.
  ctx.provide('typert', {} as never)
  await ctx.plugin(RefactoryConsole, CONFIG)
  return { ctx, service: ctx.refactoryConsole }
}

const signal = (): AbortSignal => new AbortController().signal

/** The failure a Remote error carries, or a defect when it is not one. */
async function failureOf(operation: Promise<unknown>): Promise<{ code: string; message: string }> {
  try {
    await operation
  } catch (error: unknown) {
    const failure = remoteErrorOf(error)
    if (failure === undefined) throw error
    return { code: failure.code, message: failure.message }
  }
  throw new Error('expected the operation to fail')
}

describe('the picker service against the real loop', () => {
  it('registers itself as the refactoryConsole service when it is plugged', async () => {
    const { ctx } = await setup()
    // `ctx.get`, not `ctx.refactoryConsole`: the property proxy is reserved for
    // declared injections, and this spec asserts the registration itself.
    expect(ctx.get('refactoryConsole')).toBeInstanceOf(RefactoryConsole)
  }, 60_000)

  it('offers the modes the checkout can actually run, each with the verb that runs it', async () => {
    const { service } = await setup()
    const modes = await service.modes(signal())
    const ids = modes.map(mode => mode.id)
    expect(ids).toContain('circle_packing/adaptive')
    const scored = modes.find(mode => mode.id === 'circle_packing/adaptive')
    expect(scored?.scored).toBe(true)
    expect(scored?.action).toBe('start')
    expect(scored?.enabled).toBe(true)
    // The authoring modes are offered too, and each names its own verb rather
    // than the one the loop would refuse.
    const authoring = modes.filter(mode => !mode.scored && mode.action !== 'start')
    expect(authoring.length).toBeGreaterThan(0)
    expect(authoring.every(mode => ['create', 'edit', 'design'].includes(mode.action))).toBe(true)
  }, 120_000)

  it('names the outer loops, optimizers and evaluators a run can choose between', async () => {
    const { service } = await setup()
    const strategies = await service.strategies(signal())
    expect(strategies.optimizers.map(entry => entry.name)).toContain('random')
    expect(strategies.outer_loops.length).toBeGreaterThan(0)
    expect(strategies.evaluators.length).toBeGreaterThan(0)
    expect(strategies.optimizers.find(entry => entry.name === 'random')?.description.length).toBeGreaterThan(0)
  }, 120_000)

  it('lists the checkout\'s packages of modes with the modes each declares', async () => {
    const { service } = await setup()
    const packages = await service.packages(signal())
    const names = packages.map(pkg => pkg.name)
    expect(names).toContain('circle_packing')
    expect(names).toEqual([...names].sort((left, right) => left.localeCompare(right)))
    expect(packages.find(pkg => pkg.name === 'circle_packing')?.enabled).toBe(true)
    expect(packages.find(pkg => pkg.name === 'circle_packing')?.modes.length).toBeGreaterThan(0)
  }, 120_000)

  it('refuses to start an authoring mode, naming the action that runs it', async () => {
    const { service } = await setup()
    const modes = await service.modes(signal())
    const authoring = modes.find(mode => !mode.scored && mode.action !== 'start')
    expect(authoring, 'the checkout must declare at least one authoring mode').toBeDefined()
    const failure = await failureOf(service.start({ mode: authoring!.id }, signal()))
    expect(failure.code).toBe('refactory/authoring-mode')
    expect(failure.message).toContain(`"${authoring!.action}" action`)
  }, 120_000)

  it('reports a run that is not there as the console\'s own refusal', async () => {
    const { service } = await setup()
    const failure = await failureOf(service.status(join(REPO, '.factory', 'loop', 'no-such-run'), 0, signal()))
    expect(failure.code).toBe('refactory/console-failed')
    expect(failure.message).toContain('is not a run directory')
  }, 120_000)
})
