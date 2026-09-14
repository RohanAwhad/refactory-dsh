"""The sympy package: modes whose verdict comes from an exact algebra engine.

One package, one mode, and the operations it calls. ``PACKAGE`` is the manifest the
registry discovers; ``TOOLS`` in ``tools.py`` beside it declares the one module this
package needs from the machine, and the registry reads that declaration without
importing anything here.

This package exists beside ``lean_pack`` to make one point: a Python tool and a
non-Python tool are declared the same way, verified by the same reader, and reported
with the same message when they are absent.
"""

from __future__ import annotations

from refactory_dsh.nn import Package

from .mode import Exact

PACKAGE = Package(
    name="sympy_pack",
    description="Establish closed forms of sums exactly with sympy, and score what checked out.",
    modes=(Exact,),
    ops=("refactory_packages.sympy_pack.ops",),
)
