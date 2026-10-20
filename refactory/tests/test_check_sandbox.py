"""Tests for check-sandbox limits, signal classification, truncation guard and attempt counters.

Each test fails without the corresponding production change.

| Test | Fails without | Production change |
|------|---------------|-------------------|
| test_100mb_file_write_passes | DEFAULT_FILE_MB was 64 < 100 | sandbox.py constant |
| test_file_size_limit_stop | DEFAULT_FILE_MB was 64 and no naming | sandbox.py + design_ops.py |
| test_sigxfsz_exit_classified | check_failure_kind returns code_failure for 153 | authoring_evidence.py 128+N block |
| test_sigkill_exit_classified | check_failure_kind returns code_failure for 137 | authoring_evidence.py 128+N block |
| test_sigxcpu_exit_classified | check_failure_kind returns code_failure for 152 | authoring_evidence.py 128+N block |
| test_sigabrt_exit_code_failure | (regression guard — 134 must NOT be uncertain) | authoring_evidence.py allowlist |
| test_normal_exits_still_code_failure | (regression guard) | (no production change needed) |
| test_create_materialise_refuses_truncated | create_ops.materialise has no guard | create_ops.py refuse_truncated call |
| test_review_reloops_independent | repair() uses a single counter | create_ops.py repair() rewrite |
| test_exhausted_limits_name_which | the stop message is generic | create_ops.py repair() rewrite |
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from refactory_dsh import authoring_evidence as evidence
from refactory_dsh import sandbox


# ── Fixture: fake result for check_failure_kind ──────────────────────────────

@dataclass
class FakeResult:
    ok: bool = False
    timed_out: bool = False
    returncode: int = 1
    stdout: str = ""
    stderr: str = ""
    prep_failure: dict | None = None

    def problem(self) -> str:
        return ""


# ── R1: file-size limit ─────────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform == "win32", reason="RLIMIT_FSIZE is POSIX-only")
def test_100mb_file_write_passes():
    """A sandboxed command that writes a 100 MB file succeeds under DEFAULT_FILE_MB.

    Fails without the fix: DEFAULT_FILE_MB was 64 (< 100).
    """
    import tempfile

    with tempfile.TemporaryDirectory() as workdir:
        result = sandbox.run_command(
            "python3 -c \""
            "f = open('big.bin', 'wb'); "
            "f.write(b'x' * (100 * 1024 * 1024)); "
            "f.close(); "
            "print('done')\"",
            workdir=Path(workdir),
            timeout_s=60,
        )
        assert result.ok, f"expected ok, got exit {result.returncode}: {result.stderr}"


@pytest.mark.skipif(sys.platform == "win32", reason="RLIMIT_FSIZE is POSIX-only")
def test_file_size_limit_stop():
    """A check that exceeds a small explicit file_mb is killed by SIGXFSZ.

    Verifies the process does not succeed when the file-size limit is hit.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as workdir:
        # Write 2 MB with a 1 MB limit
        result = sandbox.run_command(
            "python3 -c \""
            "f = open('big.bin', 'wb'); "
            "f.write(b'x' * (2 * 1024 * 1024)); "
            "f.close()\"",
            workdir=Path(workdir),
            timeout_s=30,
            file_mb=1,
        )
        assert not result.ok
        # The process is killed by SIGXFSZ: either direct (rc = -25) or shell-wrapped (153)
        assert result.returncode in (-25, 153, 159), (
            f"expected SIGXFSZ exit, got {result.returncode}"
        )


# ── R3: shell-wrapped signal classification ─────────────────────────────────

def test_sigxfsz_exit_classified_as_uncertain():
    """Exit 153 (128+SIGXFSZ) is classified as 'uncertain'.

    Fails without the fix: check_failure_kind returns 'code_failure' for 153.
    """
    result = FakeResult(returncode=153)
    assert evidence.check_failure_kind(result) == "uncertain"


def test_sigkill_exit_classified_as_uncertain():
    """Exit 137 (128+SIGKILL) is 'uncertain', not 'code_failure'.

    Fails without the fix: check_failure_kind returns 'code_failure' for 137.
    """
    result = FakeResult(returncode=137)
    assert evidence.check_failure_kind(result) == "uncertain"


def test_sigxcpu_exit_classified_as_uncertain():
    """Exit 152 (128+SIGXCPU) is 'uncertain'.

    Fails without the fix: check_failure_kind returns 'code_failure' for 152.
    """
    result = FakeResult(returncode=152)
    assert evidence.check_failure_kind(result) == "uncertain"


def test_sigabrt_exit_is_code_failure():
    """Exit 134 (128+SIGABRT) is 'code_failure', not 'uncertain'.

    SIGABRT can be the change's own fault (a native extension it broke, a C
    assertion its code triggers).  It is retried and decided by base comparison,
    not classified as infrastructure.
    """
    result = FakeResult(returncode=134)
    assert evidence.check_failure_kind(result) == "code_failure"


def test_sigbus_exit_is_code_failure():
    """Exit 135 (128+SIGBUS) is 'code_failure', not 'uncertain'."""
    result = FakeResult(returncode=135)
    assert evidence.check_failure_kind(result) == "code_failure"


def test_sigsegv_exit_is_code_failure():
    """Exit 139 (128+SIGSEGV) is 'code_failure', not 'uncertain'."""
    result = FakeResult(returncode=139)
    assert evidence.check_failure_kind(result) == "code_failure"


def test_normal_exits_still_code_failure():
    """Normal test failure exit codes are not misclassified.

    Guards against over-broad classification.
    """
    for code in (1, 2, 42, 128, 129):
        result = FakeResult(returncode=code)
        kind = evidence.check_failure_kind(result)
        assert kind == "code_failure", f"exit {code} should be code_failure, got {kind}"


