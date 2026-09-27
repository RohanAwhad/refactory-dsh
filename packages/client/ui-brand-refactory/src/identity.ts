import { GEARS_BLUE, GEARS_INK } from './artwork.ts'

/** Product identity shared by server metadata and browser occupants. */
export const PRODUCT_NAME = 'Refactory'
export const PRODUCT_DESCRIPTION = 'Create, run and improve agent workflows.'
export const PRODUCT_URL = 'https://github.com/akashgit/refactory-dsh'
/** The original two gears, with theme-aware ink on a transparent background. */
export const FAVICON = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 69 69"><style>svg{color:#18324f}@media(prefers-color-scheme:dark){svg{color:#fff}}</style><defs><mask id="blue" style="mask-type:alpha"><image href="${GEARS_BLUE}" width="69" height="69"/></mask><mask id="ink" style="mask-type:alpha"><image href="${GEARS_INK}" width="69" height="69"/></mask></defs><path fill="#2b8cff" mask="url(#blue)" d="M0 0h69v69H0z"/><path fill="currentColor" mask="url(#ink)" d="M0 0h69v69H0z"/></svg>`
