#!/usr/bin/env bash
# refactory-dsh — one-command install for the self-contained single repo.
#
# The repo is a DSH fork: the DSH source sits at the repo root, the glue at
# `refactory/`, and the vendored dependencies (refactory + SRF) are git
# submodules under `refactory/vendor/`. This script sets up the Node side
# (pnpm) and the Python side (uv venv + uv pip editable install), so
# `python -m refactory_dsh.registry` lists the packages of modes and
# `python -m refactory_dsh.loop` optimizes one.
#
#   git clone <repo> --recurse-submodules
#   ./install.sh
#   ./refactory-dsh --port 3081 --no-open
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GLUE="$HERE/refactory"
VENDOR="$GLUE/vendor"
VENV="$HERE/.venv"

# Node 22 is required (engines: ^22.19 || >=24); prefer the nvm install when
# present so `node` resolves to a supported version regardless of PATH.
if [ -x "$HOME/.nvm/versions/node/v22.23.2/bin/node" ]; then
  export PATH="$HOME/.nvm/versions/node/v22.23.2/bin:$PATH"
fi

echo "== refactory-dsh install =="
echo "  repo:  $HERE"

# 1. vendored dependencies (refactory + SRF) as git submodules.
git -C "$HERE" submodule update --init --recursive

# 2. Node side: install the pnpm workspace (DSH), then build the client/host
#    bundles the web GUI serves at launch.
if ! command -v pnpm >/dev/null 2>&1; then
  echo "  error: pnpm not found — install Node >=22.19 + corepack, then retry" >&2
  exit 1
fi
(cd "$HERE" && pnpm install --frozen-lockfile && pnpm run build)

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

# 4. Python deps: the glue and the packages of modes it discovers.
uv pip install --python "$PY" -q 'structlog>=24'
uv pip install --python "$PY" -q -e "$GLUE"

# 5. the modes the registry lists and the loop runs.
echo "  packages installed:"
"$PY" -m refactory_dsh.registry list || true

echo "  done. run:  $HERE/refactory-dsh --port 3081 --no-open"
