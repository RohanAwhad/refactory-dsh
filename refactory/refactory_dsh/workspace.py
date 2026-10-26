"""Agent workspaces: where an agent node changes files and runs commands.

An agent node may declare a workspace (``Node.workspace``, in the canonical form
`nn.graph.workspace_declaration` gives it). The graph still decides what crosses into
shared state: the runtime collects the node's output from its workspace before delivery
checks it, so what a later step reads -- materialise, the check, the reviews, the
target guard -- is what it always read.

``none`` (the default)
    The run directory is the node's working folder, as before workspaces.
``scratch``
    A fresh empty folder per dispatch. Its declared writes are copied out of it.
``clone`` (``of: attempt`` or ``of: base``)
    A prepared clone of the run's worktree, at its last attempt or at its base, with
    the installs, setup and generate done. ``collect: diff`` writes the clone's diff
    into the node's answer file as ``===FILE:`` and ``===DELETE:`` blocks;
    ``collect: files`` copies its declared writes out of it, as a scratch folder's are.

Both runtimes run a workspace node through this module: the spine's worker
(`worker_node.execute`) and the walker (`nn.executor.Executor._visit_agent`) each
prepare (`dispatched`), run the session there (`ask`) and collect (`collect`) before
`agent_delivery.finish`, which would otherwise capture the reply text into the answer
file.

A clone is prepared once per node per run, and persists across commands, fix-ups and
attempts. Its preparation belongs to the product, because it needs the run's worktree:
this module is core, and reaches it through the ``refactory.workspace_clone`` entry
point, as it reaches the worktree's state through ``refactory.worktree_state``.

A workspace is local by default: a folder on this machine, with DSH's sandbox policy
``workspace-write`` rooted at it wherever DSH can enforce that (`confinement`). On the
``openshell`` worker backend the same folder is packed into a kernel-confined OpenShell
sandbox, the agent runs there, and the folder comes back to the same collect step
(`_complete_in_openshell`).
"""

from __future__ import annotations

import fcntl
import functools
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from refactory_dsh.nn.graph import workspace_declaration

#: Where a run keeps its workspaces, one directory per node.
ROOT = Path(".refactory") / "workspaces"

#: Refactory's own directory inside a workspace: the agent's HOME and the files of its
#: session live there, inside the folder the sandbox lets it write, and none of it is
#: ever collected (git is told to ignore it too).
OWN = ".refactory"

#: Never part of what a workspace delivers, at any depth: the environments an install
#: makes, and the caches running the project's tools leaves (a builder that runs the
#: tests in its workspace makes them). With ``OWN`` at the top, these are what `collect`
#: leaves out.
EXCLUDED_DIRS = (".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")

#: Where `collect` leaves a refusal for materialise: the emission it wrote could not
#: carry the workspace's change, and why (`refusal_for`).
REFUSAL = Path(".refactory") / "workspace-refusal.json"

#: Seconds a workspace's preparation is given beyond its node's session: a scratch
#: folder, or a clone's own git work. A clone's installs, generate and setup are
#: budgeted on top (`preparation_budget_s`).
SCRATCH_S = 30
CLONE_S = 120

#: What generate and setup are each given in a clone when the mode declares no
#: ``check_timeout_s``, the knob a mode's check gives them.
DEFAULT_COMMAND_S = 600

#: The blocks an emission is written in. A line of a workspace file that starts with one
#: would be read as a new block, so such a file cannot be carried (`derive_emission`).
_HEADER = re.compile(r"^===(FILE|EDIT|DELETE):", re.MULTILINE)

_UNSET: object = object()
_clone_preparer: Any = _UNSET


class WorkspaceError(ValueError):
    """A workspace could not be prepared, used or collected; the message says why."""


