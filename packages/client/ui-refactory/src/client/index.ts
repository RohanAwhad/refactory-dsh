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
import type { ClientRemote } from '@deepseek-ai/dsh-api-gateway/client'
import type { RefactoryConsoleRemote } from '@deepseek-ai/dsh-refactory-console/types'
import { runStreamContribution, singleEvent } from './run-stream.ts'
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-api-remotes/client'
import type { ConnectionHandle } from '@deepseek-ai/dsh-client-connection/client'
import type {} from '@deepseek-ai/dsh-client-ui-renderer/client'
import type {} from '@deepseek-ai/dsh-client-ui-session/client'
import type {} from '@deepseek-ai/dsh-client-ui-sidebar-right/client'
import { REFACTORY_ID, refactoryDefinition } from './definition.ts'
import { refactoryConsoleOverConnection, refactoryFace } from './face.ts'
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
 * Required browser services: Connection RPC, the Gateway's owned stream carrier,
 * the tab registry, keyed seat, and copy.
 */
export const inject = ['connection', 'remote', 'slots', 'locale', 'sidebarRightTabs']

/**
 * Client plugin body: register the type, its dictionaries, then its body.
 * @param ctx - client root context carrying the registry, the slots, and the RPC caller.
 */
export async function apply(ctx: ClientContext): Promise<void> {
  const t = ctx.locale.bind(NS)
  ctx.effect(() => ctx.sidebarRightTabs.register(refactoryDefinition(t)), 'ui-refactory: refactory type')
  ctx.effect(() => ctx.locale.register(NS, { zh, en }), 'ui-refactory: dictionaries')

  const remote = ctx.get('remote') as ClientRemote
  await remote.$mount(runStreamContribution)
  // Mounting registers a separate Cordis service. Enter its dependency scope
  // after registration; requesting it in the outer inject would deadlock mount.
  ctx.inject(['remote.refactoryConsole'], scope => {
    const store = createRefactoryStore()
    const connection = scope.get('connection') as ConnectionHandle | undefined
    if (connection === undefined) throw new Error('ui-refactory: the connection service is unavailable')
    const mounted = scope.get('remote') as ClientRemote & { refactoryConsole: Pick<RefactoryConsoleRemote, 'runStream'> }
    const inject = refactoryFace(refactoryConsoleOverConnection(connection.rpc, async function* (request, signal) {
      for await (const event of mounted.refactoryConsole.runStream(request, signal)) yield singleEvent(event)
    }))
    scope.effect(() => scope.slots.inject('sidebar.right.pane.tab', () => scope.slots.register(
      { name: 'sidebar.right.pane.tab', key: REFACTORY_ID, locale: NS, store, inject },
      RefactoryBody,
    )), 'ui-refactory: refactory tab body')
  })
}
