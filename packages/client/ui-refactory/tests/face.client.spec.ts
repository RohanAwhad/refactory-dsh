/**
 * The picker's face: what one gesture reads, what it writes, and what a retired
 * read is not allowed to write.
 *
 * The Remote face is scripted and the store is real, so a spec states what the
 * Host answered with and reads the store's own state — the same handoff the body
 * sees.
 */
import { describe, expect, it } from 'vitest'
import type { Mock } from 'vitest'
import type { RemoteResult } from '@deepseek-ai/dsh-api-remotes/client'
import type {
  RefactoryModeChoice,
  RefactoryPackageChoice,
} from '@deepseek-ai/dsh-refactory-console/types'
import type { SessionId } from '@deepseek-ai/dsh-session/types'
import type { TabId } from '@deepseek-ai/dsh-client-ui-dockkit'
import { refactoryFace } from '../src/client/face.ts'
import { NO_SETTINGS, createRefactoryStore } from '../src/client/store.ts'
import type { RefactoryChoices } from '../src/client/store.ts'
import {
  AUTHORING, CANDIDATES, FRAME, GATED, GATED_STEERING, MODE, PACKAGE, RECEIPT, SINGLE,
  STEERED, STEERING, STRATEGIES,
  failure, fakeConsole, ok, settle,
} from './fake-console.client.ts'

const SESSION = 's-test' as SessionId
const TAB = 'tab-1' as TabId

const CHOICES: RefactoryChoices = { modes: [MODE], strategies: STRATEGIES, packages: [PACKAGE] }

type StoreInstance = ReturnType<ReturnType<typeof createRefactoryStore>['create']>

/** One promise a spec settles by hand, so a read can be retired before it lands. */
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((settlePromise) => { resolve = settlePromise })
  return { promise, resolve }
}

/** Take the answer one scripted method resolves with for the rest of the spec. */
function answer(mock: Mock, result: RemoteResult<unknown>): void {
  mock.mockImplementation(async () => result as never)
}

function harness() {
  const instance = createRefactoryStore().create()
  const scripted = fakeConsole()
  const face = refactoryFace(scripted.remote)(SESSION, instance.actions)
  const controller = new AbortController()
  instance.actions.start(TAB)
  return { instance, scripted, face, signal: controller.signal, controller }
}

function tab(instance: StoreInstance) {
  return instance.getSnapshot().byTab[TAB]
}