@dataclass
class Workspace:
    """One node's workspace for one dispatch."""

    node: str
    kind: str
    collect: str
    #: The node's directory under the run's ``.refactory/workspaces``.
    root: Path
    #: Where the agent works: its working directory, and the root its tools may write.
    path: Path
    of: str = ""
    #: The commit (or tree) a clone's diff is taken from.
    base: str = ""
    #: What the agent's environment overrides: its PATH, HOME and virtual environment.
    env: dict[str, str | None] = field(default_factory=dict)
    #: The DSH sandbox runner that confines it, or "" when none can here.
    confine: str = ""
    #: Why it is not confined, when it is not.
    unconfined: str = ""
    #: How a clone was brought to this dispatch: cloned, kept, verified, reset or seeded.
    synced: str = ""
    #: Files a pending merge left conflicted: always part of the emission.
    conflicts: tuple[str, ...] = ()
    #: Whether the emission may delete a file; a run without a worktree cannot.
    deletes: bool = True
    #: What the preparation ran: installs and setup, or generate alone.
    prepared: str = ""
    #: What an install, generate or setup reported when it failed. The node still runs.
    problems: tuple[str, ...] = ()
    #: Files the preparation made or rewrote, by path, with their content's digest: left out
    #: of the emission while the agent leaves them as they were made.
    skip: dict[str, str] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        """The workspace as the execution contract and the receipt record it."""
        return {
            "kind": self.kind, **({"of": self.of} if self.of else {}), "collect": self.collect,
            "path": str(self.path), **({"base": self.base} if self.base else {}),
            "sandbox": self.confine and f"workspace-write ({self.confine})" or f"not confined: {self.unconfined}",
            **({"synced": self.synced} if self.synced else {}),
            **({"prepared": self.prepared} if self.prepared else {}),
            **({"problems": list(self.problems)} if self.problems else {}),
        }


def get_clone_preparer() -> Any | None:
    """The product's clone preparation, discovered through its entry point on first call."""
    global _clone_preparer
    if _clone_preparer is _UNSET:
        eps = importlib.metadata.entry_points(group="refactory.workspace_clone")
        if len(eps) > 1:
            raise RuntimeError(
                f"expected at most one 'refactory.workspace_clone' entry point, "
                f"got {len(eps)}: {[ep.name for ep in eps]}"
            )
        _clone_preparer = next(iter(eps)).load() if eps else None
    return _clone_preparer


def preparation_budget_s(declaration: Any, command_timeout_s: Any = None) -> int:
    """Seconds a node's dispatch reserves to prepare its workspace, beyond its session.

    A clone may run three installs (`invocations.INSTALL_TIMEOUT_S` each, as a check's
    envelope reserves), generate and setup (``command_timeout_s`` each, the mode's
    ``check_timeout_s``), and its own git work. A scratch folder is only made.
    """
    from refactory_dsh.invocations import INSTALL_TIMEOUT_S

    declared = workspace_declaration(declaration)
    if declared is None:
        return 0
    if declared["kind"] == "scratch":
        return SCRATCH_S
    command = int(float(command_timeout_s)) if command_timeout_s else DEFAULT_COMMAND_S
    return CLONE_S + 3 * INSTALL_TIMEOUT_S + 2 * command


def _slug(node: str) -> str:
    """A directory name for a node id: the id when it is one, else a digest of it."""
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", node) and node not in (".", ".."):
        return node
    return hashlib.sha256(node.encode()).hexdigest()[:16]


