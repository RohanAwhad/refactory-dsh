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
    # Declared, because this package imports circle_packing.ops for RADII and
    # score_centers. Without the declaration, removing circle_packing would leave a
    # package that fails at import time and nothing would have warned anyone.
    requires=("circle_packing",),
)