describe('refactoryFace — reading the described choices', () => {
  it('seeds the tab and writes the three catalogues the Host answered with', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.modes, ok([MODE]))
    answer(scripted.strategies, ok(STRATEGIES))
    answer(scripted.packages, ok([PACKAGE]))
    face.load(TAB, signal)
    expect(tab(instance)?.choices).toEqual({ kind: 'loading' })
    await settle()
    expect(tab(instance)?.choices).toEqual({ kind: 'ready', value: CHOICES })
    expect(scripted.modes).toHaveBeenCalledWith(signal)
    expect(scripted.strategies).toHaveBeenCalledWith(signal)
    expect(scripted.packages).toHaveBeenCalledWith(signal)
  })

  it('records the reason the mode catalogue could not be read', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.modes, failure('refactory/console-failed', 'the console exited 2'))
    answer(scripted.strategies, ok(STRATEGIES))
    answer(scripted.packages, ok([PACKAGE]))
    face.load(TAB, signal)
    await settle()
    expect(tab(instance)?.choices).toEqual({ kind: 'failed', message: 'the console exited 2' })
  })

  it('records the reason the strategies could not be read', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.modes, ok([MODE]))
    answer(scripted.strategies, failure('refactory/console-failed', 'no strategies'))
    answer(scripted.packages, ok([PACKAGE]))
    face.load(TAB, signal)
    await settle()
    expect(tab(instance)?.choices).toEqual({ kind: 'failed', message: 'no strategies' })
  })

  it('records the reason the packages could not be read', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.modes, ok([MODE]))
    answer(scripted.strategies, ok(STRATEGIES))
    answer(scripted.packages, failure('refactory/console-failed', 'no registry'))
    face.load(TAB, signal)
    await settle()
    expect(tab(instance)?.choices).toEqual({ kind: 'failed', message: 'no registry' })
  })

  it('does not read a tab whose record has already ended', () => {
    const { scripted, face, controller } = harness()
    controller.abort()
    face.load(TAB, controller.signal)
    expect(scripted.modes).not.toHaveBeenCalled()
    expect(scripted.strategies).not.toHaveBeenCalled()
    expect(scripted.packages).not.toHaveBeenCalled()
  })

  it('drops a read a newer one has retired, so the newest answer is the one kept', async () => {
    const { instance, scripted, face, signal } = harness()
    const first = deferred<RemoteResult<readonly RefactoryModeChoice[]>>()
    scripted.modes.mockImplementation(async () => first.promise as never)
    answer(scripted.strategies, ok(STRATEGIES))
    answer(scripted.packages, ok([PACKAGE]))
    face.load(TAB, signal)
    scripted.modes.mockImplementation(async () => ok([AUTHORING]) as never)
    face.reload(TAB, signal)
    await settle()
    first.resolve(ok([MODE]))
    await settle()
    expect(tab(instance)?.choices).toEqual({ kind: 'ready', value: { ...CHOICES, modes: [AUTHORING] } })
  })

  it('does not re-read a tab whose record has already ended', () => {
    const { scripted, face, controller } = harness()
    controller.abort()
    face.reload(TAB, controller.signal)
    expect(scripted.modes).not.toHaveBeenCalled()
  })

  it('marks a reload as in flight, keeping what the tab has chosen', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.modes, ok([MODE]))
    answer(scripted.strategies, ok(STRATEGIES))
    answer(scripted.packages, ok([PACKAGE]))
    face.load(TAB, signal)
    await settle()
    face.choose(TAB, MODE.id)
    face.reload(TAB, signal)
    expect(tab(instance)?.choices).toEqual({ kind: 'loading' })
    expect(tab(instance)?.chosen).toBe(MODE.id)
    expect(scripted.modes).toHaveBeenCalledTimes(2)
  })

  it('chooses a mode and a level-two name, which it only reports', () => {
    const { instance, face } = harness()
    face.choose(TAB, MODE.id)
    face.setKnob(TAB, 'rounds', '7')
    face.setSetting(TAB, 'optimizer', 'random')
    expect(tab(instance)?.chosen).toBe(MODE.id)
    expect(tab(instance)?.knobs).toEqual({ rounds: '7' })
    expect(tab(instance)?.settings).toEqual({ ...NO_SETTINGS, optimizer: 'random' })
  })
})

describe('refactoryFace — starting a run', () => {
  it('starts the chosen mode with only the settings that were chosen, its knobs, then reads the run', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.start, ok(RECEIPT))
    answer(scripted.status, ok(FRAME))
    face.start(
      TAB,
      MODE.id,
      [{ name: 'rounds', value: '4' }, { name: 'source_root', value: '/tmp/pkg' }],
      { ...NO_SETTINGS, optimizer: 'random' },
      signal,
    )
    await settle()
    expect(scripted.start).toHaveBeenCalledWith({
      mode: 'p/adaptive',
      settings: { optimizer: 'random' },
      knobs: ['rounds=4', 'source_root=/tmp/pkg'],
    }, signal)
    expect(tab(instance)?.watching).toBe('/runs/live')
    expect(scripted.status).toHaveBeenCalledWith('/runs/live', 0, signal)
    expect(tab(instance)?.frame).toEqual({ kind: 'ready', value: FRAME })
  })

  it('takes the whole settings object when every seam was chosen', async () => {
    const { scripted, face, signal } = harness()
    answer(scripted.start, ok(RECEIPT))
    answer(scripted.status, ok(FRAME))
    face.start(TAB, MODE.id, [], { strategy: 'beam', optimizer: 'random', evaluator: 'score' }, signal)
    await settle()
    expect(scripted.start).toHaveBeenCalledWith({
      mode: 'p/adaptive',
      settings: { strategy: 'beam', optimizer: 'random', evaluator: 'score' },
      knobs: [],
    }, signal)
  })

  it('shows the Host\'s refusal instead of starting a run', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.start, failure('refactory/authoring-mode', '"p/create" is an authoring mode'))
    face.start(TAB, AUTHORING.id, [], NO_SETTINGS, signal)
    await settle()
    expect(tab(instance)?.notice).toEqual({
      code: 'refactory/authoring-mode',
      message: '"p/create" is an authoring mode',
    })
    expect(tab(instance)?.watching).toBeNull()
    expect(scripted.status).not.toHaveBeenCalled()
  })

  it('does not start a run for a tab whose record has already ended', () => {
    const { scripted, face, controller } = harness()
    controller.abort()
    face.start(TAB, MODE.id, [], NO_SETTINGS, controller.signal)
    expect(scripted.start).not.toHaveBeenCalled()
  })
})

