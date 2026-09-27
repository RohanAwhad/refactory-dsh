# Preset UI follow-up — 2026-09-27

Based on main `78acf93b1a453474f09e487bc4431c5ddfe85928` (PR #248). The new-chat picker and Agent presets settings now offer CEO and Minimal only. CEO's description explains creating, running and improving Refactory workflows, coordinating agents and returning questions and results. Both English and Chinese copy are supplied.

## Behavior and native checks

- The native new-chat picker contains exactly CEO and Minimal; selecting each updates the current choice. An unsupported old default is normalized through the native selection controller when the picker loads.
- Settings has one Agent presets navigation button and two cards. Native View and Set as default actions are retained; extra-preset authoring is disabled. The CEO composition viewer opens successfully.
- Existing sessions retain their actual preset labels. The host roster, worker presets, CLI behavior and stored defaults are unchanged by UI filtering.
- Native Chrome verification used the disposable preview home on port 3095. No model calls or execution launches were needed. Captures crop only browser chrome and surrounding application space.

| Surface | Capture |
|---|---|
| Two-choice picker and CEO description | [Picker](assets/presets-20260927/picker-dark.png) |
| Settings cards and navigation | [Settings](assets/presets-20260927/settings-dark.png) |

## Checks and portability

- GUI suite: **170 passed in 11 files**. New coverage checks filtered snapshots, unchanged source data, locale copy, stable snapshot identity, old-default fallback, native errors and actions, slot replacement/disposal, and reversible duplicate-navigation suppression.
- Production build, host/client typechecks, lint and whitespace checks passed.
- Preset components and controllers are reused through public slot registrations in the local branding plugin. The pinned shell enumerates raw section registrations, so a scoped presentation observer hides its duplicate navigation button. Recheck that bridge and the typed preset faces on upgrades.
- Two development dependencies expose existing pinned upstream types and test stores. No upstream version or compatibility-patch changes, runtime preset deletion, or new fork. The existing eight compatibility patches described in [upstream compatibility](upstream-compatibility.md) remain unchanged.
