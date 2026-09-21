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

**Decided, and seen in a browser** — the hero is deliberately text-only.
`RefactoryHeroMark` claims the slot and renders an empty `<span aria-hidden>`, so
nothing can collide and the harness's own whale stays out. The reason is the asset,
not the grid: the only artwork in the repository is an **opaque-background** lockup
(`400x76`, no alpha), the hero's next column already reads `re:factory`, and a crop
into the 34px square is a fragment of the wordmark on a dark box. A real hero mark
needs a transparent square glyph asset, which does not exist here. The blank-session
page was rendered and inspected: the headline reads "re:factory" once, with the
`Preview` badge, and no stray mark or gap.

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

**Root cause found, fixed, and seen working in a browser.** The picker was fine;
**session creation was failing**. The `ceo` preset (the launcher's default) mounts
`tool-refactory`, but `packages/bundle/web-app` never depended on
`@deepseek-ai/dsh-tool-refactory`, so the profile's healed `node_modules` mirror
carried no such package and every session failed with:

```
SessionCreateError: session create failed: agent-preset/invalid:
agent-presets: preset "ceo" failed to mount: row "tool-refactory" names a
plugin that cannot be resolved: @deepseek-ai/dsh-tool-refactory
```

The client swallowed it — `selectWorkspace(...).catch(...)` in
`ConversationRoot.tsx:309` — so clicking looked inert and the console stayed clean.
Adding the dependency fixed it: the workspace auto-selects, the composer goes live,
and the `refactory` tool answers from the conversation.

`packages/preset/agent-presets/tests/preset-plugin-dependencies.spec.ts` now asserts
that every plugin a shipped preset names is reachable from the install anchor; with
the dependency removed it fails naming `ceo: @deepseek-ai/dsh-tool-refactory`.

The rest of the report was stale rather than broken: the workspace list contains
this checkout, and the `ceo` preset carries no broken badge.

## What the browser session saw

Rendered with a headless Chromium against a second instance on `:3099`, and
inspected as an image. The recorded walkthrough is at
[`refactory/docs/assets/refactory-gui.gif`](refactory/docs/assets/refactory-gui.gif):
the workspace session with a live composer, the workspace picker, a message sent to
the CEO, and the `refactory` tool card it produced.

1. **Hero** — reads "re:factory" once with the `Preview` badge. No stray fragment,
   no empty gap.
2. **Sidebar** — the lockup top-left and the workspace list; the name appears once.
3. **Preset picker** — lists `CEO` (no broken badge) beside `Workspace Write` and
   the model route.
4. **Workspace picker** — opens a menu with the four workspaces and "Add
   workspace…", and picking one creates the session and enables the composer.
5. **No red text, error strip, or console error.**

## What is already fixed and should not be re-litigated

| fix | file | how it is held |
|---|---|---|
| the sidebar name rendered twice | `src/client/Brand.tsx` | 9 tests in `tests/browser-plugin.client.spec.tsx` |
| the hero overrunning its column | same, text-only by decision | same, and the reason is in the JSDoc |
| the `science` preset's invalid YAML and stale path | `~/.dsh/.agent-presets/science/` | it parses; the roster lists it |
| two workspaces chosen by working directory | `refactory/refactory_dsh/paths.py` | `refactory/tests/test_project_root.py` |
| a workspace click creating no session | `packages/bundle/web-app/package.json` | `packages/preset/agent-presets/tests/preset-plugin-dependencies.spec.ts` |
| the `refactory` tool had 8 of the console's verbs | `packages/refactory/tool-refactory/src/index.ts` | `tests/tool-refactory.spec.ts` |

## Rebuilding after a client change

The GUI loads client modules at runtime from `packages/*/lib/client.js`, **not**
from the built frontend dist (`apps/web/dist` does not contain `REFACTORY_LOGO`).
After changing a client package:

```bash
pnpm run build:lib:client
```

then refresh the page. `pnpm run dev:web` also reloads client plugins live.

## Related, and now reachable

Both of the gaps this section used to list are closed in the tool surface:

- **Authoring.** `create`, `edit` and `design` are reachable through the `refactory`
  tool's actions of the same name (the console forwards to the Python entry points),
  so a person can ask the CEO to create or revise a mode from the conversation.
- **The winner.** A run's state is addressable: `best` returns it and `adopt` writes
  it as a new package, both as tool actions. See
  [architecture.md §11](refactory/docs/architecture.md).
