import type { SidebarBrandMarkOwnerProps } from '@deepseek-ai/dsh-client-ui-sidebar/client'
import { REFACTORY_LOGO } from './logo.ts'

/**
 * The re:factory brand mark for the sidebar.
 *
 * The asset is the upstream **lockup** — glyph and wordmark on one 400x76 canvas
 * (aspect 5.26) — so it already reads "refactory". That is why
 * {@link RefactoryBrandName} renders nothing: the sidebar row would otherwise
 * carry the name twice, colliding with itself. The width is bounded so the
 * lockup cannot overrun the row it shares with the New Session affordance.
 *
 * `alt=""` is deliberate: the mark is decorative chrome, exactly like the
 * harness's own fallback logo it replaces, and a literal alt would be
 * product-visible copy the brand package has no locale seat to own.
 * @param props - host-supplied mark presentation.
 * @param props.size - requested mark height in px.
 */
export function RefactoryBrandMark({ size }: SidebarBrandMarkOwnerProps) {
  return (
    <img
      src={REFACTORY_LOGO}
      height={size}
      alt=""
      style={{ display: 'block', maxWidth: '100%', objectFit: 'contain' }}
    />
  )
}

/**
 * The sidebar's name slot, deliberately empty.
 *
 * The lockup in the mark slot already carries the wordmark and the host renders
 * the two slots side by side, so a name here is the same word twice. The slot is
 * still claimed with an empty node rather than left unregistered: an unclaimed
 * slot falls back to the harness's own name and version line, which is a third
 * brand again.
 * @returns an empty node holding the slot.
 */
export function RefactoryBrandName() {
  return <span aria-hidden="true" />
}

/**
 * The brand mark for the blank-session hero: deliberately nothing.
 *
 * The hero lays the slot out in a **34px grid column** whose next column is the
 * headline, and `hero.headline` is the wordmark itself — "re:factory". The only
 * artwork in the repository is a 5.26:1 lockup on an **opaque dark background**
 * (400x76, no alpha), so rendering it at the requested height overruns the column
 * onto the headline, and cropping it into the 34px square shows a fragment of the
 * wordmark on a dark box. The name is already on screen, so the hero is
 * deliberately text-only; the slot is claimed with an empty node rather than left
 * unregistered, which is what keeps the harness's own whale out.
 *
 * A real hero mark needs a transparent square glyph asset, which does not exist
 * here. See `GUI-HANDOFF.md`.
 * @returns an empty node holding the slot.
 */
export function RefactoryHeroMark() {
  return <span aria-hidden="true" />
}
