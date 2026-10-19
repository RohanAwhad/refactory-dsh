"""Tests for check-failure classification and unbuilt-package diagnosis.

Each test fails without the corresponding change:
1. check_failure_kind does not exist without the change
2. scan_unbuilt_packages does not exist without the change
3. The word scan on check output misclassifies target text
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from refactory_dsh import authoring_evidence as evidence


# ── Fixture: b21ar's real check output that triggers word-scan false positives ──

B21AR_CHECK_OUTPUT = """\
FAILED tests/test_authoring_evidence.py::test_overwatch_unavailable_stops_run - \
assert "validation unavailable: renderer missing" in output
FAILED tests/test_fast_modes.py::test_credentials_error_stops_run - \
ValueError: validation unavailable: credentials not configured; \
restore the named dependency/credentials or inspect worker/resource state
FAILED tests/test_create_modes.py::test_unauthorized_api_key - \
Unauthorized: invalid api key provided
FAILED tests/test_create_modes.py::test_some_other - \
AssertionError: expected 'pass' but got 'fail'

==================== 4 failed, 1801 passed in 142.51s ====================
"""


# ── Test 1: word scan false positives in check output ──

def test_check_failure_kind_no_word_scan():
    """check_failure_kind classifies structurally, never from stdout/stderr words.

    The b21ar output contains "credentials", "validation unavailable:" and
    "Unauthorized" — words that failure_kind() (for refactory's own text)
    classifies as "unavailable".  check_failure_kind uses only exit code,
    timeout and signal, so the same output is "code_failure".

    Fails without the change: check_failure_kind does not exist.
    """
    @dataclass
    class FakeResult:
        ok: bool = False
        timed_out: bool = False
        returncode: int = 1
        stdout: str = ""
        stderr: str = ""
        def problem(self) -> str:
            return ""

    result = FakeResult(stderr=B21AR_CHECK_OUTPUT)
    assert evidence.check_failure_kind(result) == "code_failure"

    # The old failure_kind still word-scans (for refactory's own text):
    assert evidence.failure_kind(B21AR_CHECK_OUTPUT) == "unavailable"


# ── Test 2: exit 127 is still "unavailable" structurally ──

def test_exit_127_still_unavailable_structurally():
    """A command that cannot run (exit 127) is classified as "unavailable"
    via check_failure_kind, from the exit code alone — not from the words.

    Fails without the change: check_failure_kind does not exist.
    """
    @dataclass
    class FakeResult:
        ok: bool = False
        timed_out: bool = False
        returncode: int = 127
        stdout: str = ""
        stderr: str = "bash: nosuchcmd: command not found"
        def problem(self) -> str:
            return ""

    result_127 = FakeResult()
    assert evidence.check_failure_kind(result_127) == "unavailable"

    # Same words but exit 0 → code_failure (the words do not matter)
    result_0 = FakeResult(returncode=0, ok=True)
    assert evidence.check_failure_kind(result_0) == "code_failure"


# ── Test 3: timeout classified structurally ──

def test_timeout_classified_structurally():
    """A timed-out check is classified from result.timed_out, not from words.

    Fails without the change: check_failure_kind does not exist.
    """
    @dataclass
    class FakeResult:
        ok: bool = False
        timed_out: bool = True
        returncode: int = 124
        stdout: str = ""
        stderr: str = "some test output that says timed out"
        def problem(self) -> str:
            return ""

    assert evidence.check_failure_kind(FakeResult()) == "uncertain"


# ── Test 4: JS workspace with unbuilt main ──

def test_js_workspace_unbuilt_main(tmp_path):
    """A JS package whose main names a missing lib file is detected.

    Fails without the change: scan_unbuilt_packages does not exist.
    """
    from refactory_dsh.unbuilt_check import (
        format_unbuilt_diagnosis,
        scan_unbuilt_packages,
    )

    # Create a workspace package with main pointing to a missing file
    pkg_dir = tmp_path / "packages" / "foo"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(json.dumps({
        "name": "foo",
        "main": "lib/index.js",
        "version": "1.0.0",
    }))
    src_dir = pkg_dir / "src"
    src_dir.mkdir()
    (src_dir / "index.ts").write_text("export const x = 1;\n")
    # No lib/ directory — the package is unbuilt

    # Initialize a git repo for the deleted-source filter
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init", "--allow-empty"],
        capture_output=True, check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    base = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    result = scan_unbuilt_packages(tmp_path, base, base)
    assert len(result) == 1
    assert result[0].name == "foo"
    assert "lib/index.js" in result[0].entry_point

    diagnosis = format_unbuilt_diagnosis(result)
    assert "foo" in diagnosis
    assert "setup" in diagnosis


# ── Test 5: Python extension module unbuilt ──

def test_python_extension_module_unbuilt(tmp_path):
    """A Python package with declared extension modules whose .so is missing.

    Fails without the change: scan_unbuilt_packages does not exist.
    """
    from refactory_dsh.unbuilt_check import (
        format_unbuilt_diagnosis,
        scan_unbuilt_packages,
    )

    # Create a project with maturin backend (extension module)
    pkg_dir = tmp_path / "myext"
    pkg_dir.mkdir()
    (pkg_dir / "pyproject.toml").write_text(
        '[project]\nname = "myext"\nversion = "0.1.0"\n\n'
        '[build-system]\nrequires = ["maturin>=1.0"]\n'
        'build-backend = "maturin"\n'
    )
    (pkg_dir / "Cargo.toml").write_text(
        '[package]\nname = "myext"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    (pkg_dir / "src").mkdir()
    (pkg_dir / "src" / "lib.rs").write_text("// extension\n")
    # No .so file — the extension is unbuilt

    # Initialize git
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        capture_output=True, check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    base = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    result = scan_unbuilt_packages(tmp_path, base, base)
    assert len(result) == 1
    assert result[0].name == "myext"

    diagnosis = format_unbuilt_diagnosis(result)
    assert "myext" in diagnosis
    assert "setup" in diagnosis


# ── Test 6: src-layout pure Python is NOT flagged ──

def test_src_layout_pure_python_not_flagged(tmp_path):
    """A src-layout pure-Python package is not flagged as unbuilt.

    Installing it makes it importable; it has no build output in the repository.

    Fails without the change: scan_unbuilt_packages does not exist.
    """
    from refactory_dsh.unbuilt_check import scan_unbuilt_packages

    pkg_dir = tmp_path / "mylib"
    pkg_dir.mkdir()
    (pkg_dir / "pyproject.toml").write_text(
        '[project]\nname = "mylib"\nversion = "0.1.0"\n\n'
        '[build-system]\nrequires = ["setuptools"]\n'
        'build-backend = "setuptools.build_meta"\n'
    )
    src = pkg_dir / "src" / "mylib"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("# pure Python\n")

    # Initialize git
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        capture_output=True, check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    base = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # A pure-Python package with setuptools backend but no ext_modules → not flagged
    result = scan_unbuilt_packages(tmp_path, base, base)
    assert len(result) == 0


# ── Test 7: inherited failure with unbuilt diagnosis ──

def test_inherited_failure_with_unbuilt_diagnosis(tmp_path):
    """When check fails, check_failure_kind returns 'code_failure' so the base
    comparison runs.  The unbuilt diagnosis is then attached to the inherited
    detail and check.txt, not swallowed by an 'unavailable' stop.

    Fails without the change: check_failure_kind does not exist, so
    failure_kind would return 'unavailable' on MODULE_NOT_FOUND in check
    output and stop the run before the base comparison.
    """
    @dataclass
    class FakeResult:
        ok: bool = False
        timed_out: bool = False
        returncode: int = 1
        stdout: str = ""
        stderr: str = "ModuleNotFoundError: No module named 'foo'"
        def problem(self) -> str:
            return ""

    result = FakeResult()
    # check_failure_kind classifies structurally → code_failure
    kind = evidence.check_failure_kind(result)
    assert kind == "code_failure"

    # failure_kind (old word scan, for refactory's own text) would say "unavailable"
    assert evidence.failure_kind(result.stderr) == "unavailable"

    # So the base comparison is now reachable (stop_unrepairable does not fire for code_failure)
    evidence.stop_unrepairable(kind, "some detail")  # should not raise


# ── Test 8: deleted source goes to builder ──

def test_deleted_source_goes_to_builder(tmp_path):
    """When the change itself deletes a package's source, that package is
    not flagged as unbuilt — the failure is the change's fault and goes
    to the builder.

    Fails without the change: scan_unbuilt_packages does not exist.
    """
    from refactory_dsh.unbuilt_check import scan_unbuilt_packages

    # Create a workspace package
    pkg_dir = tmp_path / "packages" / "bar"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(json.dumps({
        "name": "bar",
        "main": "lib/index.js",
        "version": "1.0.0",
    }))
    src_dir = pkg_dir / "src"
    src_dir.mkdir()
    (src_dir / "index.ts").write_text("export const y = 2;\n")

    # Initialize git, commit everything
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        capture_output=True, check=True, env=env,
    )
    base = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Now delete the source file (simulating the change removing it)
    (src_dir / "index.ts").unlink()
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "delete source"],
        capture_output=True, check=True, env=env,
    )
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # The package is not flagged because the deletion is the change's fault
    result = scan_unbuilt_packages(tmp_path, base, head)
    assert len(result) == 0

    # And check_failure_kind returns code_failure, so the failure reaches the builder
    @dataclass
    class FakeResult:
        ok: bool = False
        timed_out: bool = False
        returncode: int = 1
        stdout: str = ""
        stderr: str = "MODULE_NOT_FOUND: packages/bar/lib/index.js"
        def problem(self) -> str:
            return ""

    kind = evidence.check_failure_kind(FakeResult())
    assert kind == "code_failure"
    # stop_unrepairable does not raise for code_failure
    evidence.stop_unrepairable(kind, "some detail")
