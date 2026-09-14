"""What a package needs that is not Python, and how the absence reads.

Every tool this system has carried so far has been a Python callable: an op is a
function of the run context, the validator resolves a node's ``call`` to a real
object, and ``package.ops`` lists modules. Lean is not a callable. It is a
toolchain, a proof checker with its own file syntax, and -- past the core library --
a checkout of mathlib that a per-problem project names as a dependency. A package of
such modes has to be able to say so, and the system has to be able to answer "can
this machine run that?" before a run spends any budget.

**The declaration.** A package puts a ``tools.py`` beside its ``__init__.py`` and
assigns ``TOOLS`` to a tuple of :class:`Tool`. ``Package`` gains no field, for three
reasons. The manifest describes the package's own interface -- name, modes, ops,
which packages it requires -- and every field on it is validated by
``Package.__post_init__`` as a property of the package. Whether Lean is installed is
a property of the *machine*, and it changes without the package changing; putting it
on the manifest would mix a fact about the host into a value the registry compares,
copies and describes. Second, a declaration the registry reads *separately* can be
read **before** the package is imported, which is the case that matters: the usual
way a missing tool announces itself is an ``ImportError`` from the package's own
ops, and the registry reports that as "cannot import package sympy_pack: No module
named 'sympy'" -- a message naming the symptom, not the fix, and a false accusation
in the common case where the package is fine and the machine is not. The reading in
:func:`read_tools` therefore loads ``tools.py`` by path and never executes the
package. Third, ``Node`` already has a ``tools`` field and it means something else
in the lowered IR -- the agent's tool allowlist -- so reusing it would put two
unrelated facts behind one name.

**Not a cordis plugin.** The DSH side composes capabilities as plugins, and a Lean
capability would be one there: something has to put an MCP server or a command
runner in front of the model. That seam answers "what can the running agent do?" and
is decided by whoever writes ``cordis.yml``. A package cannot reach it: the registry
discovers packages by scanning a directory, and the library is not allowed to know
any package by name. The question here is different and earlier -- "can this
package's ops run on this machine at all?" -- and it must be answerable statically,
before the run starts, by a reader that is only reading. The two compose rather than
compete: the plugin gives the agent a Lean tool, and the declaration gives the
package a way to refuse to start without one.

**Provisioning: refuse, and name the fix.** Nothing here installs anything. A
declaration carries the exact command as ``Tool.install`` and the message is built
from it, so a missing tool reads as "install it with: ..." rather than as a crash.
Installing on demand was rejected because a toolchain is not a per-package affair
(mathlib is gigabytes, and every package that needed it would fetch its own), because
a run's budget is evaluation budget and a download is not an evaluation, and because
a package that mutates the machine it lands on is a package nobody can audit. The
one place absence is fatal is the node that is about to use the tool, and there the
failure is :class:`ToolMissing`, raised with the fix in the message.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

#: The file a package declares its requirements in, beside ``__init__.py``.
DECLARATION = "tools.py"

#: What a declared tool is, and therefore how its presence is decided. ``binary``
#: is looked up on ``PATH``, ``python`` is looked up as an importable module, and
#: ``path`` is a directory that must exist -- a checkout, a cache, a dataset.
TOOL_KINDS = ("binary", "python", "path")

#: How long a version probe may take. A tool that hangs is a tool this machine
#: cannot use, and the check must not be able to hang a run either.
PROBE_TIMEOUT_S = 20

#: An environment variable name, for a declaration's ``env``.
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ToolchainError(ValueError):
    """A tool declaration is malformed, or a declared tool is not usable here."""


class ToolDeclError(ToolchainError):
    """A package's ``tools.py`` is not a readable declaration."""


class ToolMissing(ToolchainError):
    """A tool a package declared is not present on this machine."""


@dataclass(frozen=True)
class Tool:
    """One thing a package needs that is not a Python callable.

    ``install`` and ``why`` are required rather than optional. A declaration
    without them can say "absent" and nothing else, which is the failure this
    module exists to replace; the point of declaring a tool is to be able to name
    what it is for and what to do about it.
    """

    name: str
    kind: str = "binary"
    #: What is looked up: a command name, an importable module, or a path. For
    #: ``path``, ``$NAME`` expands from the environment, so a checkout that lives
    #: wherever its owner put it is still declarable.
    probe: str = ""
    #: The exact command that makes the tool available, quoted in the failure.
    install: str = ""
    #: What the tool is used for, in one clause; it is part of the failure message.
    why: str = ""
    version_args: tuple[str, ...] = ()
    #: ``True`` when only some of the package's modes need this tool. Its absence
    #: is then a reported fact rather than a reason to refuse the package, which is
    #: what keeps a mathlib-only mode from making the core-library modes unusable.
    optional: bool = False
    #: Environment the tool needs *at run time*, as ``(name, value)`` pairs. Read
    #: here, where the real environment is visible, and forwarded into a sandboxed
    #: run, which is somewhere else entirely -- see :func:`tool_env`. A tuple of
    #: pairs rather than a mapping so the declaration stays hashable.
    env: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").replace("-", "").isalnum():
            raise ToolDeclError(
                f"tool name {self.name!r} must be letters, digits, '_' or '-', e.g. 'lean'"
            )
        if self.kind not in TOOL_KINDS:
            raise ToolDeclError(
                f"tool {self.name!r}: unknown kind {self.kind!r}; known: {list(TOOL_KINDS)}"
            )
        if not self.probe.strip():
            raise ToolDeclError(f"tool {self.name!r} declares nothing to look for")
        if not self.install.strip():
            raise ToolDeclError(
                f"tool {self.name!r} declares no install command; a declaration that "
                "cannot name the fix leaves the failure unusable"
            )
        if not self.why.strip():
            raise ToolDeclError(
                f"tool {self.name!r} declares no 'why'; the failure message has to say "
                "what the tool is for"
            )
        for variable, _ in self.env:
            if not _ENV_NAME.match(variable):
                raise ToolDeclError(
                    f"tool {self.name!r} declares env {variable!r}, which is not an "
                    "environment variable name"
                )

    def resolved(self) -> str:
        """The probe with ``~`` and ``$VARS`` expanded, for a ``path`` tool."""
        return os.path.expanduser(os.path.expandvars(self.probe))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "probe": self.probe,
            "install": self.install,
            "why": self.why,
            "optional": self.optional,
            "env": [variable for variable, _ in self.env],
        }


