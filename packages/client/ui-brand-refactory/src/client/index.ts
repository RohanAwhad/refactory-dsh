import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-ui-renderer/client'
import type {} from '@deepseek-ai/dsh-client-ui-sidebar/client'
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import { installPresets } from './presets.ts'
import { installPresetNavigation, installTitle, installWelcome } from './presentation.ts'
import { RefactoryBrandMark, RefactoryBrandName, RefactoryHeroMark } from './Brand.tsx'

/** Required service: the UI slot registry. */
export const inject = ['slots', 'locale']

/**
 * Fill the sidebar brand slots and the blank-session hero mark with the
 * original two-gear Refactory brand, and use its blue accent. This replaces the
 * DeepSeek whale + wordmark wherever the generic brand slots render.
 * @param ctx - Client root context.
 */
export function apply(ctx: ClientContext): void {
  ctx.effect(() => installTitle(document))
  installWelcome(ctx)
  installPresets(ctx)
  ctx.effect(() => installPresetNavigation(document, () => ctx.locale.bind('settings.agentPreset')('nav')))

  // Accent: logo blue, both schemes (the override layer disposes with the fiber).
  const theme = ctx.get('theme')
  if (theme !== undefined) {
    ctx.effect(() => theme.overrideTokens('refactory-brand', {
      '--dsw-alias-brand-primary': { light: '#176bcc', dark: '#76b6ff' },
      '--refactory-logo-ink': { light: '#18324f', dark: '#ffffff' },
      '--refactory-surface': { light: '#ffffff', dark: '#192535' },
      '--refactory-canvas': { light: '#f4f7fb', dark: '#101923' },
      '--refactory-border': { light: '#d7e1ec', dark: '#35485e' },
      '--refactory-muted': { light: '#52657b', dark: '#afc1d5' },
      '--refactory-success': { light: '#197659', dark: '#7cddba' },
      '--refactory-warning': { light: '#895710', dark: '#f0c77f' },
      '--refactory-error': { light: '#b52640', dark: '#ff9ca9' },
      '--refactory-on-accent': { light: '#ffffff', dark: '#0b2038' },
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
