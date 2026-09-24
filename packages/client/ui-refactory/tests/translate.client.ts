/**
 * Test double of the locale lookup chain: a translate stub over plain
 * dictionaries.
 *
 * The harness ships this helper in `@deepseek-ai/dsh-client-test-runtime`, whose
 * published entry imports another package's `src/` files and therefore cannot
 * load outside the harness monorepo. This is the same chain, minus that
 * dependency: first dictionary that owns the key wins, then the key stays
 * visible, with `{name}` interpolation.
 */

/**
 * Build a translate stub resolving through `dicts` in order.
 * @param dicts - dictionaries consulted in order.
 * @returns the translate function, assignable to any props `t` seat.
 */
export function makeTranslate(...dicts: readonly Record<string, string>[]): (key: string, params?: Record<string, unknown>) => string {
  return (key, params) => {
    let template = key
    for (const dict of dicts) {
      const hit = dict[key]
      if (hit !== undefined) {
        template = hit
        break
      }
    }
    if (params === undefined) return template
    return template.replace(/\{(\w+)\}/gu, (match, name: string) => name in params ? String(params[name]) : match)
  }
}
