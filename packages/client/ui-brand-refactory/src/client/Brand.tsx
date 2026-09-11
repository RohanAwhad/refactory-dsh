import type { SidebarBrandMarkOwnerProps } from '@deepseek-ai/dsh-client-ui-sidebar/client'
import { REFACTORY_LOGO } from './logo.ts'

/**
 * The re:factory brand mark. `size` comes from the host surface (sidebar rail
 * vs expanded row); the image keeps the wordmark's natural aspect ratio.
 * @param props.size - requested height in px.
 */
export function RefactoryBrandMark({ size }: SidebarBrandMarkOwnerProps) {
  return <img src={REFACTORY_LOGO} height={size} alt="re:factory" style={{ display: 'block' }} />
}

/**
 * The brand name rendered beside the mark. Text rather than the raster wordmark
 * so it scales cleanly and inherits the theme's label color.
 */
export function RefactoryBrandName() {
  return <span style={{ fontWeight: 650, letterSpacing: '0.01em' }}>refactory</span>
}

/**
 * The hero brand mark (replaces the DeepSeek whale on the blank-session hero).
 * @param props.size - requested height in px, default 34.
 */
export function RefactoryHeroMark({ size = 34 }: { size?: number }) {
  return <img src={REFACTORY_LOGO} height={size} alt="re:factory" style={{ display: 'block' }} />
}
