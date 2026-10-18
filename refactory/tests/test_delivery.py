"""A design run that passes its reviews ends with a pull request (#350).

One test per path a person takes: a new project, an existing repository, a reused
project name, a follow-up to an open pull request, a redo, `--no-pr`, a run that fails,
a base with commits nobody pushed, a name already taken on GitHub, and no gh login.
GitHub is `fake_gh.py` over bare repositories on disk, which git reaches through
`url.<base>.insteadOf`; the model is a fake; the repositories, the worktrees, the
pushes and the registry are real.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from refactory_dsh import cli, console, delivery, nn, projects, worktree
from refactory_dsh.design import DesignMode
from refactory_dsh.projects import projects_dir
from tests_support import DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, RESEARCH_ANSWER, REVIEW_ANSWERS, FakeModel, git, git_repo, render, stub

NAME = "delivered_tool"
FAKE_GH = Path(__file__).resolve().parent / "fake_gh.py"
LOGIN = "octo"


# ── PR title and body unit tests ───────────────────────────────────────────


class _TitleCtx:
    """Minimal context for _title() and _body() unit tests."""
    def __init__(self, facts=None, files=None):
        self.facts = dict(facts or {})
        self._files = dict(files or {})
    def exists(self, name):
        return name in self._files
    def read_text(self, name):
        return self._files[name]


def test_pr_title_from_design_heading():
    ctx = _TitleCtx(
        facts={"brief": "Fix audit", "name": "b2"},
        files={"design.md": "# Audit bookkeeping and PR formatting\n\nSome text.\n"},
    )
    assert delivery._title(ctx) == "Audit bookkeeping and PR formatting"


def test_pr_title_strips_design_prefix():
    ctx = _TitleCtx(
        facts={"brief": "Fix audit", "name": "b2"},
        files={"design.md": "# Design: Audit bookkeeping\n\nSome text.\n"},
    )
    assert delivery._title(ctx) == "Audit bookkeeping"


def test_pr_title_falls_back_to_brief():
    ctx = _TitleCtx(
        facts={"brief": "Fix the audit gate", "name": "b2"},
        files={"design.md": "No heading here, just text.\n"},
    )
    assert delivery._title(ctx) == "Fix the audit gate"


def test_pr_title_truncates_at_word_boundary():
    long_heading = "Fix " + "word " * 20  # well over 72 chars
    ctx = _TitleCtx(
        facts={"brief": "short", "name": "b2"},
        files={"design.md": f"# {long_heading}\n"},
    )
    title = delivery._title(ctx)
    assert len(title) <= delivery.TITLE_LIMIT
    assert title.endswith("…")


def test_pr_title_never_cuts_inside_backticks():
    heading = "Fix how gate_overwatch and its refusal of the `code_reviewer` backtick_spans are reported in the pipeline"
    ctx = _TitleCtx(
        facts={"brief": "short", "name": "b2"},
        files={"design.md": f"# {heading}\n"},
    )
    title = delivery._title(ctx)
    assert len(title) <= delivery.TITLE_LIMIT
    # Even number of backticks: no unclosed pair.
    assert title.count("`") % 2 == 0, f"unclosed backtick in title: {title!r}"


def test_pr_body_has_summary_verification_and_original_request(tmp_path):
    import subprocess
    ctx = _TitleCtx(
        facts={"brief": "Build a tool\nthat prints built.", "check": "python tool.py", "name": "b2",
               "inherited_failures": ["tests/test_env.py::test_needs_profile"]},
        files={"design.md": "# The design heading\n\nFirst paragraph of the design.\n\n---\n"},
    )
    # Set up a minimal git repo so worktree.git works.
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], capture_output=True, check=True)
    (tmp_path / "file.txt").write_text("content\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True, check=True)
    base = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    (tmp_path / "tool.py").write_text("print('built')\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "attempt"], capture_output=True, check=True)
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    from types import SimpleNamespace
    found = SimpleNamespace(path=str(tmp_path), base=base, head=head, branch="refactory/run-test")
    body = delivery._body(ctx, found)
    assert "## Summary" in body
    assert "First paragraph of the design." in body
    assert "## Verification" in body
    assert "python tool.py" in body
    assert "the health check, code review and QA found nothing blocking" in body
    assert "`tests/test_env.py::test_needs_profile`" in body
    assert "## Original request" in body
    assert "Build a tool\nthat prints built." in body


def test_pr_verification_names_inherited_failures_and_the_new_tests_on_the_base():
    import json
    ctx = _TitleCtx(
        facts={"inherited_failures": ["tests/test_env.py::test_needs_profile"]},
        files={"test-guardedness.json": json.dumps(
            {"summary": {"guarding": 3, "not_guarding": 1, "cannot_run_on_base": 0, "total_new_tests": 4}})},
    )
    lines = delivery._base_evidence(ctx)
    assert "`tests/test_env.py::test_needs_profile`" in lines
    assert "3 of 4 fail without the change; 1 pass there, so they do not guard it" in lines


def test_pr_verification_says_why_the_new_tests_were_not_run_on_the_base():
    import json
    ctx = _TitleCtx(files={"test-guardedness.json": json.dumps(
        {"unavailable": "the base worktree could not be prepared: no uv"})})
    assert "not run: the base worktree could not be prepared: no uv" in delivery._base_evidence(ctx)
    assert delivery._base_evidence(_TitleCtx()) == ""
    leaked = _TitleCtx(files={"test-guardedness.json": json.dumps(
        {"unavailable": "setup failed: ANTHROPIC_API_KEY=sk-live-123 not accepted"})})
    assert "sk-live-123" not in delivery._base_evidence(leaked)


def _git_pair(tmp_path):
    """Set up a minimal two-commit git repo; return (base, head, path)."""
    import subprocess
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], capture_output=True, check=True)
    (tmp_path / "f.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True, check=True)
    base = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    (tmp_path / "g.txt").write_text("b\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "work"], capture_output=True, check=True)
    head = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    return base, head


def test_contract_design_gives_title_summary_and_changes(tmp_path):
    design_text = (
        "# Enforce clause-level audit coverage\n\n"
        "## Summary\n\n"
        "The overwatch audit missed a clause. This change adds clause-level checking.\n\n"
        "## Rule and sites\n\n"
        "The rule: every constraint clause is checked.\n\n"
        "- `authoring_evidence.py:132-157` — **[changed]** adds clause rows.\n"
        "- `design_ops.py:116-246` — **[left: only the gate; clauses are audit-side]**\n\n"
        "## Docs\n\n"
        "- `docs/models.md` — add clause-level audit section\n\n"
        "## Changes\n\n"
        "- `refactory/refactory_dsh/authoring_evidence.py`\n"
        "- `refactory/tests/test_authoring_evidence.py`\n"
    )
    ctx = _TitleCtx(
        facts={"brief": "Add clause checking", "check": "pytest", "name": "b17_clauses"},
        files={"design.md": design_text},
    )
    title = delivery._title(ctx)
    assert title == "Enforce clause-level audit coverage"
    assert title != "b17_clauses"

    base, head = _git_pair(tmp_path)
    from types import SimpleNamespace
    found = SimpleNamespace(path=str(tmp_path), base=base, head=head, branch="refactory/run-test")
    body = delivery._body(ctx, found)
    assert "## Summary" in body
    assert "clause-level checking" in body
    assert "## What changed" in body
    assert "authoring_evidence.py" in body


@pytest.mark.parametrize("heading,run_name,expected_not_equal", [
    ("# Design: b15b_reference_oracle", "b15b_reference_oracle", "b15b_reference_oracle"),
    ("# Design: b16b_graph_ends \u2014 Make sinks() agree with scheduler reachability",
     "b16b_graph_ends", "b16b_graph_ends"),
    ("# Design: Fork-branch redo reopens join and downstream",
     "b15a_fork_redo", "b15a_fork_redo"),
    ("# Design: Generalize set-aside to every declared output on re-dispatch",
     "b16a_set_aside", "b16a_set_aside"),
])
def test_pre_contract_design_title_is_not_the_run_name(heading, run_name, expected_not_equal):
    design_text = f"{heading}\n\n## Problem\n\nThe current implementation has a bug that causes incorrect results.\n"
    ctx = _TitleCtx(
        facts={"brief": "Fix something important in the codebase.", "name": run_name},
        files={"design.md": design_text},
    )
    title = delivery._title(ctx)
    assert title, f"title should not be empty for heading={heading!r}"
    assert title != expected_not_equal, f"title should not be the run name: {title!r}"


def test_pre_contract_design_gives_prose_summary():
    from refactory_dsh import design_contract

    design_text = (
        "# Design: b15b_reference_oracle\n\n"
        "## Problem\n\n"
        "The oracle returns stale references when the cache is cold.\n"
    )
    parsed = design_contract.parse(design_text, "b15b_reference_oracle")
    assert parsed.summary == "The oracle returns stale references when the cache is cold."
    assert "## Problem" not in parsed.summary




@pytest.fixture
def github(tmp_path, monkeypatch) -> Path:
    """GitHub, as far as delivery can tell: gh logged in as `octo`, repositories on disk."""
    root = tmp_path / "github"
    root.mkdir()
    (root / "login").write_text(LOGIN + "\n")
    bin_dir = tmp_path / "bin"
    stub(bin_dir, "gh", f'exec "{sys.executable}" "{FAKE_GH}" "$@"')
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_ROOT", str(root))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.file://{root}/.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://github.com/")
    monkeypatch.setattr(delivery, "VERIFY_WAIT_S", 0)
    return root


def _published(github: Path, slug: str, source: Path) -> Path:
    """A repository on GitHub holding ``source``'s main, which ``source`` names as origin."""
    bare = github / f"{slug}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git(bare.parent, "init", "-q", "--bare", "-b", "main", bare.name)
    (github / f"{slug}.json").write_text(json.dumps({"visibility": "private", "default_branch": "main"}))
    git(source, "remote", "add", "origin", f"https://github.com/{slug}.git")
    git(source, "push", "-q", f"https://github.com/{slug}.git", "main:refs/heads/main")
    return bare


