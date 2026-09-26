# re:factory

Describe a class of work, and a graph of typed nodes builds a mode for it: an agentic harness that can be run and searched.

You get a mode you can run from the terminal or the browser, a loop that searches its architecture and knobs for a better one, and a report that names every edit it kept and every one it refused.

## Quickstart

```bash
git clone https://github.com/akashgit/refactory-dsh.git
cd refactory-dsh
./install.sh
uv run refactory doctor
uv run refactory package smoke circle_packing/adaptive
```

`install.sh` installs the Node workspace and one Python project, then creates the DSH profile a mode runs on. It needs Node `^22.19` or `>=24`, [uv](https://docs.astral.sh/uv) and git; it finds or installs the pinned pnpm itself. Save your provider key through **Settings → Models** before model-backed work. Nested create/edit smoke uses the managed DSH credential store; an exported or checkout `.env` key alone does not survive its deliberate secret scrub. See [model setup](refactory/docs/models.md#credentials-for-nested-execution).

Create/edit validates the full emitted mode with a **3,600-second budget per smoke
pass**. Set `--knob smoke_timeout_s=7200` on `refactory ceo --mode create` or
`--mode edit`, or set the same knob in the GUI Modes panel. The Python create/edit
entry points expose `--smoke-timeout`. This whole-mode budget is separate from a
single model completion's timeout. Lowering also resolves the enclosing CEO command
allowance from this knob (up to three passes plus ten minutes of startup overhead),
so the shell runner cannot cut it off at its ordinary ten-minute default. Other
operations keep their existing command limit. A timeout fails validation and records the
limit, elapsed time, current item when available, and preserved workdir in
`smoke.json`, including return code 124. If a revision’s source comparison times
out, `revision.json` records that incomplete pass separately and validation fails;
no non-regression result or package provenance is written for it. On a timed-out
model pass, `model: requested` describes intent, not proof that a request reached
the provider. Partial work is not a measured score. Generic non-model sandbox
commands retain their 180-second default.

`refactory doctor` checks local prerequisites and downstream credential configuration without calling a model. Its ready result does not verify authentication or quota:

```
  ok    python >= 3.12        3.13.2
  ok    uv                    /Users/you/.local/bin/uv
  ok    node >= 22.19         /Users/you/.nvm/versions/node/v22.23.2/bin/node
  ok    import refactory_dsh  this package
  ok    DSH profile 'graph'   /Users/you/.dsh/profiles/graph
  ok    packages of modes     10 package(s) under /Users/you/refactory-dsh

refactory doctor: local configuration ready (authentication not tested) — try `refactory list`, or `refactory ceo "an idea" --mode design`
```

The package count is the line that is yours rather than the tool's: it counts the packages the checkout ships plus whatever you create under `.factory/packages`. A fresh clone reports ten.

`refactory package smoke` then scores one shipped mode once, offline and in a sandbox:

```
{
  "mode": "circle_packing/adaptive",
  "package": "circle_packing",
  "exit_ok": true,
  "error": null,
  "score": 0.875,
  "iterations": 21
}
```

## What just happened

Four words run through everything here.

| word | what it is |
|---|---|
| **mode** | an agentic harness for a class of work: a Python class holding a graph of typed nodes over a `Task`, declared knobs, and a `score()` over the items. |
| **outer loop** | the search over a mode: propose typed edits, validate them, evaluate the survivors, keep what improves, and report what was refused. |
| **CEO** | the agent that authors a mode from your words, or runs the one you name. It stops at gates a person owns. |
| **panel** | the same choices in the browser: pick a mode, press Start, read the run. |

A `Task` is what makes a harness general: it lists the items, prepares each one, says what to ask for, and judges each pass. A mode's score is the share of items that came out right, not a claim about the one example that was tried. `--single` is the explicit one-off special case.

`circle_packing/adaptive` is a fixture: small, deterministic and offline on purpose, so the whole loop can be tested and searched without a provider. The mode you create from your own brief is the real thing.

## Try this next

Run a mode on the runtime that ships, with your terminal attached so a gate reaches you:

```bash
uv run refactory ceo counting_pack/fixed_step --intent "count to twenty"
```

Search a mode's knobs for a better configuration. This one is offline and deterministic, and finishes with a `best_score` above its `seed_score`:

```bash
uv run refactory optimize circle_packing/adaptive --epochs 2 --batch-size 4 --optimizer random --preset knob_only --seed 5
```

It prints `"seed_score": 0.795` and `"best_score": 0.875`. Drop `--preset knob_only` to let the search change the graph too; that can add an `agent` node, which calls a model.

Open the GUI, pick a mode and press Start:

```bash
uv run refactory ui --port 3081
```

## The command line

Every command is `uv run refactory <verb>`. `uv run refactory <verb> --help` is the current truth for one verb.

### Create and build

| command | what it does |
|---|---|
| `refactory ceo "<what you want>" --mode create --task <module:Class>` | build a harness for a class of work: the Task lists the items and judges each pass, and the produced package binds a `data` node to it. Research, a strategy you approve, a build, three parallel reviews, an audit, then register and smoke; runtime depends on model latency and the number of review/repair cycles. |
| `refactory ceo "<what you want>" --mode create --single` | the special case: a harness that runs once, for one input, with no Task binding. |
| `refactory ceo "<what you want>" --mode design --check "<command>"` | design a checked artifact instead of a package; this is the default mode. |
| `refactory ceo "<what you want>" --mode edit --revise <package>/<mode>` | revise an existing mode and refuse a regression. |
| `refactory ceo <package>/<mode>` | run that mode instead of authoring anything. |

An edit inherits the source mode's Task and data-node requirements when no new
`task_ref` or `single_shot` knob is supplied. The final `create.json` describes the
emitted package's actual bindings, including `task_refs` and bindings per mode;
its legacy `task_ref` field is populated when there is exactly one distinct Task.

| option | what it does |
|---|---|
| `--mode <mode>` | `design` (the default), `create`, `edit`, or `<package>/<mode>`. |
| `--name <name>` | the package or artifact name; without it your words become the name. |
| `--check "<command>"` | design only: run inside the artifact directory; the gate reloops until it exits 0. |
| `--revise <package>/<mode>` | edit only: the mode being revised. |
| `--task <module:Class>` | create only: the class of work the produced harness binds a `data` node to. One of `--task` or `--single` is required. |
| `--single` | create only: build a harness for one run of one thing. |
| `--knob name=value` | set one of the authoring mode's knobs for this run, e.g. `allow_regression=1`. |
| `--workdir <dir>` | where the run's evidence lands (default `.factory/interactive/<package>-<mode>`). |
| `--provider <route>` | the provider route that model is on, e.g. `deepseek-official`. |
| `--model <id>` | the model this run uses, e.g. `deepseek-v4-pro`. |
| `--approve <gate>=<value>` | answer a gate up front, e.g. `gate_strategy=proceed`, for a run with nobody at it. A gate a person would be asked at is theirs: run without this and answer it, or drive the mode node by node with the `wf_open` tool. `modes` marks a person's gate `(user)`. |
| `--headless` | run without a conversation: one task, one report, then exit, for scripts and CI. Without it the run is live — each turn prints as it happens and a line you type between turns reaches the CEO. |

The create and design QA gate uses current `BLOCKING: <finding>` statements from
all three review reports. `BLOCKING: none` is a clean verdict. Fenced transcripts
and sections headed `Previous review`, `Superseded`, or `Archived review` are
history; a `Current findings` heading resumes current findings. The synthesis
preserves the full reports and their digests, so changing a report after synthesis
requires fresh synthesis before the gate can proceed.

### Manage packages of modes

| command | what it does |
|---|---|
| `refactory list` | every package and mode this checkout can run. |
| `refactory list modes --json` | the catalogue as JSON, with each mode's `action`, parameters and guidance. |
| `refactory package verify <package>` | import every mode and validate its graph. |
| `refactory package show <package>/<mode>` | one mode, described. |
| `refactory package tools <package>` | the tools it declares, and whether this machine has them. |
| `refactory package add <dir>` | install a package from a directory (`--as` to rename). |
| `refactory package import <dir-or-git-url>` | import one, recording where it came from (`--ref`, `--as`). |
| `refactory package remove <package>` | remove it. |
| `refactory package enable/disable <package>` | include it, or leave it out of the catalogue. |
| `refactory package smoke <package>/<mode>` | run it once, in a sandbox, without a model (free). |
| `refactory package smoke <package>/<mode> --model` | score a mode whose graph has an `agent` node, by supplying a model. |

Package import uses the literal `PACKAGE.name` when available, even if the source
folder has a different name. `--as` copies the package under a new identity and
rewrites its literal self-qualified Python imports, module references and mode
identifiers. Relative imports, comments, docstrings and external dependencies are
preserved. A rename requires a direct literal `PACKAGE = Package(name=...)` (a
positional literal name also works); dynamic identities and self-qualified
f-strings must be prepared explicitly before import. Provenance records the source
path or git URL/ref and original package identity. A refused import leaves no
installed copy.

`refactory package` also accepts `--json`, `--force` and `--project`.

### Use a mode

`refactory ceo <package>/<mode>` runs a mode you already have on DSH with your terminal attached, so a gate reaches you. It is the same `ceo` verb that authors a mode; a positional that names one is what says to run it.

**A run is a live session.** The CEO's turns print as they complete, each node is reported as it runs, a gate is a prompt, and a line you type between turns reaches the CEO — which can amend a node's prompt and re-run it. `--headless` builds the older one-shot argv instead, for a script or CI, and then the run prints only its final JSON.

| command | what it does |
|---|---|
| `refactory ceo <package>/<mode> --intent "<what it is for>"` | run the mode on DSH with your terminal attached, so a gate reaches you. |
| `refactory ceo <package>/<mode> --knob name=value` | set one of the mode's knobs for this run. |
| `refactory ceo <package>/<mode> --source <dir>` | read the items from here instead of the source the mode declares. |
| `refactory ceo <package>/<mode> --source-format directory\|jsonl\|csv` | how to read `--source` (default: a directory of one item per subdirectory). |
| `refactory ceo <package>/<mode> --workdir <dir>` | where the run's evidence lands. |
| `refactory ceo <package>/<mode> --timeout <seconds>` | how long to wait at a gate; `0` waits for you. |
| `refactory ceo <package>/<mode> --model <id> --provider <route>` | the model this run uses. |
| `refactory ceo <package>/<mode> --approve <gate>=<value>` | answer a gate before the run instead of at it, for a run nobody can ask. For a gate a person owns, leave it to them: they are asked when the run reaches it. |

### Optimize it

`refactory optimize <package>/<mode>` searches a mode's architecture and knobs: propose typed edits, validate, evaluate, keep what improves.

| option | what it does | default |
|---|---|---|
| `--epochs <n>` | rounds of propose → validate → evaluate → select | 2 |
| `--batch-size <n>` | candidates proposed per epoch | 4 |
| `--optimizer <name>` | how candidates are proposed: `random`, `hill_climb`, `evolutionary`, `archive`, `islands`, `map_elites`, `autoresearch` | `autoresearch` |
| `--strategy <name>` | which outer loop drives the search: `default`, `best_of_n`, `reflective`, `reflective_sampler`, `tree_search`, `staged_curriculum`, `staged_v1`, `adaptive_meta`, `autoscientists`, `research_then_optimize` | the harness's own (`default`) |
| `--evaluator walker\|dsh` | which runtime scores a candidate: fast in-process, or the one that ships | `walker` |
| `--preset <name>` | which edits are allowed: `broad`, `structure`, `conservative`, `knob_only` | `broad` |
| `--seed <n>` | reproduce a search exactly | 0 |
| `--elite <n>` | how many parents each round proposes against | 3 |
| `--max-evaluations <n>` | a hard ceiling on evaluated candidates | 64 |
| `--holdout <fraction>` | the share of a Task's items kept out of the search and used to audit the winner afterwards; one item cannot be split, which the audit records | 0.3 |
| `--parsimony <cost>` | what each node of a candidate's graph costs its score: the search compares `raw_score − parsimony × nodes` | 0.01 |
| `--attempts <n>` | retries per batch after a refusal | 3 |
| `--runs-root <dir>` | the run directory itself; read it back with `--run <this path>` | `.factory/loop` |
| `--intent "<words>"` | what the run is for; seeds the ledger the agents read | empty |
| `--model <name>` | the model the reasoning optimizer asks (not the mode's own agents) | the configured route |
| `--provider <route>` | the provider route that model is on | your settings default |
| `--verbose` | print every event, and each candidate's own run output, not only the meaningful ones | off |

**Every candidate is searched on the train part of the items, and the winner is then re-evaluated on the held-out part.** The part travels in the run directory (`subset.json` beside each candidate), not in a knob, so a search cannot choose its own exam; the audit lands in `<run root>/holdout.json` and in the report as `{"train", "holdout", "gap", "overfit", "threshold", "instances"}`, with `overfit` true when `train − holdout` exceeds 0.15. The score the search compares has the size cost taken off it, and the unpenalised number is recorded beside it as `raw_score`. `refactory best` reports the audit, and `refactory adopt` refuses an overfit winner unless you pass `--allow-overfit`.

### Read the run, and steer it

| command | what it does |
|---|---|
| `refactory runs` | every run under the root, newest first. |
| `refactory status --run <id>` | where it is, and the events since an offset. |
| `refactory leaderboard --run <id>` | candidates by score, each with its diff against its parent. |
| `refactory candidate <id> --run <id>` | one candidate, exactly what it changed. |
| `refactory best --run <id>` | the winner, with the state that scored it and the held-out audit of whether it works beyond its training items. |
| `refactory artifacts --run <id> --contents` | what it left on disk. |
| `refactory adopt --run <id> --as <package>` | write the winner as a new package, input untouched. |
| `refactory adopt --run <id> --as <package> --allow-overfit` | adopt it anyway, with the audit recorded beside the package. |
| `refactory steer --run <id> --note "<words>" --kind info\|correction\|constraint` | add what you said to the run's intent ledger. The proposer reads the ledger before every generation, so a note steers the next one. |
| `refactory steer --run <id> --verdict <gate>=<value>` | record a gate's answer, so a resume replays it. |
| `refactory steer --run <id> --pause` / `--resume` | hold a search at its next generation boundary, or release it. |
| `refactory steer --run <id> --stop` | end a search at its next boundary, recording that a person asked. |
| `refactory steer --run <id> --keep <candidate>` | follow that candidate up: it stays a parent even once a newer batch outscores it. Add `--lineage` for its descendants too. |
| `refactory steer --run <id> --reject <candidate>` | stop searching around that candidate: it leaves the leaderboard, so it cannot be proposed from, reported best or adopted. Add `--lineage` for the whole branch. |
| `refactory steer --run <id> --adopt [--as <package>]` | write the run's current winner as a package now, without ending the search. |
| `refactory stop --run <id>` | ask a run to stop now. |

**A search says what it is doing while it does it.** `refactory optimize` prints the meaningful events as they happen — each candidate's score, the best so far, what the optimizer kept and refused with the running rule tally, each epoch's best, and the held-out audit — while the final JSON stays on stdout for a caller to parse. `--verbose` adds every event and each candidate's own run output, and `refactory status --run <id>` shows the same records, which is what the Web panel reads.

The read verbs pass through the console's own `--project`, `--runs-root`, `--after`, `--limit`, `--state` and `--contents` options.

### Everything else

| command | what it does |
|---|---|
| `refactory doctor` | whether this machine can run a mode, and what to install if not. |
| `refactory ui --port <n> --no-open` | the Web GUI (below). |

## The GUI

```bash
uv run refactory ui --port 3081 --no-open
```

It prints one line; open that URL, token and all:

```
dsh web: http://127.0.0.1:3081/?token=<per-process token>
```

The token is per-process, and the bare `http://127.0.0.1:3081/` answers `401`. Leave off `--no-open` and your browser opens for you.

1. **Pick the workspace.** Choose this checkout. The app remembers it next time.
2. **Send one message.** Any message will do — "hello". A session has to exist before the Sidebar's expand control appears in the header, which is the step that confuses people.
3. **Open the panel:** press the expand control in the conversation header (its tooltip is `Open the sidebar`), then the **Modes** tab.

The tab exists because of the pnpm patches in `patches/`: `@deepseek-ai/dsh-web-app` is patched to mount the Modes panel and its console, and `@deepseek-ai/dsh-agent-presets` to ship the `ceo` preset its default names. `install.sh` applies them, so `refactory ui` carries the panel. Without the patches the pinned harness still runs, but the Modes tab is not there.

The panel reads the same console the CLI does, so a run started in the browser is the run `refactory status --run <id>` reads back. [The GUI tutorial](refactory/docs/tutorials/05-drive-it-from-the-gui.md) walks the conversation surface, and [the panel tutorial](refactory/docs/tutorials/07-pick-and-watch-from-the-panel.md) covers the widget.

**Drive a mode step by step in the chat.** Ask the CEO to open one and walk it, for example `Open counting_pack/fixed_step and drive it one node at a time`. The CEO calls `wf_open`, then one `wf_next` per node, reporting each; its coding tools are denied while a run is open, so it cannot do the work itself. Tell it a step is wrong and it amends that node's prompt (`wf_edit set-prompt`/`append-prompt`) and re-runs it (`wf_redo`). A `user` gate is put to you as a question in the chat. One mode at a time per session; a detached search (`Start`) is unaffected.

## Documentation

| document | what it answers |
|---|---|
| **[concepts.md](refactory/docs/concepts.md)** | what a **package**, a **mode**, the **outer loop** and the **GUI** are — the mental model, before the commands |
| **[`refactory/docs/tutorial.html`](refactory/docs/tutorial.html)** | every mode, both v2 pipelines, the outer loop and all its options, the GUI — as one generated page |
| [tutorials/](refactory/docs/tutorials/index.md) | how do I do the next thing, in order |
| [architecture.md](refactory/docs/architecture.md) | what the layers are, and which one owns a behaviour |
| [datasets.md](refactory/docs/datasets.md) | a mode that iterates items, and the Task that supplies and judges them |
| [models.md](refactory/docs/models.md) | providers, model ids and `~/.dsh/settings.yaml` |
| [api/](refactory/docs/api/index.md) | what every public name is for |
| [hardening.md](refactory/docs/hardening.md) | which guarantees are enforced, and by what |
| [packing-tools.md](refactory/docs/packing-tools.md) | how a mode declares a tool it needs installed |
| [contributing.md](refactory/docs/contributing.md) | how to change this repo |

The HTML tour, the API reference and the config catalog are generated from the code, and two gates keep the commands honest without trusting the prose: the tour's commands are recipes that `uv run python -m refactory_dsh.tools.check_recipes` executes, and every flag in this README's option tables is checked against the real parser (`refactory/tests/test_cli.py`). Neither gate proves a command does what the sentence around it says; that is what the tutorials' recorded output is for.

## What ships, and where it lives

The checkout ships ten packages of modes — `circle_packing`, `counting_pack`, `sympy_pack`, `lean_pack`, `review_pack`, `annealing_pack`, `loop_mode`, `dataset_probe`, `java_build_pack`, and the authoring pack `default_pack` — plus ten outer-loop strategies, seven optimizers and two evaluators (`walker`, `dsh`). `uv run refactory list` is the current list. Most are fixtures: small, deterministic and offline on purpose. `default_pack` authors modes from a brief, and `java_build_pack/build` is the shipped example of a mode with an `agent` node.

`packages/` holds the six TypeScript packages this repository owns: the graph bundle a mode runs on, the workflow spine, the model-facing `refactory` tool, the console, and the two client bundles. `refactory/` holds the Python glue — `refactory_dsh/` is the CLI, the mode library and the outer loop, `refactory_packages/` holds the packages of modes, and `refactory/docs/` holds the documentation. `install.sh` is the one-command install, and `refactory-dsh` is the GUI launcher. [architecture.md](refactory/docs/architecture.md) says which layer owns a behaviour.

## Models

A run uses the model named by `agent-default-model` in `~/.dsh/settings.yaml`, and `--model`/`--provider` override it for one run without writing anything back. The file is hot-reloaded, and the GUI's **Settings → Models** page writes it and the credential store for you, including custom providers and a local OpenAI-compatible server. Keys come from the environment (`.env` at the checkout root) or from `~/.dsh/.credentials.yaml`.

[models.md](refactory/docs/models.md) has the routes, the settings file by hand, and the one-run examples.

## Development

```bash
uv run python -m pytest refactory/tests -q          # the Python glue, offline
pnpm run build && pnpm run test                     # the six TypeScript packages
uv run python -m refactory_dsh.tools.gen_api_reference --check   # docs match the code
```

Add `--run-integration` to pytest to make real model calls, and `--tier model` to `uv run python -m refactory_dsh.tools.check_recipes` for the documented commands that need one. [contributing.md](refactory/docs/contributing.md) is the fuller guide.

## Requirements

- Node `^22.19` or `>=24`, with npm. The installer finds the pinned pnpm or installs it.
- [uv](https://docs.astral.sh/uv) and git.
- Python 3.12 or newer; `uv sync` creates the virtual environment.
- A provider key saved through **Settings → Models** for nested model execution: `refactory ceo`, and any mode whose graph has an `agent` node.

The shipped fixture modes and the `walker` evaluator run offline, so you can try the whole loop without a key.

## Known limitations

- **A mode that calls a model costs money to run, and a search can create one.** The fixtures and the walker evaluator are offline, but the default `--preset broad` lets `optimize` add an `agent` node to a candidate, and the default `autoresearch` optimizer asks a model for proposals. Add `--optimizer random --preset knob_only` to keep a search offline.
- **The GUI needs the patches in `patches/`.** They are applied by `install.sh`; a plain install of the pinned harness alone does not carry the Modes panel.
- **`trimul` (GPU task) needs `numpy`** — not in the project's dependencies; the math tasks do not need it.
- **The console's Remote face is stated by hand, not generated.** The pinned Typert generator emits `./typert` and `./remote` only when its own protocol package is a workspace project, and a pinned install resolves that protocol from `node_modules`. The Host gateway discovers the console from its runtime `@Remote` markers and binds arguments by the method parameter names; the picker sends those same names through the Connection's generic RPC (`connection.rpc.call('/api', 'refactoryConsole/<method>', { args })`), because the pinned Client Remote assembly is a prebuilt bundle that carries no namespace for a fork-local package. The generator would remove the chance of the two drifting.
- **Ships as a repo plus `install.sh`**, not a single binary: bundling Node and Python into one per-platform executable was considered and deferred.

While a stop is active, cursor-moving skip, redo and expansion are refused before
state changes or output archival. Unpause that boundary explicitly before such
edits. Skipping a future node (or fork target) with an armed pause is also refused;
prompt amendments remain available while paused.