describe('refactoryFace — using one mode once', () => {
  it('uses the chosen mode once with the same knob values, and keeps the result', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.run, ok(SINGLE))
    face.run(TAB, MODE.id, [{ name: 'rounds', value: '4' }, { name: 'source_root', value: '/tmp/pkg' }], signal)
    expect(tab(instance)?.single).toEqual({ kind: 'running' })
    await settle()
    expect(scripted.run).toHaveBeenCalledWith({
      mode: 'p/adaptive',
      knobs: ['rounds=4', 'source_root=/tmp/pkg'],
    }, signal)
    expect(tab(instance)?.single).toEqual({ kind: 'ready', value: SINGLE })
  })

  it('keeps a run that stopped at a gate, so the body can say a person is being waited on', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.run, ok(GATED))
    face.run(TAB, MODE.id, [], signal)
    await settle()
    expect(tab(instance)?.single).toEqual({ kind: 'ready', value: GATED })
  })

  it('shows the Host\'s refusal, and stops waiting on a run that will not arrive', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.run, failure('refactory/authoring-mode', '"p/create" is an authoring mode'))
    face.run(TAB, AUTHORING.id, [], signal)
    await settle()
    expect(tab(instance)?.notice).toEqual({
      code: 'refactory/authoring-mode',
      message: '"p/create" is an authoring mode',
    })
    expect(tab(instance)?.single).toBeNull()
  })

  it('does not use a mode for a tab whose record has already ended', () => {
    const { scripted, face, controller } = harness()
    controller.abort()
    face.run(TAB, MODE.id, [], controller.signal)
    expect(scripted.run).not.toHaveBeenCalled()
  })
})

describe('refactoryFace — reading and stopping a run', () => {
  it('reads one status frame from the offset the reader last reached', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.status, ok(FRAME))
    face.refresh(TAB, '/runs/live', 3, signal)
    expect(tab(instance)?.frame).toEqual({ kind: 'loading' })
    await settle()
    expect(scripted.status).toHaveBeenCalledWith('/runs/live', 3, signal)
    expect(tab(instance)?.frame).toEqual({ kind: 'ready', value: FRAME })
  })

  it('shows why a status could not be read', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.status, failure('refactory/console-failed', '/runs/gone is not a run directory'))
    face.refresh(TAB, '/runs/gone', 0, signal)
    await settle()
    expect(tab(instance)?.frame).toEqual({ kind: 'failed', message: '/runs/gone is not a run directory' })
  })

  it('does not read a status for a tab whose record has already ended', () => {
    const { scripted, face, controller } = harness()
    controller.abort()
    face.refresh(TAB, '/runs/live', 0, controller.signal)
    expect(scripted.status).not.toHaveBeenCalled()
  })

  it('asks the run to stop, then reads where it got to', async () => {
    const { scripted, face, signal } = harness()
    answer(scripted.stop, ok({ stopped: true }))
    answer(scripted.status, ok(FRAME))
    face.stop(TAB, '/runs/live', signal)
    await settle()
    expect(scripted.stop).toHaveBeenCalledWith('/runs/live', signal)
    expect(scripted.status).toHaveBeenCalledWith('/runs/live', 0, signal)
  })

  it('shows why a run could not be asked to stop', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.stop, failure('refactory/console-failed', 'the run would not stop'))
    face.stop(TAB, '/runs/live', signal)
    await settle()
    expect(tab(instance)?.notice).toEqual({ code: 'refactory/console-failed', message: 'the run would not stop' })
    expect(scripted.status).not.toHaveBeenCalled()
  })

  it('does not ask a run to stop for a tab whose record has already ended', () => {
    const { scripted, face, controller } = harness()
    controller.abort()
    face.stop(TAB, '/runs/live', controller.signal)
    expect(scripted.stop).not.toHaveBeenCalled()
  })
})