@contextmanager
def dispatched(workdir: Path, node: str, declaration: Any, *, knobs: dict[str, Any] | None = None,
               facts: dict[str, Any] | None = None, route: dict[str, Any] | None = None,
               runtime_root: Path | None = None, budget: Any = None) -> Iterator[Workspace | None]:
    """Prepare ``node``'s workspace for one dispatch and hold it until the block ends.

    Yields None when the node declares none, and the caller dispatches as before. The
    node's workspace is locked for the block: one dispatch at a time works in it.
    """
    declared = workspace_declaration(declaration)
    if declared is None:
        yield None
        return
    workdir = Path(workdir).resolve()
    root = workdir / ROOT / _slug(node)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield prepare(workdir, node, declared, knobs=knobs, facts=facts, route=route,
                          runtime_root=runtime_root, budget=budget)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def prepare(workdir: Path, node: str, declaration: Any, *, knobs: dict[str, Any] | None = None,
            facts: dict[str, Any] | None = None, route: dict[str, Any] | None = None,
            runtime_root: Path | None = None, budget: Any = None) -> Workspace | None:
    """``node``'s workspace, made ready for a dispatch; None when it declares none.

    A scratch folder is made afresh. A clone is prepared by the product's preparer: once
    per run, then brought to the run's last attempt (or kept at its base) on each later
    dispatch. Raises `WorkspaceError` when it cannot be.
    """
    declared = workspace_declaration(declaration)
    if declared is None:
        return None
    workdir = Path(workdir).resolve()
    root = workdir / ROOT / _slug(node)
    root.mkdir(parents=True, exist_ok=True)
    if declared["kind"] == "scratch":
        workspace = _prepare_scratch(root, node, declared)
    else:
        preparer = get_clone_preparer()
        if preparer is None:
            raise RuntimeError(
                f"agent node {node!r} works in a clone, and no 'refactory.workspace_clone' entry point "
                "is installed to prepare one; re-run `uv sync` or `./install.sh`")
        knobs = dict(knobs or {})
        command_s = int(float(knobs.get("check_timeout_s") or DEFAULT_COMMAND_S))
        prepared = preparer(workdir, root, declared["of"], facts=dict(facts or {}),
                            command_timeout_s=command_s, budget=budget)
        workspace = Workspace(
            node=node, kind="clone", of=declared["of"], collect=declared["collect"], root=root,
            path=Path(prepared["path"]), base=str(prepared["base"]), env=dict(prepared.get("env") or {}),
            synced=str(prepared.get("synced", "")), conflicts=tuple(prepared.get("conflicts") or ()),
            deletes=bool(prepared.get("deletes", True)), prepared=str(prepared.get("prepared", "")),
            problems=tuple(str(problem) for problem in prepared.get("problems") or ()),
            skip=dict(prepared.get("skip") or {}))
        if workspace.problems:
            _warn(f"preparing agent node {node!r}'s workspace reported: " + "; ".join(workspace.problems))
    workspace.confine, workspace.unconfined = confinement(route, runtime_root)
    _write_json(root / "dispatch.json", workspace.describe())
    if not workspace.confine:
        _warn(f"agent node {node!r} works in {workspace.path} without DSH's workspace-write policy "
              f"({workspace.unconfined}); its tools keep the access a node has without a workspace")
    return workspace


def strip_run_paths(env: dict[str, str], run_worktree: str | Path | None) -> dict[str, str]:
    """``env`` with the run worktree's ``.venv/bin`` and ``node_modules/.bin`` off its PATH.

    A workspace imports its own code: the run worktree's environment first on PATH would
    make ``python`` and the project's tools the worktree's, whose editable install
    imports the worktree and not the workspace.
    """
    if not run_worktree:
        return dict(env)
    worktree = Path(run_worktree)
    exclude = {str(worktree / ".venv" / "bin"), str(worktree / "node_modules" / ".bin")}
    kept = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry and entry not in exclude]
    return {**env, "PATH": os.pathsep.join(kept)}


def _run_worktree(workdir: Path) -> str | None:
    """The run worktree's path, through the product's worktree-state bridge, when the run has one."""
    from refactory_dsh.agent_delivery import get_worktree_state

    state = get_worktree_state()
    found = state.recorded(workdir) if state is not None else None
    return str(found.path) if found is not None else None


def _prepare_scratch(root: Path, node: str, declared: dict[str, str]) -> Workspace:
    """A fresh empty folder for this dispatch; an earlier dispatch's is removed first.

    Its HOME is the folder itself, so the folder is empty when the agent starts and
    nothing outside it is the agent's to write. The run worktree's environment is taken
    off its PATH, as a clone's is.
    """
    previous = sorted(entry for entry in root.glob("scratch-*") if entry.is_dir())
    for entry in previous:
        shutil.rmtree(entry, ignore_errors=True)
    number = 1 + max((int(entry.name.removeprefix("scratch-")) for entry in previous
                      if entry.name.removeprefix("scratch-").isdigit()), default=0)
    path = root / f"scratch-{number}"
    path.mkdir(parents=True)
    from refactory_dsh.invocations import HOOK_SUPPRESS

    run_worktree = _run_worktree(root.parent.parent.parent)
    env: dict[str, str | None] = {"HOME": str(path), **HOOK_SUPPRESS}
    if run_worktree:
        env["PATH"] = strip_run_paths(dict(os.environ), run_worktree)["PATH"]
        # The worktree's own virtual environment, which the run's environment overlay
        # names, is not this folder's.
        env["VIRTUAL_ENV"] = None
    return Workspace(node=node, kind="scratch", collect=declared["collect"], root=root, path=path,
                     env=env, synced="fresh", deletes=False)


