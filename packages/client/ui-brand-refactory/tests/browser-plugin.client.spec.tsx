// @vitest-environment jsdom
import { Context, Service } from '@deepseek-ai/cordis'
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { apply, inject } from '../src/client/index.ts'
import { RefactoryBrandMark, RefactoryBrandName, RefactoryHeroMark } from '../src/client/Brand.tsx'
import { brandIndex, apply as hostApply } from '../src/index.ts'
import { installTitle, installWelcome, productTitle } from '../src/client/presentation.ts'
import { FAVICON } from '../src/identity.ts'

const contexts: Context[] = []
afterEach(async () => {
  cleanup()
  await Promise.all(contexts.splice(0).map(ctx => ctx.fiber.dispose()))
})

const HOLES = [
  'sidebar.brand.mark',
  'sidebar.brand.name',
  'conversation.hero.brand.mark',
] as const

/** One contribution a declaration is waiting for, and what it installed. */
interface Injection {
  readonly contribute: () => (() => void) | Iterable<() => void>
  readonly installed: (() => void)[]
}

/**
 * The slice of the harness `ui-renderer` SlotRegistry these specs drive:
 * a contribution into a declared slot installs now, one into an undeclared slot
 * waits, collapse disposes what was installed, and a redeclaration installs it
 * again. The published harness ships that registry only as a browser bundle, so
 * the suite carries this stand-in; it extends Cordis `Service` for the one
 * behaviour the bundle documents as registry-owned — install through the
 * caller's `ctx.effect`, so plugin unload removes the contribution.
 */
class DeclaringSlotRegistry extends Service {
  private readonly declared = new Set<string>()
  private readonly injections = new Map<string, Injection[]>()
  private readonly occupants = new Map<string, unknown[]>()

  /**
   * @param ctx - owning root context.
   */
  constructor(ctx: Context) {
    super(ctx, 'slots')
  }

  /**
   * Register one occupant, declaring the child slots it names.
   * @param spec - the slot being filled and the children it declares.
   * @param component - the component installed into that slot.
   * @returns the disposer that removes the occupant and collapses its children.
   */
  register(spec: { name: string, children?: Record<string, unknown> }, component: unknown): () => void {
    const occupants = this.occupants.get(spec.name) ?? []
    occupants.push(component)
    this.occupants.set(spec.name, occupants)
    const children = Object.keys(spec.children ?? {})
    for (const child of children) this.declare(child)
    return () => {
      const list = this.occupants.get(spec.name)
      const index = list?.indexOf(component) ?? -1
      if (index >= 0) list?.splice(index, 1)
      for (const child of children) this.collapse(child)
    }
  }

  /**
   * Install a contribution for each declaration lifetime of one slot.
   * @param key - the declared slot to depend on.
   * @param contribute - what to install while the declaration stands.
   * @returns the disposer that cancels the wait and removes the contribution.
   */
  inject(key: string, contribute: () => (() => void) | Iterable<() => void>): () => void {
    return this.ctx.effect(() => {
      const injection: Injection = { contribute, installed: [] }
      const injections = this.injections.get(key) ?? []
      injections.push(injection)
      this.injections.set(key, injections)
      if (this.declared.has(key)) this.install(injection)
      return () => {
        const index = injections.indexOf(injection)
        if (index >= 0) injections.splice(index, 1)
        this.retract(injection)
      }
    })
  }

  /**
   * The occupants currently registered into one slot.
   * @param key - the slot to read.
   * @returns the components installed into it.
   */
  entries(key: string): readonly unknown[] {
    return this.occupants.get(key) ?? []
  }

  private declare(key: string): void {
    if (this.declared.has(key)) return
    this.declared.add(key)
    for (const injection of this.injections.get(key) ?? []) this.install(injection)
  }

  private collapse(key: string): void {
    if (!this.declared.delete(key)) return
    for (const injection of this.injections.get(key) ?? []) this.retract(injection)
    this.occupants.delete(key)
  }

  private install(injection: Injection): void {
    const result = injection.contribute()
    const disposers = typeof result === 'function' ? [result] : [...result]
    injection.installed.push(...disposers)
  }

