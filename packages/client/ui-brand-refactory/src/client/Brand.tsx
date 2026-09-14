import type { HeroBrandMarkOwnerProps } from '@deepseek-ai/dsh-client-ui-conversation/client'
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
 * @param props - host-supplied mark presentation.
 * @param props.size - requested mark height in px.
 */
export function RefactoryBrandMark({ size }: SidebarBrandMarkOwnerProps) {
  return (
    <img
      src={REFACTORY_LOGO}
      height={size}
      alt="re:factory"
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
 * The brand mark for the blank-session hero.
 *
 * The hero lays the mark out in a **34px grid column** beside a headline that is
 * the wordmark itself (`hero.headline` is "re:factory"), so a 5.26:1 lockup
 * rendered at the requested height overruns its column and runs into that
 * headline. This fills the requested **square** by cropping the lockup instead —
 * the slot's contract is a square edge, and the name is already on screen beside
 * it, so repeating it would be the collision this fixes.
 * @param props - host-supplied mark presentation.
 * @param props.size - requested square edge in px; the host always supplies it.
 * @param props.className - host class carrying the surrounding mark geometry.
 */
export function RefactoryHeroMark({ size, className }: HeroBrandMarkOwnerProps) {
  return (
    <img
      src={REFACTORY_LOGO}
      alt=""
      aria-hidden="true"
      className={className}
      style={{
        display: 'block',
        width: size,
        height: size,
        objectFit: 'cover',
        objectPosition: 'left center',
      }}
    />
  )
}