# ── confinement ───────────────────────────────────────────────────────────────

def confinement(route: dict[str, Any] | None, runtime_root: Path | None = None) -> tuple[str, str]:
    """Which DSH sandbox runner confines a workspace's tools here, or "" and why none does.

    Only the ``dsh`` backend in ``skip`` mode is given a policy row (`workers.permission_patch`):
    another backend brings its own sandbox, and ``default`` keeps DSH's own default.
    DSH fails a confined command closed where its runner chain has no working runner, so
    the policy is set only where one works here: bubblewrap or Landlock on Linux,
    Seatbelt on macOS. Elsewhere the node keeps today's full access, and the dispatch
    record and a warning say so.
    """
    route = route or {}
    backend = route.get("worker_backend", "dsh") or "dsh"
    if backend != "dsh":
        return "", f"the {backend} worker backend runs the agent under its own sandbox settings"
    if (route.get("permission_mode") or "skip") != "skip":
        return "", "the route's permission mode is 'default', so DSH's own policy applies"
    forced = os.environ.get("REFACTORY_WORKSPACE_SANDBOX", "")
    if forced == "off":
        return "", "REFACTORY_WORKSPACE_SANDBOX=off"
    if runtime_root is None:
        from refactory_dsh.paths import dsh_root

        runtime_root = dsh_root()
    return _sandbox_runner(str(Path(runtime_root).expanduser().resolve()))


@functools.lru_cache(maxsize=8)
def _sandbox_runner(runtime_root: str) -> tuple[str, str]:
    """The first runner of DSH's chain that works on this machine, probed once per process."""
    if sys.platform == "darwin":
        return ("seatbelt", "") if shutil.which("sandbox-exec") else ("", "sandbox-exec is not available")
    if not sys.platform.startswith("linux"):
        return "", f"DSH has no local sandbox runner for {sys.platform} here"
    bwrap = shutil.which("bwrap")
    if bwrap and _runs([bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--unshare-pid", "--proc", "/proc",
                        "--die-with-parent", "--", "true"]):
        return "bwrap", ""
    launcher = _landlock_launcher(Path(runtime_root))
    if launcher is not None and _runs([str(launcher), "--probe"]):
        return "landlock", ""
    return "", "neither bubblewrap nor DSH's Landlock launcher can confine a command on this machine"


def _runs(argv: list[str]) -> bool:
    try:
        return subprocess.run(argv, capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _landlock_launcher(runtime_root: Path) -> Path | None:
    """DSH's Landlock launcher in the runtime's installed packages, as DSH resolves it."""
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine().lower(), platform.machine().lower())
    package = f"node-addon-system-linux-{arch}"
    candidates = [runtime_root / "node_modules" / "@deepseek-ai" / package / "bin" / "landlock-run",
                  *sorted((runtime_root / "node_modules" / ".pnpm").glob(
                      f"@deepseek-ai+{package}@*/node_modules/@deepseek-ai/{package}/bin/landlock-run"))]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


# ── the session ───────────────────────────────────────────────────────────────

