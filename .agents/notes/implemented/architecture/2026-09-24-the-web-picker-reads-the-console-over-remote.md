# Agent Note: The Web picker reads the console through one Remote service

Status: implemented

English | [中文](2026-09-24-the-web-picker-reads-the-console-over-remote.zh.md)

## Problem

Issue #49 asked for two ways to reach a refactory run: a person asks the CEO in words, and a person picks from the Web. The conversation half shipped as the `refactory` tool. The picker half had no way to exist: every DSH-side reader of the console was a tool, and a tool is reached by a model call, so a person pressing a button had nothing to press. The console's own answer is the right set of choices — modes carry the `action` that runs them, the level-two registries carry names with their own descriptions, the registry carries each package's enabled state — but none of it crossed the Host/browser boundary.

## Decision

**A second Host reader, as a Typert Remote service.** `@deepseek-ai/dsh-refactory-console` subclasses `TypertRemoteService`, injects `subprocess` and `typert`, and registers `ctx.refactoryConsole`. Its methods are the picker's vocabulary: `modes`, `strategies`, `runs`, `packages`, `start`, `status`, `stop`, and the three package changes, each answering with the registry's catalogue as it now stands.

**One process boundary, two callers.** The service holds the same `Console` the tool holds, from `@deepseek-ai/dsh-tool-refactory/console`, and resolves the four deployment settings through the shared `resolveConsoleSettings`, so the tool and the service cannot disagree about the checkout, the interpreter, or the timeout. `startInvocation` and `registryInvocation` moved into that module for the same reason: the console's flags have one owner. The tool keeps its own `Config` schema and its own fields, because schemastery's only object composition is a mutating `set`, and the shared defaults table (`CONSOLE_DEFAULTS`) is what keeps the caps single-sourced.

**The described choices are the console's, unchanged.** The catalogue's `action` field decides which verb runs a mode, so `start` reads it rather than re-deriving it: an authoring mode is refused with the verb that runs it, before any process starts. A package that declares no modes reports `enabled: null` rather than a guess, because the registry reports that state on each mode.

**The browser half is one right-pane tab type.** `@deepseek-ai/dsh-client-ui-refactory` registers kind `refactory` into `ctx.sidebarRightTabs` and its body into the keyed `sidebar.right.pane.tab` seat, the same two-stage path `ui-sidebar-files` uses. The body draws only described choices: a mode, a strategy, an optimizer and an evaluator are selected, never typed. The one free-text field is the import source, which no catalogue can offer.

## Consequences

The service is the third reader of one JSON contract, and the panel renders what the tool returns. A person who picks a mode in the pane and a model that calls `start` run the same console verb with the same flags, and a console refusal reads the same in both places — the panel shows the console's own message for everything except the three codes the service raises itself.

Status is read on demand: the panel reads one frame when a run starts and when the reader asks again. A live-updating run view needs a poll cadence the picker does not own, and the Host's `runs` listing has no screen yet; both are recorded under the panel's known limitations.

The subsystem gained a page: [Refactory](../../../../docs/subsystems/refactory.md) owns the seam, the described choices, and the refusal codes, and the `ctx.refactoryConsole` reference is generated there.

## Alternatives considered

**Putting the console behind the existing tool.** A Remote method that called the tool would have made a person's gesture a tool call, and a tool call without a model turn has no caller: the panel would have had to fabricate a tool invocation and read a model-facing result. The console is not a tool's private implementation; it is a process boundary with a JSON contract, and the service reads it directly.

**Reimplementing the console reads in TypeScript.** Parsing run directories, reading progress files, and re-deriving which modes are startable would have been a second answer to questions the console already answers, and the two answers would drift the first time the loop's layout changed.

**Exposing the console from the CEO preset instead of the host composition.** The preset mounts per session, so its services are not what the Typert gateway enumerates; the host composition is where every other Remote namespace is mounted, and the `web-app` patch is where this one is.

**Letting the panel refuse a non-startable mode in the browser.** The panel would then own a copy of the rule the console already states, and a mode that became unstartable between two reads would be refused by the wrong half. The panel offers every described mode; the Host refuses, with the action named.
