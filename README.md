# re:factory

A **self-contained single repo** that turns refactory's graph semantics into a
running product: refactory defines the workflow, DSH executes it.

```
refactory (the language)  →  graph.json  →  DSH (the runtime)
   Workflow.to_dict()                        workflow-spine + CEO + subagents
```

- **refactory** (`refactory/vendor/remote-factory`) is the *language* — typed nodes,
  `Verdict`, `OptKnob`s — plus the outer-loop optimizer (`SwarmEngine`, 8-operator MAP-Elites).
- **SRF** (`refactory/vendor/srf`) is the first package of science modes (gepa, best_of_n, …)
  and tasks (circle_packing, …).
- **DSH** is this repo — an upstream-forked DeepSeek Harness with the workflow-spine,
  the science runner, and the re:factory brand.

Three **meta-modes** ship as DSH presets:

| Mode | What it does |
|---|---|
| **create-v2** | create a new mode (a `graph.json`) |
| **outer-loop** | optimize an existing mode via MAP-Elites |
| **design-v2** | design software (research → strategy → build → review) |

---

## Prerequisites

- **Node ≥ 22.19** (nvm `v22.23.2` is what it's built against)
- **pnpm** (via corepack)
- **Python ≥ 3.11** (for the venv)
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
2. `pnpm install` — the DSH workspace
3. creates a self-contained `.venv`
4. `pip install` the glue + the minimal semantics deps (pydantic, structlog, networkx, pyyaml, filelock)
5. links `factory` (refactory) and `srf` into the venv
6. regenerates the three agent presets + their `graph.json`

No absolute paths are baked in — everything resolves from the repo root
(`refactory/refactory_dsh/paths.py`), with `REFACTORY_DSH_HOME` / `DSH_ROOT` /
`SRF_GRAPHS` / `VENV_BIN` / `NODE_BIN` overrides if you use a non-default layout.

---

## Run

### Interactive (the product)

```bash
./refactory-dsh --port 3081 --no-open
```

Open the printed URL, then pick **create-v2**, **outer-loop**, or **design-v2**
as the agent preset. The CEO drives the graph through `wf_*` tools; you steer at
the gates.

### Headless science runner

```bash
node --import tsx/esm apps/cli/src/bin.ts --profile science run \
  --mode gepa \
  --task circle_packing \
  --budget 1 \
  --graphs refactory/vendor/srf/graphs \
  --python-bin .venv/bin
```

prints `{"best_score": …}` and the session log.

### End-to-end example: create a mode, then optimize it

1. Run **create-v2** with the intent *"create a basic inner loop for circle packing"*.
   It emits `.factory/modes/circle_packing_loop.graph.json`.
2. Run **outer-loop** with `REFACTORY_TARGET_GRAPH` pointing at that file; it
   mutates the mode's knobs/topology and reports the best score.

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

This repo is a **fork of `deepseek-ai/deepseek-harness`** on branch
`feature/workflow-spine`. Our changes are isolated deltas so they re-apply after
a rebase:

- new packages/files (e.g. `packages/client/ui-brand-refactory`, `refactory/`)
- one-line wiring edits (`cordis.patch.yml` rows, `tsconfig` references, `paths` mappings)
- two locale strings (`hero.headline`)

`refactory` + `srf` are git **submodules** (your own repos), so they rebase/update
independently.

---

## Known limitations

- **`trimul` (GPU task) needs `numpy`** — not in the minimal semantics deps; the
  math tasks (`circle_packing`, `autocorrelation_inequality`) don't need it.
- The refactory **CLI/pipeline tests** (22) fail on a `remote-factory` package
  *metadata* lookup — the submodule is `.pth`-linked, not `pip install -e`'d;
  the 174 behavioral outer-loop tests pass.
- The logo is an inlined downscale of `remote-factory/docs/assets/refactory_logo.png`;
  refresh it in `packages/client/ui-brand-refactory/src/client/logo.ts` if upstream changes.
