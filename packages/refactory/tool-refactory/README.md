---
description: "The model-facing refactory tool over the Python outer-loop optimizer: one action per console verb, run lifecycle, on-disk run artifacts, and the browser run card, for users and maintainers choosing, configuring, or debugging the tool."
kind: "package-reference"
---

# @deepseek-ai/dsh-tool-refactory

English | [中文](README.zh.md)

## Summary

`dsh-tool-refactory` gives the agent one tool, `refactory`, that starts and reads a refactory outer-loop run: an optimizer proposes edits to a Python mode, evaluates them, and keeps what improves the score. Use it to list the modes a checkout can run, start a run in its own process, watch that run while it is still going, and read back its leaderboard, per-candidate diffs, and on-disk artifacts. The loop is a Python program and stays one: every action runs `python -m refactory_dsh.console <verb>` through the host subprocess capability and returns that console's JSON unchanged, so the tool, the browser card, and a shell all read one answer. Its cost is a configured Python interpreter and a checkout that holds `refactory/`; because the tool reimplements nothing, the actions and artifacts it offers are exactly the ones the console exposes.

## Table of Contents

- [Use this package](#use-this-package)
- [Understand the implementation](#understand-the-implementation)
- [Further Exploration](#further-exploration)
- [Model Experience](#model-experience)
- [Known Limitations and Deferred Work](#known-limitations-and-deferred-work)
- [Dev Note](#dev-note)

-----

<a id="use-this-package"></a>
## Use this package

Mount the package in a composition that already provides the `tools`, `subprocess`, and `systemPrompt` services; the agent or the Web view then drives a run through the `refactory` tool's eight actions.

### When to choose it

Choose this package when a session should start, watch, or read a refactory run: the `ceo` preset already mounts it, and the Web view renders each call as a run card instead of a generic tool row. Avoid it when no Python interpreter can import `refactory_dsh`; every action shells out to that interpreter, and a missing one fails the call rather than degrading to a partial answer. To work inside the loop's own process, run the console entry points that `refactory/README.md` documents directly; a run started that way appears in a `runs` listing only while it lives under the configured run root.

### Minimal configuration

The package mounts as one `cordis.yml` row with no required config, and the defaults resolve the interpreter as `<repoRoot>/.venv/bin/python`. The `ceo` preset mounts the same row with `repoRoot` pinned to the process working directory, so a checkout elsewhere needs one edit there.

```yaml
- name: '@deepseek-ai/dsh-tool-refactory'
```

| Field | Default | Meaning |
|---|---|---|
| `toolName` | `refactory` | The model-facing tool name; it also names the prompt section and the browser card's dispatch key |
| `repoRoot` | the process working directory | The checkout holding `refactory/` and `.venv/`; it is also the working directory of every console call |
| `pythonBin` | `<repoRoot>/.venv/bin` | The directory holding the `python` that can import `refactory_dsh` |
| `timeoutMs` | `600000` (ten minutes) | Wall-clock ceiling for one console call, from 1 ms to one hour; it does not bound the run itself |
| `collectBytes` | `4194304` (4 MiB) | Per-stream collection cap; a longer stream keeps its tail |

### What each action does

Every action returns the console's JSON answer, from which the tool renders one short summary for the model and projects the payload for the browser card.

| Action | Also needs | What it reports |
|---|---|---|
| `modes` | — | Every mode the checkout can start, with its parameters and whether it is startable |
| `runs` | — | Every run under the run root, newest first, with its mode, best score, and whether it finished |
| `start` | `mode` | The new run's id, root, and pid; the run continues in its own process |
| `status` | `run` | The run's state, counters, best score, and the progress events after `after` |
| `artifacts` | `run` | The run's readable artifacts, with contents when `contents` is true |
| `leaderboard` | `run` | The run's candidates by score, each with its diff against its parent |
| `candidate` | `run`, `candidate` | One candidate and what it changed against the parent it came from |
| `stop` | `run` | Whether the run's process accepted a stop signal |

A call that omits the argument its action needs fails before any process starts, and a console refusal comes back as a tool error carrying the console's own reason after `refactory <action> failed:`, so the model corrects the call instead of reading a silent success.

-----

<a id="understand-the-implementation"></a>
## Understand the implementation

<details>
<summary>Implementation internals — click to expand</summary>

This section explains how one call reaches the loop and what a run leaves behind; the observable behavior is fully covered in [Use this package](#use-this-package).

### One call, one console verb

`Console.argv` builds the argv without a shell: the interpreter, `-m refactory_dsh.console`, the verb, then each option as `--flag value` (a `true` boolean becomes a bare flag, and `false` or `undefined` is dropped), then a mode or candidate id as a positional. `Console.run` spawns that argv with the working directory set to `repoRoot`, ignores stdin, collects stdout and stderr under `collectBytes`, and combines the caller's abort signal with a `timeoutMs` deadline. A nonzero exit is not thrown: the tool reads the console's own `{"error": ...}` from stdout, reports it as a tool error, and keeps the exact argv so a failure can be reproduced by hand.

### One JSON answer, two readers

The console prints one JSON document per call. The tool returns `{ action, data }`; `output.render` turns `data` into the summary lines the model reads, and `output.presentationMeta` projects the same pair into the session log. The browser card derives its row from that logged metadata, so a replayed call renders the run view rather than a sentence about the run.

### The run directory

A run is a directory of files, and the console reads it rather than a service. `start` writes `console.json` before the loop's first artifact exists, which is how an unfinished run stays visible and watchable.

| File | What it holds |
|---|---|
| `console.json` | What the console recorded about a start: run id, mode, root, pid, and settings |
| `progress.jsonl` | One JSON line per loop step; `status` tails it from the `after` offset |
| `journal.jsonl` | Every loop event, and the only place a candidate's whole state is written down |
| `run.json` | The run directory's layout version, so a reader fails loudly on a foreign layout |
| `report.json` | The finished run's summary: best score, leaderboard, lineage, and the rejection and accepted-op histograms |
| `report.md` | The same report as Markdown, the artifact a person reads first |
| `gradient.json` | The gradient facts the optimizer reads: knob attribution, cost, run failures, and accepted-op mix |
| `loss.json` | The batch's aggregated scores: best, mean, and improved |
| `rejections.jsonl` | Every proposal the validator refused, with its reasons |
| `manifest.json` | The artifact manifest behind the `artifacts` action |
| `runs/<candidate-id>/candidate.json` | One candidate's recorded evaluation |

### The outer loop

The loop names its eight steps after PyTorch: `init` builds the genesis state from the mode, `forward` executes and scores each unevaluated candidate, `loss` aggregates the batch, `backward` records gradient facts, `optimizer` proposes edits that the validator accepts or refuses, `step` applies the accepted edits to their parents, `scheduler` decides whether another epoch runs, and `report` writes the leaderboard and the histograms. Every candidate is validated before it can cost an inner run. The full contract, including the edit algebra and the validator's invariants, lives in [the Pythonic-modes plan](../../../refactory/docs/plans/pythonic-modes.md).

### Source map

| File | Role |
|---|---|
| [`src/index.ts`](src/index.ts) | Plugin entry: the `Config` schema, the eight verbs, the prompt section, the tool registration, and the model-facing summary renderer |
| [`src/console.ts`](src/console.ts) | The process boundary: argv assembly, spawn, JSON decode, and refusal reporting |

**Runtime invariant:** No runtime invariant companion is published because the package holds no durable state of its own: a call spawns one console process, returns its JSON, and the run it may have started lives in the run directory the loop owns. Every relationship an invariant could assert is either the platform's (a child process exits) or the loop's (a journal replays to the same population).

</details>

-----

<a id="further-exploration"></a>
## Further Exploration

Read these pages when the package-level contract is not enough. They move from the Python loop this tool drives, to the capability it calls, to the tool contract it satisfies.

- [Pythonic modes and the optimizer loop](../../../refactory/docs/plans/pythonic-modes.md) — the loop's steps, edit algebra, and acceptance gates; the `refactory/` project's own `README.md` is its entry point.
- [refactory group map](../README.md) — the sibling group page and its package table.
- [Subprocess capability](../../subprocess/subprocess/README.md) — the `ctx.subprocess` service this package spawns the console through.
- [Adding a tool cookbook](../../../docs/cookbook/adding-a-tool.md) — the tool registration, presentation, and Model Experience contract.
- [ceo preset](../../preset/agent-presets/presets/ceo/agent.cordis.yml) — the row that mounts this package.

-----

<a id="model-experience"></a>
## Model Experience

### The tool definition

#### What the model sees

The model sees one tool named `refactory` whose required `action` parameter selects one of eight verbs — `modes`, `runs`, `start`, `status`, `artifacts`, `leaderboard`, `candidate`, `stop` — and whose optional parameters address one run or one candidate (`run`, `runId`, `candidate`, `mode`), bound a listing (`limit`), extend an artifact read (`contents`), move the run root (`runsRoot`), poll from an offset (`after`), or pass run settings (`settings`). The description states the loop's purpose and each verb's one-line help; the arguments reach the console as `--flag value` options and positionals exactly as the model spelled them.

#### Token effect

The definition and the description are fixed for a given `toolName` and verb set, so every request that can see the tool pays the same cost, whatever the run's size. Each result adds one short summary: a count line plus one line per listed mode, run, artifact, or candidate.

#### KV Cache effect

The definition is prefix-stable while the composition and the tool's visibility are unchanged. Results append as ordinary tool messages after the reusable request prefix; renaming the tool through `toolName` changes the definition and the prompt section together, so reuse is lost from there.

### System prompt section

#### What the model sees

The package registers one system prompt section named `tool:refactory`, placed by `ctx.systemPrompt.getSectionOrder('TOOL_REFACTORY')`, and its text follows the configured `toolName`. The section states that a run starts in its own process and continues after the call returns, so the model starts a run and then watches it instead of blocking on it.

##### tool:refactory

```markdown
Use the refactory tool to run and read a refactory outer-loop run. A run starts in its own process and continues after the call returns, so start it, then watch it with the "status" action (pass an "after" offset to read only what is new) instead of blocking on it.
```

#### Token effect

Fixed cost on every request while the package is mounted and the section is visible; the text changes only with `toolName`.

#### KV Cache effect

Prefix-stable unless `toolName` changes or the composition reorders prompt sections, either of which invalidates reuse from the section onward.

## Known Limitations and Deferred Work

<a id="known-limitations-and-deferred-work"></a>

These limits define when the tool is a poor fit. They are current package constraints, not a task backlog.

- **A Python interpreter must be able to import `refactory_dsh`** — every action shells out to `<pythonBin>/python -m refactory_dsh.console`, so a missing interpreter, virtual environment, or checkout fails the call instead of degrading.
- **The run card claims the default tool name** — the browser row is registered under the wire name `refactory`, so only that `toolName` renders a run card rather than a generic tool row.
- **A run outlives the call, and nothing streams** — `start` returns as soon as the process is spawned; watching means polling `status` with an `after` offset, and `stop` only signals the process and reports whether it was alive.
- **The run root bounds what `runs` lists** — `start` refuses a run id whose directory already exists, and `runs` lists only directories under the configured root, which defaults to `<repoRoot>/.factory/loop`.
- **Every action costs one process** — the tool keeps no state between calls, so each `status` poll starts a fresh interpreter and re-imports `refactory_dsh`.
- **The reading surface is the console's** — actions and artifacts the console does not expose are unavailable, and the tool cannot read a run whose files the console cannot parse.

<a id="dev-note"></a>
### Dev Note

<details>
<summary>Working context for maintainers — click to expand</summary>

This Dev Note is working context for maintainers: open questions and directions that are not decided. It is explicitly non-authoritative — shipped behavior, limits, and accepted rationale live in the sections above, the package code, and the linked sources.

#### Open directions

- **Push instead of poll** — the run view polls `status` from an `after` offset. Tailing the progress file and pushing frames would remove the poll, but the console owns the run's layout and no design exists.
- **Per-action cards** — the browser card renders every action as flat lines. Dedicated layouts for a leaderboard's diffs and the artifact tree remain open.
- **Runs outside the run root** — `run` accepts an absolute directory while `runs` lists only the configured root; whether the two should agree is undecided.

</details>