  private retract(injection: Injection): void {
    for (const dispose of injection.installed.reverse()) dispose()
    injection.installed.length = 0
  }
}

async function bench(declare = true) {
  const ctx = new Context()
  contexts.push(ctx)
  await ctx.plugin(DeclaringSlotRegistry).await()
  ctx.provide('locale', { getLocale: () => ({ active: 'en' }) } as never)
  const slots = ctx.get('slots') as DeclaringSlotRegistry
  const declareHoles = () => slots.register({
    name: 'root',
    children: Object.fromEntries(HOLES.map(name => [name, { kind: 'single', scope: 'root' }])),
  } as never, () => null)
  const disposeHoles = declare ? declareHoles() : undefined
  return { ctx, slots, declareHoles, disposeHoles }
}

describe('re:factory browser-brand plugin', () => {
  it('brands the initial HTML and public assets without touching upstream bytes', () => {
    const html = '<head><title>DeepSeek Harness</title><link rel="icon" href="./favicon.svg"><link rel="manifest" href="./manifest.webmanifest"></head><script src="upstream.js"></script>'
    const branded = brandIndex(html)
    expect(branded).toContain('<title>Refactory</title>')
    expect(branded).toContain('/refactory-brand.svg')
    expect(branded).toContain('/refactory.webmanifest')
    expect(branded).toContain('<script src="upstream.js"></script>')
    expect(html).toContain('<title>DeepSeek Harness</title>')
    const routes: Array<{ path: string }> = []
    let taps = 0, disposed = 0
    const effects: Array<() => void> = []
    hostApply({ effect: (fn: () => () => void) => { effects.push(fn()) }, webServer: {
      tapIndex: () => { taps++; return () => { disposed++ } },
      register: (route: { path: string }) => { routes.push(route); return () => { disposed++ } },
    } } as never)
    expect(taps).toBe(1)
    expect(routes.map(route => route.path)).toEqual(['/refactory-brand.svg', '/refactory.webmanifest'])
    effects.forEach(dispose => dispose())
    expect(disposed).toBe(3)
    expect(FAVICON).toContain('prefers-color-scheme:dark')
  })

  it('declares only the slot service it uses', () => {
    expect(inject).toEqual(['slots', 'locale'])
  })

  it('tints the primary accent when a theme service is mounted, and disposes it', async () => {
    const before = await bench()
    const calls: Array<[string, unknown]> = []
    let disposed = 0
    before.ctx.provide('theme', {
      overrideTokens: (id: string, tokens: unknown) => {
        calls.push([id, tokens])
        return () => { disposed += 1 }
      },
    } as never)

    const fiber = before.ctx.plugin({ inject: [...inject], apply })
    await fiber.await()
    expect(calls).toHaveLength(1)
    expect(calls[0]?.[0]).toBe('refactory-brand')
    expect(calls[0]?.[1]).toMatchObject({ '--dsw-alias-brand-primary': { light: '#176bcc', dark: '#76b6ff' } })
    await fiber.dispose()
    expect(disposed).toBe(1)
  })

  it('fills the sidebar + hero brand slots regardless of build profile', async () => {
    const before = await bench()
    const fiber = before.ctx.plugin({ inject: [...inject], apply })
    await fiber.await()
    for (const hole of HOLES) expect(before.slots.entries(hole)).toHaveLength(1)
  })

  it('removes every occupant on teardown and refills after redeclaration', async () => {
    const before = await bench()
    const fiber = before.ctx.plugin({ inject: [...inject], apply })
    await fiber.await()

    before.disposeHoles?.()
    for (const hole of HOLES) expect(before.slots.entries(hole)).toHaveLength(0)
    before.declareHoles()
    await Promise.resolve()
    for (const hole of HOLES) expect(before.slots.entries(hole)).toHaveLength(1)

    await fiber.dispose()
    for (const hole of HOLES) expect(before.slots.entries(hole)).toHaveLength(0)

    const after = await bench(false)
    await after.ctx.plugin({ inject: [...inject], apply }).await()
    for (const hole of HOLES) expect(after.slots.entries(hole)).toHaveLength(0)
    after.declareHoles()
    await Promise.resolve()
    for (const hole of HOLES) expect(after.slots.entries(hole)).toHaveLength(1)
  })

})

