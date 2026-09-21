"""The interactive review package.

One mode whose pipeline ends at a gate **a person answers**. It exists because
every shipped mode was runnable end to end without a human, so the path where a run
pauses for one — `Node.gate_kind == "user"`, the CEO presenting it, the answer
reaching the ledger and the gate — had no mode to exercise it.
"""

from __future__ import annotations

from refactory_dsh.nn import Package

from .mode import Review

PACKAGE = Package(
    name="review_pack",
    description="Draft a short note, check it, and let a person approve it before it is published.",
    modes=(Review,),
    ops=("refactory_packages.review_pack.ops",),
)
