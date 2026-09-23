# re:factory

English | [中文](README.zh.md)

**Describe what you want; a graph of typed nodes designs, builds and improves it.**

```bash
git clone <this-repo> --recurse-submodules && cd <this-repo>
./install.sh
uv run refactory ceo "a mode that drafts a haiku and checks the syllable count" --mode create
```

That is the whole product. Everything below is what it is made of.

| layer | what it owns |
|---|---|
| **the mode** | a Python class: a graph of typed nodes, declared knobs, and a `score()`. The unit you create, run and search. |
| **the outer loop** | the search over a mode: propose typed edits → validate → evaluate → keep what improves, and report what it refused. |
| **the CEO** | one agent that drives a mode's graph node by node, spawning a subagent per model call, and stops at the gates a person owns. |
| **the panel** | the same choices in the browser: pick a mode, press Start, read the run. |

Two runtimes execute the same graph and are kept honest by running one mode on both and comparing the facts: the **walker** (in-process, what a search uses) and **DSH** (what ships). A mode is lowered to one IR — `Workflow.to_dict()` — and that JSON is the only thing the runtime sees.

---

<a id="run-from-source"></a>
## Install

**Prerequisites:** [uv](https://docs.astral.sh/uv), Node ≥ 22.19 + pnpm (corepack), git, and a DeepSeek API key in `.env` for anything that calls a model.

```bash
git clone <this-repo> --recurse-submodules
cd <this-repo>
./install.sh
```

`install.sh` does four things, in order:

1. `git submodule update --init --recursive` — the vendored language (`factory`) and reference modes (`srf`).
2. `pnpm install --frozen-lockfile && pnpm run build` — the DSH workspace and the client/host bundles the Web view serves.
3. `uv sync` — **one** Python project at the root: it creates `.venv`, installs the three local packages in editable mode and puts the `refactory` command on `.venv/bin`, pinned by `uv.lock`.
4. Creates the DSH profile a mode runs on (`dsh plugin --profile graph add …`). A profile is machine state, so this is a once-per-machine step; skip it with `REFACTORY_SKIP_DSH_PROFILE=1`.

Then check the machine, not the docs:

```bash
uv run refactory doctor
```

```
  ok    python >= 3.12        3.13.2
  ok    uv                    /Users/you/.local/bin/uv
  ok    node >= 22.19         /Users/you/.nvm/versions/node/v22.23.2/bin/node
  ok    import factory        the vendored graph language (refactory/vendor/remote-factory)
  ok    import refactory_dsh  this package
  ok    DSH profile 'graph'   /Users/you/.dsh/profiles/graph
  ok    packages of modes     14 package(s)

refactory doctor: ready — try `refactory list`, or `refactory ceo "an idea" --mode design`
```

---

<a id="run"></a>
## Use

Four steps, four commands.

###
1. Create a mode

```bash
uv run refactory ceo "one agent drafts a product blurb; one op checks its length and cliches" \
  --mode create --name blurb
```

`create` is remote-factory's **create-v2**: a research phase, a strategy phase **you approve**, a build, three reviews in parallel, an audit of their evidence, then registration and a smoke run. It writes a Python package under `.factory/packages/<name>` and registers it.

- `--mode design` (the default) designs a checked **artifact** instead of a package. It needs `--check "<command that proves it works>"`.
- `--mode edit` revises an existing mode and refuses a regression; it needs the mode
  to revise: `--mode edit --revise <package>/<mode>`.
- A name is optional: without `--name`, your words become the name.
- Ask for a model call in what you say ("one agent drafts …") and the created mode has an `agent` node. Otherwise it is deterministic — which is a feature: an offline mode is one a search can evaluate hundreds of times.
- A `data` node and a `Task` are asked for the same way, and the validate gate *checks* you got one: `--data-node` and `--task my_pack.tasks:MyTask`.

###
2. Use it

```bash
uv run refactory run blurb/writer --intent "a blurb about teapots"
```

Runs the mode on **DSH** with your terminal attached, so a gate only a person can answer reaches you. The run's directory holds `facts.json`, `trace.json`, `state.json`, `result.json`, the intent ledger and the lowered graph.

`uv run refactory package smoke <package>/<mode>` is the fast check, and it runs **without** a model on purpose — a mode with an `agent` node cannot be smoked, and says so instead of failing obscurely.

###
3. Optimize it

```bash
uv run refactory optimize blurb/writer --epochs 4 --batch-size 6 --optimizer random --seed 5
```

The **outer loop**: it proposes typed edits to the mode — `set_knob`, `duplicate`, `insert_after`, … — validates every candidate before it may cost a run, evaluates what survives, and keeps what improves. It reports the leaderboard *and* every rejection, so a run that found nothing says why.

`uv run refactory list` shows what it can search. The loop's own options, the ten strategies, seven optimizers, two evaluators and four edit presets are in [the HTML tour](refactory/docs/tutorial.html#loop).

###
4. Use it again

```bash
uv run refactory adopt --run <the run id the last command printed> --as blurb_tuned
uv run refactory run blurb_tuned/writer --intent "another blurb"
```

`adopt` writes the winning state as a new package and leaves its input untouched, so the tuned mode is a mode like any other.

### Read a run

```bash
uv run refactory runs                                       # every run, newest first
uv run refactory status --run <id>                          # where it is
uv run refactory leaderboard --run <id>                     # candidates, with diffs against their parents
uv run refactory candidate <candidate> --run <id>           # one candidate, exactly what it changed
uv run refactory best --run <id>                            # the winner, with the state that scored it
uv run refactory artifacts --run <id> --contents            # what it left on disk
uv run refactory steer --run <id> --note "keep it small"    # what the run is for, while it runs
uv run refactory stop --run <id>
```

### Manage packages

```bash
uv run refactory list                                       # every package and mode
uv run refactory package verify my_pack                     # does it import and validate
uv run refactory package smoke my_pack/<mode> --json        # run it once, offline
uv run refactory package add|import|remove|enable|disable …
```

---

## The GUI: ask instead of typing

```bash
uv run refactory ui --port 3081 --no-open
```

Open the printed `http://127.0.0.1:3081/?token=…` — the token is per-process, and the bare URL answers `401` without it.

- **The conversation** carries the `refactory` tool: *"what modes can I run?"*, *"start a run of counting_pack/fixed_step with the random optimizer"*, *"show me the leaderboard"*, *"keep the winner as a package called my_tuned"*, *"this run is for making the count land exactly"*. The CEO's coding tools are disabled on purpose: it can choose, start, read, steer and author, and cannot wander off and implement something itself.
- **The panel** (right Sidebar → **Modes**) is the same choices as clicks: the mode, the outer loop, the optimizer and the evaluator are all *described* choices read from the same console, and pressing **Start** names the run it began.
- **A gate a person owns is still a person's.** The runtime asks you — the terminal in `refactory run`, the dialog in the GUI. The model cannot answer it.

[The GUI tutorial](refactory/docs/tutorials/05-drive-it-from-the-gui.md) walks through it, and [the panel tutorial](refactory/docs/tutorials/07-pick-and-watch-from-the-panel.md) covers the widget.

---

## Documentation

| document | what it answers |
|---|---|
| **[`refactory/docs/tutorial.html`](refactory/docs/tutorial.html)** | every mode, both v2 pipelines, the outer loop and all its options, the GUI — as one generated page |
| [tutorials/](refactory/docs/tutorials/index.md) | how do I do the next thing, in order |
| [architecture.md](refactory/docs/architecture.md) | what the layers are, and which one owns a behaviour |
| [datasets.md](refactory/docs/datasets.md) | a mode that iterates items, and the Task that supplies and judges them |
| [api/](refactory/docs/api/index.md) | what every public name is for |
| [hardening.md](refactory/docs/hardening.md) | which guarantees are enforced, and by what |
| [packing-tools.md](refactory/docs/packing-tools.md) | how a mode declares a tool it needs installed |
| [contributing.md](refactory/docs/contributing.md) | how to change this repo |

The HTML tour, the API reference and the config catalog are **generated from the code**, and the commands this README shows are recipes that `uv run python -m refactory_dsh.tools.check_recipes` executes — a doc that lies fails a gate rather than a reader.

---

## What ships

**The modes.** `default_pack` is the authoring package: `create` (create-v2), `design` (design-v2) and `edit`, each with a plan-approval gate a person answers. The rest are fixtures — small, deterministic and offline on purpose, so the whole system can be tested and searched without a provider: `circle_packing`, `counting_pack`, `sympy_pack`, `lean_pack`, `review_pack`, `phase_pack`, `annealing_pack`, `loop_mode`, and `dataset_probe` (a Task-bound data node).

**The loop.** Ten strategies (ports of SRF's own loops: `default`, `best_of_n`, `reflective`, `tree_search`, `staged_curriculum`, …), seven optimizers (`random`, `hill_climb`, `evolutionary`, `archive`, `islands`, `map_elites`, `autoresearch`) and two evaluators (`walker`, `dsh`).

**The panel.** `packages/client/ui-refactory` + `packages/refactory/refactory-console`: a Typert Remote service over the same console the tool reads, and a Sidebar tab over that.

---

## Layout

```
apps/                        DSH apps (CLI, web)
packages/                    DSH packages (workflow-spine, graph bundle, ui-refactory, …)
refactory/                   the glue — the modes, the loop, the console
  refactory_dsh/               cli.py is the entry point; nn/ is the language; tools/ generates docs
  refactory_packages/          the packages of modes
  docs/                        the HTML tour, tutorials, architecture, API reference
  vendor/remote-factory/       the language (git submodule)
  vendor/srf/                  the reference modes (git submodule)
pyproject.toml               the one Python project (`uv sync`, `uv run refactory …`)
uv.lock                      the pinned environment
install.sh                   one-command install
refactory-dsh                the GUI launcher (what `refactory ui` runs)
```

A mode is a Python class in `refactory_packages/<package>/`, not a `graph.json`; the decision record is [pythonic-modes.md](refactory/docs/plans/pythonic-modes.md).

---

## Development

```bash
uv run python -m pytest refactory/tests -q          # the glue: 700+ tests, offline
pnpm run test                                        # the runtime and the client
pnpm run typecheck && pnpm run lint                  # TypeScript
pnpm run test:docs                                   # documentation gates
uv run python -m refactory_dsh.tools.check_recipes   # every command this repo documents
```

The integration tier makes real model calls:

```bash
uv run python -m pytest refactory/tests -q --run-integration   # one mode on both runtimes
uv run python -m refactory_dsh.tools.check_recipes --tier model
```

### Rebasing on upstream DSH

This repo is a **fork of `deepseek-ai/deepseek-harness`**. Our changes are isolated deltas so they re-apply after a rebase: new packages/files, one-line wiring edits (`cordis.patch.yml` rows, `tsconfig` references, `paths` mappings), and two locale strings. `factory` and `srf` are git **submodules**, so they rebase independently.

---

## Known limitations

- **A mode that calls a model costs money to run.** Nothing else does: the fixtures and the walker are offline, and the loop's `walker` evaluator never calls a model unless the mode itself has `agent` nodes.
- **`trimul` (GPU task) needs `numpy`** — not in the project's dependencies; the math tasks do not need it.
- **The panel needs the GUI from this checkout.** An installed `dsh` from before this work does not carry the client plugin; `uv run refactory ui` runs the right one.
- **Ships as a repo + `install.sh`**, not a single binary: bundling Node and Python into one per-platform executable was considered and deferred.
