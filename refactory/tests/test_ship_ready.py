"""Shippable: a stranger can install, run and extend this.

Issue #31. Everything else was legible to us. This is the check that the *docs* are
true — not the code's own knowledge of itself — because the failure it guards is a
tutorial that cannot be followed, which no unit test of the library would notice.

It is deliberately about the documented path: the commands the README and the
tutorials show, run as written, with the output they promise.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = ROOT / "refactory" / "docs"
PYTHON = ROOT / ".venv" / "bin" / "python"

#: Every doc a stranger is told to read, in the order they meet it.
SHIPPED_DOCS = (
    ROOT / "README.md",
    DOCS / "tutorials" / "index.md",
    DOCS / "tutorials" / "01-write-a-mode.md",
    DOCS / "tutorials" / "02-optimize-it.md",
    DOCS / "architecture.md",
    DOCS / "api" / "index.md",
)


def _bash_blocks(text: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)```", text, re.DOTALL)


# ── every command a doc shows exists ───────────────────────────────────────


@pytest.mark.parametrize("document", SHIPPED_DOCS, ids=lambda p: p.name)
def test_every_module_a_document_names_is_runnable(document):
    """`python -m refactory_dsh.X` in a doc is a command a reader will run."""
    modules = set()
    for block in _bash_blocks(document.read_text()):
        modules.update(re.findall(r"python -m (refactory_dsh[\w.]*)", block))
        modules.update(re.findall(r"\.venv/bin/python -m (refactory_dsh[\w.]*)", block))
    for module in modules:
        result = subprocess.run(
            [str(PYTHON), "-c", f"import {module}"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        assert result.returncode == 0, (
            f"{document.name} tells the reader to run `python -m {module}`, which "
            f"does not import: {result.stderr.strip()[:200]}"
        )


def test_every_documented_cli_accepts_the_flags_it_shows():
    """A flag in a doc is a promise about an argument parser."""
    checked = 0
    for document in SHIPPED_DOCS:
        for block in _bash_blocks(document.read_text()):
            for line in block.splitlines():
                if "--help" in line or "python -m refactory_dsh" not in line:
                    continue
                # `python -m refactory_dsh.loop <mode> --epochs 2 ...`
                module = re.search(r"-m (refactory_dsh[\w.]*)", line)
                if module is None:
                    continue
                flags = set(re.findall(r"(--[a-z][\w-]*)", line))
                if not flags:
                    continue
                # Where the flags live depends on the command: a package's own
                # options are on its top-level parser, while `console status --run`
                # declares `--run` on the `status` subcommand. Asking the wrong
                # parser reports a valid command as broken.
                rest = line.split(module.group(1), 1)[1].split()
                # A subcommand is a bare word; an argument is not. `console status
                # --run X` has the verb `status`, while `sandbox --smoke pkg/mode`
                # has only a positional, and treating that positional as a verb asks
                # the wrong parser and reports a valid command as broken.
                verb = [
                    word for word in rest
                    if not word.startswith("-") and "/" not in word and "." not in word
                ]
                argv = [str(PYTHON), "-m", module.group(1)]
                if verb:
                    argv.append(verb[0])
                argv.append("--help")
                result = subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT))
                missing = sorted(flag for flag in flags if flag not in result.stdout)
                assert missing == [], (
                    f"{document.name} shows {missing} for `{module.group(1)}`, which "
                    "its argument parser does not accept"
                )
                checked += 1
    assert checked, "no documented command line was checked, so this proves nothing"


@pytest.mark.parametrize(
    "module",
    ("refactory_dsh.sandbox", "refactory_dsh.loop", "refactory_dsh.console"),
)
def test_asking_a_module_for_help_is_not_an_error(module):
    """`--help` is a question, so it answers on stdout and succeeds.

    A reader who types `--help` and gets exit status 2 has been told they made a
    mistake. Two of these modules printed their flags only to stderr, which also
    means a script that captures stdout saw an empty help page.
    """
    result = subprocess.run(
        [str(PYTHON), "-m", module, "--help"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert result.returncode == 0, (
        f"`python -m {module} --help` exited {result.returncode}; "
        f"stderr was {result.stderr.strip()[:200]!r}"
    )
    assert result.stdout.strip(), f"`python -m {module} --help` printed nothing to stdout"


# ── the tour runs, and produces what it promises ───────────────────────────


@pytest.mark.parametrize("seed,expected", [("5", "0.955")])
def test_the_tour_produces_the_numbers_it_documents(tmp_path, seed, expected):
    """The five-minute tour's headline result, run exactly as written."""
    out = tmp_path / "run"
    result = subprocess.run(
        [
            str(PYTHON), "-m", "refactory_dsh.loop", "circle_packing/adaptive",
            "--epochs", "2", "--batch-size", "4", "--optimizer", "random",
            "--seed", seed, "--runs-root", str(out),
        ],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr[-2000:]
    report = json.loads(result.stdout[result.stdout.index("{"):])
    assert str(report["seed_score"]) == "0.875", report["seed_score"]
    assert str(report["best_score"]) == expected, (
        f"the tutorial documents best_score {expected} and got {report['best_score']}"
    )


def test_the_tour_works_from_a_directory_that_is_not_the_checkout(tmp_path):
    """The docs say any directory works. A stranger may well be somewhere else."""
    result = subprocess.run(
        [
            str(PYTHON), "-m", "refactory_dsh.registry", "list",
        ],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "circle_packing/adaptive" in result.stdout, (
        "the catalogue depends on the working directory, so a reader following the "
        "docs from anywhere else sees a different system"
    )


# ── the tools a mode needs ─────────────────────────────────────────────────


def test_a_missing_tool_is_reported_with_what_to_install():
    """The shipping story for a non-Pythonic tool: declared, checked, explained."""
    from refactory_dsh import toolchain

    declared = toolchain.Tool(
        name="absent_thing", kind="binary", probe="definitely-not-installed-xyz",
        install="pkg install definitely-not-installed-xyz",
        why="proving theorems",
    )
    statuses = toolchain.check([declared])
    missing = toolchain.absent(statuses)
    assert [status.name for status in missing] == ["absent_thing"]
    assert not missing[0].present
    # The declaration cannot be made without naming the fix, which is the point:
    # a failure that cannot say what to do next is the thing this replaces.
    with pytest.raises(toolchain.ToolDeclError):
        toolchain.Tool(name="no_fix", probe="x", why="something")


def test_a_declared_tool_that_is_missing_names_the_fix_when_required():
    from refactory_dsh import toolchain

    with pytest.raises(toolchain.ToolMissing) as refused:
        toolchain.preflight("lean_pack")
    message = str(refused.value)
    assert "lean" in message.lower(), message
    assert "install" in message.lower() or "curl" in message.lower(), (
        f"the failure does not name what to do about it: {message}"
    )


def test_a_package_that_needs_nothing_still_loads_without_the_toolchain():
    """Graceful absence: one package's missing tool must not break the others."""
    from refactory_dsh import registry

    for name in ("circle_packing", "sympy_pack"):
        package = registry.load(name, ROOT)
        assert package.modes, f"{name} declares no modes"


# ── the docs a stranger needs are all present ──────────────────────────────


@pytest.mark.parametrize("document", SHIPPED_DOCS, ids=lambda p: p.name)
def test_every_shipped_document_exists(document):
    assert document.exists(), f"{document} is linked or referenced but missing"


def test_the_readme_points_at_the_documentation():
    """A stranger starts at the README. It has to tell them where to go next."""
    readme = (ROOT / "README.md").read_text()
    for target in ("refactory/docs/architecture.md", "refactory/docs/tutorials"):
        assert target in readme, f"the README does not mention {target}"


def test_every_document_the_readme_links_opens():
    """The names on the README's Documentation table are promises, not prose.

    The table is hand-written, so a moved or renamed file leaves a row pointing at
    nothing. Resolving each link is what turns the table into a check.
    """
    readme = (ROOT / "README.md").read_text()
    section = readme.split("## Documentation", 1)[1].split("\n## ", 1)[0]
    links = [
        target
        for target in re.findall(r"\]\(([^)#]+)\)", section)
        if not target.startswith(("http://", "https://"))
    ]
    assert links, "the README's Documentation section links nothing"
    for target in links:
        assert (ROOT / target).exists(), (
            f"README.md links {target}, which does not exist"
        )
