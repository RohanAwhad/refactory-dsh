"""The Lean package: modes whose verdict comes from a proof checker.

One package, one mode, and the operations it calls. ``PACKAGE`` is the manifest the
registry discovers; ``TOOLS`` in ``tools.py`` beside it is the separate declaration
of what this package needs from the machine, which the registry reads without
importing anything here -- see ``refactory_dsh.toolchain`` for why the two are split.

The package imports and lists on a machine with no Lean at all. What it cannot do
there is decide a theorem, and that failure is a named tool with an install command
rather than an import error.
"""

from __future__ import annotations

from refactory_dsh.nn import Package

from .mode import Prover

PACKAGE = Package(
    name="lean_pack",
    description="Prove theorems in Lean 4 and score what the proof checker accepted.",
    modes=(Prover,),
    ops=("refactory_packages.lean_pack.ops",),
)
