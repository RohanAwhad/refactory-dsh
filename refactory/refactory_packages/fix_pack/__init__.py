"""The fix package: a mode that changes code in a repository, written as one you would write.

One mode, `make_pass`, which makes a failing check pass. It exists to show the steps any
mode that changes a repository attaches (`refactory_dsh.repo_change`), the same ones
design and design_fast take: a worktree of the target on the run's own branch, an
``emit.txt`` applied as one commit per attempt, the check run in the worktree's own
environment, and the run's place settled at the end.
"""

from __future__ import annotations

from refactory_dsh.nn import Package

from .mode import MakePass

PACKAGE = Package(
    name="fix_pack",
    description="Make a failing check pass in a repository, on a branch of its own.",
    modes=(MakePass,),
    # repo_change is named too, so a run pins the steps it calls as well as its own.
    ops=("refactory_packages.fix_pack.ops", "refactory_dsh.repo_change"),
)