describe('refactoryFace — the watched run\'s ledger', () => {
  it('reads the ledger and the gates the run is stopped at', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.steer, ok(GATED_STEERING))
    face.readSteering(TAB, '/runs/live', signal)
    expect(tab(instance)?.steering).toEqual({ kind: 'loading' })
    await settle()
    expect(scripted.steer).toHaveBeenCalledWith({ run: '/runs/live' }, signal)
    expect(tab(instance)?.steering).toEqual({ kind: 'ready', value: GATED_STEERING })
  })

  it('records why the ledger could not be read, where the ledger is drawn', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.steer, failure('refactory/console-failed', 'no ledger at /runs/live'))
    face.readSteering(TAB, '/runs/live', signal)
    await settle()
    expect(tab(instance)?.steering).toEqual({ kind: 'failed', message: 'no ledger at /runs/live' })
    expect(tab(instance)?.notice).toBeNull()
  })

  it('appends a note and takes the ledger the Host answers with', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.steer, ok(STEERED))
    face.addNote(TAB, '/runs/live', 'try the other seed', 'correction', signal)
    await settle()
    expect(scripted.steer).toHaveBeenCalledWith(
      { run: '/runs/live', note: 'try the other seed', kind: 'correction' },
      signal,
    )
    expect(tab(instance)?.steering).toEqual({ kind: 'ready', value: STEERED })
  })

  it('records a verdict for the gate the run is stopped at', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.steer, ok(GATED_STEERING))
    face.answerGate(TAB, '/runs/live', 'approve', 'proceed', signal)
    await settle()
    expect(scripted.steer).toHaveBeenCalledWith({ run: '/runs/live', verdicts: ['approve=proceed'] }, signal)
    expect(tab(instance)?.steering).toEqual({ kind: 'ready', value: GATED_STEERING })
  })

  it('controls the run itself rather than its ledger', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.steer, ok(STEERED))
    face.controlRun(TAB, '/runs/live', { control: 'pause' }, signal)
    await settle()
    expect(scripted.steer).toHaveBeenCalledWith({ run: '/runs/live', control: 'pause' }, signal)
    expect(tab(instance)?.steering).toEqual({ kind: 'ready', value: STEERED })
  })

  it('carries the candidate a keep or a reject names, and the name an adopt writes', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.steer, ok(STEERED))
    face.controlRun(TAB, '/runs/live', { control: 'reject', target: 'bbbb22', lineage: true }, signal)
    await settle()
    expect(scripted.steer).toHaveBeenCalledWith(
      { run: '/runs/live', control: 'reject', target: 'bbbb22', lineage: true },
      signal,
    )

    answer(scripted.steer, ok(STEERED))
    face.controlRun(TAB, '/runs/live', { control: 'adopt', as: 'better' }, signal)
    await settle()
    expect(scripted.steer).toHaveBeenLastCalledWith(
      { run: '/runs/live', control: 'adopt', as: 'better' },
      signal,
    )
    expect(tab(instance)?.steering).toEqual({ kind: 'ready', value: STEERED })
  })

  it('reads the candidates, and reads them again after an act that changes them', async () => {
    const { instance, scripted, face, signal } = harness()
    face.readCandidates(TAB, '/runs/live', signal)
    await settle()
    expect(scripted.leaderboard).toHaveBeenCalledWith('/runs/live', signal)
    expect(tab(instance)?.candidates).toEqual({ kind: 'ready', value: CANDIDATES })

    answer(scripted.steer, ok(STEERED))
    scripted.leaderboard.mockClear()
    face.controlRun(TAB, '/runs/live', { control: 'keep', target: 'aaaa11' }, signal)
    await settle()
    expect(scripted.leaderboard).toHaveBeenCalledWith('/runs/live', signal)
  })

  it('records why the candidates could not be read, where the table is drawn', async () => {
    const { instance, scripted, face, signal } = harness()
    answer(scripted.leaderboard, failure('refactory/console-failed', 'no such run'))
    face.readCandidates(TAB, '/runs/gone', signal)
    await settle()
    expect(tab(instance)?.candidates).toEqual({
      kind: 'failed',
      message: 'no such run',
    })
  })

  it('shows a refused note as the notice, keeping the ledger that was already read', async () => {
    const { instance, scripted, face, signal } = harness()
    instance.actions.steered(TAB, STEERING)
    answer(scripted.steer, failure('refactory/console-failed', 'an empty note records nothing'))
    face.addNote(TAB, '/runs/live', '', 'info', signal)
    await settle()
    expect(tab(instance)?.notice).toEqual({
      code: 'refactory/console-failed',
      message: 'an empty note records nothing',
    })
    expect(tab(instance)?.steering).toEqual({ kind: 'ready', value: STEERING })
  })

  it('refreshes the ledger alongside the status, so a note added elsewhere appears', async () => {
    const { scripted, face, signal } = harness()
    answer(scripted.status, ok(FRAME))
    answer(scripted.steer, ok(STEERING))
    face.refresh(TAB, '/runs/live', 3, signal)
    await settle()
    expect(scripted.status).toHaveBeenCalledWith('/runs/live', 3, signal)
    expect(scripted.steer).toHaveBeenCalledWith({ run: '/runs/live' }, signal)
  })

  it('does not read or write a ledger for a tab whose record has already ended', () => {
    const { scripted, face, controller } = harness()
    controller.abort()
    face.readSteering(TAB, '/runs/live', controller.signal)
    face.addNote(TAB, '/runs/live', 'no', 'info', controller.signal)
    face.answerGate(TAB, '/runs/live', 'approve', 'proceed', controller.signal)
    expect(scripted.steer).not.toHaveBeenCalled()
  })
})

