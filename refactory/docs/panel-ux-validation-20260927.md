# Panel UX follow-up — 2026-09-27

Based on main `6e12c94ba6adcbbf08c26ac5b52782e2c9af3241` (PR #247). This follow-up separates Run and Optimize setup, adds contextual field help and expands the brief into a native dialog. It retains the original two-gear artwork and theme. The [panel guide](tutorials/07-pick-and-watch-from-the-panel.md#find-the-controls) describes the new navigation.

## Behavior and evidence

- Run shows **Run once**; Optimize shows **Start optimization**, search limits and folded advanced controls. Package, mode, inputs, routing, profile settings and the watched run remain shared. Switching views does not dispatch work. Hidden controls retain their DOM and values. The selected view is stored per tab and survives splitting or closing another dock pane.
- Field help works on hover, keyboard focus and click. Escape, scrolling or resizing dismisses it. Tooltips use a viewport-clamped portal so the panel does not clip them. Mode input descriptions come from the actual mode declaration.
- Brief is first and spans the form. Its dialog supports multiline drafts, Apply/Cancel, Escape and Command/Control+Enter. Cancel leaves the saved input unchanged; closing restores focus to the trigger. Applying text does not launch a run.
- The stock hero headline stylesheet is mounted with the Refactory hero, so plugin-effect teardown cannot leave the displayed logo without its presentation rules. Native clean reload confirmed the original logo with no stock headline or Preview badge.

Native Chrome checks used the existing disposable preview home on port 3095. No model calls or execution launches were needed for this follow-up. Checks covered both themes, a roughly 430 px panel, keyboard draft apply/cancel, reopening saved text, tooltip placement, Run/Optimize switching and persistence when a split pane was closed. Screenshots crop browser chrome and unrelated tabs; application pixels are unchanged.

| Surface | Native capture |
|---|---|
| Compact Run | [Run in a narrow panel](assets/panel-ux-20260927/run-narrow-dark.png) |
| Compact Optimize | [Optimize in a narrow panel](assets/panel-ux-20260927/optimize-narrow-dark.png) |
| Contextual help | [Evaluation limit explanation](assets/panel-ux-20260927/optimize-help-dark.png) |
| Large brief editor | [Dark](assets/panel-ux-20260927/brief-editor-dark.png), [light](assets/panel-ux-20260927/brief-editor-light.png) |

## Checks and boundaries

- `pnpm test:gui`: **163 passed in 10 files**. New tests cover intent switching, keyboard tab navigation, input identity and values, exact optimization dispatch, watched-run preservation, unsupported authoring optimization, tooltip interaction, brief apply/cancel and focus restoration. Branding tests cover hero unmount/remount.
- `pnpm build`: host/client typechecks and both production bundles passed.
- `pnpm lint` and `git diff --check`: passed.
- No upstream version, dependency, lockfile, compatibility patch, execution engine or run schema changes. All changes remain in local plugins and documentation; no upstream fork or new patch is introduced.
- Execution, graph activity and steering contracts reuse [the prior native acceptance](panel-validation-20260927.md). This UI-only follow-up does not rerun the broader historical suites or claim their baseline failures are resolved.

The setup views survive dock remounts within the tab lifetime; this does not introduce persistence across a full browser reload. Existing advanced controls and catalogue descriptions remain partly English in the Chinese shell. The title and hero presentation bridges still require inspection on upstream upgrades.
