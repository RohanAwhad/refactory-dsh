/** Host half: the initial HTML's title; the whole picker lives in the browser export. */
import type { Context } from '@deepseek-ai/cordis'
import { titleIndex } from './title.ts'

/** The slice of the host web server this half uses. */
interface IndexTaps {
  tapIndex(transform: (html: string) => string): () => void
}

/**
 * Host plugin body: name the product in the page's initial `<title>` once a web
 * server is there to serve it (`title.ts`). Nothing else reaches the host tree.
 * @param ctx - the host context this package's row is applied to.
 */
export function apply(ctx: Context): void {
  ctx.inject(['webServer'], scope => {
    const server = scope.get('webServer') as IndexTaps | undefined
    if (server !== undefined) scope.effect(() => server.tapIndex(titleIndex), 'ui-refactory: page title')
  })
}
