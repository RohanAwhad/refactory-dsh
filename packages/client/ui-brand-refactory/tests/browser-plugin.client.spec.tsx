// @vitest-environment jsdom
import { Context } from '@deepseek-ai/cordis'
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { SlotRegistry } from '@deepseek-ai/dsh-client-ui-renderer/client'
import { apply, inject } from '../src/client/index.ts'
import { RefactoryBrandMark, RefactoryBrandName, RefactoryHeroMark } from '../src/client/Brand.tsx'
import { apply as hostApply } from '../src/index.ts'

afterEach(() => {
  cleanup()
})

const HOLES = [
  'sidebar.brand.mark',
  'sidebar.brand.name',
  'conversation.hero.brand.mark',
] as const

async function bench(declare = true) {
  const ctx = new Context()
  await ctx.plugin(SlotRegistry).await()
  const slots = ctx.get('slots') as SlotRegistry
  const declareHoles = () => slots.register({
    name: 'root',
    children: Object.fromEntries(HOLES.map(name => [name, { kind: 'single', scope: 'root' }])),
  } as never, () => null)
  const disposeHoles = declare ? declareHoles() : undefined
  return { ctx, slots, declareHoles, disposeHoles }
}

describe('re:factory browser-brand plugin', () => {
  it('keeps the host Loader entry inert', () => {
    expect(hostApply).not.toThrow()
  })

  it('declares only the slot service it uses', () => {
    expect(inject).toEqual(['slots'])
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

  /**
   * Superseded: this used to assert a text name beside the lockup. The asset is
   * the upstream lockup, which already reads "refactory", and the hero renders
   * its mark in a 34px column beside a headline that is the wordmark — so a name
   * beside either one is the same word twice, colliding. The components now
   * render the name once; see the "brand renders a name once" suite below.
   */
  it('renders the lockup as the sidebar mark and nothing in the hero', () => {
    const mark = render(<RefactoryBrandMark size={34} />)
    expect(mark.container.querySelector('img')?.getAttribute('src')).toContain('data:image/png')
    expect(mark.container.querySelector('img')?.getAttribute('height')).toBe('34')
    mark.unmount()

    // The hero reserves a 34px column and its next column is the wordmark, so
    // any visible mark here is the collision. It claims the slot and draws
    // nothing; see the hero test below.
    const hero = render(<RefactoryHeroMark />)
    expect(hero.container.querySelector('img')).toBeNull()
    expect(hero.container.textContent).toBe('')
    hero.unmount()

    expect(render(<RefactoryBrandName />).container.textContent).toBe('')
  })
})

describe('the brand renders a name once', () => {
  /**
   * The asset is the upstream lockup: glyph and wordmark on one 400x76 canvas,
   * so it already reads "refactory". The sidebar renders the mark and the name
   * side by side, and the hero renders the mark in a 34px column beside a
   * headline that is the wordmark itself. Both slots therefore collide with the
   * name when either component repeats it.
   */
  it('renders the sidebar lockup with no second copy of the name', () => {
    const { container } = render(
      <>
        <RefactoryBrandMark size={24} />
        <RefactoryBrandName />
      </>,
    )
    const img = container.querySelector('img')
    // Decorative, like the harness logo it replaces: the lockup's own wordmark is
    // not an accessible name, and a literal alt would be copy this package has no
    // locale seat to own (`verify-client-ui-i18n`).
    expect(img?.getAttribute('alt')).toBe('')
    // The name slot holds the slot without adding visible text beside the lockup.
    expect(container.textContent).toBe('')
  })

  it('bounds the sidebar lockup so it cannot overrun its row', () => {
    const { container } = render(<RefactoryBrandMark size={24} />)
    const img = container.querySelector('img') as HTMLImageElement
    expect(img.style.maxWidth).toBe('100%')
    expect(img.getAttribute('height')).toBe('24')
  })

  it('draws nothing in the hero, so nothing can collide with the headline', () => {
    // The hero's grid column is 34px and its next column is `hero.headline`,
    // which is "re:factory". A 5.26:1 lockup at height 34 is ~179px wide, and a
    // square crop of it is a fragment of the wordmark.
    const { container } = render(<RefactoryHeroMark />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('svg')).toBeNull()
    expect(container.textContent).toBe('')
    // Still an element, so the slot is claimed and the harness's own whale does
    // not reappear beside the re:factory headline.
    expect(container.firstElementChild).not.toBeNull()
    expect(container.firstElementChild?.getAttribute('aria-hidden')).toBe('true')
  })

  it('keeps the hero slot claimed by the plugin', async () => {
    const before = await bench()
    const fiber = before.ctx.plugin({ inject: [...inject], apply })
    await fiber.await()
    // Claiming the mark slot is what suppresses the fallback whale.
    expect(before.slots.entries('conversation.hero.brand.mark')).toHaveLength(1)
  })
})