describe('transparent Refactory identity', () => {
  it('renders one readable wordmark beside an independently bounded glyph', () => {
    const { container } = render(<><RefactoryBrandMark size={24} /><RefactoryBrandName /></>)
    expect(container.querySelector('[aria-label="Refactory"]')).not.toBeNull()
    const mark = container.querySelector('svg')!
    expect(mark.getAttribute('width')).toBe('24')
    expect(mark.getAttribute('height')).toBe('24')
    expect(mark.getAttribute('aria-hidden')).toBe('true')
    expect(container.querySelector('img')).toBeNull()
  })

  it('claims the hero with a transparent mark and exactly one product name', () => {
    const { container } = render(<RefactoryHeroMark />)
    expect(container.querySelector('[aria-label="Refactory"]')).not.toBeNull()
    expect(container.querySelector('[data-refactory-hero]')).not.toBeNull()
    expect(container.querySelector('svg')?.getAttribute('width')).toBe('280')
  })

  it('keeps session titles and provider names intact while following title changes', async () => {
    expect(productTitle('DeepSeek model — DeepSeek Harness')).toBe('DeepSeek model — Refactory')
    expect(productTitle('DeepSeek provider')).toBe('DeepSeek provider')
    document.title = 'DeepSeek Harness'
    const dispose = installTitle(document)
    expect(document.title).toBe('Refactory')
    document.title = 'My session — DeepSeek Harness'
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(document.title).toBe('My session — Refactory')
    dispose()
    document.title = 'DeepSeek Harness'
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(document.title).toBe('DeepSeek Harness')
  })
})


describe('upstream presentation bridges', () => {
  it('hides the full stock headline through the public slot wrapper', () => {
    const { container, rerender } = render(<div><span><div data-slot="conversation.hero.brand.mark"><RefactoryHeroMark /></div></span><span data-stock-headline>Into the Unknown <span>Preview</span></span></div>)
    expect(getComputedStyle(container.querySelector('[data-stock-headline]')!).display).toBe('none')
    expect(getComputedStyle(container.querySelector('[data-refactory-hero]')!).display).not.toBe('none')
    rerender(<div />)
    expect(container.querySelector('style')).toBeNull()
    rerender(<div><span><div data-slot="conversation.hero.brand.mark"><RefactoryHeroMark /></div></span><span data-stock-headline>Into the Unknown <span>Preview</span></span></div>)
    expect(getComputedStyle(container.querySelector('[data-stock-headline]')!).display).toBe('none')
  })

  it('changes welcome copy in both locales while retaining the original controller props', () => {
    let locale = 'en', disposed = 0
    const acknowledge = () => undefined
    const original = { options: { id: 'welcome-notice', order: 2 }, component: () => null,
      inject: () => ({ acknowledge, accepted: false, t: (key: string) => 'upstream:' + key }) }
    let occupant: { inject: () => { acknowledge: unknown; accepted: boolean; t: (key: string) => string } } | undefined
    let teardown: (() => void) | undefined
    installWelcome({ locale: { getLocale: () => ({ active: locale }) }, slots: {
      inject: (_: string, factory: () => () => void) => { teardown = factory() },
      entries: () => [original], subscribe: () => () => { disposed++ },
      register: (options: typeof occupant, component: unknown) => { expect(component).toBe(original.component); occupant = options; return () => { disposed++ } },
    } } as never)
    const props = occupant!.inject()
    expect(props.acknowledge).toBe(acknowledge)
    expect(props.accepted).toBe(false)
    expect(props.t('welcomeTitle')).toBe('Welcome to Refactory')
    expect(props.t('saveError')).toBe('upstream:saveError')
    locale = 'zh'
    expect(props.t('welcomeTitle')).toBe('欢迎使用 Refactory')
    teardown!()
    expect(disposed).toBe(2)
  })
})
