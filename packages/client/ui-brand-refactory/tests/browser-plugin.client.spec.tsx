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
  it('renders the logo asset as the mark and the hero glyph', () => {
    const mark = render(<RefactoryBrandMark size={34} />)
    expect(mark.container.querySelector('img')?.getAttribute('src')).toContain('data:image/png')
    expect(mark.container.querySelector('img')?.getAttribute('height')).toBe('34')
    mark.unmount()

    const hero = render(<RefactoryHeroMark size={30} />)
    const glyph = hero.container.querySelector('img') as HTMLImageElement
    // Sized through the style box, not the attributes: the crop needs both
    // dimensions fixed plus object-fit, which attributes cannot express.
    expect(glyph.style.width).toBe('30px')
    expect(glyph.style.height).toBe('30px')
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
    expect(img?.getAttribute('alt')).toBe('re:factory')
    // The name slot holds the slot without adding visible text beside the lockup.
    expect(container.textContent).toBe('')
  })

  it('bounds the sidebar lockup so it cannot overrun its row', () => {
    const { container } = render(<RefactoryBrandMark size={24} />)
    const img = container.querySelector('img') as HTMLImageElement
    expect(img.style.maxWidth).toBe('100%')
    expect(img.getAttribute('height')).toBe('24')
  })

  it('fits the hero mark to the requested square instead of the lockup aspect ratio', () => {
    const { container } = render(<RefactoryHeroMark size={34} className="fish" />)
    const img = container.querySelector('img') as HTMLImageElement
    // The hero's grid column is 34px; a 5.26:1 lockup at height 34 is ~179px wide
    // and runs into the "re:factory" headline beside it.
    expect(img.style.width).toBe('34px')
    expect(img.style.height).toBe('34px')
    expect(img.className).toBe('fish')
  })

  it('takes no accessible name in the hero, where the headline already carries it', () => {
    const { container } = render(<RefactoryHeroMark size={34} />)
    const img = container.querySelector('img') as HTMLImageElement
    expect(img.getAttribute('alt')).toBe('')
    expect(img.getAttribute('aria-hidden')).toBe('true')
  })
})