def _remote_head(github: Path, slug: str, branch: str) -> str:
    try:
        return git(github / f"{slug}.git", "rev-parse", "-q", "--verify", f"refs/heads/{branch}")
    except subprocess.CalledProcessError:
        return ""


def _pulls(github: Path, slug: str) -> list[dict]:
    path = github / f"{slug}.pulls.json"
    return json.loads(path.read_text()) if path.exists() else []


def _calls(tmp_path: Path) -> str:
    log = tmp_path / "bin" / "calls.log"
    return log.read_text() if log.exists() else ""


def _answers(*emits: str) -> list[str]:
    """Every answer a design run needs, in the order its graph asks."""
    return [RESEARCH_ANSWER, "perspectives", "one plan", DESIGN_DOC_ANSWER,
            DESIGN_REVIEW_ANSWER, *emits, *REVIEW_ANSWERS]


def _mode(**knobs) -> DesignMode:
    """Design mode with some of its knobs set, as a person sets them for one run."""
    mode = DesignMode()
    state = mode.state_dict()
    for name, value in knobs.items():
        state["knobs"][name]["value"] = value
    mode.load_state_dict(state)
    return mode


def _design(workdir: Path, emit: dict[str, str] | None = None, *, mode: DesignMode | None = None,
            check: str = "python tool.py", **task: str):
    model = FakeModel(_answers(render(emit or {"tool.py": "print('built')\n"})))
    return (mode or DesignMode()).run(
        workdir, model=model, verdicts={"gate_strategy": "proceed"},
        task={"name": NAME, "brief": "Build a tool\nthat prints built.", "check": check, **task},
    )


def _record(workdir: Path) -> dict:
    return json.loads((workdir / delivery.RECORD).read_text())


# ── the paths a person takes ───────────────────────────────────────────────
def test_a_new_project_gets_a_private_repository_whose_first_pull_request_holds_all_the_code(github, tmp_path):
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    slug = f"{LOGIN}/{NAME}"
    project = (projects_dir() / NAME).resolve()
    assert json.loads((github / f"{slug}.json").read_text())["visibility"] == "private"
    # main on GitHub is the project's empty first commit; the pull request is all of the code.
    assert _remote_head(github, slug, "main") == git(project, "rev-parse", "main")
    [pull] = _pulls(github, slug)
    branch = json.loads((run / "design.json").read_text())["branch"]
    assert (pull["head"], pull["base"], pull["title"]) == (branch, "main", "Do the thing")
    assert _remote_head(github, slug, branch) == git(project, "rev-parse", branch)
    assert git(project, "ls-tree", "--name-only", "main") == ""
    assert "Build a tool\nthat prints built." in pull["body"] and "python tool.py" in pull["body"]
    url = f"https://github.com/{slug}/pull/1"
    assert result.facts["pull_request"] == url
    assert _record(run)["status"] == "opened" and _record(run)["verified"] is True
    assert json.loads((run / "design.json").read_text())["delivery"]["pull_request"] == url
    [entry] = projects.load()["projects"]
    assert (entry["github"], entry["runs"][-1]["pull_request"]) == (slug, url)
    # The project now names the repository it was delivered to.
    assert git(project, "config", "--get", "remote.origin.url") == f"https://github.com/{slug}.git"


def test_the_approval_gate_reads_what_delivery_will_do_before_anything_is_built(github, tmp_path):
    graph = DesignMode().build()
    assert "delivery.md" in graph.nodes["gate_strategy"].reads
    assert "delivery.md" in graph.nodes["gate_strategy"].notes
    run = tmp_path / "run"
    seen: list[tuple[bool, list[Path]]] = []

    class Watching(FakeModel):
        """Notes, as the approved design is built, what the gate had read and what GitHub held."""

        def complete(self, prompt: str, *, system: str = "", workdir: object = None) -> str:
            if "Write the artifact `" in prompt:
                seen.append(((run / delivery.PLAN).exists(), sorted(github.glob("*/*.git"))))
            return super().complete(prompt, system=system, workdir=workdir)

    result = DesignMode().run(run, model=Watching(_answers(render({"tool.py": "print('built')\n"}))),
                              verdicts={"gate_strategy": "proceed"},
                              task={"name": NAME, "brief": "a tool", "check": "python tool.py"})
    assert result.exit_ok, result.error
    assert seen == [(True, [])], "the plan existed at the gate, and the repository did not until delivery"
    said = (run / delivery.PLAN).read_text()
    assert f"creates the private GitHub repository {LOGIN}/{NAME}" in said and "--no-pr" in said
    assert list(github.glob("*/*.git")) == [github / LOGIN / f"{NAME}.git"]
    assert [edge.target for edge in graph.edges if edge.source == "report"] == ["deliver"]


