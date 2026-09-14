"""What this package needs that is not Python.

Read by the registry by path, before the package is imported, so the answer exists
even when the package does not import -- see ``refactory_dsh.toolchain`` for why the
declaration lives here rather than on ``Package``.

Three tools, and the distinction between them is the point. ``lean`` is required:
without the proof checker no mode in this package can decide anything, because the
check *is* the proof checker and this package never asserts a theorem itself.
``lake`` and ``mathlib`` are optional, because only the mathlib problem needs them:
declaring them required would make the core problem unrunnable on a machine that has
exactly what the core problem needs.
"""

from __future__ import annotations

import os
from pathlib import Path

from refactory_dsh.toolchain import Tool

#: The command that installs a Lean 4 toolchain. ``elan`` puts both ``lean`` and
#: ``lake`` on the PATH, which is why one hint serves two tools.
ELAN = "curl https://elan.lean-lang.org/elan-init.sh -sSf | sh"


def _elan_home() -> str:
    """Where this machine keeps its Lean toolchains.

    Declared, and resolved **here**, because the run these tools are needed for is
    contained: the sandbox rewrites ``HOME`` to its own working directory, and elan
    finds its toolchains through ``HOME``. Without forwarding this, ``lake env lean``
    inside a sandbox reports "no default toolchain configured" on a machine where
    Lean is installed and works -- an absent tool reported by a machine that has it.

    ``ELAN_HOME`` wins when it is set, so a toolchain kept somewhere other than the
    default is used where its owner put it.
    """
    return os.environ.get("ELAN_HOME") or str(Path.home() / ".elan")


#: Both binaries are in the same toolchain directory, so both need the same one
#: variable to find it.
_LEAN_ENV = (("ELAN_HOME", _elan_home()),)

TOOLS: tuple[Tool, ...] = (
    Tool(
        name="lean",
        kind="binary",
        probe="lean",
        install=ELAN,
        why="the proof checker that decides whether a candidate solution is a proof",
        version_args=("--version",),
        env=_LEAN_ENV,
    ),
    Tool(
        name="lake",
        kind="binary",
        probe="lake",
        install=ELAN,
        why="the build tool that gives a per-problem project its library path",
        version_args=("--version",),
        optional=True,
        env=_LEAN_ENV,
    ),
    Tool(
        name="mathlib",
        kind="path",
        probe="$MATHLIB_PATH",
        install=(
            "git clone https://github.com/leanprover-community/mathlib4 ~/mathlib4 && "
            "cd ~/mathlib4 && lake exe cache get && export MATHLIB_PATH=~/mathlib4"
        ),
        why="the library that the sum_odd_eq_sq statement and its proof both need",
        optional=True,
    ),
)
