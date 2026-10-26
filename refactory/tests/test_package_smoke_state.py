"""A created package is marked in `refactory list` until its smoke passes (dm-264).

The walkthrough's create run stopped when readme_pack's smoke failed, and `refactory
list` still showed readme_pack like any other package. Register has to put a package
where the smoke can import it, so it is listed from that moment; what the listing owes
the person is that it says the package has not been shown to run.
"""
from __future__ import annotations

import json
from pathlib import Path

from refactory_dsh import create_ops, nn, registry, sandbox
from refactory_dsh.create_ops import STAGING_DIR, register
from refactory_dsh.steering import Ledger
from tests_support import git_repo

NAME = "smoke_state_pkg"


def _project(tmp_path: Path, monkeypatch) -> Path:
    project = git_repo(tmp_path / "project")
    (project / ".factory" / "packages").mkdir(parents=True)
    monkeypatch.setattr("refactory_dsh.paths.project_for_nodes", lambda: project)
    monkeypatch.setattr("refactory_dsh.create_ops.project_for_nodes", lambda: project)
    monkeypatch.setattr("refactory_dsh.sandbox.describe_package", lambda name, **_: sandbox.SandboxResult(
        ok=True, payload={"describe": {"name": name, "modes": []}, "ids": [f"{name}/x"]}))
    return project


def _registered(tmp_path: Path, project: Path) -> nn.RunContext:
    """A create run that staged a package and registered it, as the graph does."""
    run = tmp_path / "run"
    staging = run / STAGING_DIR / ".factory" / "packages" / NAME
    staging.mkdir(parents=True)
    (staging / "__init__.py").write_text(
        "from refactory_dsh.nn import Package\n\n"
        f"PACKAGE = Package(name={NAME!r}, description='made by a create run')\n")
    (staging / ".origin.json").write_text(json.dumps({"origin": "create-mode", "trust": "generated"}))
    ctx = nn.RunContext(run, facts={"name": NAME, "package_root": str(staging), "source_hash": "",
                                    "source_existed": False, "brief": "make it",
                                    "artifact": str(run)}, enforce_writes=False)
    ctx.facts.update(register(ctx))
    ctx.write_json("brief.json", {"brief": "make it"})
    ctx.write_json("strategy.json", {"acceptance_criteria": ["it runs"]})
    Ledger.seed(run, "make it")
    return ctx


def _line(project: Path) -> str:
    return next(line for line in registry.render_catalogue(project).splitlines()
                if line.startswith(f"{NAME}/"))


def test_a_registered_package_is_unverified_until_its_smoke_passes(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    ctx = _registered(tmp_path, project)
    shared = registry.workspace_root(project) / NAME
    assert json.loads((shared / registry.ORIGIN).read_text())["smoke"] == "pending"
    assert _line(project) == f"{NAME}/  made by a create run  [generated, unverified (smoke not passed)]"

    # The smoke fails, as readme_pack's did: still marked.
    def fails(ctx):
        raise ValueError("the new mode did not finish a run: WebAssembly.instantiate(): Out of memory")

    monkeypatch.setattr(create_ops, "smoke", fails)
    assert create_ops.gate_smoke(ctx) == "reloop"
    assert registry.smoke_state(NAME, project) == "pending"
    assert "unverified" in _line(project)

    # It passes: listed like any other package.
    monkeypatch.setattr(create_ops, "smoke", lambda ctx: {"smoke_score": 1.0, "smoke_ok": True})
    assert create_ops.gate_smoke(ctx) == "proceed"
    assert registry.smoke_state(NAME, project) == "passed"
    assert _line(project) == f"{NAME}/  made by a create run  [generated]"

    # A later attempt that fails takes the mark back: a pass is not kept past a failure.
    monkeypatch.setattr(create_ops, "smoke", fails)
    create_ops.gate_smoke(ctx)
    assert registry.smoke_state(NAME, project) == "pending"


def test_a_package_written_in_place_is_unverified_from_registration(tmp_path, monkeypatch):
    """Without a staging directory `materialise` writes the shared package itself."""
    project = _project(tmp_path, monkeypatch)
    shared = registry.workspace_root(project) / NAME
    shared.mkdir()
    (shared / "__init__.py").write_text(
        "from refactory_dsh.nn import Package\n\n"
        f"PACKAGE = Package(name={NAME!r}, description='made in place')\n")
    (shared / registry.ORIGIN).write_text(json.dumps({"origin": "create-mode", "trust": "generated"}))
    run = tmp_path / "run"
    run.mkdir()
    register(nn.RunContext(run, facts={"name": NAME}, enforce_writes=False))
    assert registry.smoke_state(NAME, project) == "pending"
    assert _line(project).endswith("[generated, unverified (smoke not passed)]")


def test_a_package_no_run_registered_is_not_marked(tmp_path):
    """Installed, added or adopted packages never went through a run's smoke."""
    root = registry.workspace_root(tmp_path) / "added_pkg_state"
    root.mkdir(parents=True)
    (root / "__init__.py").write_text(
        "from refactory_dsh.nn import Package\n\nPACKAGE = Package(name='added_pkg_state', "
        "description='added by hand')\n")
    (root / registry.ORIGIN).write_text(json.dumps({"origin": "/somewhere", "trust": "generated"}))
    listing = registry.render_catalogue(tmp_path)
    assert "added_pkg_state/  added by hand  [generated]" in listing
    assert "unverified" not in listing
    (root / registry.ORIGIN).write_text("not json")
    assert registry.smoke_state("added_pkg_state", tmp_path) == "passed"
