import type { ReactNode } from 'react'

/** Decorative graph motif; deliberately conveys no invented execution progress. */
export function WorkflowArt({ className }: { readonly className?: string | undefined }): ReactNode {
  return <svg className={className} viewBox="0 0 110 64" fill="none" aria-hidden="true">
    <path d="M15 32h20c10 0 8-19 20-19h25M35 32c10 0 8 19 20 19h25M88 21v22" stroke="currentColor" strokeWidth="1.5" opacity=".4" />
    <rect x="4" y="22" width="22" height="20" rx="6" fill="currentColor" fillOpacity=".12" stroke="currentColor" />
    <rect x="49" y="3" width="28" height="20" rx="6" fill="currentColor" fillOpacity=".08" stroke="currentColor" strokeOpacity=".6" />
    <rect x="49" y="41" width="28" height="20" rx="6" fill="currentColor" fillOpacity=".08" stroke="currentColor" strokeOpacity=".6" />
    <circle cx="89" cy="13" r="5" fill="currentColor" />
    <circle cx="89" cy="51" r="5" fill="currentColor" fillOpacity=".25" />
    <path d="m12 28 5 4-5 4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M57 13h12M57 51h12" stroke="currentColor" strokeOpacity=".7" strokeLinecap="round" />
  </svg>
}

/** A label always accompanies color; completion does not imply a passing score. */
export function stateTone(state: string): string {
  if (/failed|died|error|exhaust/i.test(state)) return 'error'
  if (/wait|paus|gate|halt/i.test(state)) return 'warning'
  if (/finished|complete|done/i.test(state)) return 'success'
  if (/running|starting|ready/i.test(state)) return 'active'
  return 'neutral'
}
