---
description: "The dsh graph bundle: one CEO Agent over a refactory mode graph, for users running one committed mode end to end."
kind: "package-bundle"
---

# @deepseek-ai/dsh-graph

## Summary

The `graph` profile layer drives one refactory mode graph end to end: it mounts the workflow spine with `executeCommands` on, replaces the base persona with the CEO wording, and adds a `run` subcommand that takes a mode and a task. You get one JSON report — the run's `best_score`, its session id, its work directory, and the CEO's final message — after the agent reaches quiescence. Install it into a profile with one `dsh plugin` command; the layer stacks over `dsh-base`, which supplies the model connection, the tool set, the session store, and the `shell` executor that deterministic nodes run in. The bundle holds no graph of its own: it loads `<mode>.graph.json` from `--graphs` at startup, so a mode is one file plus the Python ops its commands call. Choose it to run a committed mode; when the mode declares a `user` gate, the terminal answerer puts the gate to the person, so the same profile serves an unattended run and one with a person at the gate. Refactory's `python -m refactory_dsh.interactive <package>/<mode>` is the entry point that prepares that run: it lowers the mode with its intent ledger folded in and starts this profile with the terminal attached.

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

### Install into a profile

```text
dsh plugin --profile graph add <checkout>/packages/bundle/graph
dsh plugin --profile graph remove @deepseek-ai/dsh-graph
```

`graph` is not one of the shipped profile templates, so the profile must exist before a run: `dsh --profile graph` on a machine without one stops with `profile "graph" does not exist; create it with 'dsh plugin --profile graph add <package>'`. The `add` form above links the package from a checkout, because `@deepseek-ai/dsh-graph` is not published to the npm registry. The command initializes the profile over `dsh-base` and installs the package; because the manifest declares `dsh.bundle.patch`, the reconcile step appends `@deepseek-ai/dsh-graph` to `dsh.profile.bundles`, after `@deepseek-ai/dsh-base`, and the layer activates on the next start. A dependency without that declaration installs as a plain package and never becomes a layer. `remove` drops the dependency and the layer together.

### Run a mode

For normal use, run `uv run refactory ceo <package>/<mode>` from the checkout. The Python bridge lowers the selected mode, supplies its graph directory and Python environment, and enables interactive chat unless `--headless` is given. The lower-level example below retains the legacy SRF defaults and requires its own graph files; it is not the new-user quickstart. See [the interface guide](../../../refactory/docs/interfaces.md).

```text
dsh --profile graph run --mode gepa --task circle_packing --budget 20
```

`--mode` and `--task` are required, and `run` is the default subcommand. `--budget` defaults to 10 and reaches the ops as `SRF_BUDGET`; `--knobs` takes a JSON object of `OptKnob` overrides; `--provider` and `--model` select the CEO's route. `--graphs` defaults to the checkout's `refactory/vendor/srf/graphs` (or `$SRF_GRAPHS`) and must hold `<mode>.graph.json`; `--python-bin` defaults to `.venv/bin` (or `$VENV_BIN`) and must hold the Python that has `srf` and `factory` installed. `--workdir` defaults to a fresh temporary directory. A missing graph fails at startup with the path it looked for and the `srf graphs` command that generates it.

The run prints one JSON object with `mode`, `task`, `budget`, `best_score`, `session_id`, `workdir`, and `final` — the first 400 characters of the CEO's last message. `best_score` is 0 when no evaluated proposal was accepted.

### What you get

