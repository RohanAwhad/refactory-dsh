"""Running code we did not write.

create-mode writes Python and then we run it. Nothing sandboxed it: `check_package`
imported the generated package into the driver process to validate it, and the smoke
run executed it in-process too. Hand-written modes are trusted; a generated package
is not, and that distinction only appeared when create-mode existed.

Two bars, because they catch different things:

- **Validation without execution.** A generated package is parsed, not imported. An
  AST check guarantees it is syntactically valid, declares a `PACKAGE`, and declares
  its modes and operations -- which is what most broken generations actually are --
  and it guarantees it without running a line of the model's code.
- **Execution under limits.** The smoke run is a real execution, so it happens in a
  subprocess with resource limits, a scrubbed environment and a working directory
  it cannot escape.

The old system sandboxed the candidate program (`srf/ops/common/sandbox.py`, under
firejail or gvisor). This is the same idea for a different artifact.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from refactory_dsh import registry

#: Ceilings for one sandboxed run.
DEFAULT_TIMEOUT_S = 180
DEFAULT_MEMORY_MB = 2048
DEFAULT_CPU_S = 120
DEFAULT_FILE_MB = 64

#: Anything whose name looks like a credential is removed from the environment.
#: A generated package has no reason to read a key, and every reason not to.
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")

#: The environment a sandboxed run is allowed to see. An allowlist, not a denylist:
#: a name nobody thought of is a name that is not passed.
_ALLOWED_ENV = (
    "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "PYTHONPATH", "VIRTUAL_ENV",
    "REFACTORY_WORKDIR", "REFACTORY_KNOBS", "DSH_WORKFLOW_NODE", "SYSTEMROOT",
)


class SandboxError(RuntimeError):
    """A sandboxed run could not be carried out."""


@dataclass
class SandboxResult:
    """What a sandboxed run produced."""

    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    def problem(self) -> str:
        if self.timed_out:
            return "the run exceeded its time limit"
        if self.returncode != 0:
            tail = self.stderr.strip().splitlines()
            return tail[-1] if tail else f"exit {self.returncode}"
        return ""


# ── validation without execution ───────────────────────────────────────────
def ast_check(name: str, project: Path | None = None) -> list[str]:
    """Everything wrong with a package that can be seen without importing it."""
    project = Path(project or Path.cwd())
    root = registry.where(name, project)
    problems: list[str] = []
    if root is None:
        return [f"no package {name!r} in either root"]
    files = sorted(root.glob("*.py"))
    if not (root / "__init__.py").exists():
        return ["__init__.py is missing; a package needs one"]

    parsed: dict[str, ast.Module] = {}
    for path in files:
        try:
            parsed[path.name] = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:
            problems.append(f"{path.name} does not parse: line {exc.lineno}: {exc.msg}")
    if problems:
        return problems

    init = parsed["__init__.py"]
    packages = [
        node for node in init.body
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", "") == "PACKAGE" for t in node.targets)
    ]
    if not packages:
        problems.append("__init__.py does not assign PACKAGE at module level")
    else:
        text = ast.unparse(packages[0].value)
        if "Package(" not in text:
            problems.append("PACKAGE is not built by Package(...)")
        if f"name=" not in text.replace(" ", ""):
            problems.append("PACKAGE does not declare a name")
        if "modes=" not in text.replace(" ", ""):
            problems.append("PACKAGE declares no modes")

    mode_classes = {
        cls.name: cls
        for module in parsed.values()
        for cls in module.body
        if isinstance(cls, ast.ClassDef)
    }
    if not mode_classes:
        problems.append("no class is declared anywhere in the package")
    for class_name, cls in sorted(mode_classes.items()):
        methods = {
            node.name for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        bases = {ast.unparse(base) for base in cls.bases}
        declared = any("Mode" in base for base in bases)
        if declared and "build" not in methods:
            problems.append(f"class {class_name} looks like a Mode but declares no build()")
        if declared and "score" not in methods:
            problems.append(
                f"class {class_name} looks like a Mode but declares no score(), so it "
                "produces no signal"
            )

    for module in parsed.values():
        for node in ast.walk(module):
            if isinstance(node, ast.Call):
                called = ast.unparse(node.func)
                if called in ("eval", "exec", "compile", "__import__"):
                    problems.append(
                        f"the package calls {called}(); a generated package is not run "
                        "with that reach"
                    )
    return sorted(set(problems))


# ── execution under limits ─────────────────────────────────────────────────
def scrub(env: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a sandboxed run sees: an allowlist, with secrets dropped.

    The project's own `python` is put on the PATH: a brief's check runs in a
    subprocess with none of the caller's environment, and "run the tests" means the
    interpreter that has the project installed.
    """
    from refactory_dsh.paths import venv_bin

    source = dict(os.environ if env is None else env)
    out: dict[str, str] = {}
    for name in _ALLOWED_ENV:
        if name not in source:
            continue
        if any(marker in name.upper() for marker in _SECRET_MARKERS):
            continue
        out[name] = source[name]
    bindir = str(venv_bin())
    existing = out.get("PATH", "")
    out["PATH"] = f"{bindir}{os.pathsep}{existing}" if existing else bindir
    out.setdefault("VIRTUAL_ENV", str(Path(bindir).parent))
    return out


