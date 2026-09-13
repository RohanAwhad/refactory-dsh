"""The circle-packing package.

One package, one mode, and the operations it calls. ``PACKAGE`` is the manifest
the registry discovers; nothing outside this directory needs to know it exists.
"""

from __future__ import annotations

from refactory_dsh.nn import Package

from .mode import CirclePacking

PACKAGE = Package(
    name="circle_packing",
    description="A population search over circle placements, and the task it scores.",
    modes=(CirclePacking,),
    ops=("refactory_packages.circle_packing.ops",),
)