def test_exit_1_is_code_failure():
    """A check that ends with exit 1 is a code failure, not infrastructure."""
    result = FakeResult(returncode=1)
    assert evidence.check_failure_kind(result) == "code_failure"


# ── R2: truncation guard in create materialiser ──────────────────────────────

def test_create_materialise_refuses_truncated(tmp_path, monkeypatch):
    """create_ops.materialise refuses a file with a truncation marker.

    Fails without the fix: create_ops.materialise has no truncation guard.
    """
    from refactory_dsh import create_ops, registry
    from refactory_dsh.worktree import AttemptRefused

    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    # Set up a minimal workspace root so registry.workspace_root works
    ws = tmp_path / ".factory" / "packages"
    ws.mkdir(parents=True)
    name = "truncation_probe"

    marker = "... (line truncated to 2000 chars)"
    content = f"print('hello')\n{marker}\nprint('world')\n"
    emit = f"===FILE: __init__.py===\n{content}"

    class FakeCtx:
        workdir = tmp_path
        facts = {"name": name}

        def read_text(self, n):
            if n == "emit.txt":
                return emit
            return ""

        def write_json(self, n, payload):
            import json
            dest = tmp_path / n
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(payload))

    with pytest.raises(AttemptRefused, match="truncated"):
        create_ops.materialise(FakeCtx())


# ── R4: separate check and review attempt counters ───────────────────────────

class _FakeCtx:
    """Minimal context for testing repair() directly."""

    def __init__(self, tmp_path, knobs=None):
        self.facts = {}
        self.knobs_dict = knobs or {}
        self.workdir = tmp_path

    def knob(self, name, default=None):
        return self.knobs_dict.get(name, default)

    def set_fact(self, name, value):
        self.facts[name] = value

    def read_text(self, name):
        return ""

    def write_text(self, name, content):
        dest = self.workdir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)

    def exists(self, name):
        return (self.workdir / name).exists()


def test_review_reloops_independent_of_check_attempts(tmp_path):
    """Two review reloops leave check_attempts_used at 0.

    Fails without the fix: repair() uses a single counter.
    """
    from refactory_dsh.create_ops import repair

    ctx = _FakeCtx(tmp_path, knobs={
        "attempts": "10", "check_attempts": "5", "review_attempts": "5",
    })
    repair(ctx, ["review finding 1"], kind="review")
    repair(ctx, ["review finding 2"], kind="review")
    assert ctx.facts.get("check_attempts_used", 0) == 0
    assert ctx.facts["review_attempts_used"] == 2

    # And vice versa:
    ctx2 = _FakeCtx(tmp_path / "ctx2", knobs={
        "attempts": "10", "check_attempts": "5", "review_attempts": "5",
    })
    repair(ctx2, ["check finding 1"], kind="check")
    assert ctx2.facts["check_attempts_used"] == 1
    assert ctx2.facts.get("review_attempts_used", 0) == 0


def test_exhausted_limits_name_which(tmp_path):
    """Each sub-limit's stop message names the specific limit.

    Fails without the fix: the stop message is generic.
    """
    from refactory_dsh.create_ops import repair

    ctx = _FakeCtx(tmp_path / "a", knobs={
        "attempts": "10", "check_attempts": "2", "review_attempts": "2",
    })
    repair(ctx, ["f1"], kind="check")
    with pytest.raises(ValueError, match="check attempts exhausted"):
        repair(ctx, ["f2"], kind="check")

    ctx2 = _FakeCtx(tmp_path / "b", knobs={
        "attempts": "10", "check_attempts": "5", "review_attempts": "2",
    })
    repair(ctx2, ["r1"], kind="review")
    with pytest.raises(ValueError, match="review reloop limit reached"):
        repair(ctx2, ["r2"], kind="review")


def test_overall_cap_still_applies(tmp_path):
    """The overall attempts cap still stops the run when check + review together hit it."""
    from refactory_dsh.create_ops import repair

    ctx = _FakeCtx(tmp_path / "c", knobs={
        "attempts": "3", "check_attempts": "5", "review_attempts": "5",
    })
    repair(ctx, ["c1"], kind="check")
    repair(ctx, ["r1"], kind="review")
    with pytest.raises(ValueError, match="authoring unresolved"):
        repair(ctx, ["c2"], kind="check")


# ── Fast-mode kind-specific counters ─────────────────────────────────────────

def test_fast_repair_records_kind(tmp_path):
    """Fast-mode _repair records which kind spent the repair."""
    from refactory_dsh.fast_ops import _repair

    class FastCtx:
        def __init__(self, wd, knobs=None):
            self.facts = {}
            self.knobs_dict = knobs or {}
            self.workdir = wd
            self.budget = None

        def knob(self, name, default=None):
            return self.knobs_dict.get(name, default)

        def set_fact(self, name, value):
            self.facts[name] = value

        def write_text(self, name, content):
            dest = self.workdir / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)

        def write_json(self, name, payload):
            import json
            dest = self.workdir / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(payload))

        def read_json(self, name, default=None):
            return default

        def exists(self, name):
            return (self.workdir / name).exists()

    ctx = FastCtx(tmp_path / "fast", knobs={"repair_attempts": "5"})
    _repair(ctx, ["check problem"], kind="check")
    assert ctx.facts.get("check_repairs_used") == 1
    assert ctx.facts.get("review_repairs_used", 0) == 0

    ctx2 = FastCtx(tmp_path / "fast2", knobs={"repair_attempts": "5"})
    _repair(ctx2, ["review problem"], kind="review")
    assert ctx2.facts.get("review_repairs_used") == 1
    assert ctx2.facts.get("check_repairs_used", 0) == 0
