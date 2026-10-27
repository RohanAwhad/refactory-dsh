"""Run-written PR text for the README's wordcount design (dm-261).

Its pull-request.md opened with "The repository is empty.", true of every new project
and nothing about the change; its title was cut short; and three follow-ups were listed
twice, once from QA and once from the overwatch. The design is the run's own.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactory_dsh import delivery
from refactory_dsh.create_ops import FOLLOW_UPS

DESIGN = (Path(__file__).resolve().parent / "fixtures" / "wordcount_design.md").read_text()

#: The follow-ups the run listed twice, as its QA judge and its overwatch recorded them.
FINDINGS = [
    f"wordcount.py: Unhandled {error} produces a raw traceback instead of a clean error "
    "message (beyond brief scope, exits non-zero)"
    for error in ("IsADirectoryError when given a directory path",
                  "UnicodeDecodeError on binary/non-UTF-8 files",
                  "PermissionError on unreadable files")
]


class _Ctx:
    def __init__(self, files: dict[str, str]):
        self.facts = {"brief": "Build wordcount.py and a sample.txt; verify line, word and "
                               "character counts", "name": "wordcount",
                      "check": "python wordcount.py sample.txt"}
        self._files = files

    def exists(self, name: str) -> bool:
        return name in self._files

    def read_text(self, name: str) -> str:
        return self._files[name]

    def read_json(self, name: str, default=None):
        return json.loads(self._files[name]) if name in self._files else default


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), "-c", "user.email=t@example.com",
                           "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def _run(path: Path, *, new: bool) -> SimpleNamespace:
    """The run's branch: the wordcount files over a new project's empty first commit, or
    over a person's repository."""
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    if not new:
        (path / "README.md").write_text("tools\n")
        _git(path, "add", ".")
    _git(path, "commit", "-q", "--allow-empty", "-m", "Start")
    base = _git(path, "rev-parse", "HEAD")
    (path / "wordcount.py").write_text("import sys\n")
    (path / "sample.txt").write_text("Hello world\n")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "attempt")
    return SimpleNamespace(path=str(path), base=base, head=_git(path, "rev-parse", "HEAD"),
                           branch="refactory/run-50262c2c27c1")


def _summary(body: str) -> str:
    return body.split("## Summary\n\n", 1)[1].split("\n\n## ", 1)[0]


def test_a_new_projects_summary_says_what_the_change_builds(tmp_path):
    body = delivery._body(_Ctx({"design.md": DESIGN}), _run(tmp_path / "new", new=True))
    summary = _summary(body)
    assert summary.startswith('The brief asks to "Build wordcount.py and a sample.txt;')
    assert "This change adds a Python script" in summary
    assert "repository is empty" not in body


def test_an_existing_projects_summary_is_the_designs_own(tmp_path):
    """There the opening states the problem the change fixes; it is kept as written."""
    body = delivery._body(_Ctx({"design.md": DESIGN}), _run(tmp_path / "old", new=False))
    assert _summary(body).startswith("The repository is empty. The brief asks to")


@pytest.mark.parametrize("summary, kept", [
    ("The repository is empty. It adds a tool.", "It adds a tool."),
    ("This new project is empty. There are no files in the repository yet. It adds a tool.",
     "It adds a tool."),
    # A sentence that also says what the change does is kept.
    ("The repository is empty, so this change adds a tool. It prints counts.", None),
    # All it says: kept rather than leave the Summary empty.
    ("The repository is empty.", None),
    ("It adds a tool. The repository is empty.", None),
])
def test_only_sentences_about_the_empty_start_are_left_out(summary, kept):
    assert delivery._built_summary(summary) == (kept or summary)


def test_the_title_is_the_whole_heading_when_github_takes_it():
    """It was cut at 72 characters, mid-phrase; GitHub takes the whole heading (dm-217).
    `test_delivery` holds the cut past GitHub's limit."""
    title = delivery._title(_Ctx({"design.md": DESIGN}))
    heading = DESIGN.splitlines()[0][2:]
    assert len(heading) > 72 and title == heading


def test_a_follow_up_both_gates_name_is_listed_once(tmp_path):
    files = {"design.md": DESIGN,
             FOLLOW_UPS: json.dumps({"qa": FINDINGS, "overwatch": FINDINGS})}
    ctx = _Ctx(files)
    assert delivery._follow_ups(ctx) == [f"{finding} (qa, overwatch)" for finding in FINDINGS]
    body = delivery._body(ctx, _run(tmp_path / "new", new=True))
    section = body.split("## Follow-ups\n\n", 1)[1].split(delivery.BODY_END, 1)[0]
    assert section.split("\n\n", 1)[0].strip().splitlines() == [f"- {finding} (qa, overwatch)" for finding in FINDINGS]