- The `run` subcommand with its mode, task, budget, graph, Python, model, and knob options.
- The CEO persona: `You are the CEO of an interactive refactory workflow, powered by the {{model}} model.`, with the working directory appended as the persona suffix.
- The [workflow spine](../../workflow/workflow-spine/README.md) row: prefix `wf`, `executeCommands: true`, `maxIterations: 500`, a one-hour node-command timeout, and a `ceoDeny` list that leaves the CEO with the `wf_*` tools. The row is host-level, so its guard covers every top-level agent on the host, which on this profile is the CEO the runner boots: agent-node workers run in processes of their own.
- The `subagent` and `subagent_fork` rows with the `ceo` preset's `toolFilter` allow-list, so a subagent started through either gets the file and web tools, `skill`, `todo_write` and `send_message`, and no `bash`.
- The worker-thread code runtime, so a node's Python op runs without blocking the agent loop.
- The item loader a `DataNode` iterates through: the spine runs `python -m refactory_dsh.items list|begin|end`, so a data-driven mode's items are selected by the same code on this profile as on the walker.
- The runner, which drives the single CEO agent to quiescence and reports the run.
- The terminal answerer, which puts a `user` gate to the person at the terminal and writes their verdict into the run's intent ledger. Without a terminal (a piped or captured stdin) it answers nothing, so a headless run refuses a person's gate rather than inventing a verdict.

-----

<a id="understand-the-implementation"></a>
## Understand the implementation

<details>
<summary>Implementation internals — click to expand</summary>

