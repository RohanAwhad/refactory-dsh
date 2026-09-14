# re:factory

English | [中文](README.zh.md)

A **self-contained single repo** that turns refactory's graph semantics into a running product: refactory defines the workflow, DSH executes it.

```
refactory (the language)  →  graph.json  →  DSH (the runtime)
   Workflow.to_dict()                        workflow-spine + CEO + subagents
```

- **refactory** (`refactory/vendor/remote-factory`) is the *language* — typed nodes, `Verdict`, `OptKnob`s — plus the outer-loop optimizer (`SwarmEngine`, 8-operator MAP-Elites).
- **SRF** (`refactory/vendor/srf`) is the first package of science modes (gepa, best_of_n, …) and tasks (circle_packing, …).
- **DSH** is this repo — an upstream-forked DeepSeek Harness with the workflow-spine, the science runner, and the re:factory brand.

Three **meta-modes** ship as refactory modes, and one DSH preset carries the loop:

| Mode | What it does |
|---|---|
| `python -m refactory_dsh.create` | create a new mode: a package of Python plus a `graph.json` |
| `python -m refactory_dsh.edit` | revise an existing mode from a stated intent |
| `python -m refactory_dsh.design` | design software (research → strategy → build → review) |
| **ceo** preset (`refactory` tool) | run and read an outer-loop run from the Web view |

The three meta-modes are Python entry points rather than presets: they write Python and a `graph.json`, which is not something a workflow graph can carry. The loop itself is a mode too — `loop_mode/loop` is its eight steps as a graph.

---

## Prerequisites

- **Node ≥ 22.19** (nvm `v22.23.2` is what it's built against)
- **pnpm** (via corepack)
- **uv** (Python package manager — creates the venv; it manages its own Python ≥ 3.11)
- **git** (with `--recurse-submodules`)

---

## Install

```bash
git clone <this-repo> --recurse-submodules
cd <this-repo>
./install.sh
```

`install.sh` does, in order:

1. `git submodule update --init --recursive` — pulls refactory + SRF
2. `pnpm install` + `pnpm run build` — the DSH workspace and its client/host bundles
3. `uv venv .venv --seed` — a self-contained venv (pip kept for the mode-creation entry points)
4. `uv pip install` the glue + the minimal semantics deps (pydantic, structlog, networkx, pyyaml, filelock)
5. `uv pip install -e … --no-deps` links `factory` (refactory) and `srf` into the venv
6. writes the Python mode packages and the `graph.json` files the science runner loads

No absolute paths are baked in — everything resolves from the repo root (`refactory/refactory_dsh/paths.py`), with `REFACTORY_DSH_HOME` / `DSH_ROOT` / `SRF_GRAPHS` / `VENV_BIN` / `NODE_BIN` overrides if you use a non-default layout.

---

<a id="run"></a> <a id="run-from-source"></a>
## Run

### Interactive (the product)

```bash
./refactory-dsh --port 3081 --no-open
```

Open the printed URL. The default preset is **ceo**, which carries the `refactory` tool: list the modes a checkout can run, start an outer-loop run, watch it while it is in flight, and read its leaderboard, candidate diffs, and artifacts — all from the conversation. For a refactory *graph*, the `wf_*` tools drive it node by node and you steer at the gates.

### Headless science runner

```bash
node --import tsx/esm apps/cli/src/bin.ts --profile science run \
  --mode gepa \
  --task circle_packing \
  --budget 1
```

prints `{"best_score": …}` and the session log. `--graphs` and `--python-bin` default to the checkout's own `.venv/bin` and `refactory/vendor/srf/graphs`, so a fresh clone needs neither flag. Pick the CEO model with `--provider` (e.g. `deepseek-official`, `openai`, `anthropic` — the pi-ai routes resolve keys from `settings.yaml` + `.env`).

### SRF: the reference package of modes

**SRF is the shipped example of how to build a *package of modes* on refactory.** It is not part of the runtime — it is a template you copy. Each mode is one `graph.json` (refactory `Workflow.to_dict()`) plus the Python ops its `FnNode`s call, all inside one package:

```
srf/
  graphs/gepa.graph.json       # a mode: nodes + edges + knobs
  srf/ops/gepa/…               # the ops that FnNode commands run
  srf/tasks/…                  # benchmark tasks (circle_packing, …)
```

To add your own mode you only touch that package: write a `graph.json`, implement its ops, drop it in `graphs/`, and it runs under the same `--profile science` runner — no change to DSH. That is the whole refactory→DSH contract: the language defines the graph, the runtime executes it, and a mode package supplies the domain logic in between.

### End-to-end example: create a mode, then optimize it

1. `python -m refactory_dsh.create` with the intent *"a basic inner loop for circle packing"*. It writes a package under `.factory/packages/`, which the registry finds on the next run.
2. Start an outer-loop run on it — from the Web view through the `refactory` tool, or `python -m refactory_dsh.loop <package>/<mode>`. It proposes edits, evaluates them, keeps what improves the score, and reports the best score it reached.

---

## Layout

```
apps/                    DSH apps (CLI, web)
packages/                DSH packages (workflow-spine, science, ui-brand-refactory, …)
refactory/               the glue
  refactory_dsh/           ops (outer_loop, scaffold) + path resolution
  graphs/                  create_v2 / outer_loop / design_v2 graph.json
  presets/                 the shipped CEO preset skeleton
  vendor/remote-factory/   refactory (git submodule)
  vendor/srf/              SRF (git submodule)
  gen_presets.py           regenerates the 3 agent presets
install.sh               one-command install
refactory-dsh            the launcher
```

---

## Development

### Regenerate the presets after editing a graph

```bash
.venv/bin/python refactory/gen_presets.py
```

### Tests

```bash
# the outer-loop faithfulness harness (8 operators, archive, reflection)
FACTORY_CLAUDE_BIN=/bin/true .venv/bin/python -m pytest refactory/tests -q

# refactory's outer-loop suite
FACTORY_CLAUDE_BIN=/bin/true .venv/bin/python -m pytest \
  refactory/vendor/remote-factory/tests/test_outer_loop -q

# the DSH client/host GUI suite
pnpm run test:gui
```

### Rebasing on upstream DSH

This repo is a **fork of `deepseek-ai/deepseek-harness`** on branch `feature/workflow-spine`. Our changes are isolated deltas so they re-apply after a rebase:

- new packages/files (e.g. `packages/client/ui-brand-refactory`, `refactory/`)
- one-line wiring edits (`cordis.patch.yml` rows, `tsconfig` references, `paths` mappings)
- two locale strings (`hero.headline`)

`refactory` + `srf` are git **submodules** (your own repos), so they rebase/update independently.

---

## Known limitations

- **`trimul` (GPU task) needs `numpy`** — not in the minimal semantics deps; the math tasks (`circle_packing`, `autocorrelation_inequality`) don't need it. Its tests `importorskip("numpy")`, so the suite passes without it.
- The logo is an inlined downscale of `remote-factory/docs/assets/refactory_logo.png`; refresh it in `packages/client/ui-brand-refactory/src/client/logo.ts` if upstream changes.
- Ships as **repo + `install.sh`**, not a single binary — bundling Node + Python runtimes into one per-platform executable was considered and deferred; the repo is already self-contained and installable.