def _limits(memory_mb: int, cpu_s: int, file_mb: int):  # pragma: no cover - posix only
    """A preexec that caps what the child can consume."""
    import resource

    def apply() -> None:
        caps = (
            (resource.RLIMIT_CPU, (cpu_s, cpu_s)),
            (resource.RLIMIT_FSIZE, (file_mb * 1024 * 1024,) * 2),
        )
        for which, value in caps:
            try:
                resource.setrlimit(which, value)
            except (ValueError, OSError):
                pass
        for attribute, value in (
            ("RLIMIT_AS", memory_mb * 1024 * 1024),
            ("RLIMIT_DATA", memory_mb * 1024 * 1024),
        ):
            which = getattr(resource, attribute, None)
            if which is None:
                continue
            try:
                resource.setrlimit(which, (value, value))
            except (ValueError, OSError):
                pass
        try:
            os.setsid()
        except OSError:
            pass

    return apply


def run_sandboxed(
    code: str,
    *,
    workdir: Path,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpu_s: int = DEFAULT_CPU_S,
    file_mb: int = DEFAULT_FILE_MB,
    env: dict[str, str] | None = None,
) -> SandboxResult:
    """Run a snippet in a subprocess with limits, from the workspace, scrubbed.

    A subprocess rather than a thread or a same-process guard, because the thing
    being contained is *code*: only a separate process can be killed, capped and
    given its own working directory.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    child_env = scrub(env)
    child_env["HOME"] = str(workdir)
    child_env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    kwargs: dict[str, Any] = {}
    if sys.platform != "win32":
        kwargs["preexec_fn"] = _limits(memory_mb, cpu_s, file_mb)
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(workdir),
            env=child_env,
            timeout=timeout_s,
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        return SandboxResult(
            ok=False,
            timed_out=True,
            stdout=(exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            stderr="timed out",
        )
    payload: dict[str, Any] = {}
    for line in reversed(result.stdout.strip().splitlines()):
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            payload = loaded
            break
    return SandboxResult(
        ok=result.returncode == 0,
        payload=payload,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def run_command(
    command: str,
    *,
    workdir: Path,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpu_s: int = DEFAULT_CPU_S,
    file_mb: int = DEFAULT_FILE_MB,
    env: dict[str, str] | None = None,
) -> SandboxResult:
    """Run a shell command under the same containment as a code snippet.

    A command is what a brief declares as its check ("run the tests", "build it"),
    and it is arbitrary, so it gets the same limits and the same scrubbed
    environment as anything else we did not write.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    child_env = scrub(env)
    child_env["HOME"] = str(workdir)
    kwargs: dict[str, Any] = {}
    if sys.platform != "win32":
        kwargs["preexec_fn"] = _limits(memory_mb, cpu_s, file_mb)
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(workdir),
            env=child_env,
            timeout=timeout_s,
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(ok=False, timed_out=True, stderr="timed out")
    return SandboxResult(
        ok=result.returncode == 0,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def smoke(identifier: str, workdir: Path, *, project: Path | None = None, **kwargs: Any) -> SandboxResult:
    """Run one mode once, in a sandbox, and report what it scored."""
    project = Path(project or Path.cwd())
    code = (
        "import json\n"
        "from pathlib import Path\n"
        "from refactory_dsh import registry\n"
        f"package, mode = registry.resolve(Path({str(project)!r}), {identifier!r})\n"
        f"result = mode().evaluate(Path({str(Path(workdir))!r}))\n"
        "print(json.dumps({'exit_ok': result.exit_ok, 'error': result.error,"
        " 'score': result.score, 'iterations': result.iterations}))\n"
    )
    return run_sandboxed(code, workdir=Path(workdir), env=dict(os.environ), **kwargs)


def verify_sandboxed(
    name: str, *, project: Path | None = None, **kwargs: Any
) -> SandboxResult:
    """Everything wrong with a package, checked without importing it here.

    The check needs the package loaded, and loading **is** execution. So the whole
    check runs in a subprocess: the package name, its ops modules, every mode's
    construction, its score and its knobs, and every validator rule. This is the
    only place a generated package is loaded, and it is loaded somewhere that
    cannot reach the driver's filesystem, environment, or imports.

    Answers in one call what would otherwise be three: there is no read-only way
    to ask whether model-written code is a usable mode.
    """
    project = Path(project or Path.cwd())
    code = (
        "import json\n"
        "import importlib\n"
        "from pathlib import Path\n"
        "from refactory_dsh import registry\n"
        "from refactory_dsh.nn import validate_state\n"
        "problems = []\n"
        f"package = registry.load({name!r}, Path({str(project)!r}))\n"
        f"expected = {name!r}\n"
        # Concatenation, not a nested f-string: `{name!r}` inside one would insert
        # the quotes too and break the enclosing literal.
        "if package.name != expected:\n"
        "    problems.append('PACKAGE.name is ' + repr(package.name) + ', not ' + repr(expected))\n"
        "if not package.modes:\n"
        "    problems.append('PACKAGE.modes is empty; a package with no mode cannot be run')\n"
        "for ops_module in package.ops:\n"
        "    try:\n"
        "        importlib.import_module(ops_module)\n"
        "    except Exception as exc:\n"
        "        problems.append(f'the declared ops module {ops_module!r} does not import:"
        " {type(exc).__name__}: {exc}')\n"
        "for mode_class in package.modes:\n"
        "    identifier = f'{package.name}/{mode_class.name}'\n"
        "    try:\n"
        "        mode = mode_class()\n"
        "    except Exception as exc:\n"
        "        problems.append(f'{identifier}: cannot be constructed: {exc}')\n"
        "        continue\n"
        "    if not mode.is_scored() and not getattr(mode, 'unscored_reason', ''):\n"
        "        problems.append(f'{identifier} does not override score(), so it produces no signal')\n"
        "    if not mode.parameters():\n"
        "        problems.append(f'{identifier} declares no knobs, so there is nothing to optimize')\n"
        "    for issue in validate_state(mode.state_dict(), invariants=mode.invariants(),"
        " resolve_calls=True):\n"
        "        problems.append(f'{identifier}: {issue}')\n"
        "print(json.dumps({'problems': problems}))\n"
    )
    return run_sandboxed(code, workdir=project, env=dict(os.environ), **kwargs)


def describe_package(name: str, *, project: Path | None = None, **kwargs: Any) -> SandboxResult:
    """A package's manifest and mode ids, read without importing it here.

    Reading ``PACKAGE`` means importing the module that declares it, and for a
    generated package that is the model's code. So the read happens where the
    check happens. A caller that only wants to *name* what a package contains — to
    report what was registered, or to choose a mode to smoke — should not have to
    run the package in the driver to find out.
    """
    project = Path(project or Path.cwd())
    code = (
        "import json\n"
        "from pathlib import Path\n"
        "from refactory_dsh import registry\n"
        f"package = registry.load({name!r}, Path({str(project)!r}))\n"
        "print(json.dumps({'describe': package.describe(), 'ids': package.ids()}))\n"
    )
    return run_sandboxed(code, workdir=project, env=dict(os.environ), **kwargs)


def promote(project: Path, name: str, *, note: str = "") -> dict[str, Any]:
    """Mark a package trusted, explicitly, and record who did it.

    Nothing acts on the tier until someone decides it may: a package that was
    generated stays generated until a person says otherwise.
    """
    project = Path(project)
    root = registry.workspace_root(project) / name
    if not (root / "__init__.py").exists():
        raise SandboxError(f"no workspace package {name!r}")
    origin = root / registry.ORIGIN
    recorded = json.loads(origin.read_text()) if origin.exists() else {}
    import datetime

    recorded["trust"] = "trusted"
    recorded.setdefault("origin", "promoted")
    recorded["promoted_at"] = datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat(timespec="seconds")
    if note:
        recorded["note"] = note
    origin.write_text(json.dumps(recorded, indent=2))
    return recorded


def main(argv: list[str] | None = None) -> int:
    """``python -m refactory_dsh.sandbox --ast <pkg> | --smoke <pkg>/<mode>``.

    Runnable so the smoke check can itself be run under a different containment
    (firejail, gvisor, a container) without changing anything above it.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    project = Path.cwd()
    workdir = Path(os.environ.get("REFACTORY_WORKDIR") or project)
    try:
        if argv[:1] == ["--ast"] and len(argv) == 2:
            problems = ast_check(argv[1], project)
            print(json.dumps({"package": argv[1], "problems": problems}))
            return 1 if problems else 0
        if argv[:1] == ["--verify"] and len(argv) == 2:
            result = verify_sandboxed(argv[1], project=project)
            problems = list(result.payload.get("problems") or [])
            if not result.ok and not problems:
                problems = [result.problem() or "the check could not be run"]
            print(json.dumps({"package": argv[1], "problems": problems}))
            return 1 if problems else 0
        if argv[:1] == ["--smoke"] and len(argv) == 2:
            result = smoke(argv[1], workdir, project=project)
            print(json.dumps({"ok": result.ok, "problem": result.problem(), **result.payload}))
            return 0 if result.ok else 1
    except SandboxError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(
        "usage: python -m refactory_dsh.sandbox --ast <package> | "
        "--verify <package> | --smoke <package>/<mode>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":  # pragma: no cover - the entry point
    raise SystemExit(main())
