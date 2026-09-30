/**
 * The refactory-web bundle's startup provider: it publishes the refactory
 * checkout root so the `refactory-console` row can name it as `repoRoot`.
 *
 * A patch cannot compute that path itself. `!!js` in a patch is evaluated as
 * `new Function('ctx', 'expr', ...)`, so there is no `import.meta` and no way to
 * reach the directory the patch was loaded from. This plugin has both, so it
 * resolves the checkout above its own module and hands the path over. The old
 * pnpm patch used `process.cwd()`, which for a profile-installed bundle names the
 * *user's* working directory rather than this checkout.
 * @module @deepseek-ai/dsh-refactory-web/startup
 */

import { fileURLToPath } from 'node:url'
import type { Context } from '@deepseek-ai/cordis'

/** Stable Cordis plugin name. */
export const name = 'refactory-web-startup'

/** Service provided by this plugin and injected by the `refactory-console` row. */
export const REFACTORY_WEB_STARTUP_SERVICE = 'refactoryWebStartup'

/** What the `refactory-console` row reads from this service. */
export interface RefactoryWebStartupValues {
  /** Absolute path of the checkout holding `refactory/` and `.venv/`. */
  readonly repoRoot: string
}

/**
 * Publish the resolved checkout root.
 *
 * `../../../../` is four levels above this module in both layouts:
 * `src/startup.ts` under tsx and the bundled `lib/startup.js` under a built
 * install sit at the same depth under
 * `<checkout>/packages/bundle/refactory-web/`, so the same relative URL finds the
 * checkout either way. The bundle is installed into a profile by path, so this
 * resolves to this checkout rather than into a profile's `node_modules`.
 * @param ctx - the context this bundle's patch is applied to.
 */
export function apply(ctx: Context): void {
  ctx.provide(REFACTORY_WEB_STARTUP_SERVICE, {
    repoRoot: fileURLToPath(new URL('../../../../', import.meta.url)),
  } satisfies RefactoryWebStartupValues)
}
