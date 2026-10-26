import { productTitle } from '../title.ts'

/**
 * Keep the document title naming the product as the shell retitles it (a session's
 * title, then the product's). The pin has no configurable document-title input, so
 * this narrow, disposable observer is the bridge; recheck it on upstream upgrades.
 * @param doc - the page's document.
 * @returns the disposer that stops following title changes.
 */
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
