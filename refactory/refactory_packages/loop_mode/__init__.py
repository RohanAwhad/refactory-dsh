"""The outer loop, expressed as a mode.

The loop is not a special program. It is a mode like any other: eight steps, an
edge back from the last to the first, and a score that reads what the run
produced. Writing it this way is what makes the loop *editable* — the thing the
outer loop does to a mode, another outer loop can now do to the loop.
"""

from __future__ import annotations

from refactory_dsh.nn import Package

from .mode import LoopAsMode

PACKAGE = Package(
    name="loop_mode",
    description="The optimizer loop itself as a graph of eight steps, runnable and searchable.",
    modes=(LoopAsMode,),
    ops=("refactory_packages.loop_mode.ops",),
)
