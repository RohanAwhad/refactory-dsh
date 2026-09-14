"""What this package needs that is not Python.

One tool, and it is a Python module -- which is the point of declaring it here
rather than importing it in ``ops.py``. The same mechanism that names a Lean binary
and a mathlib checkout names an importable module, so a package that needs a Python
tool and a package that needs a toolchain fail the same way and are reported by the
same reader. A package that needs nothing declares nothing and carries no
``tools.py`` at all.

Read by the registry by path, before this package is imported, so the answer exists
even when the import is what fails -- see ``refactory_dsh.toolchain``.
"""

from __future__ import annotations

from refactory_dsh.toolchain import Tool

TOOLS: tuple[Tool, ...] = (
    Tool(
        name="sympy",
        kind="python",
        probe="sympy",
        install=".venv/bin/pip install sympy",
        why="the exact algebra engine that decides whether a conjectured closed form is the right one",
    ),
)
