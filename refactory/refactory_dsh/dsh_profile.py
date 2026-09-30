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

#: The profile the browser GUI boots: the pinned harness's own web template.
WEB_PROFILE = "web"

#: The bundle that profile must carry, for the same by-path reason. Without it the
#: web profile has no `ceo` preset, and the composition still names `ceo` as the
#: roster default, so every session the GUI creates without naming one fails with
#: ``agent-preset/not-found`` rather than falling back to a shipped preset.
WEB_BUNDLE = "@deepseek-ai/dsh-refactory-presets"

#: The web profile's second owned bundle: the console, the right-sidebar picker
#: and the re:factory brand rows. It is a separate layer from the preset bundle
#: because the two own different rows; the browser surface is not complete without
#: both, so the profile is only ready when it carries both.
WEB_SURFACE_BUNDLE = "@deepseek-ai/dsh-refactory-web"

#: ``(profile, bundle)`` for every profile/bundle pair this checkout owns. A
#: profile may appear more than once: `web` carries both the preset and the surface
#: bundle, and both are installed with their own ``dsh plugin add``.
PROFILE_BUNDLES = (
    (GRAPH_PROFILE, GRAPH_BUNDLE),
    (WEB_PROFILE, WEB_BUNDLE),
    (WEB_PROFILE, WEB_SURFACE_BUNDLE),
)


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


def bundle_directory(bundle: str) -> str:
    """The checkout directory holding a bundle package, from the bundle's NAME.

    Derived rather than looked up per profile, so adding another owned profile or
    bundle is a one-line change to :data:`PROFILE_BUNDLES` and cannot name the wrong
    directory: the old code took the directory from the *profile* name, which only
    ever worked because `graph` and its bundle happened to agree.
    """
    return bundle.rsplit("/", 1)[-1].replace("dsh-", "", 1)


def add_command(
    name: str = GRAPH_PROFILE,
    bundle: str | None = None,
    *,
    checkout: Path | None = None,
) -> str:
    """The command that creates the profile, copy-pasteable, with the real path.

    ``bundle`` selects which of the profile's owned bundles the command installs;
    when it is omitted the profile's first owned bundle is used. Raises
    :class:`KeyError` for a profile this checkout does not own — or for a
    ``(profile, bundle)`` pair it does not own — rather than falling back to one of
    them: a command naming the wrong bundle directory reads as a plausible fix and
    is worse than no command at all.
    """
    root = Path(checkout) if checkout is not None else REPO_ROOT
    if bundle is None:
        try:
            bundle = next(entry for profile, entry in PROFILE_BUNDLES if profile == name)
        except StopIteration:
            raise KeyError(name) from None
    elif (name, bundle) not in PROFILE_BUNDLES:
        raise KeyError(bundle)
    directory = bundle_directory(bundle)
    return f"dsh plugin --profile {name} add {root / 'packages' / 'bundle' / directory}"


def missing_bundles() -> list[tuple[str, str, str]]:
    """Every checkout-owned bundle its profile does not carry yet.

    Returns one ``(profile, bundle, reason)`` row per gap, in profile order, and an
    empty list when this machine is ready. Both caller shapes want the same answer:
    ``install.sh`` iterates the rows to create what is absent, and ``doctor`` renders
    them so a person sees *which* profile is short rather than a bare boolean.
    """
    gaps: list[tuple[str, str, str]] = []
    for name, bundle in PROFILE_BUNDLES:
        problem = requirement(name, bundle)
        if problem is not None:
            gaps.append((name, bundle, problem))
    return gaps


def requirement(name: str = GRAPH_PROFILE, bundle: str = GRAPH_BUNDLE) -> str | None:
    """Why DSH cannot start this profile's work, and the command that fixes it.

    ``None`` when the profile carries the bundle. The reason is stated because the
    bare ``profile "graph" does not exist`` is indistinguishable from a packaging
    bug: the profile is not derived from the bundle, so a checkout that built and
    imported its package perfectly still has no profile until somebody runs the
    command.
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
            f"the DSH profile {name!r} exists but does not carry {bundle}, so it is "
            f"not this checkout's {name!r} profile. Add it with `{add_command(name, bundle)}`"
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
    "PROFILE_BUNDLES",
    "ProfileError",
    "WEB_BUNDLE",
    "WEB_PROFILE",
    "WEB_SURFACE_BUNDLE",
    "add_command",
    "bundle_directory",
    "dsh_home",
    "declared_bundles",
    "is_ready",
    "missing_bundles",
    "profile_dir",
    "require",
    "requirement",
]
