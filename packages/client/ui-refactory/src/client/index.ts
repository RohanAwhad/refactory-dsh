/**
 * Browser half: register `refactory` as a right-Sidebar tab type.
 *
 * The public two-stage path, unmodified: the type into `ctx.sidebarRightTabs`,
 * the body into the keyed `sidebar.right.pane.tab` seat under the type's `id`.
 *
 * The file split is this package's layering: what the type IS
 * (`definition.ts`), what it keeps (`store.ts`), how it asks (`face.ts`), what it
 * draws (`RefactoryBody.tsx`), what it says (`locales.ts`), and this module,
 * which only wires them together.
 */
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-api-remotes/client'
import type {} from '@deepseek-ai/dsh-client-ui-renderer/client'
import type {} from '@deepseek-ai/dsh-client-ui-session/client'
import type {} from '@deepseek-ai/dsh-client-ui-sidebar-right/client'
import { REFACTORY_ID, refactoryDefinition } from './definition.ts'
import { refactoryFace } from './face.ts'
import { RefactoryBody } from './RefactoryBody.tsx'
import { en, zh } from './locales.ts'
import { createRefactoryStore } from './store.ts'

export type { SidebarRefactoryKey } from './locales.ts'
export type {
  Loadable, RefactoryChoices, RefactoryNotice, RefactorySettingName, RefactorySettings,
  RefactorySingleState, RefactoryState, RefactoryTabState,
} from './store.ts'
export type { RefactoryInjected, RefactoryRemote } from './face.ts'
export type { RefactoryBodyProps } from './RefactoryBody.tsx'

/** This package's copy namespace. */
const NS = 'sidebarRefactory'

/**
 * Required browser services: the tab registry, the keyed seat, the Remote
 * carrier and its namespace, and copy.
 */
export const inject = ['slots', 'locale', 'sidebarRightTabs', 'remote', 'remote.refactoryConsole']

/**
 * Client plugin body: register the type, its dictionaries, then its body.
 * @param ctx - client root context carrying the registry, the slots, and the Remote face.
 */
export function apply(ctx: ClientContext): void {
  const t = ctx.locale.bind(NS)
  ctx.effect(() => ctx.sidebarRightTabs.register(refactoryDefinition(t)), 'ui-refactory: refactory type')
  ctx.effect(() => ctx.locale.register(NS, { zh, en }), 'ui-refactory: dictionaries')

  const store = createRefactoryStore()
  const inject = refactoryFace(ctx.remote)
  ctx.effect(() => ctx.slots.inject('sidebar.right.pane.tab', () => ctx.slots.register(
    { name: 'sidebar.right.pane.tab', key: REFACTORY_ID, locale: NS, store, inject },
    RefactoryBody,
  )), 'ui-refactory: refactory tab body')
}