def ask(workspace: Workspace, client: Any, prompt: str, *, workdir: Path, system: str | None = None) -> str:
    """One completion with the agent working in ``workspace``.

    ``workdir`` stays the run directory, where the client writes its receipt. The client
    must take ``cwd``: a text-only client has nowhere to put the agent, and asking it
    anyway would collect a workspace nothing worked in.
    """
    route = client.route() if callable(getattr(client, "route", None)) else {}
    if route.get("worker_backend") == "openshell":
        return _complete_in_openshell(workspace, client, prompt, workdir=Path(workdir), system=system or "")
    import inspect

    try:
        parameters = dict(inspect.signature(client.complete).parameters)
    except (TypeError, ValueError):  # pragma: no cover - a builtin
        parameters = {}
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        parameters.update(dict.fromkeys(("cwd", "env", "confine", "system")))
    if "cwd" not in parameters:
        raise WorkspaceError(
            f"agent node {workspace.node!r} works in a workspace, and its model client cannot run an "
            "agent anywhere but its own directory: its complete() takes no cwd")
    kwargs: dict[str, Any] = {"workdir": Path(workdir), "cwd": workspace.path}
    if system is not None:
        kwargs["system"] = system
    if "env" in parameters:
        kwargs["env"] = dict(workspace.env)
    if "confine" in parameters:
        kwargs["confine"] = bool(workspace.confine)
    return str(client.complete(prompt, **kwargs))


#: The agent an OpenShell sandbox runs: Claude Code, the binary the gateway's
#: ``claude-code`` provider lets reach the model, with the prompt and the reply as files
#: of the uploaded workspace, so both travel with it.
OPENSHELL_AGENT = ("claude -p --permission-mode bypassPermissions "
                   "< .refactory/session/prompt.md > .refactory/session/response.md")


def _complete_in_openshell(workspace: Workspace, client: Any, prompt: str, *, workdir: Path,
                           system: str = "") -> str:
    """Run the session in an OpenShell sandbox holding ``workspace``, then bring it back.

    The workspace is packed with `openshell_backend.pack_workspace`, the agent runs
    inside the sandbox, and `openshell_backend.download_artifacts` returns the folder,
    whose files `sync_back` lays over the workspace here. `collect` then reads it as it
    reads a local one, so both backends deliver through one collect path.
    """
    from refactory_dsh import openshell_backend
    from refactory_dsh.model import DEFAULT_TIMEOUT_S, node_session_budget_s

    session = workspace.path / OWN / "session"
    session.mkdir(parents=True, exist_ok=True)
    (session / "prompt.md").write_text(f"{system}\n\n{prompt}" if system else prompt)
    (session / "response.md").unlink(missing_ok=True)
    context = client.context() if callable(getattr(client, "context", None)) else {}
    timeout = node_session_budget_s(int(context.get("attempts") or 1),
                                    int(context.get("timeout_s") or DEFAULT_TIMEOUT_S))
    staging = workspace.root / "openshell"
    shutil.rmtree(staging, ignore_errors=True)
    code, downloaded = openshell_backend.run_workspace_session(
        workspace.path, ["sh", "-c", OPENSHELL_AGENT], context=context, timeout=timeout,
        dest=staging, receipts=Path(workdir))
    if downloaded is None:
        raise WorkspaceError(f"agent node {workspace.node!r}: the OpenShell sandbox's workspace could not "
                             "be downloaded, so its work cannot be collected")
    sync_back(downloaded, workspace.path)
    response = downloaded / OWN / "session" / "response.md"
    if code != 0:
        raise WorkspaceError(f"agent node {workspace.node!r}: the agent in the OpenShell sandbox exited {code}")
    return response.read_text() if response.is_file() else ""


def _excluded(relative: Path) -> bool:
    """Whether a workspace path is never delivered: git's state, refactory's own, an environment."""
    parts = relative.parts
    if not parts:
        return False
    if parts[0] in (".git", OWN):
        return True
    return any(part in EXCLUDED_DIRS for part in parts)


