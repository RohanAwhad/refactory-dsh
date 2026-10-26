// @vitest-environment jsdom
/**
 * The product's name in the page title, on the refactory GUI and on the additive page
 * (`./refactory-dsh --additive`), which leaves the brand plugin out and was titled
 * "DeepSeek Harness". Moved here from `ui-brand-refactory`, which only one of the two
 * pages mounts.
 */
import { describe, expect, it } from 'vitest'
import { installTitle } from '../src/client/title.ts'
import { PRODUCT_NAME, productTitle, titleIndex } from '../src/title.ts'

describe('the page title', () => {
  it('is the product name the brand shows (its `identity.ts`, pinned by its own spec)', () => {
    expect(PRODUCT_NAME).toBe('Refactory')
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

  it('names the product in the initial HTML and changes nothing else', () => {
    const html = '<head><title>DeepSeek Harness</title><link rel="icon" href="./favicon.svg"></head>'
    expect(titleIndex(html)).toBe('<head><title>Refactory</title><link rel="icon" href="./favicon.svg"></head>')
    // The brand's own index tap may already have run: nothing left to translate.
    expect(titleIndex('<title>Refactory</title>')).toBe('<title>Refactory</title>')
    expect(titleIndex('<p>DeepSeek Harness</p>')).toBe('<p>DeepSeek Harness</p>')
  })
})
