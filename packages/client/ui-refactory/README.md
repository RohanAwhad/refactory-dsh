---
description: "The right Sidebar's refactory picker tab type for the dsh web client: the console's described choices — modes, strategies and packages — a run started from them, and the status of the run being read."
kind: "package-reference"
---

# @deepseek-ai/dsh-client-ui-refactory

English | [中文](README.zh.md)

## Summary

The right Sidebar's picker tab type: choose a mode from the console's catalogue, choose the outer loop, optimizer and evaluator a run is started with, manage the packages of modes, start a run, and read where it got to. It is a page type reached from the guide and claims no address. Every choice it offers is a described choice the Host listed over the `refactoryConsole` Remote namespace — a mode, a strategy and a package are never typed — and the one thing no catalogue can offer, a package import's source, is the only free-text field it draws. Nothing in `ui-sidebar-right` knows this package.

## Table of Contents

- [What it registers](#what-it-registers)
- [The picker](#the-picker)
- [Model Experience](#model-experience)
- [Known Limitations and Deferred Work](#known-limitations-and-deferred-work)
- [Dev Note](#dev-note)

-----

<a id="what-it-registers"></a>
## What it registers

- **The type** — `ctx.sidebarRightTabs.register(...)` with kind `refactory`, id `@deepseek-ai/dsh-client-ui-refactory`, band `builtin`, no patterns, and one guide entry (order 10, titled from the `sidebarRefactory` namespace) that opens the type.
- **The body** — the keyed `sidebar.right.pane.tab` seat under that id: the picker, with its one control, reload, at the right of its header row.

Six source files under `src/client/`: `definition.ts` (the type), `store.ts` (what it keeps), `face.ts` (how it asks, Remote binding included), `RefactoryBody.tsx` (what it draws, with its failure-line helper), `locales.ts` (what it says), and `index.ts` (the wiring).

Its Host half is [`@deepseek-ai/dsh-refactory-console`](../../refactory/refactory-console/README.md), which runs the same `python -m refactory_dsh.console` invocations the model-facing tool runs.

<a id="the-picker"></a>
## The picker

The mount reads three lists at once over `remote.refactoryConsole`: `modes`, `strategies` and `packages`. They are stored as one read — loading, ready, or failed with the Host's reason — because a picker that offered modes without the strategies to run them would offer an unusable choice.

| Section | What it does |
|---|---|
| Start a run | A mode chosen from the catalogue (each option's own identifier), then the outer loop, optimizer and evaluator chosen from the three registries, each with the description the implementation gives itself. The button appears once a mode is chosen; the Host decides whether that mode can run, and a refusal comes back as the notice line. |
| Run status | After a start, the run's root, state, best score (or a stated absence) and evaluation count, with a gesture to read the next frame from the cursor and one to ask the run to stop. A run that has finished offers no stop. |
| Packages | Every package with the number of modes it declares and its enabled state, a control to enable or disable it when the registry reported a state, a control to remove it, and an import form taking a directory or git URL and an optional name. |

A refusal lands in one place: the three codes the Host names precisely — an unknown mode, an authoring mode, a disabled package — have copy of their own, and every other failure shows the console's own message through `error.failed`.

State lives in the type's own store, bucketed by tab id: the choices read, the chosen mode, the three chosen names, the run being read with its last frame, the last notice, and whether a package change is in flight. The owner's `signal` ends a bucket: on abort the tab is forgotten, and a read or a change that settles afterwards writes nothing.

<a id="model-experience"></a>
## Model Experience

None, as this package draws the picker in the browser and registers nothing model-facing.

#### KV Cache effect

None; no Remote answer or store write reaches a model request.

## Known Limitations and Deferred Work

<a id="known-limitations-and-deferred-work"></a>

- **Status is read on demand.** A run in flight does not update itself: the reader presses refresh (or starts a run, which reads the first frame). Live polling needs a cadence the picker does not own yet.
- **The import source is free text** — a directory or git URL is the one choice the registry's own catalogue cannot offer, so the import form is the only typed field.
- **Host messages are shown as the Host wrote them** — a console refusal reaches the reader in the console's English, through the localized `error.failed` line; only the three described-choice codes have copy of this package's own.
- **No run history.** The picker reads the run it started; the `runs` listing exists on the Host and no screen offers it yet.
- **A package change re-reads the whole catalogue** — enable, disable, remove and import each answer with the package list as it now stands, and the mode catalogue is not re-read, so a newly imported package's modes appear after a reload.

<a id="dev-note"></a>
### Dev Note

<details>
<summary>Working context for maintainers — click to expand</summary>

None.

</details>

**Runtime invariant:** No companion is published. The picker's only runtime state is one Slot store per tab, written by the body that owns it and forgotten on the tab's abort signal; there is no second observation of it to compare against.