def sync_back(source: Path, workspace: Path) -> list[str]:
    """Make ``workspace``'s files ``source``'s: what changed, what was added and removed.

    For a workspace that came back from elsewhere (an OpenShell sandbox's download):
    git's state, refactory's own directory and the environments stay as they are here,
    so `collect` diffs against the same base. The sandbox's run artifacts
    (``.factory/run.*``) are not part of the work. Returns the paths it changed.
    """
    from refactory_dsh.openshell_backend import RUN_ARTIFACTS

    source, workspace = Path(source), Path(workspace)

    def files(root: Path) -> dict[Path, Path]:
        found: dict[Path, Path] = {}
        for folder, directories, names in os.walk(root):
            here = Path(folder).relative_to(root)
            # Pruned, not filtered: an environment can hold more files than the work.
            kept = []
            for name in directories:
                if _excluded(here / name):
                    continue
                if (Path(folder) / name).is_symlink():
                    names.append(name)
                    continue
                kept.append(name)
            directories[:] = kept
            for name in names:
                relative = here / name
                if _excluded(relative):
                    continue
                if relative.parts[:1] == (".factory",) and relative.name in RUN_ARTIFACTS:
                    continue
                found[relative] = Path(folder) / name
        return found

    theirs, ours = files(source), files(workspace)
    changed: list[str] = []
    for relative in sorted(set(ours) - set(theirs)):
        ours[relative].unlink()
        changed.append(str(relative))
    for relative, path in sorted(theirs.items()):
        target = workspace / relative
        if relative in ours and not path.is_symlink() and not target.is_symlink() and \
                target.read_bytes() == path.read_bytes() and \
                (target.stat().st_mode & 0o111) == (path.stat().st_mode & 0o111):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            target.unlink()
        shutil.copy2(path, target, follow_symlinks=False)
        changed.append(str(relative))
    return changed


# ── collect ───────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=300,
                            env={**os.environ, "GIT_LITERAL_PATHSPECS": "0"})
    if result.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args[:3])} failed in {repo}: "
                             f"{result.stderr.decode(errors='replace').strip()}")
    return result.stdout.decode("utf-8", errors="surrogateescape")


#: What a diff of a workspace leaves out (`_excluded`), as git pathspecs.
_PATHSPECS = (".", f":(exclude,top){OWN}", ":(exclude,top).git",
              *(spec for name in EXCLUDED_DIRS
                for spec in (f":(exclude,glob)**/{name}", f":(exclude,glob)**/{name}/**")))


def file_state(path: Path) -> dict[str, str]:
    """What differs in the working tree at ``path`` from its HEAD: each changed or untracked
    file's content digest, or ``deleted``. Refactory's own directory, the environments and
    the caches are left out, as `collect` leaves them out."""
    listing = _git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    found: dict[str, str] = {}
    for entry in listing.split("\0"):
        if len(entry) < 4:
            continue
        name = entry[3:]
        if _excluded(Path(name)):
            continue
        target = Path(path) / name
        found[name] = (_content_digest(target) if target.is_file() or target.is_symlink() else "deleted")
    return found


def _content_digest(target: Path) -> str:
    if target.is_symlink():
        return "symlink:" + os.readlink(target)
    return hashlib.sha256(target.read_bytes()).hexdigest()


