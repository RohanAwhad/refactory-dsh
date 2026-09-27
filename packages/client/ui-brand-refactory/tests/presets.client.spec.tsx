// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/react'
import type { Context } from '@deepseek-ai/cordis'
import { createSnapshotStore } from '@deepseek-ai/dsh-client-store'
import type {
  AgentPresetSeatInjected, AgentPresetSeatState, AgentPresetSectionInjected, AgentPresetSectionState,
} from '@deepseek-ai/dsh-client-ui-agent-preset/client'
import { installPresets, presetRows, presetSeat, presetSection } from '../src/client/presets.ts'
import { installPresetNavigation } from '../src/client/presentation.ts'

const options = ['ceo', 'standard', 'ptc', 'minimal', 'cordis', 'custom'].map(id => ({
  id, name: id === 'ceo' ? 'CEO' : id, trust: 'system' as const, description: `Original ${id}`,
}))
afterEach(cleanup)

it('keeps one native preset nav button across rerenders and restores presentation on disposal', async () => {
  const click = vi.fn()
  const view = render(<><nav><button>Agent presets</button><button>Agent presets</button></nav>
    <div data-slot="sidebar.settings"><nav><button>General</button><button onClick={click}>Agent presets</button>
      <button style={{ display: 'flex' }}>Agent presets</button></nav></div></>)
  const buttons = view.getAllByText('Agent presets')
  let label = 'Agent presets'
  const stop = installPresetNavigation(document, () => label)
  expect(buttons[0]!.style.display).toBe('')
  expect(buttons[1]!.style.display).toBe('')
  expect(buttons[2]!.style.display).toBe('')
  expect(buttons[3]!.style.display).toBe('none')
  buttons[2]!.click()
  expect(click).toHaveBeenCalledOnce()
  label = '智能体预设'
  buttons[2]!.textContent = label
  buttons[3]!.textContent = label
  await waitFor(() => expect(buttons[3]!.style.display).toBe('none'))
  buttons[2]!.remove()
  await waitFor(() => expect(buttons[3]!.style.display).toBe('flex'))
  const duplicate = document.createElement('button')
  duplicate.textContent = label
  buttons[3]!.parentElement!.append(duplicate)
  await waitFor(() => expect(duplicate.style.display).toBe('none'))
  stop()
  expect(duplicate.style.display).toBe('')
})

function seat(current = 'standard') {
  const store = createSnapshotStore<AgentPresetSeatState>({ options, current, error: null, busy: false, introduce: false })
  const face: AgentPresetSeatInjected = {
    hooks: { agentPresetSeat: store }, load: vi.fn(async () => {}), introduced: vi.fn(),
    select: vi.fn(async id => { store.update(state => { state.current = id }); return undefined }),
  }
  return { store, face }
}

it('filters selectable presets, localizes CEO metadata, and leaves host rows intact', () => {
  const { face, store } = seat('minimal')
  let lang: 'en' | 'zh' = 'en'
  const adapted = presetSeat(face, () => lang).hooks.agentPresetSeat
  const first = adapted.getSnapshot()
  expect(first.options.map(row => row.id)).toEqual(['ceo', 'minimal'])
  expect(first.options[0]?.description).toContain('Create, run, and improve Refactory workflows')
  expect(adapted.getSnapshot()).toBe(first)
  expect(store.getSnapshot().options).toBe(options)
  expect(options[0]?.description).toBe('Original ceo')
  const changed = vi.fn(), unsubscribe = adapted.subscribe(changed)
  store.update(state => { state.error = 'Host unavailable' })
  expect(changed).toHaveBeenCalledOnce()
  expect(adapted.getSnapshot().error).toBe('Host unavailable')
  lang = 'zh'
  expect(adapted.getSnapshot().options[0]?.description).toContain('协调智能体')
  unsubscribe()
  expect(presetRows(options, 'en', false).map(row => row.id)).toEqual(options.map(row => row.id))
})

it('stages CEO through the native controller when an old default is no longer offered', async () => {
  const { face, store } = seat()
  const adapted = presetSeat(face, () => 'en')
  await adapted.load()
  expect(face.load).toHaveBeenCalledOnce()
  expect(face.select).toHaveBeenCalledExactlyOnceWith('ceo')
  expect(store.getSnapshot().current).toBe('ceo')
  expect(adapted.hooks.agentPresetSeat.getSnapshot().current).toBe('ceo')
  expect(adapted.select).toBe(face.select)
})

it('retains Minimal selections and preserves native failures', async () => {
  const { face, store } = seat('minimal')
  const adapted = presetSeat(face, () => 'en')
  await adapted.load()
  expect(face.select).not.toHaveBeenCalled()
  store.update(state => { state.current = 'standard'; state.error = 'Cannot load roster' })
  await adapted.load()
  expect(face.select).not.toHaveBeenCalled()
  expect(adapted.hooks.agentPresetSeat.getSnapshot().error).toBe('Cannot load roster')
})

