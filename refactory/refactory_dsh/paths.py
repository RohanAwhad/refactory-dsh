"""Path resolution for refactory-dsh.

This file lives at ``<repo>/refactory/refactory_dsh/paths.py``. The glue lives at
``refactory/``; DSH itself runs from the pinned install the root manifest declares,
or from a source checkout at the repo root while one is there. Nothing else hardcodes
an absolute path; everything asks here, with environment overrides for non-default
layouts.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
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


def refactory_home() -> Path:
    """Refactory's own per-user directory: global memory, the project registry, run worktrees.

    Outside every project, because what lives here belongs to no one checkout.
    ``REFACTORY_HOME`` overrides it, which is also how the tests keep it out of the real
    home directory.
    """
    return Path(os.environ.get("REFACTORY_HOME") or Path.home() / ".refactory").expanduser()


#: The oldest Node 22 DSH supports.
NODE_22_MINIMUM = (22, 19)

#: Node binaries a system or package manager installs outside a version manager.
SYSTEM_NODES = (
    Path("/usr/local/bin/node"),
    Path("/usr/bin/node"),
    Path("/opt/homebrew/opt/node@22/bin/node"),
    Path("/usr/local/opt/node@22/bin/node"),
)


def node_bin() -> str:
    """The Node binary that runs DSH, as an absolute path so it works under a
    restricted command PATH. Order: ``NODE_BIN``, the newest installed Node 22 (at
    least 22.19), the current ``PATH``, then a bare ``node``.

    Node 22 is chosen even when the default ``node`` is newer: under Node 24.20 DSH
    aborts intermittently with no output (workers fail ``exit -6``, about one call
    in four when three run at once), and under Node 22 it does not.
    """
    env = os.environ.get("NODE_BIN")
    if env:
        return env
    found = _installed_node_22()
    if found is not None:
        return found
    on_path = shutil.which("node")
    if on_path:
        return on_path
    return "node"


def _installed_node_22() -> str | None:
    """The newest supported Node 22 that nvm, fnm, the system or ``PATH`` provides."""
    home = Path.home()
    managed = [
        *Path(os.environ.get("NVM_DIR") or home / ".nvm").glob("versions/node/v22.*/bin/node"),
        *Path(os.environ.get("FNM_DIR") or home / ".local" / "share" / "fnm")
        .glob("node-versions/v22.*/installation/bin/node"),
    ]
    probed = [*SYSTEM_NODES, *([Path(on_path)] if (on_path := shutil.which("node")) else [])]
    candidates = [(_installed_version(path), path) for path in managed if path.is_file()]
    candidates += [(_installed_version(path.resolve()) or _binary_version(str(path)), path)
                   for path in probed if path.is_file()]
    supported = [(version, path) for version, path in candidates
                 if version is not None and version[0] == 22 and version[:2] >= NODE_22_MINIMUM]
    return str(max(supported)[1]) if supported else None


def _installed_version(path: Path) -> tuple[int, int, int] | None:
    """The version an install names its directory after (nvm, fnm, volta, Homebrew's
    Cellar, a CI tool cache), so most Nodes are identified without being run."""
    for part in reversed(path.parts):
        version = _version(part)
        if version is not None:
            return version
    return None


def _version(text: str) -> tuple[int, int, int] | None:
    """A ``MAJOR.MINOR.PATCH`` version, with or without a leading ``v``, or None."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text.strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


@functools.lru_cache(maxsize=None)
def _binary_version(binary: str) -> tuple[int, int, int] | None:
    """What a Node binary reports with ``--version``; None when it cannot say."""
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return _version(out.stdout)


#: The CLI entry inside a DSH source checkout, relative to that checkout's root.
SOURCE_CLI_ENTRY = Path("apps") / "cli" / "src" / "bin.ts"

#: The CLI entry an install of the pinned harness provides, relative to the checkout root.
INSTALLED_CLI_ENTRY = Path("node_modules") / "@deepseek-ai" / "dsh" / "lib" / "bin.js"


def installed_dsh() -> Path:
    """The `dsh` launcher an install of the pinned harness provides.

    The repository declares `@deepseek-ai/dsh` as a dependency, so pnpm writes this
    launcher into `node_modules/.bin`. Returned whether or not it exists, so a caller
    that selected this branch can name the missing path in its own error.
    """
    return dsh_root() / "node_modules" / ".bin" / "dsh"


def dsh_command(*, root: Path | None = None, cli: str | None = None) -> list[str]:
    """The argv prefix that launches DSH, before DSH's own arguments.

    Three answers, in order. ``DSH_CLI`` names an executable outright, which is how a
    caller drives a pinned install the checkout does not resolve on its own. A source
    checkout — one that still holds ``apps/cli/src/bin.ts`` — runs its own CLI by
    preloading ``source_bootstrap.mjs``, which installs tsx and the source
    configuration before the entry runs. Anything else runs the installed harness's
    entry, which is the only launcher left once the source copy is gone.

    Both Node-launched answers run on :func:`node_bin`. The installed entry is run
    directly rather than through `node_modules/.bin/dsh`: that launcher runs whichever
    ``node`` is first on ``PATH``, which is how DSH came to run on a Node it aborts
    under while ``node_bin`` named a supported one.

    The source preload resolves tsx and its source configuration from the selected
    checkout. The child's working directory remains the caller's, so an agent can
    read and write its own item artifacts without launching from the checkout.
    """
    override = (cli if cli is not None else os.environ.get("DSH_CLI", "")).strip()
    selected_root = Path(root) if root is not None else dsh_root()
    if override:
        return [override]
    source = selected_root / SOURCE_CLI_ENTRY
    if source.exists():
        preload = Path(__file__).with_name("source_bootstrap.mjs").as_uri()
        return [node_bin(), "--import", preload, str(source.resolve())]
    entry = selected_root / INSTALLED_CLI_ENTRY
    if entry.exists():
        return [node_bin(), str(entry)]
    return [str(selected_root / "node_modules" / ".bin" / "dsh")]


def venv_bin() -> Path:
    """The venv whose python has refactory_dsh and the packages installed."""
    return Path(os.environ.get("VENV_BIN") or REPO_ROOT / ".venv" / "bin")


def python() -> Path:
    return venv_bin() / "python"