def test_an_existing_repository_gets_a_pull_request_into_its_default_branch(github, tmp_path):
    repo = git_repo(tmp_path / "repo")
    slug = "acme/app"
    _published(github, slug, repo)
    refs = git(repo, "for-each-ref")
    main = _remote_head(github, slug, "main")
    run = tmp_path / "run"
    result = _design(run, target=str(repo))
    assert result.exit_ok, result.error
    branch = json.loads((run / "design.json").read_text())["branch"]
    [pull] = _pulls(github, slug)
    assert (pull["head"], pull["base"]) == (branch, "main")
    assert _remote_head(github, slug, "main") == main, "delivery never pushes the default branch"
    assert _remote_head(github, slug, branch) == git(repo, "rev-parse", branch)
    # The person's repository keeps its remotes and remote-tracking branches as they were;
    # only the run's own branch is new.
    assert git(repo, "remote") == "origin"
    assert git(repo, "for-each-ref", "--exclude", f"refs/heads/{branch}") == refs
    assert f"opens a pull request from {branch} into {slug}:main" in (run / delivery.PLAN).read_text()


def test_unpushed_commits_in_the_base_are_never_pushed(github, tmp_path, capsys):
    repo = git_repo(tmp_path / "repo")
    slug = "acme/app"
    _published(github, slug, repo)
    (repo / "mine.py").write_text("x = 1\n")
    git(repo, "add", "mine.py")
    git(repo, "commit", "-q", "-m", "mine, not pushed")
    run = tmp_path / "run"
    result = _design(run, target=str(repo))
    assert result.exit_ok, result.error
    said = (run / delivery.PLAN).read_text()
    assert "won't open a pull request" in said and "1 commit(s) that are not on acme/app" in said
    assert "--base origin/main" in said
    assert _pulls(github, slug) == [] and git(github / f"{slug}.git", "branch") == "* main"
    assert "no pull request was opened" in capsys.readouterr().err
    assert result.facts["pull_request"] == ""


def test_a_name_already_taken_on_github_is_never_pushed_into(github, tmp_path, capsys):
    taken = git_repo(tmp_path / "theirs")
    _published(github, f"{LOGIN}/{NAME}", taken)
    before = git(github / f"{LOGIN}/{NAME}.git", "for-each-ref")
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    assert "already exists" in (run / delivery.PLAN).read_text()
    assert git(github / f"{LOGIN}/{NAME}.git", "for-each-ref") == before
    assert "repo create" not in _calls(tmp_path) and _pulls(github, f"{LOGIN}/{NAME}") == []
    assert git((projects_dir() / NAME), "remote") == ""
    assert "already exists" in capsys.readouterr().err


def test_a_reused_project_name_opens_its_pull_request_in_the_repository_it_was_given(github, tmp_path):
    first = _design(tmp_path / "one", {"tool.py": "print(1)\n"})
    second = _design(tmp_path / "two", {"tool.py": "print(2)\n"})
    assert first.exit_ok and second.exit_ok, (first.error, second.error)
    slug = f"{LOGIN}/{NAME}"
    assert _calls(tmp_path).count("repo create") == 1
    assert [pull["number"] for pull in _pulls(github, slug)] == [1, 2]
    assert second.facts["pull_request"] == f"https://github.com/{slug}/pull/2"
    assert "opens a pull request from" in (tmp_path / "two" / delivery.PLAN).read_text()


@pytest.mark.parametrize("refused", ["exit 4", "HTTP 401"])
def test_without_gh_login_the_run_succeeds_and_says_its_work_stays_local(github, tmp_path, capsys, refused):
    (github / "login").unlink()
    if refused == "HTTP 401":
        (github / "logged-out-as-401").write_text("")
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    err = capsys.readouterr().err
    assert "run warning: no pull request was opened: gh is not logged in" in err
    assert _record(run)["status"] == "kept local"
    assert "repo create" not in _calls(tmp_path) and not list(github.glob("*/*.git"))


def test_no_pr_keeps_the_work_on_its_branch_and_asks_github_nothing(github, tmp_path, capsys):
    run = tmp_path / "run"
    result = _design(run, mode=_mode(pr=False))
    assert result.exit_ok, result.error
    assert _calls(tmp_path) == ""
    assert "run warning" not in capsys.readouterr().err
    assert "`pr` is off (--no-pr)" in (run / delivery.PLAN).read_text()
    assert result.facts["delivery_result"].startswith("not delivered")
    assert result.facts["delivery"].startswith("once the reviews pass, this run won't open"), "the plan is kept"


def test_a_failed_run_creates_no_repository_and_opens_no_pull_request(github, tmp_path):
    result = _design(tmp_path / "run", check="false", mode=_mode(check_attempts=1))
    assert result.exit_ok is False
    calls = _calls(tmp_path)
    assert "repo create" not in calls and "pr create" not in calls and not list(github.glob("*/*.git"))
    assert not (tmp_path / "run" / delivery.RECORD).exists()


