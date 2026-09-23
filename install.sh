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

# ── prerequisites ────────────────────────────────────────────────────────────
# The script satisfies what it can instead of asking the reader to: Node has to be
# there (it is a runtime, not a shim), but pnpm is pinned by `packageManager` in
# package.json, and corepack can run that pin *without* the reader enabling anything.
#
# The failure this exists for: a machine with Node 22 and corepack but no `pnpm` on
# PATH stopped at "pnpm not found — install Node >=22.19 + corepack", which is advice
# the reader had already followed.
if ! command -v node >/dev/null 2>&1; then
  echo "  error: no node on PATH. Install Node >=22.19 (https://nodejs.org, or nvm:" >&2
  echo "         nvm install 22 && nvm use 22) and run this script again." >&2
  exit 1
fi
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
NODE_MINOR="$(node -p 'process.versions.node.split(".")[1]')"
if [ "$NODE_MAJOR" -lt 22 ] || { [ "$NODE_MAJOR" -eq 22 ] && [ "$NODE_MINOR" -lt 19 ]; }; then
  echo "  error: node $(node --version) is too old; this workspace needs >=22.19 or >=24." >&2
  echo "         nvm install 22 && nvm use 22" >&2
  exit 1
fi
echo "  node:  $(node --version)"
# The even releases are the ones this workspace is tested on. An odd major is a
# current release, not LTS, and it may be younger than the packages here — worth
# saying before a build failure rather than after one.
if [ $((NODE_MAJOR % 2)) -eq 1 ] && [ "$NODE_MAJOR" -gt 22 ]; then
  echo "  note:  node $(node --version) is a current release; 22 and 24 are what this workspace is tested on." >&2
  echo "         If the build below fails, try: nvm install 22 && nvm use 22" >&2
fi

# The pnpm version this workspace pins, read from its own manifest.
PNPM_PIN="$(sed -n 's/.*"packageManager"[[:space:]]*:[[:space:]]*"pnpm@\([^"]*\)".*/\1/p' "$HERE/package.json" | head -1)"
PNPM_PIN="${PNPM_PIN:-latest}"

# One way to run pnpm, out of three, in the order that needs the least from the
# reader: the shim if it is on PATH; else corepack, which runs the pin without
# anything being enabled; else npx, which needs only the npm that ships with Node.
# Corepack is *not* bundled from Node 25 on, so npx is the route that always exists.
PNPM=(pnpm)
if ! command -v pnpm >/dev/null 2>&1; then
  if command -v corepack >/dev/null 2>&1; then
    echo "  pnpm:  not on PATH; using corepack with the pinned pnpm@$PNPM_PIN"
    PNPM=(corepack pnpm)
  elif command -v npx >/dev/null 2>&1; then
    echo "  pnpm:  neither pnpm nor corepack is on PATH; using npx to run pnpm@$PNPM_PIN"
    PNPM=(npx --yes "pnpm@$PNPM_PIN")
  else
    echo "  error: no way to run pnpm: neither pnpm, nor corepack, nor npx is on PATH." >&2
    echo "         Install it once, then run this script again:" >&2
    echo "           npm install -g pnpm@$PNPM_PIN" >&2
    exit 1
  fi
fi
"${PNPM[@]}" --version >/dev/null 2>&1 || {
  echo "  error: \`${PNPM[*]}\` did not run. Install it once with:" >&2
  echo "           npm install -g pnpm@$PNPM_PIN" >&2
  exit 1
}

# 1. vendored dependencies (the language + the reference modes) as git submodules.
git -C "$HERE" submodule update --init --recursive

# 2. Node side: install the pnpm workspace (DSH), then build the client/host
#    bundles the web GUI serves at launch.
(cd "$HERE" && "${PNPM[@]}" install --frozen-lockfile && "${PNPM[@]}" run build)

# 3. Python side: **one project, one lockfile, one command**. `uv sync` reads the
#    root `pyproject.toml`, creates `.venv`, installs the three local packages in
#    editable mode (`refactory-dsh`, the vendored `factory` and `srf`) and puts the
#    `refactory` console script on `.venv/bin` — which is why every documented
#    command is `uv run refactory …` rather than an interpreter path.
if ! command -v uv >/dev/null 2>&1; then
  echo "  error: uv not found. Install it with:" >&2
  echo "           curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  echo "         (or: brew install uv, or pipx install uv) and run this script again." >&2
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
