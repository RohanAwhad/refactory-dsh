"""The counting_pack package.

One package, two modes, and the operations they call. ``PACKAGE`` is the manifest
the registry discovers; nothing outside this directory needs to know it exists.
"""

from __future__ import annotations

from refactory_dsh.nn import Package

from .mode import FixedStep, RandomWalk

PACKAGE = Package(
    name="counting_pack",
    description="Two counting modes that score how close the count reached lands to a target.",
    modes=(RandomWalk, FixedStep),
    ops=("refactory_packages.counting_pack.ops",),
)
