import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-host-webserver'
import { FAVICON, PRODUCT_DESCRIPTION, PRODUCT_NAME } from './identity.ts'

export const inject = ['webServer']

/** Brand the initial response, before any browser plugin can load. */
export function brandIndex(html: string): string {
  return html.replace(/<title>[^<]*<\/title>/i, '<title>' + PRODUCT_NAME + '</title>')
    .replace(/<link\b[^>]*rel="icon"[^>]*>/i, '<link rel="icon" type="image/svg+xml" href="/refactory-brand.svg">')
    .replace(/<link\b[^>]*rel="manifest"[^>]*>/i, '<link rel="manifest" href="/refactory.webmanifest">')
    .replace('</head>', '<meta name="application-name" content="' + PRODUCT_NAME + '"><meta name="description" content="' + PRODUCT_DESCRIPTION + '"></head>')
}

/** Supported index/route hooks leave the installed upstream assets untouched. */
export function apply(ctx: Context): void {
  ctx.effect(() => ctx.webServer.tapIndex(brandIndex))
  for (const [path, type, body] of [
    ['/refactory-brand.svg', 'image/svg+xml', FAVICON],
    ['/refactory.webmanifest', 'application/manifest+json', JSON.stringify({
      name: PRODUCT_NAME, short_name: PRODUCT_NAME, description: PRODUCT_DESCRIPTION,
      start_url: '/', display: 'standalone',
      icons: [{ src: '/refactory-brand.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' }],
    })],
  ]) {
    ctx.effect(() => ctx.webServer.register({ kind: 'exact', path: path!, handler: (req, res) => {
      if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405, { Allow: 'GET, HEAD' }); res.end(); return }
      res.writeHead(200, { 'Content-Type': type!, 'Cache-Control': 'no-cache' })
      res.end(req.method === 'HEAD' ? undefined : body)
    } }))
  }
}
