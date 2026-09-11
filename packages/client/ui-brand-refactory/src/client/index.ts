import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-ui-renderer/client'
import type {} from '@deepseek-ai/dsh-client-ui-sidebar/client'
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import { RefactoryBrandMark, RefactoryBrandName, RefactoryHeroMark } from './Brand.tsx'

/** Required service: the UI slot registry. */
export const inject = ['slots']

/**
 * Fill the sidebar brand slots and the blank-session hero mark with the
 * re:factory brand, and tint the primary accent rose. This replaces the
 * DeepSeek whale + wordmark wherever the generic brand slots render.
 * @param ctx - Client root context.
 */
export function apply(ctx: ClientContext): void {
  // Accent: rose primary, both schemes (the override layer disposes with the fiber).
  const theme = ctx.get('theme')
  if (theme !== undefined) {
    ctx.effect(() => theme.overrideTokens('refactory-brand', {
      '--dsw-alias-brand-primary': { light: '#e11d48', dark: '#fb7185' },
    }))
  }

  // Sidebar mark + name install atomically as one declaration-aware set.
  ctx.slots.inject('sidebar.brand.mark', () =>
    ctx.slots.inject('sidebar.brand.name', function* () {
      yield ctx.slots.register({ name: 'sidebar.brand.mark' }, RefactoryBrandMark)
      yield ctx.slots.register({ name: 'sidebar.brand.name' }, RefactoryBrandName)
    }))

  // Hero mark (replaces the animated whale fallback on the empty hero).
  ctx.slots.inject('conversation.hero.brand.mark', () =>
    ctx.slots.register({ name: 'conversation.hero.brand.mark' }, RefactoryHeroMark))
}
