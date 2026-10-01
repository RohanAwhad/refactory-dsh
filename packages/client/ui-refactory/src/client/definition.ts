/**
 * Stage one of this package's registration: what the `refactory` tab type IS.
 *
 * The type is a page, not a viewer: it claims no address. It exists so a person
 * can run the outer loop without a model turn — everything it offers is a
 * described choice the Host listed, so picking a mode and starting it is a
 * gesture rather than a request to write.
 */
import type { SidebarRightTabDefinition } from '@deepseek-ai/dsh-client-ui-sidebar-right/client'
import type { TranslateNS } from '@deepseek-ai/dsh-client-locale/client'
import type {} from './locales.ts'
import { IconDatabaseOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'

/** The tab kind this package owns. */
export const REFACTORY_KIND = 'refactory'

/** This implementation's identity in the tab system, and the key its body registers under. */
export const REFACTORY_ID = '@deepseek-ai/dsh-client-ui-refactory'

/**
 * The refactory type's registry definition.
 * @param t - namespace-bound translate, read fresh on every label call.
 * @returns the definition to register.
 */
export function refactoryDefinition(t: TranslateNS<'sidebarRefactory'>): SidebarRightTabDefinition {
  return {
    id: REFACTORY_ID,
    kind: REFACTORY_KIND,
    priority: 'builtin',
    title: () => t('type.label'),
    guide: [{
      id: 'refactory',
      order: 10,
      title: () => t('guide.title'),
      description: () => t('guide.description'),
      icon: IconDatabaseOutlineRegular,
    }],
  }
}
