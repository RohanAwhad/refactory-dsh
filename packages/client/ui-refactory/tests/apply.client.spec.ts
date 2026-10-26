// @vitest-environment jsdom
/**
 * The plugin's registrations, and their removal when the plugin goes.
 *
 * The registry keeps the two operations this spec observes — register a
 * definition under its kind, read it back by kind — because a pinned install
 * publishes the harness's registry type but not its implementation; the slot,
 * locale, and Connection RPC faces are recorders, because what matters here is
 * what was handed to them — one body seat under the type's id with its store and
 * face — and that every registration is gone after dispose, which is what makes a
 * reload safe.
 */
import { describe, expect, it, vi } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import type { TypertRemoteContribution } from '@deepseek-ai/dsh-typert-protocol'
import type { SidebarRightTabDefinition } from '@deepseek-ai/dsh-client-ui-sidebar-right/client'
import { REFACTORY_ID, REFACTORY_KIND } from '../src/client/definition.ts'
import { apply, inject } from '../src/client/index.ts'
import { apply as hostApply } from '../src/index.ts'
import { RefactoryBody } from '../src/client/RefactoryBody.tsx'
import { en, zh } from '../src/client/locales.ts'

/** Stand-in for the harness tab registry: register by kind, read back by kind. */
class TabRegistry {
  private readonly byKind = new Map<string, SidebarRightTabDefinition>()

  /**
   * Register one tab type.
   * @param definition - the type to register.
   * @returns the disposer that removes it again.
   */
  register(definition: SidebarRightTabDefinition): () => void {
    this.byKind.set(definition.kind, definition)
    return () => { this.byKind.delete(definition.kind) }
  }

  /**
   * Read the type registered under one kind.
   * @param kind - the kind to look up.
   * @returns the registered definition, or undefined.
   */
  get(kind: string): SidebarRightTabDefinition | undefined {
    return this.byKind.get(kind)
  }
}

interface Recorded {
  name: string
  key: string
  locale: string
  store: unknown
  inject: unknown
  component: unknown
}

async function boot() {
  const ctx = new Context()
  const tabs = new TabRegistry()
  const registered: Recorded[] = []
  const slots = {
    inject: vi.fn((_name: string, register: () => () => void) => register()),
    register: vi.fn((options: Omit<Recorded, 'component'>, component: unknown) => {
      const entry: Recorded = { ...options, component }
      registered.push(entry)
      return () => { registered.splice(registered.indexOf(entry), 1) }
    }),
  }
  const dictionaries = new Map<string, unknown>()
  const locale = {
    // Copy is the dictionary's contract; the key stands in for the translation.
    bind: vi.fn(() => (key: string) => key),
    register: vi.fn((ns: string, dicts: unknown) => {
      dictionaries.set(ns, dicts)
      return () => { dictionaries.delete(ns) }
    }),
  }
  const connection = { rpc: { call: vi.fn(async () => ({ ok: true, value: undefined })) } }
  ctx.provide('sidebarRightTabs', tabs as never)
  ctx.provide('slots', slots as never)
  ctx.provide('locale', locale as never)
  ctx.provide('connection', connection as never)
  const mounted: TypertRemoteContribution[] = []
  const streams = {
    runStream: vi.fn(async function* () {}),
    watchStream: vi.fn(async function* () {}),
  }
  const remote = {
    refactoryConsole: streams,
    $mount: vi.fn(async (contribution: TypertRemoteContribution) => {
      mounted.push(contribution)
      ctx.provide('remote.refactoryConsole', streams as never)
    }),
  }
  ctx.provide('remote', remote as never)
  const fiber = ctx.plugin({ inject: [...inject], apply })
  await fiber.await()
  return { tabs, registered, dictionaries, fiber, mounted }
}

describe('ui-refactory apply', () => {
  it('names the product in the initial HTML title, and takes the tap back when it goes', async () => {
    const ctx = new Context()
    const taps: Array<(html: string) => string> = []
    let removed = 0
    ctx.provide('webServer', {
      tapIndex: (transform: (html: string) => string) => { taps.push(transform); return () => { removed++ } },
    } as never)
    const fiber = ctx.plugin({ apply: hostApply })
    await fiber.await()
    expect(taps).toHaveLength(1)
    expect(taps[0]!('<head><title>DeepSeek Harness</title><script src="a.js"></script></head>'))
      .toBe('<head><title>Refactory</title><script src="a.js"></script></head>')
    await fiber.dispose()
    expect(removed).toBe(1)
  })

  it('names the product in the document title while it is mounted', async () => {
    document.title = 'My session — DeepSeek Harness'
    const { fiber } = await boot()
    expect(document.title).toBe('My session — Refactory')
    await fiber.dispose()
    document.title = 'DeepSeek Harness'
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(document.title).toBe('DeepSeek Harness')
  })

  it('registers the type, its dictionaries, and the body seat under the type\'s id with a store and a face', async () => {
    const { tabs, registered, dictionaries, mounted } = await boot()
    const definition = tabs.get(REFACTORY_KIND)
    expect(definition?.id).toBe(REFACTORY_ID)
    expect(definition?.priority).toBe('builtin')
    expect(definition?.title('sidebar://refactory')).toBe('type.label')
    expect(definition?.guide?.map(entry => [entry.order, entry.title(), entry.description?.()]))
      .toEqual([[10, 'guide.title', 'guide.description']])
    expect(dictionaries.get('sidebarRefactory')).toEqual({ zh, en })
    expect(mounted).toHaveLength(1)
    expect(mounted[0]?.descriptors.map(descriptor => [descriptor.method, descriptor.mode])).toEqual([
      ['runStream', 'stream'], ['watchStream', 'stream'],
    ])
    // The seat key is the implementation's id, not the kind: an extension may
    // take the kind over, and the seat must still find this body.
    expect(registered.map(entry => [entry.name, entry.key, entry.locale, entry.component])).toEqual([
      ['sidebar.right.pane.tab', REFACTORY_ID, 'sidebarRefactory', RefactoryBody],
    ])
    expect(registered[0]?.store).toBeDefined()
    expect(typeof registered[0]?.inject).toBe('function')
  })

  it('takes every registration back when the plugin is disposed', async () => {
    const { tabs, registered, dictionaries, fiber } = await boot()
    await fiber.dispose()
    expect(tabs.get(REFACTORY_KIND)).toBeUndefined()
    expect(registered).toEqual([])
    expect(dictionaries.size).toBe(0)
  })
})
