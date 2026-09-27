import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-locale/client'
import type {} from '@deepseek-ai/dsh-client-ui-settings/client'
import type {} from '@deepseek-ai/dsh-client-ui-settings-models/client'
import { PRODUCT_NAME } from '../identity.ts'

/** Preserve session titles; translate only the shell's exact product suffix. */
export function productTitle(title: string): string {
  return title.replace(/DeepSeek Harness$/, PRODUCT_NAME)
}

/** The pin has no configurable document-title input. Keep this bridge isolated. */
export function installTitle(doc: Document): () => void {
  const update = (): void => {
    const next = productTitle(doc.title)
    if (next !== doc.title) doc.title = next
  }
  update()
  const observer = new MutationObserver(update)
  observer.observe(doc.head, { subtree: true, childList: true, characterData: true })
  return () => { observer.disconnect() }
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
