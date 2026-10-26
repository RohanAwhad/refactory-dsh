---
description: "The right Sidebar's refactory picker tab type for the dsh web client: the console's described choices — modes, strategies and packages — a run started from them, and the status of the run being read."
kind: "package-reference"
---

# @deepseek-ai/dsh-client-ui-refactory

## Summary

The right Sidebar's picker tab type: choose a mode from the console's catalogue, set the knobs its own declaration gives it, choose the outer loop, optimizer and evaluator a run is started with, manage the packages of modes, start a run or use the mode once, and read where it got to. It is a page type reached from the guide and claims no address. Every choice it offers is a described choice the Host listed over the `refactoryConsole` Remote namespace — a mode, a strategy and a package are never typed — and the only free text it draws is what no catalogue can offer: a package import's source, and a knob whose declaration names no set of accepted values. Nothing in `ui-sidebar-right` knows this package.

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
- **The page title** — the product's name, on every page this package is mounted in, the additive page (`./refactory-dsh --additive`) included, which keeps the upstream brand: the host half translates the initial HTML's `<title>` through `webServer.tapIndex`, and the browser half keeps the document title translated as the shell retitles it, replacing only the exact terminal `DeepSeek Harness` suffix so session titles stay (`src/title.ts`, `src/client/title.ts`). The observer disposes with the plugin; recheck it on upstream upgrades.

Six source files under `src/client/`: `definition.ts` (the type), `store.ts` (what it keeps), `face.ts` (how it asks, Remote binding included), `RefactoryBody.tsx` (what it draws, with its failure-line helper), `locales.ts` (what it says), and `index.ts` (the wiring).

Its Host half is [`@deepseek-ai/dsh-refactory-console`](../../refactory/refactory-console/README.md), which runs the same `python -m refactory_dsh.console` invocations the model-facing tool runs.

<a id="the-picker"></a>
## The picker

The mount reads three lists at once over `remote.refactoryConsole`: `modes`, `strategies` and `packages`. They are stored as one read — loading, ready, or failed with the Host's reason — because a picker that offered modes without the strategies to run them would offer an unusable choice.

| Section | What it does |
|---|---|
| Start a run | A mode chosen from the catalogue (each option's own identifier), one field per knob that mode declares, then the outer loop, optimizer and evaluator chosen from the three registries, each with the description the implementation gives itself. Every declared knob is drawn, including a frozen one: frozen means the search may not move it, not that the person running the mode may not say what it is, and an input-taking mode has no other way to be given its input. A knob with declared values is a choice, a bool is the choice between the two words the console reads, a number is a number input bounded by the declaration, and the rest is text. `Start` dispatches the outer loop; `Run once` uses the mode once with the same knob values, and appears only where the console says `start` runs the mode. |
| Run once result | What the single run left: its directory, state, best score, the graph spine's own status word, the nodes it stopped with due, and what the run recorded. A run stopped at a gate a person owns says so here — the spine's `ready` or `refused` — instead of reading as a failure or a hang. A mode the console runs through an authoring verb says it cannot be run once from here, so no button is offered for it. |
| Run status | Read the started run or paste the directory reported by CEO chat or the terminal. The panel shows its identity, state, score and Task counts when recorded, plus graph progress or optimizer evaluations. Only a recognized optimizer run offers optimizer controls; an unrecognized directory stays read-only until its run data is available. A finished run offers no stop. |
| Steering | A compact latest-note preview opens the full ledger: every entry, oldest first, with its kind, timestamp and complete multiline text. Search finds past text, kinds or timestamps. A multiline note form appends under the console's three note kinds and clears only after confirmation. Waiting user gates retain their own prompts and accepted verdict buttons. |
| Packages | Every package with the number of modes it declares and its enabled state, a control to enable or disable it when the registry reported a state, a control to remove it, and an import form taking a directory or git URL and an optional name. |

A refusal lands in one place: the three codes the Host names precisely — an unknown mode, an authoring mode, a disabled package — have copy of their own, and every other failure shows the console's own message through `error.failed`.

Adopt best now accepts an optional destination package name. Leave it blank to use the console's default, or choose an unused name after an existing-destination refusal. Adoption never overwrites a package; its applied or refused receipt includes the chosen name.

For searchable modes, **Search limits** exposes epochs, candidates per epoch, and the candidate evaluation limit before Start. The displayed defaults match the console (2, 4, and 64); unchanged fields keep the console defaults. Edited values must be positive safe integers and travel as search settings, separately from harness knobs. An empty, fractional, zero, or negative field prevents Start and explains the problem; Run once remains available. These limits bound the search's candidate evaluations, not individual model calls or the final held-out audit.

State lives in the type's own store, bucketed by tab id: the choices read, the chosen mode, the knob values changed for it, the three chosen names, the run being read with its last frame and its ledger, the single run with its result, the last notice, and whether a package change is in flight. Choosing another mode resets its knobs; an in-flight or completed one-shot keeps its own result and run directory. The owner's `signal` ends a bucket: on abort the tab is forgotten, and a read or a change that settles afterwards writes nothing.

Catalogue, status, ledger and candidate reads, package changes, starts, stops and one-shot runs have separate request ownership. Reloading or starting an optimizer cannot discard a one-shot result. The tab accepts only one one-shot at a time, including when its selected mode changes. A note and a verdict both answer with the whole ledger, so the section that drew before a gesture draws what the Host said after it.

<a id="model-experience"></a>
## Model Experience

None, as this package draws the picker in the browser and registers nothing model-facing.

#### KV Cache effect

None; no Remote answer or store write reaches a model request.

## Known Limitations and Deferred Work

<a id="known-limitations-and-deferred-work"></a>

- **The watched run follows durable updates.** The run stream refreshes status and the full ledger; manual refresh is also available. An open ledger keeps its text and reading position as new entries arrive. During refresh or a failed read it retains the last successful history and labels the failure. Changing watched runs closes the old viewer and clears its cached history.
- **`Run once` holds the call for the whole run.** The console's `run` verb is synchronous — it drives the mode to quiescence before answering — so the panel shows the run in flight until it returns. The optimizer's `start` is the gesture that returns immediately.
- **Attach chat runs by their exact directory.** Paste the directory the CEO reports into **Existing run directory** and choose **Read this run**. Status and Steering follow that directory; notes and user-gate verdicts use the same ledger as the CEO. Ask the CEO to continue after recording a verdict. Graph runs do not show optimizer candidate or control buttons.
- **The import source is free text**, as is a knob whose declaration names no set of accepted values — neither has a catalogue that could offer the value.
- **Host messages are shown as the Host wrote them** — a console refusal reaches the reader in the console's English, through the localized `error.failed` line; only the three described-choice codes have copy of this package's own.
- **No automatic chat-run history.** The picker reads a run it started or one attached by directory; the Host's `runs` listing is still scoped to optimizer runs.
- **Package changes refresh the catalogue.** Enable, disable, remove and import refresh both packages and modes, so enabling a selected package makes its run controls available immediately.

<a id="dev-note"></a>
### Dev Note

<details>
<summary>Working context for maintainers — click to expand</summary>

None.

</details>

**Runtime invariant:** No companion is published. The picker's only runtime state is one Slot store per tab, written by the body that owns it and forgotten on the tab's abort signal; there is no second observation of it to compare against.

The panel displays its authoritative **Modes project** before the run controls. That path comes from the host console configuration, independent of the selected chat workspace. Catalogue loads include this identity, so controls are not offered with an unknown project.
