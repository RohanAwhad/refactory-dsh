"""The sealed suite (dm-267, dm-271): no test reaches the person's DSH home or the real gh.

A test once resolved the person's own DSH home and touched a profile there during a live
run's check, and tests sent `gh api user` to the real GitHub. `conftest._sealed` gives
every test an empty DSH home of its own and refuses, and fails the test, on the real one
or the real gh. These tests hold that guard to what it promises.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import conftest


def test_a_test_gets_an_empty_dsh_home_of_its_own():
    home = Path(os.environ["DSH_HOME"])
    assert str(home.resolve()) not in conftest._REAL_DSH_HOMES
    assert os.environ["REFACTORY_MODEL_DSH_HOME"] == str(home)
    assert home.is_dir() and not any(home.iterdir())


def test_the_persons_dsh_home_is_among_the_sealed_homes():
    """Read from the session's start, without touching the filesystem."""
    assert os.path.realpath(Path.home() / ".dsh") in conftest._REAL_DSH_HOMES


def test_reaching_a_sealed_home_is_refused(tmp_path, monkeypatch):
    """The guard is exercised on a directory standing in for the real home, so that if it
    ever stopped working this test would write into a temporary directory, not the
    person's DSH home."""
    stand_in = tmp_path / "stand-in-home"
    stand_in.mkdir()
    monkeypatch.setattr(conftest, "_REAL_DSH_HOMES", (str(stand_in),))
    monkeypatch.setattr(conftest, "_REAL_DSH_HOME_IN_TEXT",
                        conftest.re.compile(conftest.re.escape(str(stand_in)) + r"(?![\w.-])"))
    with pytest.raises(conftest.SealBroken):
        os.listdir(stand_in)
    with pytest.raises(conftest.SealBroken):
        open(stand_in / "sealed-suite-probe", "w")
    with pytest.raises(conftest.SealBroken):
        subprocess.run(["true"], env={**os.environ, "DSH_HOME": str(stand_in)})
    assert not (stand_in / "sealed-suite-probe").exists()
    # Refused as intended: this test is not the one that broke the seal.
    conftest._SEAL["touched"].clear()


def test_the_gh_on_path_is_the_refusing_stand_in():
    found = shutil.which("gh")
    assert found is not None and Path(found).parent != Path(conftest._REAL_GH or "/nonexistent").parent
    result = subprocess.run(["gh", "api", "user"], capture_output=True, text=True)
    assert result.returncode == 1
    assert "the real gh is sealed off" in result.stderr
    log = conftest._SEAL["gh_log"]
    assert log.read_text().splitlines()[-1] == "gh api user"
    # Refused as intended: this test is not the one that broke the seal.
    conftest._SEAL["gh_logged"] = log.stat().st_size


@pytest.mark.skipif(conftest._REAL_GH is None, reason="this machine has no gh")
def test_running_the_real_gh_by_its_path_is_refused():
    with pytest.raises(conftest.SealBroken):
        subprocess.run([conftest._REAL_GH, "--version"], capture_output=True)
    conftest._SEAL["touched"].clear()


@pytest.mark.skipif(not os.environ.get("SEALED_SUITE_PROBE"),
                    reason="run only by test_a_test_that_breaks_the_seal_fails, in its own session")
def test_seal_probe():
    """Breaks the seal on purpose, and swallows the refusal, so only the guard can fail it."""
    try:
        os.listdir(os.environ["SEALED_SUITE_PROBE"])
    except conftest.SealBroken:
        pass
    subprocess.run(["gh", "auth", "status"], capture_output=True)


def test_a_test_that_breaks_the_seal_fails(tmp_path):
    """A test that reaches the session's DSH home or runs a gh other than the fake fails,
    even when it catches the refusal itself. The probe runs in a session of its own whose
    DSH home is this test's sealed one, so nothing outside this test is reached. (The probe's
    variable names neither DSH nor REFACTORY, or the guard would refuse the gh call itself.)"""
    inner = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"{__file__}::test_seal_probe"],
        env={**os.environ, "SEALED_SUITE_PROBE": os.environ["DSH_HOME"]},
        capture_output=True, text=True, cwd=Path(__file__).parent,
    )
    assert inner.returncode == 1, inner.stdout
    assert "reached outside its sealed environment" in inner.stdout
    assert f"os.listdir {os.environ['DSH_HOME']}" in inner.stdout
    assert "the real gh: gh auth status" in inner.stdout
