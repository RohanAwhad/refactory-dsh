---
description: "The refactory console as a Typert Remote service: the described choices a Web picker offers — modes, strategies and packages — plus starting a run, reading its status, and stopping it, for users and maintainers of the picker."
kind: "package-reference"
---

# @deepseek-ai/dsh-refactory-console

English | [中文](README.zh.md)

## Summary

`dsh-refactory-console` exposes the refactory console over Typert Remote: it lists the choices a run can be made from — the modes a checkout can start, the outer loops, optimizers and evaluators a run's settings choose between, and the packages of modes the registry knows — and it starts, reads and stops a run. It is the Web picker's Host half: it holds the same `Console` the model-facing `tool-refactory` holds, mounts the same deployment settings, and runs the same `python -m refactory_dsh.console` invocations, so a choice a person makes in the panel is the choice the model makes through the tool. Its cost is a configured Python interpreter and a checkout that holds `refactory/`; because it reimplements nothing, the choices and refusals it reports are exactly the console's.

## Table of Contents

- [What it exposes](#what-it-exposes)
- [The described choices](#the-described-choices)
- [Refusals](#refusals)
- [Model Experience](#model-experience)
- [Known Limitations and Deferred Work](#known-limitations-and-deferred-work)
- [Dev Note](#dev-note)

-----

<a id="what-it-exposes"></a>
## What it exposes

Mount the package in a composition that provides the `subprocess` and `typert` services; it registers `ctx.refactoryConsole` and the `refactoryConsole` Remote namespace.

| Method | Answers with |
|---|---|
| `modes` | Every mode the catalogue offers, each with the verb that runs it (`action`), whether it is scored and whether its package is enabled |
| `strategies` | `outer_loops`, `optimizers` and `evaluators`, each a list of named choices with what they say they are |
| `runs` | Every run under the run root, newest first |
| `packages` | Every package the registry knows, with the modes it declares |
| `start` | What the console recorded about the start: run id, root, pid, settings and log |
| `status` | The run's state, its new progress events, the offset to read from next, its counters and its best score |
| `stop` | Whether the run's process accepted a stop signal |
| `setPackageEnabled`, `removePackage`, `importPackage` | The package catalogue as it stands after the change |

### Configuration

The four deployment settings are the model-facing tool's, declared from one defaults table so a cap can only change in one place.

| Field | Default | Meaning |
|---|---|---|
| `repoRoot` | the process working directory | The checkout holding `refactory/` and `.venv/`; it is also the working directory of every console call |
| `pythonBin` | `<repoRoot>/.venv/bin` | The directory holding the `python` that can import `refactory_dsh` |
| `timeoutMs` | `600000` (ten minutes) | Wall-clock ceiling for one console call |
| `collectBytes` | `4194304` (4 MiB) | Per-stream collection cap; a longer stream keeps its tail |

<a id="the-described-choices"></a>
## The described choices

Every list this service returns is a list of described choices, so a picker never has to ask a person to type a name it could have offered. `modes` reads the console's own catalogue and keeps the fields a chooser reads — the identifier, the description, the verb that runs the mode, and whether it is startable — while the rest of the console's entry, including a mode's knobs, nodes and ports, stays on the wire untouched. `strategies` reads the three level-two registries, and `packages` reads the registry's own catalogue, grouping a package's modes under its name and reporting the enabled state each mode carries.

A package that declares no modes reports `enabled: null` rather than a guess: the registry reports that state on each mode, and an empty package names none.

<a id="refusals"></a>
## Refusals

Each method answers with one `RemoteError`, so a picker can say what to do instead of showing a failed process.

| Code | When | What the caller should do |
|---|---|---|
| `gateway/bad-request` | An address the picker must supply is empty | Supply the mode, run or package name |
| `gateway/cancelled` | The caller's signal was already aborted | Nothing; the tab is gone |
| `refactory/unknown-mode` | `start` was asked for a mode the catalogue does not offer | Read `modes` and choose one of them |
| `refactory/authoring-mode` | `start` was asked for a mode whose `action` is `create`, `edit` or `design` | Use that action; details carry it |
| `refactory/package-disabled` | `start` was asked for a mode of a disabled package | Enable the package first |
| `refactory/console-failed` | The console refused, or its answer had the wrong shape | Read the message; `details.argv` reproduces the call by hand |

<a id="model-experience"></a>
## Model Experience

None, as this package registers no tool, prompt section, or model-visible input: it is the Web picker's Host half, and everything it returns travels over Typert Remote to the browser.

#### KV Cache effect

None; no Remote answer reaches a model request.

## Known Limitations and Deferred Work

<a id="known-limitations-and-deferred-work"></a>

- **A Python interpreter must be able to import `refactory_dsh`** — every method shells out to `<pythonBin>/python -m refactory_dsh.console`, so a missing interpreter, virtual environment, or checkout fails the call instead of degrading.
- **The reading surface is the console's** — modes, strategies, packages and status are exactly what `refactory_dsh/console.py` exposes; the service reshapes none of it.
- **Console refusals are reported in the console's own words** — a message the console writes in English reaches the browser unchanged, and only the codes this service raises itself have localized copy.
- **Nothing streams** — `status` is a poll with an `after` offset; a run that continues after the last frame is not pushed anywhere.
- **One call, one process** — the service keeps no state between calls, so each read starts a fresh interpreter.

<a id="dev-note"></a>
### Dev Note

<details>
<summary>Working context for maintainers — click to expand</summary>

None.

</details>

**Runtime invariant:** No companion is published. The service holds one `Console` and no durable state of its own: every relationship an invariant could assert is either the platform's (a child process exits) or the loop's (a run directory replays to the same population).
