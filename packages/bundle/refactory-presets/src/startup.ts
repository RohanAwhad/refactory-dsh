/**
 * The refactory-presets bundle's startup provider: it publishes the absolute
 * path of the preset tree this package ships, so the `agent-presets` row can
 * name it as a configured `system` root.
 *
 * A patch cannot compute that path itself. `!!js` in a patch is evaluated as
 * `new Function('ctx', 'expr', ...)`, so there is no `import.meta` and no way
 * to reach the directory the patch was loaded from. This plugin has both, so it
 * resolves `presets/` beside its own module and hands the path over.
 * @module @deepseek-ai/dsh-refactory-presets/startup
 */

import { fileURLToPath } from 'node:url'
import type { Context } from '@deepseek-ai/cordis'

/** Stable Cordis plugin name. */
export const name = 'refactory-presets-startup'

/** Service provided by this plugin and injected by the `agent-presets` row. */
export const REFACTORY_PRESETS_STARTUP_SERVICE = 'refactoryPresetsStartup'

/** What the `agent-presets` row reads from this service. */
export interface RefactoryPresetsStartupValues {
  /** Absolute path of the root holding this package's preset directories. */
  readonly presetRoot: string
}

/**
 * Publish the resolved preset root.
 *
 * `../presets/` is one level above this module in both layouts: `src/startup.ts`
 * under tsx and the bundled `lib/startup.js` under a built install sit in the
 * same shape, so the same relative URL finds the same tree either way.
 * @param ctx - the context this bundle's patch is applied to.
 */
export function apply(ctx: Context): void {
  ctx.provide(REFACTORY_PRESETS_STARTUP_SERVICE, {
    presetRoot: fileURLToPath(new URL('../presets/', import.meta.url)),
  } satisfies RefactoryPresetsStartupValues)
}
