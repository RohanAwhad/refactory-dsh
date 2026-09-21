"""Whether the DSH profile a mode runs on exists, and how to create it.

A DSH profile is **machine state**. ``dsh plugin --profile graph add <package>``
writes ``$DSH_HOME/profiles/graph`` with the bundle named in its
``dsh.profile.bundles``; nothing derives it from the bundle's name, and the
install path used to stop before it. So a fresh clone could build, import and run
every mode through the walker and still fail the first time anything reached DSH,
with an error about a missing *profile* that reads as a packaging bug.

This module is the one place that answers the two questions such a failure needs:
*is the profile there*, and *what exactly fixes it*. The runner checks it before
spawning DSH, so the error names the reason; ``install.sh`` checks it so a fresh
clone is not left needing a manual step; and the docs link here rather than
restating the command, which is how a rename left the wrong name in three places.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from refactory_dsh.paths import REPO_ROOT

#: The profile the DSH evaluator, the equivalence tests and a manual mode run use.
GRAPH_PROFILE = "graph"

#: The bundle that profile must carry. `@deepseek-ai/dsh-graph` is not published,
#: so it is installed from a checkout by path rather than by name.
GRAPH_BUNDLE = "@deepseek-ai/dsh-graph"


class ProfileError(RuntimeError):
    """A DSH profile a run needs is absent. Names the fix and the reason."""


def dsh_home() -> Path:
    """The harness home that holds ``profiles/``; ``$DSH_HOME``, else ``~/.dsh``."""
    override = (os.environ.get("DSH_HOME") or "").strip()
    return Path(override) if override else Path.home() / ".dsh"


def profile_dir(name: str = GRAPH_PROFILE) -> Path:
    """Where the profile lives, whether or not it exists."""
    return dsh_home() / "profiles" / name


def declared_bundles(name: str = GRAPH_PROFILE) -> list[str]:
    """The bundles the profile's manifest declares, in order.

    Read from ``package.json`` rather than by importing anything: a profile that
    is absent and one that is present but does not carry the bundle are different
    failures, and telling them apart is what lets the error say which happened.
    """
    manifest = profile_dir(name) / "package.json"
    if not manifest.exists():
        return []
    try:
        loaded = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(loaded, dict):
        return []
    dsh = loaded.get("dsh")
    profile = dsh.get("profile") if isinstance(dsh, dict) else None
    bundles = profile.get("bundles") if isinstance(profile, dict) else None
    if not isinstance(bundles, list):
        return []
    return [str(entry) for entry in bundles]


def is_ready(name: str = GRAPH_PROFILE, bundle: str = GRAPH_BUNDLE) -> bool:
    """Whether a mode can be run on DSH through this profile right now."""
    return bundle in declared_bundles(name)


def add_command(name: str = GRAPH_PROFILE, *, checkout: Path | None = None) -> str:
    """The command that creates the profile, copy-pasteable, with the real path."""
    root = Path(checkout) if checkout is not None else REPO_ROOT
    return f"dsh plugin --profile {name} add {root / 'packages' / 'bundle' / name}"


def requirement(name: str = GRAPH_PROFILE, bundle: str = GRAPH_BUNDLE) -> str | None:
    """Why a run on DSH cannot start, and the command that fixes it. ``None`` when it can.

    The reason is stated because the bare ``profile "graph" does not exist`` is
    indistinguishable from a packaging bug: the profile is not derived from the
    bundle, so a checkout that built and imported its package perfectly still has
    no profile until somebody runs the command.
    """
    directory = profile_dir(name)
    if not (directory / "package.json").exists():
        return (
            f"the DSH profile {name!r} does not exist at {directory}. A DSH profile "
            "is machine state, not something the bundle creates: create it once per "
            f"machine with `{add_command(name)}`"
        )
    if bundle not in declared_bundles(name):
        return (
            f"the DSH profile {name!r} exists but does not carry {bundle}, so it "
            f"cannot run a mode. Add it with `{add_command(name)}`"
        )
    return None


def require(name: str = GRAPH_PROFILE, bundle: str = GRAPH_BUNDLE) -> None:
    """Raise :class:`ProfileError` naming the fix and the reason, or return."""
    problem = requirement(name, bundle)
    if problem is not None:
        raise ProfileError(problem)


__all__ = [
    "GRAPH_BUNDLE",
    "GRAPH_PROFILE",
    "ProfileError",
    "add_command",
    "dsh_home",
    "declared_bundles",
    "is_ready",
    "profile_dir",
    "require",
    "requirement",
]
