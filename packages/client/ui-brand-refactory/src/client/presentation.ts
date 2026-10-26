import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-locale/client'
import type {} from '@deepseek-ai/dsh-client-ui-settings/client'
import type {} from '@deepseek-ai/dsh-client-ui-settings-models/client'
// The document-title bridge is `ui-refactory`'s (`src/client/title.ts`): that package
// is on the additive page too, where this brand is not.

/**
 * The pinned settings shell lists raw registrations instead of slot winners.
 * A shadowed section therefore gets two identical navigation buttons. Scope
 * this reversible presentation bridge to the public shell wrapper and the
 * localized preset label; keep the native button, controller and focus behavior.
 */
export function installPresetNavigation(doc: Document, label: () => string): () => void {
  const hidden = new Map<HTMLElement, { value: string; priority: string }>()
  const restore = (button: HTMLElement, style: { value: string; priority: string }): void => {
    button.style.setProperty('display', style.value, style.priority)
  }
  const refresh = (): void => {
    const duplicates = new Set<HTMLElement>()
    for (const nav of doc.querySelectorAll('[data-slot="sidebar.settings"] nav')) {
      const matches = [...nav.querySelectorAll<HTMLButtonElement>('button')].filter(button => button.textContent?.trim() === label())
      for (const button of matches.slice(1)) duplicates.add(button)
    }
    for (const [button, style] of hidden) if (!duplicates.has(button)) { restore(button, style); hidden.delete(button) }
    for (const button of duplicates) {
      if (!hidden.has(button)) hidden.set(button, { value: button.style.getPropertyValue('display'), priority: button.style.getPropertyPriority('display') })
      button.style.setProperty('display', 'none', 'important')
    }
  }
  const observer = new MutationObserver(refresh)
  observer.observe(doc.body, { childList: true, subtree: true, characterData: true })
  refresh()
  return () => { observer.disconnect(); for (const [button, style] of hidden) restore(button, style) }
}

/** Scoped to our own slot marker, independent of upstream CSS module hashes. */
export const HERO_STYLE = [
  'span:has(> [data-slot="conversation.hero.brand.mark"]) { width:auto!important; height:auto!important; grid-column:1 / -1; cursor:default!important }',
  'span:has(> [data-slot="conversation.hero.brand.mark"]) + span { display:none!important }',
  'div:has(> span > [data-slot="conversation.hero.brand.mark"]) { display:flex!important; justify-content:center; grid-template-columns:none!important }',
].join('\n')

const welcome = {
  en: {
    welcomeTitle: 'Welcome to Refactory',
    welcomeBody: 'Create, run and improve reusable agent workflows. Start in the Modes panel to choose a mode, configure its inputs and follow its progress.\n\nRun once executes a mode. Start searches candidate variations of a scored mode. Questions, validation and results stay attached to the run you are watching.',
  },
  zh: {
    welcomeTitle: '欢迎使用 Refactory',
    welcomeBody: '创建、运行和改进可复用的智能体工作流。在模式面板中选择模式、配置输入并查看进度。\n\n「单次运行」执行一个模式。「开始」搜索评分模式的候选变化。问题、验证和结果始终属于正在查看的运行。',
  },
}

/**
 * Shadow the upstream welcome leaf through the public slot registry, retaining
 * its component, state source, acknowledgement, focus trap and error behavior.
 * Only two injected translation keys change. No locale registry mutation.
 */
export function installWelcome(ctx: Context): void {
  ctx.slots.inject('settings.onboarding', () => {
    let dispose: (() => void) | undefined
    const install = (): void => {
      if (dispose) return
      const original = ctx.slots.entries('settings.onboarding').find(entry => entry.options.id === 'welcome-notice')
      if (!original?.inject) return
      const inject = original.inject as () => Record<string, unknown>
      dispose = ctx.slots.register({
        name: 'settings.onboarding', id: 'welcome-notice', priority: -10,
        order: original.options.order,
        inject: () => {
          const props = inject()
          const translate = props.t as (key: string, ...args: unknown[]) => string
          return { ...props, t: (key: string, ...args: unknown[]): string => {
            const locale = ctx.locale.getLocale().active.startsWith('zh') ? 'zh' : 'en'
            const copy = welcome[locale]
            return key === 'welcomeTitle' || key === 'welcomeBody' ? copy[key] : translate(key, ...args)
          } }
        },
      } as never, original.component as never)
    }
    // Models may register after the onboarding declaration was announced.
    const unsubscribe = ctx.slots.subscribe('settings.onboarding', install)
    install()
    return () => { unsubscribe(); dispose?.() }
  })
}
