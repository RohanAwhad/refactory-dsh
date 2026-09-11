"""Path resolution for refactory-dsh — the single self-contained repo.

This file lives at ``<repo>/refactory/refactory_dsh/paths.py``. The repo is a
DSH fork: the DSH source sits at the repo root, the glue at ``refactory/``, and
the vendored dependencies (refactory + SRF) at ``vendor/``. Nothing else in the
repo hardcodes an absolute path; everything asks here, with environment
overrides for non-default layouts.

- ``REFACTORY_DSH_HOME`` — override the repo root (defaults to this repo).
- ``DSH_ROOT`` / ``SRF_GRAPHS`` / ``VENV_BIN`` / ``NODE_BIN`` — per-dependency
  overrides when a dependency lives elsewhere.
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


def deps_root() -> Path:
    """The vendored dependency root (holds ``remote-factory`` + ``srf``)."""
    env = os.environ.get("REFACTORY_DSH_HOME")
    return Path(env).expanduser() if env else REPO_ROOT


def vendor_root() -> Path:
    """The glue's ``vendor/`` directory holding the git submodules."""
    return _GLUE_DIR / "vendor"


def dsh_root() -> Path:
    """The DSH checkout that runs the workflow spine + science CLI (this repo)."""
    return Path(os.environ.get("DSH_ROOT") or REPO_ROOT)


def venv_bin() -> Path:
    """The self-contained venv whose python has refactory_dsh + factory + srf."""
    return Path(os.environ.get("VENV_BIN") or REPO_ROOT / ".venv" / "bin")


def python() -> Path:
    return venv_bin() / "python"


def srf_graphs() -> Path:
    """The directory of committed SRF ``<mode>.graph.json`` files (submodule)."""
    return Path(os.environ.get("SRF_GRAPHS") or vendor_root() / "srf" / "graphs")


def factory_root() -> Path:
    """The refactory checkout (the language; a git submodule)."""
    return vendor_root() / "remote-factory"


def node_bin() -> str:
    """The Node binary that runs DSH. Resolves to an absolute path so it works
    even under a preset's restricted command PATH. Order: ``NODE_BIN`` override,
    the nvm Node 22 install the launcher exports (DSH needs ^22.19), the current
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


def refactory_dsh_pkg() -> Path:
    """This package's directory (for PYTHONPATH when not pip-installed)."""
    return _PKG_DIR


def workdir_base() -> Path:
    """The per-mode work directories live under the glue directory."""
    return _GLUE_DIR / "workdirs"
