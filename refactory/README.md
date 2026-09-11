# refactory-dsh

A **self-contained single repo**: refactory's graph semantics → `graph.json` → DSH executes.

- **refactory** (the language, vendored at `refactory/vendor/remote-factory`) defines graphs;
  `Workflow.to_dict()` emits `graph.json`.
- **SRF** (the science modes, vendored at `refactory/vendor/srf`) supplies 13 AI-discovery harnesses.
- **DSH** (the runtime) is this repo — an upstream-forked DSH with the workflow-spine, the science
  runner, and the re:factory brand.
- **Three default meta-modes**, each a DSH agent preset: **create-v2** (create a mode),
  **outer-loop** (optimize a mode via MAP-Elites), **design-v2** (design software).

## Layout

```
apps/                    DSH apps (CLI)
packages/                DSH packages (workflow-spine, science, ui-brand-refactory, …)
refactory/               the glue (this Python package)
  refactory_dsh/           ops + path resolution
  graphs/                  create_v2 / outer_loop / design_v2 graph.json
  vendor/remote-factory/   refactory (git submodule)
  vendor/srf/              SRF (git submodule)
  gen_presets.py           regenerates the 3 agent presets
install.sh               one-command install
refactory-dsh            the launcher (opens DSH web with the 3 modes)
```

## Install

```bash
git clone <repo> --recurse-submodules
./install.sh
```

`install.sh` runs the pnpm install, creates a self-contained `.venv`, installs the glue + the
minimal semantics deps, links `factory`/`srf` into the venv, and regenerates the presets.

## Run

```bash
./refactory-dsh --port 3081 --no-open
# open the printed URL, pick create-v2 / outer-loop / design-v2 as the preset
```

Headless science runner:

```bash
node --import tsx/esm apps/cli/src/bin.ts --profile science run \
  --mode gepa --task circle_packing --budget 1 \
  --graphs refactory/vendor/srf/graphs --python-bin .venv/bin
```

The presets land in `~/.dsh/.agent-presets/{create-v2,outer-loop,design-v2}/`.
