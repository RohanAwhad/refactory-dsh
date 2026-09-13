"""Optimizers: the ``torch.optim`` of the harness world.

    from refactory_dsh.optim import AutoresearchOptimizer, RandomOptimizer

An optimizer sees a :class:`ProposalContext` — the parameter surface, the edit
catalogue, the leaderboard, the traces, and the reasons recent proposals were
refused — and returns typed edits. The base class runs the validating loop around
it, so no optimizer can spend evaluation budget on a candidate that cannot run.
"""

from __future__ import annotations

from .base import (
    Candidate,
    Optimizer,
    Proposal,
    ProposalContext,
    Rejection,
    candidate_id,
)
from .random_optimizer import AutoresearchOptimizer, RandomOptimizer, extract_json

#: The optimizers the loop can select by name.
REGISTRY: dict[str, type[Optimizer]] = {
    RandomOptimizer.name: RandomOptimizer,
    AutoresearchOptimizer.name: AutoresearchOptimizer,
}


def names() -> list[str]:
    return sorted(REGISTRY)


def create(name: str, **kwargs: object) -> Optimizer:
    """Build an optimizer by name, failing loud on an unknown one.

    Falling back silently would invalidate every result of the run, because the
    report would not say which optimizer produced them.
    """
    if name not in REGISTRY:
        raise ValueError(f"unknown optimizer {name!r}; known: {names()}")
    return REGISTRY[name](**kwargs)  # type: ignore[arg-type]


__all__ = [
    "REGISTRY",
    "AutoresearchOptimizer",
    "Candidate",
    "Optimizer",
    "Proposal",
    "ProposalContext",
    "RandomOptimizer",
    "Rejection",
    "candidate_id",
    "create",
    "extract_json",
    "names",
]