def derive_emission(path: Path, base: str, *, conflicts: tuple[str, ...] = (), deletes: bool = True,
                    skip: dict[str, str] | None = None) -> tuple[str, list[str], list[str]]:
    """The workspace's change from ``base`` as ``===FILE:`` and ``===DELETE:`` blocks.

    ``git diff --raw --no-renames <base>`` gives what tracked files did (committed or
    not), and ``git ls-files --others --exclude-standard`` the new files; refactory's own
    directory, ``.venv`` and ``node_modules`` are left out. A whole file is written for
    each change, so materialise applies it unchanged. Returns the emission, what cannot
    be carried (a binary file, a symlink, a mode change, a submodule, a line the
    emission would read as a block header, a deletion a run without a worktree cannot
    make) and what was carried only approximately (line endings or blank lines at the
    ends of a file, which a block normalises). A file in ``skip`` whose content is still
    the digest recorded there is left out: the workspace's preparation made it.
    """
    skip = skip or {}
    # Stat information the agent's tools left stale must not read as a change.
    subprocess.run(["git", "-C", str(path), "update-index", "-q", "--refresh"], capture_output=True, timeout=300)
    raw = _git(path, "diff", "--raw", "-z", "--no-renames", "--no-abbrev", base, "--", *_PATHSPECS)
    entries: dict[str, tuple[str, str, str]] = {}
    fields = raw.split("\0")
    index = 0
    while index < len(fields) - 1:
        meta = fields[index]
        if not meta.startswith(":"):
            index += 1
            continue
        old_mode, new_mode, _old, _new, status = meta[1:].split(" ")[:5]
        entries[fields[index + 1]] = (status[:1], old_mode, new_mode)
        index += 2
    untracked = [name for name in _git(path, "ls-files", "--others", "--exclude-standard", "-z", "--",
                                        *_PATHSPECS).split("\0") if name]
    for name in untracked:
        if entries.get(name, ("",))[0] == "D":
            # Removed from git's index but still on disk: the file is there, changed or not.
            entries[name] = ("M", entries[name][1], "")
        else:
            entries.setdefault(name, ("A", "000000", ""))
    for name in conflicts:
        if name not in entries:
            mode = "100755" if (path / name).is_file() and (path / name).stat().st_mode & 0o111 else "100644"
            entries[name] = ("M", mode, mode)

    blocks: list[str] = []
    refused: list[str] = []
    approximate: list[str] = []
    for name in sorted(entries):
        status, old_mode, new_mode = entries[name]
        relative = Path(name)
        if _excluded(relative):
            continue
        target = path / relative
        present = target.exists() or target.is_symlink()
        if name in skip and skip[name] == (_content_digest(target) if present else "deleted"):
            continue
        if status == "D" or not present:
            if not deletes:
                refused.append(f"{name} is deleted, and a run without a worktree can only write files")
            else:
                blocks.append(f"===DELETE: {name}===\n")
            continue
        if target.is_symlink() or new_mode == "120000":
            refused.append(f"{name} is a symlink")
            continue
        if new_mode == "160000" or old_mode == "160000" or target.is_dir():
            refused.append(f"{name} is a submodule or a directory, not a file")
            continue
        executable = bool(target.stat().st_mode & 0o111)
        was = {"100755": True, "100644": False}.get(old_mode)
        if was is not None and was != executable:
            refused.append(f"{name} changes its file mode ({old_mode} to {'100755' if executable else '100644'})")
            continue
        if was is None and executable:
            refused.append(f"{name} is executable")
            continue
        data = target.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is None or "\0" in text:
            refused.append(f"{name} is a binary file")
            continue
        header = _HEADER.search(text)
        if header:
            line = text.count("\n", 0, header.start()) + 1
            refused.append(f"{name} has a line starting with {header.group(0)} (line {line}), "
                           "which the emission would read as a block of its own")
            continue
        if "\r" in text or text != text.strip("\n") + "\n":
            approximate.append(name)
        blocks.append(f"===FILE: {name}===\n{text.strip(chr(10))}\n")
    return "".join(blocks), refused, approximate


