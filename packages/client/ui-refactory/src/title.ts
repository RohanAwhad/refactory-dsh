/**
 * The product name in the page title, on every page Refactory is part of.
 *
 * The shell titles itself `DeepSeek Harness`. This package is mounted in both web
 * compositions -- the refactory GUI and the additive page (`./refactory-dsh
 * --additive`), which keeps the upstream brand and leaves `ui-brand-refactory` out --
 * so the title is translated here rather than by the brand plugin, and both pages
 * read `Refactory`.
 */

/** The product's display name; `ui-brand-refactory`'s `identity.ts` says the same. */
export const PRODUCT_NAME = 'Refactory'

/** Preserve session titles; translate only the shell's exact product suffix. */
export function productTitle(title: string): string {
  return title.replace(/DeepSeek Harness$/, PRODUCT_NAME)
}

/** The initial HTML's `<title>`, translated before any browser plugin can load. */
export function titleIndex(html: string): string {
  return html.replace(/<title>([^<]*)<\/title>/i, (_tag, title: string) => `<title>${productTitle(title)}</title>`)
}
