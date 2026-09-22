# Refactory

English | [中文](refactory.zh.md)

The refactory seam is the DeepSeek Harness side of a refactory outer-loop run: the optimizer that proposes edits to a Python mode, evaluates them, and keeps what improves the score. The loop itself is a Python program outside this repository's package tree, and both DSH-side readers go through one process boundary — `python -m refactory_dsh.console` — so the tool and the browser read one JSON contract. The model-facing Consumer is [dsh-tool-refactory](../../packages/refactory/tool-refactory/README.md) (`ctx.tools`); the Web picker's Host half is [dsh-refactory-console](../../packages/refactory/refactory-console/README.md) (`ctx.refactoryConsole`), which exposes that console over [Typert Remote](typert.md). The loop's own vocabulary, steps, and artifacts live in the [refactory architecture overview](../../refactory/docs/architecture.md).

Source: [`packages/refactory/refactory-console/src/index.ts`](../../packages/refactory/refactory-console/src/index.ts)

## Described choices

The console reports every choice by description, and the Remote service keeps them that way rather than reshaping them. A mode's catalogue entry carries the identifier `<package>/<mode>`, its description, whether it is scored, and the `action` field that names the verb which runs it — `start` for a scored mode, and the authoring verb `create`, `edit` or `design` for one the loop cannot search. The strategy, optimizer and evaluator names come from the three level-two registries, and the package list comes from the registry's own catalogue, which reports each package's enabled state and location on its modes. A package that declares no modes therefore reports no state rather than a guess.

## The console boundary

Both readers hold the same `Console`, whose argv assembly, spawn, JSON decode, and refusal reporting are the package's own. `resolveConsoleSettings` turns the four deployment settings — `repoRoot`, `pythonBin`, `timeoutMs`, `collectBytes` — into the absolute paths one call needs, so the tool and the service cannot disagree about where the checkout is or how long a call may take. The service keeps no state between calls: each read starts a fresh interpreter, and a run that a start began lives on in its own process and in the run directory the loop owns.

## Refusals

Each method answers with one Remote error, so a picker can say what to do instead of showing a failed process.

| Code | When |
|---|---|
| `gateway/bad-request` | An address the caller must supply is empty |
| `gateway/cancelled` | The caller's signal was already aborted |
| `refactory/unknown-mode` | `start` was asked for a mode the catalogue does not offer |
| `refactory/authoring-mode` | `start` was asked for a mode whose own `action` is an authoring verb |
| `refactory/package-disabled` | `start` was asked for a mode of a package the registry reports disabled |
| `refactory/console-failed` | The console refused, or answered with a shape it does not own |

<!-- BEGIN GENERATED cordis-surface (gen-cordis-catalog.ts) — do not edit between markers -->

<a id="cordis-surface"></a>

## Cordis API

Generated from source by `scripts/gen-cordis-catalog.ts` (verified fresh by `pnpm run verify-cordis-catalog` in doc-sync; regenerate with `pnpm run gen-cordis-catalog`) — the language sides differ only in locale-specific paired document paths. Signature blocks use a `ts cordis-catalog` fence and keep the original source JSDoc; dispatch modes are defined in the [primer](../cordis-primer.md#dispatch-modes), and the framework-inherited `ctx` API lives in [cordis-api/inherited.md](../cordis-api/inherited.md).

<a id="ctxrefactoryconsole--refactoryconsole"></a>

### `ctx.refactoryConsole` — `RefactoryConsole`

Host Remote service over the refactory console.

```ts cordis-catalog
/**
 * The modes a run can be started from, each with the verb that runs it.
 * @param signal - caller cancellation.
 * @returns the console's catalogue, as the choices it describes.
 */
@Remote async modes(signal: AbortSignal): Promise<readonly RefactoryModeChoice[]>

/**
 * The named outer loops, optimizers and evaluators a run's settings choose between.
 * @param signal - caller cancellation.
 * @returns the three registries, each a list of named choices.
 */
@Remote async strategies(signal: AbortSignal): Promise<RefactoryStrategies>

/**
 * Every run under the run root, newest first.
 * @param signal - caller cancellation.
 * @returns the runs, each with where it got to.
 */
@Remote async runs(signal: AbortSignal): Promise<readonly RefactoryRunSummary[]>

/**
 * The packages the registry knows, with the modes each declares.
 * @param signal - caller cancellation.
 * @returns the packages as described choices.
 */
@Remote async packages(signal: AbortSignal): Promise<readonly RefactoryPackageChoice[]>

/**
 * Start a run from one described mode.
 *
 * The mode must be one the catalogue offers and one whose `action` is `start`;
 * an authoring mode is refused by name, with the verb that runs it, because a
 * person who picked it needs the action rather than a failed process.
 * @param request - the mode, run addressing, and settings.
 * @param signal - caller cancellation.
 * @returns what the console recorded about the start.
 */
@Remote async start(request: RefactoryStartRequest, signal: AbortSignal): Promise<RefactoryStartReceipt>

/**
 * Where one run is, and what is new since the reader last looked.
 * @param run - the run id or absolute run directory.
 * @param after - how many progress lines the caller has already read.
 * @param signal - caller cancellation.
 * @returns the run's state, the new events, and the offset for the next look.
 */
@Remote async status(run: string, after: number, signal: AbortSignal): Promise<RefactoryRunStatus>

/**
 * Ask one run to stop.
 * @param run - the run id or absolute run directory.
 * @param signal - caller cancellation.
 * @returns whether the run's process is gone.
 */
@Remote async stop(run: string, signal: AbortSignal): Promise<RefactoryStopReceipt>

/**
 * Enable or disable one package, and answer with the catalogue as it now stands.
 * @param name - the package name, as the catalogue lists it.
 * @param enabled - the state to set.
 * @param signal - caller cancellation.
 * @returns the packages as described choices, after the change.
 */
@Remote async setPackageEnabled( name: string, enabled: boolean, signal: AbortSignal, ): Promise<readonly RefactoryPackageChoice[]>

/**
 * Remove one workspace package, and answer with the catalogue as it now stands.
 * @param name - the package name, as the catalogue lists it.
 * @param signal - caller cancellation.
 * @returns the packages as described choices, after the change.
 */
@Remote async removePackage(name: string, signal: AbortSignal): Promise<readonly RefactoryPackageChoice[]>

/**
 * Bring a package in from a directory or git URL, and answer with the catalogue
 * as it now stands.
 * @param source - the directory or git URL to import from.
 * @param name - the name to import it as; empty keeps the package's own name.
 * @param signal - caller cancellation.
 * @returns the packages as described choices, after the import.
 */
@Remote async importPackage( source: string, name: string, signal: AbortSignal, ): Promise<readonly RefactoryPackageChoice[]>
```

Source: [`packages/refactory/refactory-console/src/index.ts`](../../packages/refactory/refactory-console/src/index.ts)
<!-- END GENERATED cordis-surface -->
