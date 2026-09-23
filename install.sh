#!/usr/bin/env bash
# refactory-dsh — one-command install for the self-contained single repo.
#
# The repo is a DSH fork: the DSH source sits at the repo root, the glue at
# `refactory/`, and the vendored language (`factory`) and reference modes (`srf`)
# are git submodules under `refactory/vendor/`. This script sets up the Node side
# (pnpm) and the Python side (one `uv sync` over the root project), so after it:
#
#   uv run refactory doctor      is this machine ready?
#   uv run refactory list        what can this checkout run?
#   uv run refactory ceo "an idea" --mode design
#
#   git clone <repo> --recurse-submodules
#   ./install.sh
#   uv run refactory ceo "a mode that drafts a haiku and counts its syllables" --mode create
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"

# Node 22 is required (engines: ^22.19 || >=24); prefer the nvm install when
# present so `node` resolves to a supported version regardless of PATH.
if [ -x "$HOME/.nvm/versions/node/v22.23.2/bin/node" ]; then
  export PATH="$HOME/.nvm/versions/node/v22.23.2/bin:$PATH"
fi

echo "== refactory-dsh install =="
echo "  repo:  $HERE"

# 1. vendored dependencies (the language + the reference modes) as git submodules.
git -C "$HERE" submodule update --init --recursive

# 2. Node side: install the pnpm workspace (DSH), then build the client/host
#    bundles the web GUI serves at launch.
if ! command -v pnpm >/dev/null 2>&1; then
  echo "  error: pnpm not found — install Node >=22.19 + corepack, then retry" >&2
  exit 1
fi
(cd "$HERE" && pnpm install --frozen-lockfile && pnpm run build)

# 3. Python side: **one project, one lockfile, one command**. `uv sync` reads the
#    root `pyproject.toml`, creates `.venv`, installs the three local packages in
#    editable mode (`refactory-dsh`, the vendored `factory` and `srf`) and puts the
#    `refactory` console script on `.venv/bin` — which is why every documented
#    command is `uv run refactory …` rather than an interpreter path.
if ! command -v uv >/dev/null 2>&1; then
  echo "  error: uv not found — install it (https://docs.astral.sh/uv), then retry" >&2
  exit 1
fi
(cd "$HERE" && uv sync)

# 4. the DSH profile a mode runs on. A profile is **machine state** — it is
#    created by `dsh plugin --profile graph add <package>`, not derived from the
#    bundle — so a fresh clone that skips this can run every mode through the
#    walker and still fail the first time anything reaches DSH. Idempotent: a
#    profile that already carries the bundle is left alone, so a user's own
#    profile is never overwritten. `REFACTORY_SKIP_DSH_PROFILE=1` skips it for
#    someone who only wants the Python side and does not want ~/.dsh touched.
if [ "${REFACTORY_SKIP_DSH_PROFILE:-}" = "1" ]; then
  echo "  DSH profile 'graph': skipped (REFACTORY_SKIP_DSH_PROFILE=1)"
elif "$VENV/bin/python" -c 'from refactory_dsh import dsh_profile; raise SystemExit(0 if dsh_profile.is_ready() else 1)'; then
  echo "  DSH profile 'graph': already present"
else
  echo "  DSH profile 'graph': creating (machine state, once per machine)"
  node --import tsx/esm apps/cli/src/bin.ts plugin --profile graph add "$HERE/packages/bundle/graph"
fi

# 5. say what happened, in the words the docs use.
echo
"$VENV/bin/refactory" doctor || true

echo
echo "  done. next:"
echo "    uv run refactory list"
echo "    uv run refactory ceo \"an idea\" --mode design"
echo "    uv run refactory ui          # or: $HERE/refactory-dsh --port 3081 --no-open"
