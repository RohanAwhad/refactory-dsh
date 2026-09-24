"""Path resolution for refactory-dsh.

This file lives at ``<repo>/refactory/refactory_dsh/paths.py``. The glue lives at
``refactory/``; DSH itself runs from the pinned install the root manifest declares,
or from a source checkout at the repo root while one is there. Nothing else hardcodes
an absolute path; everything asks here, with environment overrides for non-default
layouts.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# this package's directory: …/<repo>/refactory/refactory_dsh/paths.py
_PKG_DIR = Path(__file__).resolve().parent
# the glue directory: …/<repo>/refactory
_GLUE_DIR = _PKG_DIR.parent
# the product repo (the DSH fork): …/<repo>
REPO_ROOT = _GLUE_DIR.parent


def dsh_root() -> Path:
    """The DSH checkout that runs the headless model client."""
    return Path(os.environ.get("DSH_ROOT") or REPO_ROOT)


def project_root() -> Path:
    """The project a command resolves packages against.

    One answer for every entry point, because the alternative is what happened
    before there was one: the console defaulted to the repo root, the loop and
    registry CLIs defaulted to the working directory, and the authoring commands
    took ``Path.cwd()``. A package created through one of them was therefore
    invisible to the next, and which one you got depended on where you stood.

    ``REFACTORY_PROJECT`` overrides it for a checkout that keeps its packages
    somewhere else.
    """
    return Path(os.environ.get("REFACTORY_PROJECT") or REPO_ROOT)


def project_for_nodes() -> Path:
    """The project a **node command** resolves packages against.

    A node command's working directory is the run it belongs to, not the checkout, so
    the runtime states the project out loud (`REFACTORY_PROJECT`, set by the graph
    bundle). Where it is unset — the walker, a test, a person at a shell — the working
    directory is the project, which is what it has always been.
    """
    return Path(os.environ.get("REFACTORY_PROJECT") or Path.cwd())


def node_bin() -> str:
    """The Node binary that runs DSH. Resolves to an absolute path so it works
    even under a restricted command PATH. Order: ``NODE_BIN`` override, the nvm
    Node 22 install the launcher exports (DSH needs ^22.19), the current
    ``PATH``, then a bare ``node``."""
    env = os.environ.get("NODE_BIN")
    if env:
        return env
    nvm = Path.home() / ".nvm" / "versions" / "node" / "v22.23.2" / "bin" / "node"
    if nvm.exists():
        return str(nvm)
    found = shutil.which("node")
    if found:
        return found
    return "node"


#: The CLI entry inside a DSH source checkout, relative to that checkout's root.
SOURCE_CLI_ENTRY = Path("apps") / "cli" / "src" / "bin.ts"


def installed_dsh() -> Path:
    """The `dsh` launcher an install of the pinned harness provides.

    The repository declares `@deepseek-ai/dsh` as a dependency, so pnpm writes this
    launcher into `node_modules/.bin`. Returned whether or not it exists, so a caller
    that selected this branch can name the missing path in its own error.
    """
    return dsh_root() / "node_modules" / ".bin" / "dsh"


def dsh_command() -> list[str]:
    """The argv prefix that launches DSH, before DSH's own arguments.

    Three answers, in order. ``DSH_CLI`` names an executable outright, which is how a
    caller drives a pinned install the checkout does not resolve on its own. A source
    checkout — one that still holds ``apps/cli/src/bin.ts`` — runs its own CLI through
    tsx, which is what every checkout did before the harness was pinned. Anything else
    uses the installed `dsh`, which is the only launcher left once the source copy is
    gone.

    A caller that takes the source branch must run the child with its working
    directory inside the checkout: ``--import tsx/esm`` resolves from there. The
    installed branch has no such requirement, because the launcher resolves its own
    modules.
    """
    override = (os.environ.get("DSH_CLI") or "").strip()
    if override:
        return [override]
    source = dsh_root() / SOURCE_CLI_ENTRY
    if source.exists():
        return [node_bin(), "--import", "tsx/esm", str(source)]
    return [str(installed_dsh())]


def venv_bin() -> Path:
    """The venv whose python has refactory_dsh and the packages installed."""
    return Path(os.environ.get("VENV_BIN") or REPO_ROOT / ".venv" / "bin")


def python() -> Path:
    return venv_bin() / "python"