def test_a_run_without_a_worktree_has_nothing_to_deliver(github, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = tmp_path / "run"
    result = _design(run, mode=_mode(worktree=False))
    assert result.exit_ok, result.error
    assert _calls(tmp_path) == "" and "run warning" not in capsys.readouterr().err
    assert "this run has no worktree" in (run / delivery.PLAN).read_text()


# ── a redo, a resume, and what GitHub then shows ───────────────────────────
def test_delivering_again_opens_no_second_pull_request_and_leaves_its_text_alone(github, tmp_path):
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    slug = f"{LOGIN}/{NAME}"
    [before] = _pulls(github, slug)
    again = delivery.deliver_design(nn.RunContext(run, facts=result.facts, enforce_writes=False))
    assert again["pull_request"] == result.facts["pull_request"]
    assert _pulls(github, slug) == [before]
    assert _record(run)["status"] == "already open"
    assert _calls(tmp_path).count("pr create") == 1 and "pr edit" not in _calls(tmp_path)


def test_a_delivery_that_failed_after_its_push_opens_one_pull_request_when_resumed(github, tmp_path, monkeypatch):
    run = tmp_path / "run"
    real = delivery._gh

    def fails_at_create(*args, **kwargs):
        if args[:2] == ("pr", "create"):
            raise RuntimeError("the delivery stopped here")
        return real(*args, **kwargs)

    monkeypatch.setattr(delivery, "_gh", fails_at_create)
    failed = _design(run)
    assert failed.exit_ok is False and "the delivery stopped here" in (failed.error or "")
    slug = f"{LOGIN}/{NAME}"
    branch = worktree.recorded(run).branch
    assert _remote_head(github, slug, branch) and _pulls(github, slug) == []
    monkeypatch.setattr(delivery, "_gh", real)
    resumed = DesignMode.resume(run, model=FakeModel([]))
    assert resumed.exit_ok, resumed.error
    assert [pull["head"] for pull in _pulls(github, slug)] == [branch]
    assert _calls(tmp_path).count("repo create") == 1


def test_a_pull_request_that_shows_another_head_is_reported_not_claimed(github, tmp_path):
    run = tmp_path / "run"
    (github / "head-override").write_text("0" * 40 + "\n")
    result = _design(run)
    assert result.exit_ok is False
    assert "not this run's reviewed commit" in (result.error or "")
    assert _record(run)["status"] == "unverified"
    assert _record(run)["pull_request"] == f"https://github.com/{LOGIN}/{NAME}/pull/1"


def test_a_plan_that_changed_since_the_gate_keeps_the_work_local(github, tmp_path, capsys, monkeypatch):
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    # A fork holding the same main, so the run's base is on it and only the repository differs.
    fork = github / "acme/fork.git"
    git(fork.parent, "init", "-q", "--bare", "-b", "main", fork.name)
    (github / "acme/fork.json").write_text(json.dumps({"default_branch": "main"}))
    git(repo, "push", "-q", "https://github.com/acme/fork.git", "main:refs/heads/main")
    real = delivery.deliver_design

    def repointed(ctx):
        # Between the approval and delivery, the checkout started pointing elsewhere.
        git(repo, "remote", "set-url", "origin", "https://github.com/acme/fork.git")
        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", repointed)
    result = _design(tmp_path / "run", target=str(repo))
    assert result.exit_ok, result.error
    assert _pulls(github, "acme/app") == [] and _pulls(github, "acme/fork") == []
    err = capsys.readouterr().err
    assert "different from what the approval gate showed" in err and "acme/fork:main" in err


# ── refining an open pull request ──────────────────────────────────────────
def _opened(github: Path, tmp_path: Path) -> tuple[Path, str, dict]:
    """An existing repository with a pull request a design run opened."""
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    first = _design(tmp_path / "first", {"tool.py": "print('one')\n"}, target=str(repo))
    assert first.exit_ok, first.error
    return repo, first.facts["pull_request"], _pulls(github, "acme/app")[0]


def test_refine_starts_from_the_pull_requests_head_and_pushes_onto_its_branch(github, tmp_path):
    repo, url, pull = _opened(github, tmp_path)
    head = _remote_head(github, "acme/app", pull["head"])
    run = tmp_path / "refine"
    result = _design(run, {"tool.py": "print('one')\n", "more.py": "print('two')\n"}, refine=url)
    assert result.exit_ok, result.error
    found = worktree.recorded(run)
    assert found.base == head and found.target == str(repo.resolve())
    assert _remote_head(github, "acme/app", pull["head"]) == found.head
    assert git(repo, "merge-base", "--is-ancestor", head, found.head) == ""
    [after] = _pulls(github, "acme/app")
    assert (after["title"], after["body"]) == (pull["title"], pull["body"])
    assert _record(run)["status"] == "updated" and result.facts["pull_request"] == url
    assert f"pushes onto {pull['head']}, the branch of the pull request {url}" in (run / delivery.PLAN).read_text()
    ledger = (run / "intent-ledger.md").read_text()
    assert f"This run refines the pull request {url}" in ledger


def test_refine_pushes_nothing_once_the_pull_requests_branch_has_moved(github, tmp_path, capsys, monkeypatch):
    repo, url, pull = _opened(github, tmp_path)
    real = delivery.deliver_design

    def someone_pushed(ctx):
        other = tmp_path / "other"
        git(tmp_path, "clone", "-q", "--branch", pull["head"], f"https://github.com/acme/app.git", str(other))
        (other / "theirs.py").write_text("x = 2\n")
        git(other, "add", "theirs.py")
        git(other, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "theirs")
        git(other, "push", "-q", "origin", pull["head"])
        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", someone_pushed)
    run = tmp_path / "refine"
    result = _design(run, refine=url)
    assert result.exit_ok, result.error
    moved = _remote_head(github, "acme/app", pull["head"])
    assert moved != worktree.recorded(run).head
    assert git(tmp_path / "other", "rev-parse", "HEAD") == moved
    assert "moved to" in capsys.readouterr().err and _record(run)["status"] == "kept local"


def test_refine_with_review_comments_puts_them_in_the_steering(github, tmp_path):
    repo, url, pull = _opened(github, tmp_path)
    pulls = _pulls(github, "acme/app")
    pulls[0]["reviews"] = [{"author": {"login": "rev"}, "state": "CHANGES_REQUESTED", "body": "Rename --json."}]
    pulls[0]["comments"] = [{"author": {"login": "pm"}, "body": "Please keep the old flag working."}]
    pulls[0]["review_comments"] = [{"user": {"login": "rev"}, "path": "tool.py", "line": 1, "body": "Typo here."}]
    (github / "acme/app.pulls.json").write_text(json.dumps(pulls))
    run = tmp_path / "refine"
    result = _design(run, refine=url, mode=_mode(review_comments=True))
    assert result.exit_ok, result.error
    ledger = (run / "intent-ledger.md").read_text()
    for said in ("@rev (changes_requested): Rename --json.", "@pm: Please keep the old flag working.",
                 "@rev on tool.py:1: Typo here."):
        assert said in ledger


def test_refine_refuses_a_closed_pull_request_and_a_base(github, tmp_path):
    repo, url, _pull = _opened(github, tmp_path)
    refused = _design(tmp_path / "with-base", refine=url, base="main")
    assert refused.exit_ok is False and "takes no base" in (refused.error or "")
    pulls = _pulls(github, "acme/app")
    pulls[0]["state"] = "MERGED"
    (github / "acme/app.pulls.json").write_text(json.dumps(pulls))
    closed = _design(tmp_path / "closed", refine=url)
    assert closed.exit_ok is False and "is merged; only an open one can be refined" in (closed.error or "")


# ── what delivery never does ───────────────────────────────────────────────
def test_a_push_is_never_forced_and_never_to_the_default_branch(github, tmp_path):
    repo = git_repo(tmp_path / "repo")
    bare = _published(github, "acme/app", repo)
    with pytest.raises(delivery.NotDelivered, match="default branch"):
        delivery._push(repo, "acme/app", git(repo, "rev-parse", "HEAD"), "main", default="main")
    git(repo, "checkout", "-q", "-b", "side")
    (repo / "side.py").write_text("y = 1\n")
    git(repo, "add", "side.py")
    git(repo, "commit", "-q", "-m", "side")
    delivery._push(repo, "acme/app", git(repo, "rev-parse", "HEAD"), "side", default="main")
    git(repo, "commit", "-q", "--amend", "-m", "side, rewritten")
    with pytest.raises(delivery.NotDelivered):
        delivery._push(repo, "acme/app", git(repo, "rev-parse", "HEAD"), "side", default="main")
    assert git(bare, "rev-parse", "side") == git(repo, "rev-parse", "HEAD@{1}")


def test_delivery_asks_github_with_the_persons_home_not_the_checks(github, tmp_path):
    result = _design(tmp_path / "run")
    assert result.exit_ok, result.error
    homes = set((github / "homes.log").read_text().split())
    assert homes == {os.environ["HOME"]}


def test_a_remote_url_is_read_as_github_only_on_github():
    for url in ("https://github.com/acme/app.git", "https://x-access-token:t@github.com/acme/app",
                "git@github.com:acme/app.git", "ssh://git@github.com/acme/app"):
        assert delivery._slug(url) == "acme/app", url
    for url in ("https://gitlab.com/acme/app.git", "git@example.com:acme/app.git", "/srv/app.git"):
        assert delivery._slug(url) is None, url


# ── every route that starts a design run says how it is delivered ──────────
def _launched(monkeypatch) -> list[list[str]]:
    """What a `refactory ceo` run would have handed DSH; nothing is launched."""
    from refactory_dsh import workers, interactive, dsh_profile

    class _Run:
        returncode = 0

    seen: list[list[str]] = []
    monkeypatch.setattr(workers, "run_graph_command", lambda argv, **kwargs: seen.append(argv) or _Run())
    monkeypatch.setattr(interactive, "require_build", lambda: None)
    monkeypatch.setattr(dsh_profile, "require", lambda *a, **k: None)
    return seen


def test_a_run_approved_up_front_says_how_it_will_be_delivered_before_it_starts(github, tmp_path, monkeypatch,
                                                                                capsys):
    seen = _launched(monkeypatch)
    run = tmp_path / "run"
    assert cli.main(["ceo", "a tool", "--check", "true", "--name", NAME, "--workdir", str(run),
                     "--approve", "gate_strategy=proceed", "--headless"]) == 0
    said = f"refactory: delivery — once the reviews pass, this run creates the private GitHub repository {LOGIN}/{NAME}"
    assert said in capsys.readouterr().err and seen
    assert json.loads((run / delivery.SHOWN).read_text())["action"] == "create"
    # The same mode named directly, with a knob given last winning as it does in the run.
    assert cli.main(["ceo", "default_pack/design", "--knob", "name=first", "--knob", f"name={NAME}",
                     "--knob", "brief=a tool", "--knob", "check=true", "--workdir", str(tmp_path / "named"),
                     "--approve", "gate_strategy=proceed", "--headless"]) == 0
    assert said in capsys.readouterr().err
    # A gate that will be asked shows delivery.md instead, and an earlier announcement goes.
    assert cli.main(["ceo", "a tool", "--check", "true", "--name", NAME, "--workdir", str(run), "--headless"]) == 0
    assert "refactory: delivery" not in capsys.readouterr().err and not (run / delivery.SHOWN).exists()


def test_the_command_line_hands_the_delivery_flags_to_the_run_as_knobs(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "_forward", lambda module, argv: seen.append(argv) or 0)
    assert cli.main(["ceo", "a tool", "--check", "true", "--no-pr", "--refine", "acme/app#3", "--review-comments"]) == 0
    knobs = [seen[-1][i + 1] for i, arg in enumerate(seen[-1]) if arg == "--knob"]
    assert {"pr=0", "refine=acme/app#3", "review_comments=1"} <= set(knobs)


def test_the_console_and_chat_route_says_how_it_will_be_delivered_when_approved_up_front(github, tmp_path,
                                                                                        monkeypatch, capsys):
    from refactory_dsh import design

    monkeypatch.setattr(design.DesignMode, "run", lambda self, *a, **k: nn.RunResult(exit_ok=True, facts={}))
    monkeypatch.chdir(tmp_path)
    assert design.main(["--name", NAME, "--brief", "a tool", "--check", "true", "--workdir", "run",
                        "--verdict", "gate_strategy=proceed"]) == 0
    assert f"creates the private GitHub repository {LOGIN}/{NAME}" in capsys.readouterr().err
    assert (tmp_path / "run" / delivery.SHOWN).exists()
    # Again in the same directory with the gate left to ask: the earlier announcement goes.
    assert design.main(["--name", NAME, "--brief", "a tool", "--check", "true", "--workdir", "run"]) == 0
    assert "refactory: delivery" not in capsys.readouterr().err
    assert not (tmp_path / "run" / delivery.SHOWN).exists()


def test_a_run_approved_up_front_does_only_what_it_announced(github, tmp_path, capsys):
    run = tmp_path / "run"
    (run / ".refactory").mkdir(parents=True)
    (run / delivery.SHOWN).write_text(json.dumps({"action": "none", "summary": "won't open a pull request, because x"}))
    result = _design(run)
    assert result.exit_ok, result.error
    assert "before the run started it said it won't open a pull request" in (run / delivery.PLAN).read_text()
    assert "repo create" not in _calls(tmp_path) and not list(github.glob("*/*.git"))


def test_the_command_line_refuses_delivery_flags_that_cannot_apply(capsys):
    assert cli.main(["ceo", "a tool", "--check", "true", "--refine", "acme/app#3", "--base", "main"]) == 2
    assert cli.main(["ceo", "a tool", "--check", "true", "--review-comments"]) == 2
    assert cli.main(["ceo", "a tool", "--check", "true", "--mode", "design_fast", "--no-pr"]) == 2
    assert cli.main(["ceo", "a tool", "--check", "true", "--mode", "design_fast", "--refine", "acme/app#3"]) == 2
    assert cli.main(["ceo", "a tool", "--check", "true", "--no-worktree", "--refine", "acme/app#3"]) == 2
    err = capsys.readouterr().err
    assert "takes no --base" in err and "goes with --refine" in err
    assert "does not take --no-pr" in err and "--no-worktree cannot take --refine" in err
    assert "--mode design_fast does not take --refine" in err


def test_the_console_design_verb_forwards_how_the_run_is_delivered(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(console, "_forward", lambda module, argv: seen.append(argv) or {})
    assert console.main(["design", "--name", NAME, "--brief", "b", "--check", "true", "--no-pr",
                         "--refine", "acme/app#3", "--review-comments"]) == 0
    assert "--no-pr" in seen[-1] and "--review-comments" in seen[-1]
    assert seen[-1][seen[-1].index("--refine") + 1] == "acme/app#3"


def test_refactory_projects_lists_the_pull_request_a_run_opened(github, tmp_path, capsys):
    result = _design(tmp_path / "run")
    assert result.exit_ok, result.error
    capsys.readouterr()
    assert projects.main([]) == 0
    listed = capsys.readouterr().out
    assert result.facts["pull_request"] in listed and f"{LOGIN}/{NAME}" in listed


# ── the cases between the paths ────────────────────────────────────────────
def test_a_checkout_that_is_only_behind_github_gets_its_pull_request_and_keeps_its_refs(github, tmp_path):
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", "https://github.com/acme/app.git", str(other))
    (other / "later.py").write_text("x = 1\n")
    git(other, "add", "later.py")
    git(other, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "later")
    git(other, "push", "-q", "origin", "main")
    refs = git(repo, "for-each-ref")
    run = tmp_path / "run"
    result = _design(run, target=str(repo))
    assert result.exit_ok, result.error
    assert len(_pulls(github, "acme/app")) == 1
    branch = worktree.recorded(run).branch
    assert git(repo, "for-each-ref", "--exclude", f"refs/heads/{branch}") == refs
    assert not (repo / ".git" / "FETCH_HEAD").exists()


def test_a_creation_that_died_before_its_first_push_goes_on_in_the_repository_it_made(github, tmp_path, monkeypatch):
    run = tmp_path / "run"
    real = delivery._push

    def first_push_fails(path, slug, commit, branch, **kwargs):
        if branch == "main":
            raise RuntimeError("the delivery stopped here")
        return real(path, slug, commit, branch, **kwargs)

    monkeypatch.setattr(delivery, "_push", first_push_fails)
    assert _design(run).exit_ok is False
    slug = f"{LOGIN}/{NAME}"
    assert (github / f"{slug}.git").is_dir() and not _remote_head(github, slug, "main")
    monkeypatch.setattr(delivery, "_push", real)
    resumed = DesignMode.resume(run, model=FakeModel([]))
    assert resumed.exit_ok, resumed.error
    assert _calls(tmp_path).count("repo create") == 1 and len(_pulls(github, slug)) == 1
    assert _remote_head(github, slug, "main") == git(projects_dir() / NAME, "rev-parse", "main")


def test_a_repository_gh_cannot_push_to_gets_no_pull_request(github, tmp_path):
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    (github / "acme/app.json").write_text(json.dumps({"default_branch": "main", "push": False}))
    planned = delivery.plan(repo, base=git(repo, "rev-parse", "HEAD"), branch="refactory/run-0123456789ab")
    assert planned["action"] == "none" and "cannot push to acme/app" in planned["why"]


def test_a_refinement_delivered_once_is_not_refused_as_moved_when_asked_again(github, tmp_path, capsys):
    repo, url, pull = _opened(github, tmp_path)
    run = tmp_path / "refine"
    result = _design(run, refine=url)
    assert result.exit_ok, result.error
    capsys.readouterr()
    again = delivery.deliver_design(nn.RunContext(run, facts=result.facts, enforce_writes=False))
    assert again["pull_request"] == url and _record(run)["status"] == "already updated"
    assert "run warning" not in capsys.readouterr().err


def test_refine_refuses_a_pull_request_from_another_repositorys_branch(github, tmp_path):
    repo, url, _pull = _opened(github, tmp_path)
    pulls = _pulls(github, "acme/app")
    pulls[0]["cross"] = True
    (github / "acme/app.pulls.json").write_text(json.dumps(pulls))
    refused = _design(tmp_path / "fork", refine=url)
    assert refused.exit_ok is False and "another repository's branch" in (refused.error or "")


def test_a_later_report_of_the_same_run_keeps_its_pull_request_in_the_registry(tmp_path):
    repo = git_repo(tmp_path / "repo")
    projects.record_run(repo, run=tmp_path / "run", branch="refactory/run-0123456789ab", base="b", status="running")
    projects.record_delivery(repo, branch="refactory/run-0123456789ab", pull_request="https://github.com/a/b/pull/1",
                             status="opened")
    projects.record_run(repo, run=tmp_path / "run", branch="refactory/run-0123456789ab", base="b", status="succeeded")
    [entry] = projects.load()["projects"]
    assert entry["runs"][-1]["pull_request"] == "https://github.com/a/b/pull/1"


def test_design_fast_takes_none_of_delivery_s_knobs():
    from refactory_dsh.fast import DesignFastMode

    assert not {"pr", "repo_owner", "repo_visibility", "refine", "review_comments"} & set(DesignFastMode.declare())


def test_a_cli_run_ends_by_naming_its_pull_request(tmp_path):
    from refactory_dsh import interactive

    (tmp_path / "facts.json").write_text(json.dumps({"name": NAME, "pull_request": "https://github.com/a/b/pull/7",
                                                     "delivery_result": "opened: https://github.com/a/b/pull/7"}))
    assert "https://github.com/a/b/pull/7" in interactive.run_summary(tmp_path)


def test_a_new_projects_main_with_work_of_its_own_is_never_published(github, tmp_path, capsys):
    first = _design(tmp_path / "one", mode=_mode(pr=False))
    assert first.exit_ok, first.error
    project = projects_dir() / NAME
    (project / "notes.md").write_text("mine\n")
    git(project, "add", "notes.md")
    git(project, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "mine")
    second = _design(tmp_path / "two")
    assert second.exit_ok, second.error
    assert "no longer the empty first commit" in (tmp_path / "two" / delivery.PLAN).read_text()
    assert "repo create" not in _calls(tmp_path) and not list(github.glob("*/*.git"))
    assert "no pull request was opened" in capsys.readouterr().err


def test_without_a_network_the_run_succeeds_and_says_its_work_stays_local(github, tmp_path, capsys):
    (github / "offline").write_text("")
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    assert "error connecting to api.github.com" in capsys.readouterr().err
    assert _record(run)["status"] == "kept local" and not list(github.glob("*/*.git"))


def test_review_comments_without_refine_are_refused_not_dropped(github, tmp_path):
    refused = _design(tmp_path / "run", mode=_mode(review_comments=True))
    assert refused.exit_ok is False and "review_comments goes with refine" in (refused.error or "")


def test_refactory_projects_says_when_a_run_kept_its_work_local(github, tmp_path, capsys):
    (github / "login").unlink()
    assert _design(tmp_path / "run").exit_ok
    capsys.readouterr()
    assert projects.main([]) == 0
    assert "kept local" in capsys.readouterr().out


def test_a_new_projects_first_pull_request_publishes_nothing_its_base_adds_to_main(github, tmp_path, capsys):
    first = _design(tmp_path / "one", mode=_mode(pr=False))
    assert first.exit_ok, first.error
    project = projects_dir() / NAME
    git(project, "checkout", "-q", "-b", "mywork", "main")
    (project / "secret-notes.md").write_text("mine\n")
    git(project, "add", "secret-notes.md")
    git(project, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "mine")
    git(project, "checkout", "-q", "main")
    second = _design(tmp_path / "two", base="mywork")
    assert second.exit_ok, second.error
    assert "1 commit(s) besides the empty main" in (tmp_path / "two" / delivery.PLAN).read_text()
    assert "repo create" not in _calls(tmp_path) and not list(github.glob("*/*.git"))


def test_a_credential_in_a_rewritten_url_never_reaches_a_message(monkeypatch, tmp_path):
    def leaks(*args, **kwargs):
        raise ValueError("git push failed in /r: fatal: unable to access 'https://me:ghp_secret@github.com/a/b.git/'")

    monkeypatch.setattr(delivery.worktree, "git", leaks)
    stub(tmp_path / "bin", "gh", "true")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(delivery.NotDelivered) as refused:
        delivery._push(tmp_path, "a/b", "0" * 40, "x", default="main")
    assert "ghp_secret" not in str(refused.value) and "https://***@github.com" in str(refused.value)


def test_a_pull_request_never_carries_a_credential_the_check_spelled_out(github, tmp_path):
    run = tmp_path / "run"
    result = _design(run, check="API_TOKEN=abc123 python tool.py")
    assert result.exit_ok, result.error
    [pull] = _pulls(github, f"{LOGIN}/{NAME}")
    assert "abc123" not in pull["body"] and "API_TOKEN=*** python tool.py" in pull["body"]


def test_a_redo_after_its_pull_request_merged_records_it_merged(github, tmp_path, capsys):
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    slug = f"{LOGIN}/{NAME}"
    pulls = _pulls(github, slug)
    pulls[0]["state"] = "MERGED"
    (github / f"{slug}.pulls.json").write_text(json.dumps(pulls))
    capsys.readouterr()
    again = delivery.deliver_design(nn.RunContext(run, facts=result.facts, enforce_writes=False))
    assert (_record(run)["status"], again["pull_request"]) == ("merged", result.facts["pull_request"])
    assert "run warning" not in capsys.readouterr().err


def test_a_redo_after_its_pull_request_closed_keeps_it_in_the_record(github, tmp_path, capsys):
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    slug = f"{LOGIN}/{NAME}"
    pulls = _pulls(github, slug)
    pulls[0]["state"] = "CLOSED"
    (github / f"{slug}.pulls.json").write_text(json.dumps(pulls))
    delivery.deliver_design(nn.RunContext(run, facts=result.facts, enforce_writes=False))
    record = _record(run)
    assert (record["status"], record["pull_request"]) == ("kept local", result.facts["pull_request"])
    assert f"{result.facts['pull_request']} was not updated" in capsys.readouterr().err
    assert len(_pulls(github, slug)) == 1


def test_a_pull_request_gh_opened_but_reported_failing_is_the_one_delivered(github, tmp_path, monkeypatch):
    real = delivery._gh

    def opens_then_fails(*args, **kwargs):
        said = real(*args, **kwargs)
        if args[:2] == ("pr", "create"):
            raise delivery.NotDelivered("`gh pr create` failed: timeout")
        return said

    monkeypatch.setattr(delivery, "_gh", opens_then_fails)
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    assert _record(run)["status"] == "opened" and len(_pulls(github, f"{LOGIN}/{NAME}")) == 1


def test_a_repository_gh_made_but_reported_failing_is_adopted_only_when_new_and_empty(github, tmp_path,
                                                                                     monkeypatch):
    real = delivery._gh

    def creates_then_fails(*args, **kwargs):
        said = real(*args, **kwargs)
        if args[:2] == ("repo", "create"):
            raise delivery.NotDelivered("`gh repo create` failed: timeout")
        return said

    monkeypatch.setattr(delivery, "_gh", creates_then_fails)
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    assert _record(run)["status"] == "opened"
    [entry] = projects.load()["projects"]
    assert entry["github"] == f"{LOGIN}/{NAME}"


def test_a_new_repository_that_is_slow_to_take_pushes_is_waited_for(github, tmp_path, monkeypatch):
    real, calls = delivery._heads, []

    def lags(path, slug):
        calls.append(slug)
        if len(calls) == 1:
            raise delivery.NotDelivered("git ls-remote failed: repository not found")
        return real(path, slug)

    monkeypatch.setattr(delivery, "_heads", lags)
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    assert _record(run)["status"] == "opened"


def test_a_remote_git_cannot_read_keeps_the_work_local_rather_than_failing_the_run(github, tmp_path):
    repo = git_repo(tmp_path / "repo")
    git(repo, "remote", "add", "origin", "https://github.com/acme/app.git")
    git(repo, "config", "--unset", "remote.origin.url")
    planned = delivery.plan(repo, base=git(repo, "rev-parse", "HEAD"), branch="refactory/run-0123456789ab")
    assert planned["action"] == "none" and "could not read the remotes" in planned["why"]


def test_a_gh_that_cannot_even_start_keeps_the_work_local(tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text("#!/no/such/interpreter\n")
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    assert "could not run" in _record(run)["why"] and "no pull request was opened" in capsys.readouterr().err


def test_a_branch_that_moved_since_its_reviews_is_never_delivered(github, tmp_path):
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    unreviewed = {**result.facts, "commit": "0" * 40}
    with pytest.raises(ValueError, match="not at the attempt its reviews passed"):
        delivery.deliver_design(nn.RunContext(run, facts=unreviewed, enforce_writes=False))


def test_a_refinement_never_pushes_onto_a_default_branch(github, tmp_path):
    repo, url, pull = _opened(github, tmp_path)
    pulls = _pulls(github, "acme/app")
    pulls[0]["head"] = "main"
    (github / "acme/app.pulls.json").write_text(json.dumps(pulls))
    head = _remote_head(github, "acme/app", "main")
    planned = delivery.plan(repo, base=head, branch="refactory/run-0123456789ab",
                            refine={"url": url, "repo": "acme/app", "head": head})
    assert planned["action"] == "none" and "is acme/app's default branch" in planned["why"]


def test_a_new_repository_goes_where_repo_owner_and_repo_visibility_say(github, tmp_path):
    result = _design(tmp_path / "run", mode=_mode(repo_owner="acme-org", repo_visibility="internal"))
    assert result.exit_ok, result.error
    assert f"repo create acme-org/{NAME} --internal" in _calls(tmp_path)
    assert json.loads((github / "acme-org" / f"{NAME}.json").read_text())["visibility"] == "internal"
    assert result.facts["pull_request"] == f"https://github.com/acme-org/{NAME}/pull/1"


def test_a_refinement_that_github_shows_at_another_head_is_reported_not_claimed(github, tmp_path):
    repo, url, _pull = _opened(github, tmp_path)
    (github / "stale-view").write_text("0" * 40 + "\n")
    run = tmp_path / "refine"
    result = _design(run, refine=url)
    assert result.exit_ok is False and "not this run's reviewed commit" in (result.error or "")
    assert _record(run)["status"] == "unverified"


def test_a_pull_request_already_open_is_checked_again_when_asked_again(github, tmp_path):
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    (github / "stale-view").write_text("0" * 40 + "\n")
    with pytest.raises(delivery.Unverified):
        delivery.deliver_design(nn.RunContext(run, facts=result.facts, enforce_writes=False))


def test_the_targets_pre_push_hook_does_not_run_for_a_delivery(github, tmp_path):
    repo = git_repo(tmp_path / "repo")
    bare = _published(github, "acme/app", repo)
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\ntouch '{tmp_path / 'hook-ran'}'\nexit 1\n")
    hook.chmod(0o755)
    git(repo, "checkout", "-q", "-b", "side")
    (repo / "side.py").write_text("y = 1\n")
    git(repo, "add", "side.py")
    git(repo, "commit", "-q", "-m", "side")
    delivery._push(repo, "acme/app", git(repo, "rev-parse", "HEAD"), "side", default="main")
    assert git(bare, "rev-parse", "side") == git(repo, "rev-parse", "HEAD")
    assert not (tmp_path / "hook-ran").exists()


def test_nothing_delivery_runs_can_stop_to_ask_a_question():
    env = delivery._env()
    assert (env["GIT_TERMINAL_PROMPT"], env["GIT_ASKPASS"], env["GCM_INTERACTIVE"], env["GH_PROMPT_DISABLED"]) == (
        "0", "", "never", "1")
    assert env["GIT_SSH_COMMAND"].endswith("-o BatchMode=yes")


def test_the_console_announces_a_run_approved_up_front_before_it_is_handed_on(github, tmp_path, monkeypatch, capsys):
    said_first: list[str] = []
    monkeypatch.setattr(console, "_forward", lambda module, argv: said_first.append(capsys.readouterr().err) or {})
    monkeypatch.chdir(tmp_path)
    assert console.main(["design", "--name", NAME, "--brief", "a tool", "--check", "true",
                         "--verdict", "gate_strategy=proceed"]) == 0
    assert f"creates the private GitHub repository {LOGIN}/{NAME}" in said_first[-1]


# ── the second review: the approval card, the base's branch, and what is where ─
def test_the_approval_card_itself_names_the_repository_a_run_would_create(github, tmp_path):
    """The card shows only the gate's question, so the question carries the plan (#362 review)."""
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    said = f"creates the private GitHub repository {LOGIN}/{NAME}"
    assert "{{delivery}}" in DesignMode().build().nodes["gate_strategy"].notes
    # The walker's journal shows the gate as it was put, never the raw placeholder.
    journal = [json.loads(line) for line in (run / ".refactory" / "events.jsonl").read_text().splitlines()]
    shown = [record["summary"] for record in journal if record.get("node_id") == "gate_strategy" and record.get("summary")]
    assert shown and all(said in summary and "{{" not in summary for summary in shown)
    # The panel's pending-gate card, read by the console from a spine run's graph and facts.json.
    (run / "facts.json").write_text(json.dumps(result.facts, default=str))
    (run / ".refactory" / "spine-status.json").write_text(json.dumps({"status": "waiting_user", "nodes": ["gate_strategy"]}))
    graphs = run / "graphs"
    graphs.mkdir(exist_ok=True)
    (graphs / "design.graph.json").write_text(json.dumps({"nodes": {"gate_strategy": {
        "_type": "GateNode", "evaluator_type": "user",
        "gate_prompt": DesignMode().build().nodes["gate_strategy"].notes}}, "edges": [
        {"source": "gate_strategy", "target": "emit", "condition": "proceed"}]}))
    [card] = console.gates(run)
    assert said in card["prompt"] and "{{" not in card["prompt"]


def _stacked(github: Path, tmp_path: Path) -> tuple[Path, str]:
    """A repository on GitHub with a pushed branch `stack` one commit ahead of main; main is checked out."""
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    git(repo, "checkout", "-q", "-b", "stack")
    (repo / "lib.py").write_text("x = 1\n")
    git(repo, "add", "lib.py")
    git(repo, "commit", "-q", "-m", "the stack's own commit")
    git(repo, "push", "-q", "-u", "origin", "stack")
    git(repo, "checkout", "-q", "main")
    return repo, git(repo, "rev-parse", "stack")


def test_a_run_on_a_pushed_branch_opens_its_pull_request_into_that_branch(github, tmp_path):
    repo, stack = _stacked(github, tmp_path)
    run = tmp_path / "run"
    result = _design(run, target=str(repo), base="stack")
    assert result.exit_ok, result.error
    [pull] = _pulls(github, "acme/app")
    assert pull["base"] == "stack", "a stack's pull request goes into the branch it builds on"
    assert "into acme/app:stack, the branch its base came from" in (run / delivery.PLAN).read_text()
    # It carries only the run's own commit, not the stack's.
    branch = worktree.recorded(run).branch
    assert git(repo, "rev-list", "--count", f"{stack}..{branch}") == "1"


def test_a_run_from_a_checkout_on_a_pushed_branch_goes_into_its_upstream(github, tmp_path):
    repo, _stack = _stacked(github, tmp_path)
    git(repo, "checkout", "-q", "stack")
    run = tmp_path / "run"
    result = _design(run, target=str(repo))
    assert result.exit_ok, result.error
    assert _pulls(github, "acme/app")[0]["base"] == "stack"


def test_a_base_branch_github_does_not_have_falls_back_to_the_default_branch(github, tmp_path):
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    git(repo, "branch", "topic")
    planned = delivery.plan(repo, base=git(repo, "rev-parse", "topic"), branch="refactory/run-0123456789ab",
                            rev="topic")
    assert (planned["action"], planned["into"]) == ("pull_request", "main")
    assert planned["summary"].endswith("into acme/app:main")


def test_a_delivery_that_stopped_after_its_push_says_the_branch_is_on_github(github, tmp_path, monkeypatch, capsys):
    real = delivery._gh

    def no_pull_request(*args, **kwargs):
        if args[:2] == ("pr", "create"):
            raise delivery.NotDelivered("`gh pr create` failed: error connecting to api.github.com")
        return real(*args, **kwargs)

    monkeypatch.setattr(delivery, "_gh", no_pull_request)
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    branch = worktree.recorded(run).branch
    assert _remote_head(github, f"{LOGIN}/{NAME}", branch)
    err = capsys.readouterr().err
    assert f"the branch {branch} is already on GitHub, as {LOGIN}/{NAME}:{branch}" in err
    assert "stays on the local branch" not in err
    assert _record(run)["pushed"] == f"{LOGIN}/{NAME}:{branch}"


def test_a_repository_whose_creation_lost_the_network_is_finished_by_a_redo(github, tmp_path, monkeypatch, capsys):
    """gh reports the creation failed, the network then drops; the redo adopts the repository it made."""
    real = delivery._gh

    def creates_then_drops(*args, **kwargs):
        said = real(*args, **kwargs)
        if args[:2] == ("repo", "create"):
            (github / "offline").write_text("")
            raise delivery.NotDelivered("`gh repo create` failed: timeout")
        return said

    monkeypatch.setattr(delivery, "_gh", creates_then_drops)
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    assert _record(run)["status"] == "kept local" and (github / LOGIN / f"{NAME}.git").is_dir()
    (github / "offline").unlink()
    monkeypatch.setattr(delivery, "_gh", real)
    again = delivery.deliver_design(nn.RunContext(run, facts=result.facts, enforce_writes=False))
    assert again["pull_request"] == f"https://github.com/{LOGIN}/{NAME}/pull/1"
    assert _calls(tmp_path).count("repo create") == 2 and len(_pulls(github, f"{LOGIN}/{NAME}")) == 1


def test_a_repository_with_the_name_that_this_project_never_set_out_to_create_is_not_adopted(github, tmp_path):
    first = _design(tmp_path / "one", mode=_mode(pr=False))
    assert first.exit_ok, first.error
    bare = github / LOGIN / f"{NAME}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git(bare.parent, "init", "-q", "--bare", "-b", "main", bare.name)
    (github / LOGIN / f"{NAME}.json").write_text(json.dumps({"default_branch": "main"}))
    second = _design(tmp_path / "two")
    assert second.exit_ok, second.error
    assert "already exists" in (tmp_path / "two" / delivery.PLAN).read_text() and _pulls(github, f"{LOGIN}/{NAME}") == []


def test_a_run_planned_while_gh_was_logged_out_says_the_next_run_delivers(github, tmp_path):
    (github / "login").unlink()
    run = tmp_path / "run"
    assert _design(run).exit_ok
    assert "`gh auth login` signs in, for the next run" in (run / delivery.PLAN).read_text()


def test_the_pull_request_says_the_commits_are_the_runs_attempts(github, tmp_path):
    assert _design(tmp_path / "run").exit_ok
    [pull] = _pulls(github, f"{LOGIN}/{NAME}")
    assert "the run's attempts" in pull["body"]
    assert "squash merge is recommended" not in pull["body"]


def test_an_upstream_that_does_not_hold_the_base_is_not_where_the_pull_request_goes(github, tmp_path):
    repo, _stack = _stacked(github, tmp_path)
    # main moves on GitHub; a local branch at that commit still names stack as its upstream.
    (repo / "later.py").write_text("y = 2\n")
    git(repo, "add", "later.py")
    git(repo, "commit", "-q", "-m", "main moves on")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "branch", "-q", "--track", "elsewhere", "origin/stack")
    git(repo, "branch", "-q", "-f", "elsewhere", "main")
    planned = delivery.plan(repo, base=git(repo, "rev-parse", "main"), branch="refactory/run-0123456789ab",
                            rev="elsewhere")
    assert (planned["action"], planned["into"]) == ("pull_request", "main"), planned
