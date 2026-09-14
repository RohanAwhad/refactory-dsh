You are working in `/Users/akash/cursor/refactory-fresh-test/`. Always `cd` there.

**Read `GUI-HANDOFF.md` at the repo root first.** It carries the evidence for every
defect below — file and line numbers, what was verified, and what was not. Do not
re-derive it. Then read `refactory/docs/architecture.md` for what the system is.

## Why you are the one doing this

A previous session implemented the Python side and reported the GUI as working. It
was not: the user opened the Web view and reported three defects and three missing
capabilities. That session's model **accepts no image input** (`read_image` refuses
it), so it diagnosed layout from CSS and geometry instead of looking — and one fix
it "made" did not take. You can see the screen. That is the whole reason this is
your task. **Look at every state before and after you change it.**

## Part 1 — the three reported defects

**1. The hero collides with its own headline.** The hero renders the brand slot in
a `34px` grid column (`packages/client/ui-conversation/src/client/skeleton/EmptyHero.tsx:148`,
`.headline` in `HeroShell.module.css:29`) and the next column is
`hero.headline`, which is literally `re:factory`
(`ui-conversation/src/client/locales.ts:77`). The asset is a **400×76 lockup**
(glyph + wordmark, aspect 5.26), so at any requested height it is ~5× too wide.
The current state renders **nothing** in that slot — a holding fix so nothing can
collide. **Decide the real design and implement it**: either a square glyph asset,
or a change to the hero's grid so the column can hold a lockup. Say which you chose
and why.

**2. The sidebar may still be wrong.** It rendered the name twice (the lockup plus
a `<span>refactory</span>`); the name slot now renders nothing. Verify visually at
the default sidebar width (280px) and in the collapsed rail that it reads as one
brand and is not clipped by `.brand`'s `overflow: hidden`.

**3. A workspace cannot be selected.** Reported as "not loading any workspace
despite clicking, showing only a subset of modes". Established: the picker **is**
mounted (`directory-picker-auto`); all three persisted workspaces point at the
**old layout** (`~/.dsh/storages/workspace.json`: `/Users/akash/dsh`,
`/Users/akash/dsh/refactory-dsh`, `/Users/akash/dsh/open-jarvis`); **this checkout
is not in the list at all**. Also: a preset marked **broken** renders with a badge
and its click handler **returns early**
(`packages/client/ui-agent-preset/src/client/AgentPresetSection.tsx:281`), so a
broken preset is unclickable. One preset was genuinely broken and is fixed (the
`science` user preset was invalid YAML). **Not established, and it is your job:
whether the failure is the stale list, the picker not opening, or a silent
session-creation error. Get the browser console and network tab — the server log
is clean, so it is client-side or silent.**

## Part 2 — three capabilities that exist in Python and are unreachable

Full detail and acceptance criteria are in **issue #28**. Summary:

**4. Author a mode from the GUI.** `python -m refactory_dsh.create|edit|design`
work and have **no preset and no tool action**. A user cannot create a mode,
revise one, or design an artifact — which is the point of the system. The design
review (approaches considered, which was chosen and why, what it refused) should be
visible, and the result selectable immediately without a restart.

**5. Invoke and configure the outer loop as a first-class choice.** All **13 SRF
ports plus `default`** ship in `refactory_dsh/strategies.py` and are selectable —
`python -m refactory_dsh.loop --strategy`, and through the tool's
`settings.strategy` (verified: `console start … --strategy reflective` produced a
finished run). But nothing in the GUI says which strategies exist or what they do,
and the card does not show which one ran. Make it a **named choice with its
description**, make the loop's settings configurable and validated where they are
set, and show the strategy on the card and in the report.

**6. Import, remove, and manage packages and modes.** `python -m
refactory_dsh.registry` has 9 verbs — `list, show, verify, smoke, add, import,
remove, enable, disable` — and the `refactory` tool's 8 actions contain **none** of
them. Surface them, with the CLI's refusals intact: removing a package another
`requires` must fail **naming the dependent**, and removing a package must not take
out the mode a run in progress is using (or must say why it cannot).

## How to run it

```bash
./refactory-dsh --port 3099 --no-open     # this fork, from source; prints a tokenized URL
pnpm run build:lib:client                 # after ANY client change, then refresh
```

Client modules load at runtime from `packages/*/lib/client.js`, **not** from
`apps/web/dist`. Both `:3081` and `:3099` may be running; do not kill the user's.

## Constraints

- **Every acceptance criterion here is visual.** Screenshot each state, and record
  a GIF of every new flow — this repo requires one for any product-visible GUI
  change.
- Do not "fix" a layout you have not looked at. This task exists because that was
  tried.
- Do not change `ui-sidebar`, `ui-conversation`, or `ui-layout` for branding
  reasons without saying so; if the hero needs a different grid, that is a
  deliberate change to a shared surface — argue it.
- Keep the Python side green: `pytest refactory/tests/ -q` (and
  `--run-integration` for the DSH runs), `pnpm run test:docs`, `pnpm run lint`,
  `pnpm run typecheck`.
- Extend `packages/client/ui-tool/tests/refactory-row.client.spec.tsx` for any new
  card state.
- Finish by reporting what you **saw**, not only what you changed: before/after
  screenshots, and which of the six items is still open if any.
