---
description: "Open GUI defects in the refactory-dsh fork, with the evidence gathered so far and the checks that cannot be done without seeing the screen, for a session running a vision-capable model."
kind: "package-reference"
---

# GUI handoff

Three defects were reported from a real browser session. Two were investigated
with code and one could not be: **the investigating session's model accepts no
image input**, so every conclusion about layout was drawn from CSS and geometry
rather than from looking. That is why one "fix" did not take.

This file exists so a session with a vision model can start from the evidence
instead of rediscovering it.

---

## What to look at

- **URL**: `http://127.0.0.1:3099/?token=<printed at launch>` — a second instance of
  this fork, started separately so the user's own instance on `:3081` is untouched.
  Relaunch with `./refactory-dsh --port 3099 --no-open`; it prints the authenticated
  URL.
- **Nothing here is committed as a screenshot.** Take them.

## The three defects

### 1. The hero collides with its own headline

**Reported**: "the logo and name are colliding on the main panel".

**Established from code, not from pixels:**

- The asset is the upstream **lockup** — glyph and wordmark on one `400x76` canvas,
  aspect 5.26 — inlined as a data URL in
  [`src/client/logo.ts`](packages/client/ui-brand-refactory/src/client/logo.ts). It already reads "refactory".
- The hero renders the mark through the `conversation.hero.brand.mark` slot at
  `packages/client/ui-conversation/src/client/skeleton/EmptyHero.tsx:148`, inside
  `.headline`, which is a grid:
  `grid-template-columns: 34px auto auto; column-gap: 10px`
  (`HeroShell.module.css:29`).
- **Column 2 is `hero.headline`, and `hero.headline` is literally `re:factory`**
  (`packages/client/ui-conversation/src/client/locales.ts:77`).

So the mark's column is **34px** and the next column repeats the same word. Any
5.26:1 asset rendered at the requested height is ~179px wide and overruns.

**What was tried and did NOT fix it**: sizing the image to a 34px square with
`object-fit: cover; object-position: left center`. **Reasoned to be right, never
seen.** In hindsight the likely outcome is that the crop shows a fragment of the
wordmark — still wrong, and still adjacent to the same word.

**Current state — a holding fix, not a design**: `RefactoryHeroMark` claims the
slot and renders an empty `<span aria-hidden>`. Nothing visible means nothing can
collide, and claiming the slot keeps the harness's own whale out. **Decide the real
design**: either a square glyph asset for the hero, or a change to the hero's grid
in `ui-conversation` so the mark column can hold a lockup. Neither belongs in the
brand package.

```tsx
// packages/client/ui-brand-refactory/src/client/Brand.tsx
export function RefactoryHeroMark() { return <span aria-hidden="true" /> }
```

### 2. The sidebar rendered the name twice

**Fixed, and this one is verifiable without seeing it.**

`RefactoryBrandMark` rendered the lockup (which contains the wordmark) and
`RefactoryBrandName` rendered a second `<span>refactory</span>` beside it, in a
row whose mark box is 24px with no width bound
(`ui-sidebar/src/client/SidebarRoot.module.css:114-151`). The name slot now claims
the slot and renders nothing — unclaiming it would fall back to the harness's own
name and version line, a third brand.

**Also verify visually**: at the default sidebar width (280px,
`ui-layout/src/client/columns.ts:17`) the 5.26:1 lockup at `height:24` is ~126px.
Confirm it reads as a lockup and is not clipped by `.brand`'s `overflow: hidden`.

### 3. A workspace cannot be selected

**Reported**: "it's not loading any workspace despite clicking, its showing only a
subset of modes".

**Established:**

- The picker **is** mounted: `directory-picker-auto` is in the web profile
  (`dsh --profile web --dump-config` line 404).
- All three persisted workspaces are under the **old layout**
  (`~/.dsh/storages/workspace.json`): `/Users/akash/dsh`,
  `/Users/akash/dsh/refactory-dsh`, `/Users/akash/dsh/open-jarvis`. The
  pre-consolidation tree at `~/dsh/refactory-dsh` still exists but is not this
  checkout.
- **`/Users/akash/cursor/refactory-fresh-test` is not in the list at all.**
- A preset marked **broken** renders with a badge and its click handler **returns
  early**: `packages/client/ui-agent-preset/src/client/AgentPresetSection.tsx:281`
  — `if (row.broken !== undefined) return`. A broken preset is unclickable.
- One preset *was* genuinely broken and is fixed: the `science` user preset was
  invalid YAML (`!!js '<literal>' + expr` leaves trailing junk after the tag) and
  pointed at the deleted `~/dsh/refactory-dsh`. See
  `~/.dsh/.agent-presets/science/agent.cordis.yml:270`.

**Not established, and needs the browser**: whether "clicking a workspace does
nothing" is (a) the stale list, (b) the picker failing to open, or (c) a
session-creation error that is logged only in the browser console. **Get the
console output and the network tab** — the server log is clean, so it is client
side or silent.

## The question the vision session can answer that this one could not

Open the app and look. Specifically:

1. Does the **hero** show a stray fragment, an empty gap, or nothing at all?
2. Does the **sidebar** read "re:factory" once?
3. What does the **preset picker** list, and does `ceo` carry a "broken" badge?
4. What does the **workspace picker** do on click — open a dialog, go blank, or
   nothing?
5. Any red text, error strip, or console error.

## What is already fixed and should not be re-litigated

| fix | file | how it is held |
|---|---|---|
| the sidebar name rendered twice | `src/client/Brand.tsx` | 9 tests in `tests/browser-plugin.client.spec.tsx` |
| the hero overrunning its column | same, holding fix | same |
| the `science` preset's invalid YAML and stale path | `~/.dsh/.agent-presets/science/` | it parses; the roster lists it |
| two workspaces chosen by working directory | `refactory/refactory_dsh/paths.py` | `refactory/tests/test_project_root.py` |

## Rebuilding after a client change

The GUI loads client modules at runtime from `packages/*/lib/client.js`, **not**
from the built frontend dist (`apps/web/dist` does not contain `REFACTORY_LOGO`).
After changing a client package:

```bash
pnpm run build:lib:client
```

then refresh the page. `pnpm run dev:web` also reloads client plugins live.

## Related, and still open

Not GUI defects, but next in the queue and adjacent:

- `create` / `edit` / `design` are Python entry points with **no preset**, so the
  GUI has no way to author a mode. They should come back as presets whose graph
  calls `python -m refactory_dsh.create`.
- A run does not record its winning **state**, so a winner cannot be re-run or
  adopted as a mode. See [architecture.md §11](refactory/docs/architecture.md).