describe('refactoryFace — changing a package', () => {
  it('disables a package over the whole change, then takes the catalogue that came back', async () => {
    const { instance, scripted, face, signal } = harness()
    instance.actions.loaded(TAB, CHOICES)
    answer(scripted.setPackageEnabled, ok([{ ...PACKAGE, enabled: false }]))
    answer(scripted.modes, ok(CHOICES.modes))
    answer(scripted.strategies, ok(CHOICES.strategies))
    answer(scripted.packages, ok([{ ...PACKAGE, enabled: false }]))
    face.setEnabled(TAB, 'p', false, signal)
    expect(tab(instance)?.busy).toBe(true)
    await settle()
    expect(scripted.setPackageEnabled).toHaveBeenCalledWith('p', false, signal)
    expect(tab(instance)?.busy).toBe(false)
    expect(tab(instance)?.choices).toEqual({ kind: 'ready', value: { ...CHOICES, packages: [{ ...PACKAGE, enabled: false }] } })
  })

  it('removes one package, and imports one under the name it was given', async () => {
    const { instance, scripted, face, signal } = harness()
    instance.actions.loaded(TAB, CHOICES)
    answer(scripted.removePackage, ok([]))
    answer(scripted.importPackage, ok([PACKAGE]))
    answer(scripted.modes, ok(CHOICES.modes))
    answer(scripted.strategies, ok(CHOICES.strategies))
    answer(scripted.packages, ok([]))
    face.remove(TAB, 'p', signal)
    await settle()
    expect(scripted.removePackage).toHaveBeenCalledWith('p', signal)
    expect(tab(instance)?.choices).toEqual({ kind: 'ready', value: { ...CHOICES, packages: [] } })
    answer(scripted.packages, ok([PACKAGE]))
    face.importPackage(TAB, '/src/pack', 'renamed', signal)
    await settle()
    expect(scripted.importPackage).toHaveBeenCalledWith('/src/pack', 'renamed', signal)
    expect(tab(instance)?.choices).toEqual({ kind: 'ready', value: CHOICES })
  })

  it('shows why a package change was refused, and stops waiting on it', async () => {
    const { instance, scripted, face, signal } = harness()
    instance.actions.loaded(TAB, CHOICES)
    answer(scripted.removePackage, failure('refactory/console-failed', 'shipped packages are read-only'))
    face.remove(TAB, 'p', signal)
    await settle()
    expect(tab(instance)?.notice).toEqual({ code: 'refactory/console-failed', message: 'shipped packages are read-only' })
    expect(tab(instance)?.busy).toBe(false)
  })

  it('does not change a package for a tab whose record has already ended', () => {
    const { scripted, face, controller } = harness()
    controller.abort()
    face.setEnabled(TAB, 'p', true, controller.signal)
    face.remove(TAB, 'p', controller.signal)
    face.importPackage(TAB, '/src/pack', '', controller.signal)
    expect(scripted.setPackageEnabled).not.toHaveBeenCalled()
    expect(scripted.removePackage).not.toHaveBeenCalled()
    expect(scripted.importPackage).not.toHaveBeenCalled()
  })

  it('lets the newest of two overlapping changes write the tab', async () => {
    const { instance, scripted, face, signal } = harness()
    instance.actions.loaded(TAB, CHOICES)
    const first = deferred<RemoteResult<readonly RefactoryPackageChoice[]>>()
    scripted.removePackage.mockImplementation(async () => first.promise as never)
    answer(scripted.importPackage, ok([PACKAGE]))
    face.remove(TAB, 'p', signal)
    face.importPackage(TAB, '/src/pack', '', signal)
    await settle()
    first.resolve(failure('refactory/console-failed', 'too late'))
    await settle()
    // The retired change neither wrote its refusal nor cleared the flag the
    // second change is still holding.
    expect(tab(instance)?.notice).toBeNull()
    expect(tab(instance)?.choices).toEqual({ kind: 'ready', value: CHOICES })
  })
})
