"""The annealing-packing package.

One package, one mode, and the operations it calls. ``PACKAGE`` is the manifest
the registry discovers; nothing outside this directory needs to know it exists.
"""

from __future__ import annotations

from refactory_dsh.nn import Package

from .mode import Anneal

PACKAGE = Package(
    name="annealing_pack",
    description="Hill-climb a greedy circle packing by accepting non-worsening single-circle moves.",
    modes=(Anneal,),
    ops=("refactory_packages.annealing_pack.ops",),
)
