#!/usr/bin/env bash
# refactory-dsh — one-command install for the self-contained single repo.
#
# The repo is a DSH fork: the DSH source sits at the repo root, the glue at
# `refactory/`, and the vendored dependencies (refactory + SRF) are git
# submodules under `refactory/vendor/`. This script sets up the Node side
# (pnpm) and the Python side (uv venv + uv pip editable installs) and
# regenerates the three agent presets, so `./refactory-dsh` then boots DSH with
# create-v2 / outer-loop / design-v2.
#
#   git clone <repo> --recurse-submodules
#   ./install.sh
#   ./refactory-dsh --port 3081 --no-open
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GLUE="$HERE/refactory"
VENDOR="$GLUE/vendor"
VENV="$HERE/.venv"

echo "== refactory-dsh install =="
echo "  repo:  $HERE"

# 1. vendored dependencies (refactory + SRF) as git submodules.
git -C "$HERE" submodule update --init --recursive

# 2. Node side: install the pnpm workspace (DSH).
if ! command -v pnpm >/dev/null 2>&1; then
  echo "  error: pnpm not found — install Node >=22.19 + corepack, then retry" >&2
  exit 1
fi
(cd "$HERE" && pnpm install --frozen-lockfile)

# 3. Python side: self-contained venv via uv (fast; `--seed` keeps pip in the
#    venv so the create-v2 mode's `pip install -e <plugin>` step still works).
if ! command -v uv >/dev/null 2>&1; then
  echo "  error: uv not found — install it (https://docs.astral.sh/uv), then retry" >&2
  exit 1
fi
if [ ! -x "$VENV/bin/python" ]; then
  echo "  creating venv at $VENV (uv)"
  uv venv "$VENV" --seed
fi
PY="$VENV/bin/python"

# 4. Python deps: the glue + the minimal semantics deps the vendored
#    refactory (`factory.workflow`/`factory.outer_loop`) and SRF ops need.
#    (No refactory/SRF runtime deps — fastapi/uvicorn/mcp/anthropic — because
#    that runtime is retired; DSH is the runtime.)
uv pip install --python "$PY" -q \
  'pydantic>=2' 'PyYAML>=6' 'structlog>=24' 'networkx>=3.6' 'filelock>=3'
uv pip install --python "$PY" -q -e "$GLUE"

# 5. editable install of the vendored refactory + SRF with `--no-deps` so their
#    package metadata exists (importlib.metadata.version('remote-factory') etc.)
#    while their imports resolve through the editable link and the minimal deps
#    above — no heavy runtime deps are pulled.
uv pip install --python "$PY" -q -e "$VENDOR/remote-factory" --no-deps
uv pip install --python "$PY" -q -e "$VENDOR/srf" --no-deps

# 6. regenerate the three agent presets + rewritten graphs.
"$PY" "$GLUE/gen_presets.py"

echo "  done. run:  $HERE/refactory-dsh --port 3081 --no-open"