it('uses Minimal if CEO is unavailable and does not invent an available preset', async () => {
  const { face, store } = seat()
  store.update(state => { state.options = options.filter(row => row.id !== 'ceo') })
  await presetSeat(face, () => 'en').load()
  expect(face.select).toHaveBeenCalledExactlyOnceWith('minimal')
  vi.mocked(face.select).mockClear()
  store.update(state => { state.options = []; state.current = '' })
  await presetSeat(face, () => 'en').load()
  expect(face.select).not.toHaveBeenCalled()
})

it('limits settings to two rows, disables extra-preset authoring, and retains native actions and health', () => {
  const state: AgentPresetSectionState = {
    status: 'ready', error: null, authorable: true, hasDocument: true,
    rows: options.map(row => ({ ...row, isDefault: row.id === 'ceo', ...(row.id === 'ceo' ? { broken: 'Missing plugin' } : {}) })),
    copy: null, view: null, pendingDelete: null, deleting: false, revealedPaths: {},
  }
  const store = createSnapshotStore(state)
  const view = vi.fn(), makeDefault = vi.fn()
  const face = { hooks: { agentPresetSection: store }, view, makeDefault } as unknown as AgentPresetSectionInjected
  const adapted = presetSection(face, () => 'en')
  expect(adapted.hooks.agentPresetSection.getSnapshot().rows.map(row => row.id)).toEqual(['ceo', 'minimal'])
  expect(adapted.hooks.agentPresetSection.getSnapshot().rows[0]?.broken).toBe('Missing plugin')
  expect(adapted.hooks.agentPresetSection.getSnapshot().authorable).toBe(false)
  expect(store.getSnapshot().authorable).toBe(true)
  expect(adapted.view).toBe(view)
  expect(adapted.makeDefault).toBe(makeDefault)
})

it('uses public slots, follows source replacement, and removes its contributions on disposal', () => {
  type Entry = { options: { id?: string | undefined; priority?: number | undefined }; locale?: string | undefined; component: (props: Record<string, unknown>) => unknown; inject: () => unknown }
  const entries = new Map<string, Entry[]>(), listeners = new Map<string, Set<() => void>>(), dispose: (() => void)[] = []
  const emit = (key: string) => { for (const listener of [...listeners.get(key) ?? []]) listener() }
  const register = (key: string, entry: Entry) => {
    if (!key) throw new Error('A slot registration needs its name')
    entries.set(key, [...entries.get(key) ?? [], entry]); emit(key)
    return () => { entries.set(key, entries.get(key)!.filter(value => value !== entry)); emit(key) }
  }
  const ctx = { locale: { getLocale: () => ({ active: 'en' }) }, slots: {
    inject: (_: string, factory: () => () => void) => { dispose.push(factory()) },
    entries: (key: string) => entries.get(key) ?? [],
    subscribe: (key: string, listener: () => void) => {
      const group = listeners.get(key) ?? new Set(); group.add(listener); listeners.set(key, group)
      return () => group.delete(listener)
    },
    register: (options: Entry['options'] & { name: string; locale?: string; inject: Entry['inject'] }, component: Entry['component']) =>
      register(options.name, { options: { id: options.id, priority: options.priority }, component, locale: options.locale, inject: options.inject }),
  } } as unknown as Context
  installPresets(ctx)
  const first = seat('ceo')
  const slot = 'conversation.hero.agentPreset'
  const remove = register(slot, { options: {}, locale: 'settings.agentPreset', component: () => null, inject: () => first.face })
  const override = () => entries.get(slot)?.find(entry => entry.options.priority === -10)
  expect((override()!.inject() as AgentPresetSeatInjected).hooks.agentPresetSeat.getSnapshot().options).toHaveLength(2)
  expect(override()!.locale).toBe('settings.agentPreset')
  remove()
  expect(override()).toBeUndefined()
  const second = seat('minimal')
  register(slot, { options: {}, component: () => null, inject: () => second.face })
  expect((override()!.inject() as AgentPresetSeatInjected).hooks.agentPresetSeat.getSnapshot().current).toBe('minimal')

  // Reuse the real settings component's translation contract, without replacing its UI or controls.
  register('settings.section', { options: { id: 'agent-presets' }, inject: () => ({ hooks: {
    agentPresetSection: createSnapshotStore({ rows: options, authorable: true }),
  } }), component: props => <p>{(props.t as (key: string) => string)('sectionIntro')}</p> })
  const Surface = entries.get('settings.section')!.find(entry => entry.options.priority === -10)!.component as (props: Record<string, unknown>) => React.ReactNode
  const view = render(<Surface t={() => 'Upstream creator copy'} />)
  expect(view.getByText(/Choose CEO for guided Refactory workflows/)).toBeDefined()

  for (const stop of dispose) stop()
  expect(override()).toBeUndefined()
  expect(entries.get(slot)).toHaveLength(1)
  expect([...listeners.values()].every(group => group.size === 0)).toBe(true)
})
