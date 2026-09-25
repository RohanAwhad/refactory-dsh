/**
 * The picker's store: every action's write, and the bucket rule that ties a
 * write to the tab that is still on screen.
 */
import { describe, expect, it } from 'vitest'
import type { TabId } from '@deepseek-ai/dsh-client-ui-dockkit'
import { NO_SETTINGS, createRefactoryStore, knobValues } from '../src/client/store.ts'
import { FRAME, GATED_STEERING, MODE, NO_STRATEGIES, PACKAGE, SINGLE, STEERING } from './fake-console.client.ts'

const TAB = 'tab-1' as TabId
const OTHER = 'tab-2' as TabId

const CHOICES = { modes: [MODE], strategies: { ...NO_STRATEGIES, optimizers: [{ name: 'random', description: 'sample uniformly' }] }, packages: [PACKAGE] }

function open() {
  const instance = createRefactoryStore().create()
  instance.actions.start(TAB)
  return instance
}

describe('createRefactoryStore', () => {
  it('seeds a tab with the choices being read, nothing chosen, and nothing in flight', () => {
    const instance = open()
    expect(instance.getSnapshot().byTab[TAB]).toEqual({
      choices: { kind: 'loading' },
      chosen: null,
      knobs: {},
      settings: { ...NO_SETTINGS },
      watching: null,
      frame: null,
      steering: null,
      single: null,
      notice: null,
      busy: false,
    })
  })

  it('records the choices the Host answered with', () => {
    const instance = open()
    instance.actions.loaded(TAB, CHOICES)
    expect(instance.getSnapshot().byTab[TAB]?.choices).toEqual({ kind: 'ready', value: CHOICES })
  })

  it('marks a reload as in flight, keeping what the tab has chosen', () => {
    const instance = open()
    instance.actions.loaded(TAB, CHOICES)
    instance.actions.chose(TAB, MODE.id)
    instance.actions.loading(TAB)
    expect(instance.getSnapshot().byTab[TAB]?.choices).toEqual({ kind: 'loading' })
    expect(instance.getSnapshot().byTab[TAB]?.chosen).toBe(MODE.id)
  })

  it('records why the choices could not be read, and drops a selection they may not contain', () => {
    const instance = open()
    instance.actions.loaded(TAB, CHOICES)
    instance.actions.chose(TAB, MODE.id)
    instance.actions.knob(TAB, 'rounds', '7')
    instance.actions.ran(TAB, SINGLE)
    instance.actions.failed(TAB, 'the console is missing')
    expect(instance.getSnapshot().byTab[TAB]?.choices).toEqual({ kind: 'failed', message: 'the console is missing' })
    expect(instance.getSnapshot().byTab[TAB]?.chosen).toBeNull()
    expect(instance.getSnapshot().byTab[TAB]?.knobs).toEqual({})
    expect(instance.getSnapshot().byTab[TAB]?.single).toBeNull()
  })

  it('chooses one of the described modes, and can choose nothing again', () => {
    const instance = open()
    instance.actions.chose(TAB, MODE.id)
    expect(instance.getSnapshot().byTab[TAB]?.chosen).toBe(MODE.id)
    instance.actions.chose(TAB, null)
    expect(instance.getSnapshot().byTab[TAB]?.chosen).toBeNull()
  })

  it('drops the knobs typed for the last mode, and its run, when another is chosen', () => {
    const instance = open()
    instance.actions.loaded(TAB, CHOICES)
    instance.actions.chose(TAB, MODE.id)
    instance.actions.knob(TAB, 'rounds', '7')
    instance.actions.ran(TAB, SINGLE)
    instance.actions.chose(TAB, null)
    expect(instance.getSnapshot().byTab[TAB]?.knobs).toEqual({})
    expect(instance.getSnapshot().byTab[TAB]?.single).toBeNull()
  })

  it('records each knob value a person changed, by name', () => {
    const instance = open()
    instance.actions.knob(TAB, 'rounds', '7')
    instance.actions.knob(TAB, 'source_root', '/tmp/pkg')
    expect(instance.getSnapshot().byTab[TAB]?.knobs).toEqual({ rounds: '7', source_root: '/tmp/pkg' })
  })

  it('chooses one level-two name per seam', () => {
    const instance = open()
    instance.actions.setting(TAB, 'strategy', 'beam')
    instance.actions.setting(TAB, 'optimizer', 'random')
    instance.actions.setting(TAB, 'evaluator', '')
    expect(instance.getSnapshot().byTab[TAB]?.settings).toEqual({ strategy: 'beam', optimizer: 'random', evaluator: '' })
  })

  it('begins reading a run, clearing the last refusal and asking for its first frame', () => {
    const instance = open()
    instance.actions.noticed(TAB, { code: 'refactory/unknown-mode', message: 'no' })
    instance.actions.watching(TAB, '/runs/live')
    expect(instance.getSnapshot().byTab[TAB]).toMatchObject({
      watching: '/runs/live',
      frame: { kind: 'loading' },
      notice: null,
    })
  })

  it('records one status frame, and the reading of the next', () => {
    const instance = open()
    instance.actions.watching(TAB, '/runs/live')
    instance.actions.framed(TAB, FRAME)
    expect(instance.getSnapshot().byTab[TAB]?.frame).toEqual({ kind: 'ready', value: FRAME })
    instance.actions.frameLoading(TAB)
    expect(instance.getSnapshot().byTab[TAB]?.frame).toEqual({ kind: 'loading' })
  })

  it('records why the watched run\'s status could not be read', () => {
    const instance = open()
    instance.actions.watching(TAB, '/runs/live')
    instance.actions.frameFailed(TAB, '/runs/live is not a run directory')
    expect(instance.getSnapshot().byTab[TAB]?.frame).toEqual({
      kind: 'failed',
      message: '/runs/live is not a run directory',
    })
  })

  it('reads the watched run\'s ledger, and records why it could not be read', () => {
    const instance = open()
    instance.actions.watching(TAB, '/runs/live')
    expect(instance.getSnapshot().byTab[TAB]?.steering).toEqual({ kind: 'loading' })
    instance.actions.steered(TAB, STEERING)
    expect(instance.getSnapshot().byTab[TAB]?.steering).toEqual({ kind: 'ready', value: STEERING })
    instance.actions.steered(TAB, GATED_STEERING)
    expect(instance.getSnapshot().byTab[TAB]?.steering).toEqual({ kind: 'ready', value: GATED_STEERING })
    instance.actions.steeringFailed(TAB, '/runs/live is not a run directory')
    expect(instance.getSnapshot().byTab[TAB]?.steering).toEqual({
      kind: 'failed',
      message: '/runs/live is not a run directory',
    })
  })

  it('records a one-shot run being under way, what it left, and that it was refused', () => {
    const instance = open()
    instance.actions.runLoading(TAB)
    expect(instance.getSnapshot().byTab[TAB]?.single).toEqual({ kind: 'running' })
    instance.actions.ran(TAB, SINGLE)
    expect(instance.getSnapshot().byTab[TAB]?.single).toEqual({ kind: 'ready', value: SINGLE })
    instance.actions.runFailed(TAB)
    expect(instance.getSnapshot().byTab[TAB]?.single).toBeNull()
  })

  it('replaces the packages a package gesture answered with, keeping the choices already read', () => {
    const instance = open()
    instance.actions.loaded(TAB, CHOICES)
    instance.actions.packages(TAB, [])
    expect(instance.getSnapshot().byTab[TAB]?.choices).toEqual({ kind: 'ready', value: { ...CHOICES, packages: [] } })
  })

  it('drops a package list that arrives before the first read lands', () => {
    const instance = open()
    instance.actions.packages(TAB, [PACKAGE])
    expect(instance.getSnapshot().byTab[TAB]?.choices).toEqual({ kind: 'loading' })
  })

  it('records what the last refused gesture said, and clears it', () => {
    const instance = open()
    instance.actions.noticed(TAB, { code: 'refactory/console-failed', message: 'the console exited 2' })
    expect(instance.getSnapshot().byTab[TAB]?.notice).toEqual({ code: 'refactory/console-failed', message: 'the console exited 2' })
    instance.actions.noticed(TAB, null)
    expect(instance.getSnapshot().byTab[TAB]?.notice).toBeNull()
  })

  it('records whether a package change is in flight', () => {
    const instance = open()
    instance.actions.busy(TAB, true)
    expect(instance.getSnapshot().byTab[TAB]?.busy).toBe(true)
    instance.actions.busy(TAB, false)
    expect(instance.getSnapshot().byTab[TAB]?.busy).toBe(false)
  })

  it('forgets one tab and keeps the others', () => {
    const instance = open()
    instance.actions.start(OTHER)
    instance.actions.forget(TAB)
    expect(instance.getSnapshot().byTab[TAB]).toBeUndefined()
    expect(instance.getSnapshot().byTab[OTHER]).toBeDefined()
  })

  it('refuses to write a tab it does not hold', () => {
    const instance = open()
    expect(() => { instance.actions.framed(OTHER, FRAME) }).toThrow('ui-refactory: no picker for tab "tab-2"')
  })
})

describe('knobValues', () => {
  /** The values as one map, which is the order and the content a gesture sends. */
  const sent = (values: readonly { name: string; value: string }[]) =>
    Object.fromEntries(values.map(entry => [entry.name, entry.value]))

  it('takes each declaration\'s own value under anything a person changed', () => {
    expect(sent(knobValues(MODE, { rounds: '7' }))).toEqual({
      rounds: '7',
      temperature: '0.5',
      source_root: '/tmp/src',
      preset: 'broad',
      verbose: 'false',
    })
  })

  it('keeps an emptied value, which is a value a str knob accepts', () => {
    expect(sent(knobValues(MODE, { source_root: '' })).source_root).toBe('')
  })

  it('takes every declaration\'s own value before anything is typed', () => {
    expect(sent(knobValues(MODE, undefined)).rounds).toBe('3')
  })

  it('has nothing to send before a mode is chosen', () => {
    expect(knobValues(undefined, { rounds: '7' })).toEqual([])
  })
})