def collect(workspace: Workspace, workdir: Path, writes: list[str], *,
            final_response: str | None = None) -> dict[str, Any]:
    """Make ``workspace``'s work the node's declared outputs, before delivery checks them.

    ``diff``: the answer file (the first write) is written from the clone's diff
    (`derive_emission`). A change it cannot carry leaves a refusal beside it that
    materialise sends back to the node with the reasons (`refusal_for`); the next
    dispatch finds its workspace as it left it, so it can fix what was named. Any other
    write the node declares is its final response, unless the agent wrote it.
    ``files``: each declared write found in the workspace is copied to the run directory.
    """
    from refactory_dsh.agent_delivery import _path

    workdir = Path(workdir).resolve()
    summary: dict[str, Any] = {"collect": workspace.collect, "path": str(workspace.path)}
    if workspace.collect == "diff":
        if not writes:
            raise WorkspaceError(f"agent node {workspace.node!r} collects its workspace's diff and declares "
                                 "no output to write it into")
        emission, refused, approximate = derive_emission(
            workspace.path, workspace.base, conflicts=workspace.conflicts, deletes=workspace.deletes,
            skip=workspace.skip)
        answer = _path(workdir, writes[0])
        answer.parent.mkdir(parents=True, exist_ok=True)
        answer.write_text(emission)
        record = workdir / REFUSAL
        reason = ""
        if refused:
            reason = ("the workspace's change cannot be carried as text files, so nothing of it was applied: "
                      + "; ".join(refused) + ". The runtime commits text files only, without file modes; "
                      "remove these from your workspace or make them plain text, then stop")
            summary["refused"] = refused
        elif not emission:
            reason = (f"your workspace at {workspace.path} has no change from the last attempt, so there was "
                      "nothing to apply: make the change there, then stop")
            summary["refused"] = ["no change"]
        if reason:
            _write_json(record, {"file": writes[0], "digest": _digest(emission), "reason": reason})
        else:
            record.unlink(missing_ok=True)
        if approximate:
            _warn(f"agent node {workspace.node!r}: these files' line endings or end-of-file newlines are "
                  f"normalised in the emission, as every ===FILE: block's are: {', '.join(approximate)}")
            summary["normalised"] = approximate
        for name in writes[1:]:
            extra = _path(workdir, name)
            if not extra.exists():
                extra.parent.mkdir(parents=True, exist_ok=True)
                extra.write_text(final_response or "")
        summary["files"] = len(re.findall(r"^===(?:FILE|DELETE): ", emission, re.MULTILINE))
    else:
        copied = []
        for name in writes:
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise WorkspaceError(f"output {name!r} must be relative")
            source = workspace.path / relative
            if source.is_symlink():
                raise WorkspaceError(f"agent node {workspace.node!r} left {name} in its workspace as a "
                                     "symlink; an output is a file or a directory of files")
            if not source.exists():
                continue
            destination = _path(workdir, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(source, destination, symlinks=False,
                                ignore=lambda folder, names: [item for item in names
                                                              if (Path(folder) / item).is_symlink()])
            else:
                shutil.copyfile(source, destination)
            copied.append(name)
        summary["copied"] = copied
    _write_json(workspace.root / "collected.json", summary)
    return summary


def refusal_for(workdir: Path, name: str, text: str) -> str | None:
    """The reason `collect` could not carry a workspace's change, when ``text`` is what it wrote.

    Materialise asks before applying an emission. Keyed by the emission's digest, so a
    refusal never outlives the emission it was about.
    """
    record = Path(workdir) / REFUSAL
    try:
        loaded = json.loads(record.read_text())
    except (OSError, ValueError):
        return None
    if isinstance(loaded, dict) and loaded.get("file") == name and loaded.get("digest") == _digest(text):
        return str(loaded.get("reason") or "the workspace's change could not be collected")
    return None


def _digest(text: str) -> str:
    """The emission's digest, over its text as a reader with universal newlines sees it."""
    return hashlib.sha256(text.replace("\r\n", "\n").replace("\r", "\n").encode()).hexdigest()


# ── clean-up ──────────────────────────────────────────────────────────────────

def sweep(workdir: Path) -> None:
    """Remove the run's workspaces, at the run's end however it ended.

    Registered as a ``refactory.run_end`` cleanup, so the walker and the DSH evaluator
    run it, and called by a spine run's owner. A workspace another process holds is in
    use and is left; one that cannot be removed is named in the error raised once every
    other was tried.
    """
    base = Path(workdir) / ROOT
    if not base.is_dir():
        return
    left: list[str] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        try:
            lock = open(entry / "lock", "w")
        except OSError as exc:
            left.append(f"{entry}: {exc}")
            continue
        with lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            failures = _remove_tree(entry)
            if failures:
                left.append(f"{entry}: {failures[0]}")
    try:
        base.rmdir()
    except OSError:
        pass
    if left:
        raise OSError("could not remove workspace(s): " + "; ".join(left))


# ── helpers ───────────────────────────────────────────────────────────────────

def _remove_tree(path: Path) -> list[str]:
    """Remove ``path`` as far as it can be removed; what could not be, as messages."""
    failures: list[str] = []

    def record(_function: Any, _name: str, error: Any) -> None:
        failures.append(str(error[1] if isinstance(error, tuple) else error))

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=record)
    else:  # pragma: no cover - Python 3.11 names the handler onerror
        shutil.rmtree(path, onerror=record)
    return failures


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")


def _warn(message: str) -> None:
    print(f"refactory: workspace: {message}", file=sys.stderr)