The bundle is one patch document over `dsh-base` plus three runtime plugins. The patch restates the targeted rows' whole config — the persona templates, the tools mode and the two delegation rows — and inserts five rows: the worker-thread code runtime, the `graph-startup` provider, the workflow spine, the terminal answerer, and the `graph-runner` glue. `graph-startup` parses the command line, loads the mode's graph, resolves the work directory and the node-command environment, and publishes `graphStartup`; at boot, the startup plugin also discovers the `steerCommand` and `steerArgs` via a Python probe against the `refactory.steer` entry point (using `--python-bin`'s python). The probe defaults are: `$VENV_BIN`, else `$REFACTORY_DSH_HOME/.venv/bin`, else `<cwd>/.venv/bin`. The spine row, the answerer, and the runner all inject that service, so one parse parameterizes them and the graph is inlined at boot. The YAML's `steerCommand` and `steerArgs` lines are `!!js` expressions that read from `graphStartup`; when the entry point is absent (core-only install), startup publishes `steerCommand: ''` and logs a warning, and `wf_steer` is not registered. The runner boots a single CEO agent, sends the driving prompt as a user message, waits for quiescence, flushes the session, reads `best_score` from the mode's state file in the work directory, and exits. Deterministic nodes run in-process through the `shell` executor that `dsh-base` mounts, and each routes its gate by the last non-empty stdout line. A `DataNode`'s subgraph is iterated once per item: the spine asks the loader how many items there are, materialises one before each pass and closes it after, and hands the pass's nodes over exactly as it hands over the rest of the graph. A `user` gate is not run: the spine resolves a pending, review-scoped decision through the ledger bridge, or asks the terminal/GUI answerer for a fresh answer. Interactive answers are recorded as used and never silently replayed on a later visit. `console steer --verdict` queues one answer for the current declared review inputs; changing those inputs expires it. Explicit `ceo --approve` and Python/authoring `verdicts` remain standing upfront authorization. Legacy unscoped ledger verdicts remain visible but require a new answer.

### Source map

| File | Role |
|---|---|
| [`cordis.patch.yml`](cordis.patch.yml) | The layer: the persona, tools and delegation-row overrides, then the four inserted rows |
| [`src/startup.ts`](src/startup.ts) | The `graph-startup` provider: command line, graph loading, work directory, command environment |
| [`src/index.ts`](src/index.ts) | The `graph-runner` glue: one CEO agent, quiescence, session flush, and the JSON report |

</details>

-----

<a id="further-exploration"></a>
## Further Exploration

Read these pages when you want the parts this layer composes rather than the run itself.

- [Runtime architecture](../../../refactory/docs/architecture.md) — Refactory and the installed DSH core this layer patches.
- [workflow-spine](../../workflow/workflow-spine/README.md) — the spine row and the `wf_*` tools the CEO drives.
- [refactory glue](../../../refactory/README.md) — the Python modes, graphs, and ops a node command runs.
- [Models and credentials](../../../refactory/docs/models.md) — provider configuration and nested execution.

-----

<a id="model-experience"></a>
## Model Experience

### The CEO persona and the workflow tools

#### What the model sees

The layer replaces the base persona: the prefix reads `You are the CEO of an interactive refactory workflow, powered by the {{model}} model.` and the suffix states the working directory as `Your working directory is {{cwd}}.`. The inserted workflow-spine row registers the `wf_*` tools and their prompt section, and that section plus the `ceoDeny` list are what leave the CEO on the `wf_*` tools, delegation, read-only file tools, and the agent, job, goal, todo and plan-mode controls: no shell, no file writes, no web tools or `skill`, and no `workflow` or `ralph` to start a child outside the delegation rows' allow-list. A subagent started through `subagent` or `subagent_fork` sees only its row's allow-list. The code runtime and the two graph plugins register no model-facing text of their own.

#### Token effect

One persona prefix and suffix per request, plus the `wf_*` tool definitions and their prompt section; the graph's own node content reaches the model through the tool results the CEO reads, so a mode's prompt template costs tokens once, when that node is due. The `ceoDeny` guard removes the denied tools' definitions from the CEO's requests.

#### KV Cache effect

The persona, the tool definitions, and the prompt section are fixed for the life of a profile, so they sit in the cached request prefix. A node view that names the next node changes only the message suffix, and the graph is inlined at boot, so no message in a run rewrites the prefix.

## Known Limitations and Deferred Work

<a id="known-limitations-and-deferred-work"></a>

These limits define the current headless run. They are current package constraints, not a mode-authoring backlog.

- **The `graph` profile is not auto-initialized** — `dsh --profile graph` stops with a create hint until `dsh plugin --profile graph add` has created the profile, and `@deepseek-ai/dsh-graph` is not in the npm registry, so `add` takes a path into a checkout.
- **The graph is inlined at boot** — the spine reads `ctx.graphStartup.graph`, so editing `<mode>.graph.json` during a run changes nothing; restart the run to pick up an edit.
- **Node commands run unconfined** — graph commands are trusted ops and run with full sandbox access. `ceoDeny` constrains the CEO's own tool list only. A subagent started through `subagent` or `subagent_fork` gets the delegation rows' allow-list, with no `bash`, but its `write` and `edit` are not yet confined to the run directory.
- **PTC mode is outside the allow-list** — with `DSH_TOOLS_MODE` set to a PTC mode, every agent also gets `run_code`, which runs model-written code, is added after tool filters apply and cannot be named by one.
- **`best_score` is read from the work directory** — the runner prefers the mode's known state file and otherwise takes the first JSON file with a numeric `best_score`, so a mode that records its score elsewhere reports 0.
- **One workflow per process** — the default low-level run is one-shot. `--chat` keeps a conversation between turns; the public `refactory ceo` command enables it by default.
- **Separate limits** — this profile supplies `maxIterations: 500` as a fallback; a gate can declare its own cap. `--budget` reaches operations as `SRF_BUDGET`, not as a global time/token limit.

<a id="dev-note"></a>
### Dev Note

<details>
<summary>Working context for maintainers — click to expand</summary>

None.

</details>

**Runtime invariant:** No runtime invariant companion is published because the layer carries a patch document and a one-shot runner whose only shared state is the `graphStartup` service; every inserted row's package owns that row's behavior and invariants.

A terminal model failure is printed before the next chat prompt, including the
provider's error code and message. You may send a follow-up to retry. Headless runs,
or quitting after an unsuccessful turn, return a nonzero exit and `ok: false` with
`error` in the final JSON. Success requires a completed latest turn and explicit
workflow `done`; missing status is not completion. A later successful interactive
retry can still complete normally.

When reusing a run directory, the runner preserves its previous status as
`.refactory/spine-status.previous-<new-session-id>.json` before the first turn.
Only status written by this invocation can certify its completion; old artifacts
and status history remain available for inspection.
