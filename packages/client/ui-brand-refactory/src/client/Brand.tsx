import { useId } from 'react'
import type { SidebarBrandMarkOwnerProps } from '@deepseek-ai/dsh-client-ui-sidebar/client'
import { GEARS_BLUE, GEARS_INK, LOGO_BLUE, LOGO_INK } from '../artwork.ts'
import { PRODUCT_NAME } from '../identity.ts'
import { HERO_STYLE } from './presentation.ts'

/** Recolor the original artwork's alpha layers without redrawing its geometry. */
function Artwork({ mark, width }: { mark: boolean, width: number }) {
  const id = useId().replace(/:/g, '')
  const w = mark ? 69 : 400, h = mark ? 69 : 76
  return <svg width={width} height={width * h / w} viewBox={`0 0 ${w} ${h}`}
    aria-hidden={mark || undefined} role={mark ? undefined : 'img'} aria-label={mark ? undefined : PRODUCT_NAME}
    style={{ display: 'block', flex: 'none', maxWidth: '100%', color: 'var(--refactory-logo-ink, var(--dsw-alias-label-primary))' }}>
    <defs>
      <mask id={`${id}-blue`} style={{ maskType: 'alpha' }}><image href={mark ? GEARS_BLUE : LOGO_BLUE} width={w} height={h} /></mask>
      <mask id={`${id}-ink`} style={{ maskType: 'alpha' }}><image href={mark ? GEARS_INK : LOGO_INK} width={w} height={h} /></mask>
    </defs>
    <path fill="#2b8cff" mask={`url(#${id}-blue)`} d={`M0 0h${w}v${h}H0z`} />
    <path fill="currentColor" mask={`url(#${id}-ink)`} d={`M0 0h${w}v${h}H0z`} />
  </svg>
}

/** The two gears stay legible when the sidebar collapses. */
export function RefactoryBrandMark({ size = 24 }: Partial<SidebarBrandMarkOwnerProps>) {
  return <Artwork mark width={size} />
}

export function RefactoryBrandName() {
  return <span aria-label={PRODUCT_NAME} style={{ letterSpacing: '-0.035em', fontSize: 19, fontWeight: 650 }}><span style={{ color: '#2b8cff' }}>re</span>factory</span>
}

/** The complete original lockup owns the blank-chat hero. */
export function RefactoryHeroMark() {
  return <span data-refactory-hero style={{ display: 'inline-flex', maxWidth: '100%' }}><style>{HERO_STYLE}</style><Artwork mark={false} width={280} /></span>
}
