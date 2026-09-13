"""Path resolution for refactory-dsh.

This file lives at ``<repo>/refactory/refactory_dsh/paths.py``. The repo holds the
DSH fork at the root and the glue at ``refactory/``. Nothing else hardcodes an
absolute path; everything asks here, with environment overrides for non-default
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


def venv_bin() -> Path:
    """The venv whose python has refactory_dsh and the packages installed."""
    return Path(os.environ.get("VENV_BIN") or REPO_ROOT / ".venv" / "bin")


def python() -> Path:
    return venv_bin() / "python"