@dataclass(frozen=True)
class ToolStatus:
    """Whether one declared tool is present, and what was found."""

    tool: Tool
    present: bool
    detail: str = ""
    version: str = ""

    @property
    def name(self) -> str:
        return self.tool.name

    def fix(self) -> str:
        """The sentence a user gets instead of an import error."""
        if self.present:
            return f"{self.name} is available ({self.detail})"
        return (
            f"the tool {self.name!r} is {self.detail}; {self.tool.why}. Install it "
            f"with: {self.tool.install}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.tool.to_dict(),
            "present": self.present,
            "detail": self.detail,
            "version": self.version,
            "fix": "" if self.present else self.fix(),
        }

    def __str__(self) -> str:
        if self.present:
            found = f" ({self.detail})" if self.detail else ""
            version = f" {self.version}" if self.version else ""
            return f"{self.name}: present{version}{found}"
        return f"{self.name}: {self.detail}"


# ── reading the declaration ────────────────────────────────────────────────
def _module_path(package: str, project: Path | None = None) -> Path | None:
    from refactory_dsh import registry
    from refactory_dsh.paths import project_root

    root = registry.where(package, project)
    if root is None and project is None:
        root = registry.where(package, project_root())
    return None if root is None else root / DECLARATION


def _load_declaration(package: str, path: Path) -> Any:
    """Execute one package's ``tools.py``, and nothing else.

    By path rather than by import, because importing
    ``refactory_packages.<name>.tools`` would first import
    ``refactory_packages.<name>`` and run the manifest, the modes and the ops --
    which is exactly the code a missing tool can make unimportable. A declaration
    has to be readable when the package is not, or it cannot report the tool whose
    absence caused the failure. The cost is that a declaration may not use relative
    imports; it is a list of facts, and it should not need to.
    """
    spec = importlib.util.spec_from_file_location(f"_refactory_tools_{package}", path)
    if spec is None or spec.loader is None:
        raise ToolDeclError(f"{path} is not readable as a module")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # a declaration that does not run is a package defect
        raise ToolDeclError(
            f"the tool declaration {path} could not be read: {type(exc).__name__}: {exc}"
        ) from exc
    return module


def read_tools(package: str, project: Path | None = None) -> tuple[Tool, ...]:
    """What a package declares it needs. Empty when it declares nothing."""
    path = _module_path(package, project)
    if path is None:
        raise ToolDeclError(f"no package {package!r} in either root")
    if not path.exists():
        return ()
    declared = getattr(_load_declaration(package, path), "TOOLS", None)
    if declared is None:
        raise ToolDeclError(
            f"{path} declares no TOOLS; a package with no requirement should not "
            "carry a tools.py at all"
        )
    if isinstance(declared, Tool):
        raise ToolDeclError(f"{path}: TOOLS must be a tuple of Tool, not a single Tool")
    tools = tuple(declared)
    for tool in tools:
        if not isinstance(tool, Tool):
            raise ToolDeclError(f"{path}: TOOLS contains {tool!r}, which is not a Tool")
    names = [tool.name for tool in tools]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise ToolDeclError(f"{path}: {repeated} are declared more than once")
    return tools


