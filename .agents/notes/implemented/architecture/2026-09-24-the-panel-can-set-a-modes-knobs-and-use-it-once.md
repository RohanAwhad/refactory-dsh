# Agent Note: The panel can set a mode's knobs and use it once

Status: implemented

English | [中文](2026-09-24-the-panel-can-set-a-modes-knobs-and-use-it-once.zh.md)

## Problem

**The panel's whole gesture was pick a mode and press Start.** `packages/client/ui-refactory` could pick a mode, choose the outer loop, optimizer and evaluator, and press **Start**. It could not set the mode's declared knobs, and it could not use a mode once.

**A mode's inputs are its declared knobs, and the panel supplied none of them.** An input path, a command to run, a target — each is a knob the mode declares. A search started from the panel therefore ran every candidate with its inputs at their declared defaults, scored 0.0 on all of them, and looked like a broken mode when it was a panel that could not supply the input (#73).

**A person who had just created a mode could only optimize it.** The console's `run` verb and the tool's matching action already used a mode once ([What stopped a creation from the panel](2026-09-23-what-stopped-a-creation-from-the-panel.md)); the panel offered no way to say "just use it and show me the result", which is the first thing anyone wants after authoring one.

**The console read the run back from a directory the runner never wrote.** Without an explicit `--workdir`, the console's `run` verb looked in `<project>/interactive/<mode>` while the runner writes `.factory/interactive/<mode>`, so the tool was told the console left no run directory while the evidence sat one path away. The existing Python test passed a `--workdir`, so the default branch was untested.

## Decision

1. **The panel draws every declared knob of the selected mode.** The modes catalogue already carried each mode's declared surface: `refactory_dsh/console.py` builds every catalogue entry with `parameters: described["knobs"]`, and `Mode.describe()` in `refactory/refactory_dsh/nn/mode.py` returns, per knob, `{value, kind, values, range, frozen, description}`. The panel draws one field per entry, shaped by kind: a knob that declares values is a choice, a `bool` is a choice over `true`/`false`, an `int`/`float` is a number input bounded by the declaration, and an open `str` is text. Frozen knobs are drawn too — frozen means the search may not move it, not that a person may not say what it is, and the input knobs are exactly the defect. Every declared knob travels as `NAME=VALUE`, including one left at its default, so what the fields show is what the run sends.

2. **`Run once` uses the mode, and reports what happened in the console's own words.** It is offered exactly when the catalogue entry's own `action === 'start'` — the field the console's `start` also refuses an authoring mode on — so an authoring mode's row offers no button that would fail. It calls a new `@Remote run(...)` on the console service ([the Web picker's Remote namespace](2026-09-24-the-web-picker-reads-the-console-over-remote.md)), which forwards to the existing `interactive` entry point. The result carries the run's spine status, the nodes still due, the spine's error when there is one, and the run's facts. A run stopped at a gate a person owns is reported as waiting on a person; the panel does not answer gates, because a gate with no person is the thing it must not fake.

3. **The console reads the run directory the runner chose.** `refactory_dsh/interactive.py` gains `default_workdir(identifier)` as the one owner of the default, and both the runner and the console's read-back use it, instead of the console computing a second guess at where a run would have gone.

4. **A failed console call keeps its decoded payload.** `packages/refactory/tool-refactory/src/console.ts`'s `ConsoleResult` failure arm gains `value`, because the console writes where a run stopped beside the reason it exited nonzero, and dropping it loses the only durable answer. That file also gains `runInvocation`, so the console's `--knob` spelling has one owner instead of one per caller.

## Consequences

**Files.** The change lives in `packages/refactory/refactory-console/src/{types.ts,index.ts}`, `packages/refactory/tool-refactory/src/console.ts`, `packages/client/ui-refactory/src/client/{store.ts,face.ts,RefactoryBody.tsx,locales.ts,index.ts,RefactoryBody.module.css}`, the `ui-refactory` and `refactory-console` READMEs with their pairing records, `refactory/refactory_dsh/{console.py,interactive.py}`, and the tests under those packages.

**Copy is locale-owned in both languages.** The new knob fields, the `Run once` control and the stopped-at-a-gate line exist in English and Chinese, and both are reached through the package's typed dictionary.

**Evidence.** `pnpm run test:gui` reported 340 files / 4762 passed / 1 skipped; `pnpm run verify-client-ui-i18n` passed; `pnpm run typecheck` regenerated the Typert remote face, which now declares `run`; the narrowed instrumented coverage showed every touched file at 100%; and the new Python test `test_a_single_run_is_read_back_from_the_directory_the_runner_chose` in `refactory/tests/test_gui_loop.py` fails with `KeyError: 'run'` before the read-back fix.

**Two things remain open.** No browser recording of the flow was made, because no browser driver is reachable in this environment. `packages/refactory/tool-refactory/src/index.ts` has coverage gaps that the narrowed instrumented run attributes to code this change did not touch.

## Alternatives considered

**Drawing fields only for knobs a search may move.** Rejected: the frozen knobs are the mode's inputs, which is the defect being fixed, and a form that omitted them would leave the panel unable to supply the input it exists to supply.

**Reporting the one-shot result through the optimizer's status vocabulary.** Rejected: a single run has a spine status and a stop reason, and inventing a second vocabulary for the panel is how two answers to one question start.

**Letting the panel answer a gate.** Rejected: a gate a person owns is answered by a person, and the panel would have to claim an identity it does not have.

**Having the console guess the default run directory rather than ask the runner.** Rejected: two computations of one path is what produced the bug.