# ── deciding whether this machine has them ─────────────────────────────────
def _run_version(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable ({type(exc).__name__})"
    text = f"{completed.stdout}\n{completed.stderr}".strip()
    return text.splitlines()[0].strip()[:120] if text else ""


def probe(tool: Tool) -> ToolStatus:
    """Whether one declared tool is usable here, and what was found."""
    if tool.kind == "binary":
        return _probe_binary(tool)
    if tool.kind == "python":
        return _probe_python(tool)
    return _probe_path(tool)


def _probe_binary(tool: Tool) -> ToolStatus:
    found = shutil.which(tool.probe)
    if not found:
        return ToolStatus(tool, False, f"not on PATH as {tool.probe!r}")
    version = _run_version([found, *tool.version_args]) if tool.version_args else ""
    return ToolStatus(tool, True, found, version)


def _probe_python(tool: Tool) -> ToolStatus:
    try:
        spec = importlib.util.find_spec(tool.probe)
    except (ImportError, ValueError, AttributeError) as exc:
        return ToolStatus(tool, False, f"not importable ({type(exc).__name__}: {exc})")
    if spec is None:
        return ToolStatus(tool, False, f"not importable as the module {tool.probe!r}")
    try:
        module = importlib.import_module(tool.probe)
    except Exception as exc:  # a module that is found but broken is not present
        return ToolStatus(
            tool, False, f"found but not importable ({type(exc).__name__}: {exc})"
        )
    version = str(getattr(module, "__version__", "") or "")
    return ToolStatus(tool, True, f"importable as {tool.probe!r}", version)


def _probe_path(tool: Tool) -> ToolStatus:
    if tool.probe.startswith("$"):
        variable = tool.probe[1:].split("/", 1)[0]
        if not os.environ.get(variable):
            return ToolStatus(
                tool, False, f"missing: {variable} is not set, so {tool.probe} names nothing"
            )
    target = Path(tool.resolved())
    if not target.exists():
        return ToolStatus(tool, False, f"missing: {target} does not exist")
    return ToolStatus(tool, True, str(target))


def check(tools: Iterable[Tool]) -> list[ToolStatus]:
    """Probe every declared tool, present or not."""
    return [probe(tool) for tool in tools]


def absent(statuses: Iterable[ToolStatus]) -> list[ToolStatus]:
    """The ones this machine does not have."""
    return [status for status in statuses if not status.present]


def _absent_message(package: str, statuses: list[ToolStatus]) -> str:
    sentences = [
        f"the package {package} declares the tool {status.name!r} ({status.tool.why}), "
        f"and it is {status.detail}; the fix is: {status.tool.install}"
        for status in statuses
    ]
    return " ".join(sentences)


def require(package: str, name: str, project: Path | None = None) -> ToolStatus:
    """One tool a node is about to use, or :class:`ToolMissing` naming the fix.

    This is what an op calls as its first statement. A run that cannot work has to
    say so at the node that would have used the tool, and not forty minutes later
    in a traceback from a subprocess.
    """
    tools = read_tools(package, project)
    tool = next((candidate for candidate in tools if candidate.name == name), None)
    if tool is None:
        known = ", ".join(sorted(candidate.name for candidate in tools)) or "none"
        raise ToolDeclError(
            f"{package} declares no tool {name!r}; declared: {known}"
        )
    status = probe(tool)
    if not status.present:
        raise ToolMissing(_absent_message(package, [status]))
    return status


def import_tool(package: str, name: str, project: Path | None = None) -> Any:
    """The module a ``python`` tool declares, imported, or the missing-tool error.

    An op that wants sympy asks for the tool and gets the module it declares, so
    the requirement and the import are one statement rather than two that can drift
    apart.
    """
    status = require(package, name, project)
    if status.tool.kind != "python":
        raise ToolDeclError(
            f"tool {name!r} of {package} is a {status.tool.kind!r} tool; only a "
            "'python' tool names a module to import"
        )
    return importlib.import_module(status.tool.probe)


def preflight(package: str, project: Path | None = None) -> list[ToolStatus]:
    """Every required tool a package declares, or the error listing what is missing.

    ``optional`` tools are excluded: they are needed by some of the package's
    modes, and refusing the whole package over one would make the modes that do not
    need them unrunnable for no reason.
    """
    statuses = check(read_tools(package, project))
    missing = [status for status in absent(statuses) if not status.tool.optional]
    if missing:
        raise ToolMissing(_absent_message(package, missing))
    return statuses


def report(package: str, project: Path | None = None) -> list[ToolStatus]:
    """Every tool a package declares, with its status on this machine."""
    return check(read_tools(package, project))


def tool_env(package: str, project: Path | None = None) -> dict[str, str]:
    """The environment a package's *present* tools need, for a run elsewhere.

    A sandboxed run is contained by rewriting ``HOME`` to its own working
    directory, which is the right default: code we did not write should not read a
    user's dotfiles. But every per-user toolchain manager finds its installation
    *through* ``HOME`` -- ``elan`` reads ``$HOME/.elan``, and so do rustup, pyenv
    and nvm -- so containment alone makes a tool that is installed unreachable, and
    the run fails claiming there is no toolchain. That was Lean: the same mode
    scored 1.0 straight through and 0.0 under the sandbox, on one machine.

    The value is resolved **here**, in the process that reads the declaration and
    can see the real environment, then carried to the sandbox. A declaration cannot
    compute it itself: by the time package code runs, ``HOME`` is already the
    working directory.

    Absent tools contribute nothing -- forwarding for a tool that is not installed
    would only add environment a run cannot use.
    """
    forwarded: dict[str, str] = {}
    for status in check(read_tools(package, project)):
        if status.present:
            forwarded.update(dict(status.tool.env))
    return forwarded
