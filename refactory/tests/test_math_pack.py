"""Packing a tool that is not Python: the declaration, the absence, and the run.

Three things are held here, and they are separable on purpose.

The **declaration** is a file beside a package's manifest, read without importing
the package, and :class:`~refactory_dsh.toolchain.Tool` carries the command that
fixes an absence. The **absence** has to be readable -- the package loads, lists and
verifies, and the mode that needs the tool fails at the node that would have used
it, naming the tool and the install command. The **run** is real: sympy is exercised
for its exact algebra whenever it is importable, and the Lean path is exercised
against the real proof checker whenever ``lean`` is on the PATH.

Lean is not installed on every machine, and a checkout without it must still be
correct. So the Lean tests split in two: the graph, the retry gate and the tool
plumbing are exercised against a stub ``lean`` on the PATH, which is honest about
being a stub, and the proof checker itself is exercised only when it is there.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest

from refactory_dsh import nn, registry, toolchain
from refactory_dsh.loop import Loop, LoopConfig
from refactory_packages.lean_pack import ops as lean_ops
from refactory_packages.lean_pack import problems as lean_problems

HAS_LEAN = shutil.which("lean") is not None

#: The checkout root, which is also the project a package is read from.
ROOT = Path(__file__).resolve().parent.parent.parent
HAS_SYMPY = importlib.util.find_spec("sympy") is not None

#: mathlib is a multi-gigabyte checkout, so a machine either has it or does not,
#: and it announces which by the variable its own lakefile uses.
HAS_MATHLIB = bool(os.environ.get("MATHLIB_PATH")) and Path(
    os.environ.get("MATHLIB_PATH", "")
).is_dir()

needs_lean = pytest.mark.skipif(
    not HAS_LEAN, reason="the Lean 4 proof checker 'lean' is not on the PATH"
)
needs_mathlib = pytest.mark.skipif(
    not (HAS_LEAN and HAS_MATHLIB),
    reason="mathlib is not installed (set MATHLIB_PATH to a built mathlib4 checkout)",
)
needs_sympy = pytest.mark.skipif(
    not HAS_SYMPY, reason="the sympy module is not importable by this interpreter"
)

#: A PATH with nothing on it, so ``lean`` and ``lake`` are certainly absent.
NO_TOOLS_PATH = "/nonexistent-refactory-path"

DECLARATION = (
    "from refactory_dsh.toolchain import Tool\n\n"
    "TOOLS = (Tool(name='ghost', kind='binary', probe='no-such-binary-xyz',\n"
    "              install='apt install ghost', why='checking a ghost'),)\n"
)
MANIFEST = "from refactory_dsh.nn import Package\n\nPACKAGE = Package(name='{name}')\n"


# ── helpers ────────────────────────────────────────────────────────────────
def _workspace_package(root: Path, name: str, manifest: str, tools: str = "") -> Path:
    """A package in the writable root, with whatever declaration the test wants."""
    package = registry.workspace_root(root) / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(manifest)
    if tools:
        (package / "tools.py").write_text(tools)
    registry.install_workspace(root)
    return package


def _resolve_mode(identifier: str, **knobs: object) -> nn.Mode:
    """A mode of an installed package, with knobs moved to the values given."""
    mode_class = registry.resolve(Path("."), identifier)[1]
    mode = mode_class()
    if knobs:
        state = mode.state_dict()
        for name, value in knobs.items():
            state["knobs"][name]["value"] = value
        mode = mode_class(state=state)
    return mode


def _stub_lean(tmp_path: Path, *, accepts: str) -> Path:
    """A ``lean`` on the PATH that accepts a solution only if it contains ``accepts``.

    Deliberately not a proof checker. It is here to exercise the graph, the retry
    gate and the tool plumbing on a machine with no Lean, and every test that uses
    it says so.
    """
    bindir = tmp_path / "stub-bin"
    bindir.mkdir(parents=True, exist_ok=True)
    lean = bindir / "lean"
    lean.write_text(
        "#!/bin/sh\n"
        # Shell built-ins only: the test pins PATH to `/usr/bin:/bin` so the
        # toolchain under test is the stub, and a distro that keeps `grep`
        # elsewhere (NixOS) would otherwise make the stub itself unrunnable.
        "found=0\n"
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        "    Solution.lean)\n"
        "      while IFS= read -r line; do\n"
        f"        case \"$line\" in *{accepts}*) found=1 ;; esac\n"
        "      done < \"$arg\"\n"
        "      if [ \"$found\" = 1 ]; then echo 'stub: accepted'; exit 0; fi\n"
        "      echo 'stub: tactic failed'; exit 1 ;;\n"
        "  esac\n"
        "done\n"
        "echo 'stub: elaborated'\n"
        "exit 0\n"
    )
    lean.chmod(0o755)
    return bindir


# ── the declaration ────────────────────────────────────────────────────────
def test_the_lean_retry_gate_says_why_the_proof_was_rejected(tmp_path):
    """Whoever watches the run sees Lean's own error, not only that it went back."""
    ctx = nn.RunContext(tmp_path, knobs={"attempts": 4}, facts={}, task={})
    ctx.write_json(lean_ops.PROBLEM_FILE, lean_problems.THREE_CONSECUTIVE.to_state())
    ctx.write_json(lean_ops.VERIFY_FILE, {"attempt": 1, "strategy": "omega", "verified": False,
                                          "used_sorry": False, "output": "error: unsolved goals"})
    verdict = lean_ops.retry_gate(ctx)
    assert verdict == "reloop"
    allowed = min(4, len(lean_problems.THREE_CONSECUTIVE.proofs))
    assert f"attempt 1 of {allowed} (omega)" in verdict.reason and "unsolved goals" in verdict.reason


def test_a_package_declares_what_it_needs_beside_its_manifest():
    """The requirement is data, in its own file, with the fix and the reason."""
    lean = {tool.name: tool for tool in registry.tools("lean_pack")}
    assert set(lean) == {"lean", "lake", "mathlib"}
    assert lean["lean"].kind == "binary" and not lean["lean"].optional
    assert lean["mathlib"].kind == "path" and lean["mathlib"].optional
    for tool in lean.values():
        assert tool.install.strip(), f"{tool.name} must name the command that fixes it"
        assert tool.why.strip(), f"{tool.name} must say what it is for"

    sympy = {tool.name: tool for tool in registry.tools("sympy_pack")}
    assert sympy["sympy"].kind == "python"


def test_a_package_that_needs_nothing_declares_nothing():
    assert registry.tools("circle_packing") == ()
    assert "tools" not in {field.name for field in dataclasses.fields(nn.Package)}


def test_a_declaration_is_read_without_importing_the_package(tmp_path):
    """The point of the separate file: readable when the package itself is not.

    A missing tool is exactly the condition that makes a package unimportable -- the
    ops import the module that is absent -- so a declaration the registry had to
    import the package to read could never report the tool whose absence broke it.
    """
    _workspace_package(
        tmp_path,
        "unimportable_pkg",
        "raise ImportError('the ops import a module this machine does not have')\n",
        DECLARATION,
    )
    assert [tool.name for tool in registry.tools("unimportable_pkg", tmp_path)] == ["ghost"]
    with pytest.raises(nn.PackageError, match="cannot import package"):
        registry.load("unimportable_pkg", tmp_path)


def test_an_absent_tool_is_reported_and_the_package_still_lists(tmp_path):
    _workspace_package(
        tmp_path, "absent_tool_pkg", MANIFEST.format(name="absent_tool_pkg"), DECLARATION
    )
    report = registry.tool_report(tmp_path, "absent_tool_pkg")["absent_tool_pkg"]
    assert [entry["name"] for entry in report] == ["ghost"]
    assert report[0]["present"] is False
    assert "apt install ghost" in report[0]["fix"]
    assert "checking a ghost" in report[0]["fix"]
    # A missing tool is a fact about the machine, not a defect of the package, so
    # `verify` -- which answers "is this package well formed" -- stays clean.
    assert registry.verify(tmp_path, "absent_tool_pkg")["absent_tool_pkg"] == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "x", "kind": "binary", "probe": "x", "why": "something"},
        {"name": "x", "kind": "binary", "probe": "x", "install": "install x"},
        {"name": "x", "kind": "telepathy", "probe": "x", "install": "i", "why": "w"},
        {"name": "x", "kind": "binary", "probe": "", "install": "i", "why": "w"},
        {"name": "not a name", "kind": "binary", "probe": "x", "install": "i", "why": "w"},
    ],
)
def test_a_declaration_that_cannot_name_the_fix_is_refused(kwargs):
    """A declaration that can only say "absent" is the failure this replaces."""
    with pytest.raises(toolchain.ToolDeclError):
        toolchain.Tool(**kwargs)


def test_a_declaration_module_that_does_not_run_is_a_package_defect(tmp_path):
    _workspace_package(
        tmp_path,
        "broken_decl_pkg",
        MANIFEST.format(name="broken_decl_pkg"),
        "raise RuntimeError('this declaration does not even run')\n",
    )
    entry = registry.tool_report(tmp_path, "broken_decl_pkg")["broken_decl_pkg"][0]
    assert "does not even run" in entry["error"]
    assert registry.verify(tmp_path, "broken_decl_pkg")["broken_decl_pkg"], (
        "a declaration that cannot be read is a defect of the package"
    )


# ── verifying the declaration ──────────────────────────────────────────────
def test_the_declared_requirement_is_checked_before_a_run_spends_budget():
    """Whatever the answer is here, it is available without running a mode."""
    report = registry.tool_report(None, "lean_pack")["lean_pack"]
    assert {entry["name"] for entry in report} == {"lean", "lake", "mathlib"}
    for entry in report:
        assert entry["present"] == (entry["fix"] == "")
    # `preflight` is the same answer as an exception, and it refuses only over the
    # tools a mode cannot run without.
    if HAS_LEAN:
        assert [status.name for status in toolchain.preflight("lean_pack")] == [
            "lean", "lake", "mathlib"
        ]
    else:
        with pytest.raises(toolchain.ToolMissing) as refused:
            toolchain.preflight("lean_pack")
        assert "'lean'" in str(refused.value)


def test_each_kind_of_tool_is_probed_by_what_it_is(tmp_path, monkeypatch):
    imported = toolchain.Tool(
        name="json", kind="python", probe="json", install="i", why="w"
    )
    assert toolchain.probe(imported).present

    absent = toolchain.Tool(
        name="nope", kind="python", probe="no_such_module_xyz", install="i", why="w"
    )
    assert not toolchain.probe(absent).present
    assert "not importable" in toolchain.probe(absent).detail

    binary = toolchain.Tool(
        name="sh", kind="binary", probe="sh", install="i", why="w",
        version_args=("--version",),
    )
    assert toolchain.probe(binary).present

    monkeypatch.delenv("REFACTORY_TEST_CHECKOUT", raising=False)
    checkout = toolchain.Tool(
        name="checkout", kind="path", probe="$REFACTORY_TEST_CHECKOUT",
        install="i", why="w",
    )
    unset = toolchain.probe(checkout)
    assert not unset.present and "REFACTORY_TEST_CHECKOUT is not set" in unset.detail

    monkeypatch.setenv("REFACTORY_TEST_CHECKOUT", str(tmp_path))
    assert toolchain.probe(checkout).present


def test_a_python_tool_is_required_through_the_same_reader_as_a_binary(tmp_path):
    """One mechanism, two kinds of tool, and the same shape of failure."""
    _workspace_package(
        tmp_path,
        "absent_module_pkg",
        MANIFEST.format(name="absent_module_pkg"),
        "from refactory_dsh.toolchain import Tool\n\n"
        "TOOLS = (Tool(name='absent_module', kind='python',\n"
        "              probe='no_such_module_xyz', install='pip install absent_module',\n"
        "              why='doing the absent thing'),)\n",
    )
    status = toolchain.require("sympy_pack", "sympy")
    assert status.present and status.tool.kind == "python"

    with pytest.raises(toolchain.ToolMissing) as refused:
        toolchain.require("absent_module_pkg", "absent_module", tmp_path)
    message = str(refused.value)
    assert "absent_module" in message and "pip install absent_module" in message
    assert "doing the absent thing" in message


# ── the absence path ───────────────────────────────────────────────────────
def test_the_catalogue_lists_a_package_whose_tool_is_absent(monkeypatch):
    """Absence must not make a package invisible; it must make it labelled."""
    monkeypatch.setenv("PATH", NO_TOOLS_PATH)
    listing = registry.render_catalogue(Path("."))
    assert "lean_pack/prover" in listing
    assert "needs lean" in listing
    assert registry.load("lean_pack").name == "lean_pack"


def test_a_mode_whose_tool_is_absent_fails_at_its_first_step(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", NO_TOOLS_PATH)
    result = _resolve_mode("lean_pack/prover").evaluate(tmp_path / "run")
    assert not result.exit_ok
    assert result.score == 0.0
    assert result.error is not None and "'lean'" in result.error
    assert "elan" in result.error, "the failure has to name the command that fixes it"
    assert result.iterations == 2, "the run must stop before it writes a project"
    assert not (tmp_path / "run" / "project").exists()


def test_the_local_problem_needs_no_library():
    """The core problem is why the package runs without a mathlib download."""
    core = lean_problems.by_library("core")
    assert core.preamble == "", "the local problem must import nothing at all"
    library = lean_problems.by_library("mathlib")
    assert library.library == "mathlib" and "Mathlib" in library.preamble


def test_every_problem_offers_every_strategy():
    """The strategy knob means the same thing for every candidate, or it is noise."""
    for problem in lean_problems.CATALOGUE:
        assert [name for name, _ in problem.proofs] == list(lean_problems.STRATEGIES)


# ── the Lean graph, without Lean ───────────────────────────────────────────
def test_the_graph_retries_until_the_checker_accepts(monkeypatch, tmp_path):
    """With a stub checker, the pipeline and its retry gate are exercised whole."""
    monkeypatch.setenv("PATH", f"{_stub_lean(tmp_path, accepts='induction')}:/usr/bin:/bin")
    result = _resolve_mode("lean_pack/prover").evaluate(tmp_path / "run")
    assert result.exit_ok, result.error
    assert result.facts["verified"] == 1
    assert result.facts["strategy_used"] == "induction", "the gate must rotate strategies"
    assert result.facts["attempts_used"] == 2
    assert result.score == 0.9, "a proof found on the second attempt is worth 0.9"

    written = json.loads((tmp_path / "run" / "result.json").read_text())
    assert written["verified"] is True
    assert written["checker"].endswith("Solution.lean")
    assert "stub: accepted" in written["output"]


def test_a_run_the_checker_rejects_scores_zero_without_failing(monkeypatch, tmp_path):
    """A rejected proof is a result, not a crash: the run reports what Lean said."""
    monkeypatch.setenv(
        "PATH", f"{_stub_lean(tmp_path, accepts='no-such-tactic')}:/usr/bin:/bin"
    )
    result = _resolve_mode("lean_pack/prover").evaluate(tmp_path / "run")
    assert result.exit_ok, "the run completed; the theorem is what did not"
    assert result.facts["verified"] == 0
    assert result.facts["attempts_used"] == 4, "every strategy the knob allows was tried"
    assert result.score == 0.0


def test_the_strategy_knob_is_what_moves_the_lean_score(monkeypatch, tmp_path):
    """The lever the search has to find, stated where a reviewer can check it."""
    monkeypatch.setenv("PATH", f"{_stub_lean(tmp_path, accepts='induction')}:/usr/bin:/bin")
    first = _resolve_mode("lean_pack/prover", strategy="induction").evaluate(tmp_path / "a")
    later = _resolve_mode("lean_pack/prover", strategy="decide").evaluate(tmp_path / "b")
    assert first.score == 1.0, "the working strategy is found on the first attempt"
    assert later.score < first.score
    assert later.facts["strategy_used"] == "induction"


# ── the real proof checker ─────────────────────────────────────────────────
@pytest.mark.toolchain("lean")
@needs_lean
def test_the_core_problem_is_checked_by_the_real_proof_checker(tmp_path):
    """End to end, with Lean deciding: no Python assertion about a theorem here."""
    result = _resolve_mode("lean_pack/prover").evaluate(tmp_path / "run")
    assert result.exit_ok, result.error
    written = json.loads((tmp_path / "run" / "result.json").read_text())
    assert result.facts["verified"] == 1, written["output"]
    assert written["used_sorry"] is False, "a `sorry` is not a proof"
    assert (tmp_path / "run" / "project" / "Problem.lean").exists()


def test_a_declared_tools_environment_is_carried_to_a_sandboxed_run(tmp_path):
    """A per-user toolchain is found through ``HOME``; a sandbox rewrites ``HOME``.

    Containment and reachability pull in opposite directions here, and containment
    alone silently wins: the tool is installed, works from a shell, and reports
    itself absent under the sandbox. The declaration is where the fix belongs,
    because it is the one place that knows which variable the tool needs.

    The declaration is synthetic so that this holds on a machine without Lean --
    the mechanism is what is under test, not one particular toolchain.
    """
    from refactory_dsh import sandbox

    present = shutil.which("sh")
    if present is None:  # pragma: no cover - posix only, as the sandbox is
        pytest.skip("no 'sh' on PATH to declare as a present tool")
    _workspace_package(
        tmp_path,
        "env_pack",
        "PACKAGE = None\n",
        "from refactory_dsh.toolchain import Tool\n"
        "TOOLS = (\n"
        "    Tool(name='shell', kind='binary', probe='sh', install='apt-get install sh',\n"
        "         why='running the thing', env=(('ELAN_HOME', '/declared/toolchain'),)),\n"
        "    Tool(name='gone', kind='binary', probe='no-such-binary-xyz',\n"
        "         install='apt-get install gone', why='the absent one',\n"
        "         env=(('ABSENT_TOOL_HOME', '/declared/absent'),)),\n"
        ")\n",
    )
    forwarded = toolchain.tool_env("env_pack", tmp_path)

    assert forwarded.get("ELAN_HOME") == "/declared/toolchain", (
        "a present tool's declared environment must be collected"
    )
    assert "ABSENT_TOOL_HOME" not in forwarded, (
        "an absent tool contributes nothing: forwarding for a tool that is not "
        "installed would only add environment a run cannot use"
    )

    probe = sandbox.run_sandboxed(
        "import json, os\nprint(json.dumps({'home': os.environ.get('HOME'),"
        " 'elan': os.environ.get('ELAN_HOME')}))\n",
        workdir=tmp_path / "run",
        forwarded=forwarded,
    )
    assert probe.ok, probe.problem()
    assert probe.payload["elan"] == "/declared/toolchain", "forwarded env did not arrive"
    assert probe.payload["home"] == str(tmp_path / "run"), (
        "containment must still rewrite HOME; forwarding adds to it, it does not replace it"
    )


@pytest.mark.toolchain("lean")
@needs_lean
def test_the_lean_declaration_names_the_toolchain_home():
    """The real declaration, checked on a machine that has the toolchain.

    The value is resolved where the declaration is read, so it is a real path and
    not the sandbox's own working directory.
    """
    forwarded = toolchain.tool_env("lean_pack", ROOT)
    assert forwarded.get("ELAN_HOME"), (
        "lean_pack must declare the variable elan finds its toolchains through, or "
        "the mode works from a shell and fails under the sandbox"
    )
    assert Path(forwarded["ELAN_HOME"]).is_dir(), forwarded["ELAN_HOME"]



def test_containment_alone_would_have_lost_it(tmp_path):
    """The half of the previous test that makes it mean something.

    Without forwarding, the same sandboxed probe sees no ``ELAN_HOME`` at all --
    which is the state the Lean mode was in when it scored 0.0 under the sandbox
    and 1.0 straight through.
    """
    from refactory_dsh import sandbox

    probe = sandbox.run_sandboxed(
        "import json, os\nprint(json.dumps({'elan': os.environ.get('ELAN_HOME')}))\n",
        workdir=tmp_path / "run",
    )
    assert probe.ok, probe.problem()
    assert probe.payload["elan"] is None, (
        "a scrubbed run must not see the caller's toolchain home unless a package "
        "declared it needs it"
    )


def test_a_secret_is_not_forwarded_by_a_declaration(tmp_path):
    """The secret filter is not waived for declarations."""
    from refactory_dsh import sandbox

    probe = sandbox.run_sandboxed(
        "import json, os\nprint(json.dumps({'seen': os.environ.get('AWS_SECRET_KEY')}))\n",
        workdir=tmp_path / "run",
        forwarded={"AWS_SECRET_KEY": "hunter2"},
    )
    assert probe.ok, probe.problem()
    assert probe.payload["seen"] is None, "a declaration must not smuggle a secret in"


@pytest.mark.toolchain("lean")
@needs_lean
def test_the_lean_mode_works_under_the_sandbox(tmp_path):
    """The shipped path, on a machine that has Lean. This is the regression.

    The mode scored 1.0 through the walker and 0.0 under ``sandbox.smoke`` on the
    same machine, because the sandbox rewrote ``HOME`` and elan keeps its
    toolchains under it. Two paths disagreeing about one mode is the defect; this
    asserts the sandboxed path, because that is the one that ships.
    """
    from refactory_dsh import sandbox

    result = sandbox.smoke("lean_pack/prover", tmp_path / "run", project=ROOT)
    assert result.ok, result.problem()
    assert result.payload["exit_ok"], result.payload
    assert result.payload["score"] > 0.0, (
        "the Lean mode scored nothing under the sandbox; its declared environment "
        f"is not reaching the checker: {result.payload}"
    )
    written = json.loads((tmp_path / "run" / "result.json").read_text())
    assert written["verified"] is True, written["output"]
    assert written["used_sorry"] is False


@pytest.mark.toolchain("lean")
@needs_lean
def test_a_sorry_is_not_a_proof(tmp_path):
    """The exit code alone would accept an unfinished proof; the axiom dump does not.

    Lean's ``sorry`` closes a goal and elaborates cleanly, so the check reads what
    the proof assumed as well as whether the file compiled. The op is called
    directly, because the point is this one decision rather than the pipeline.
    """
    project = tmp_path / "run" / "project"
    project.mkdir(parents=True)
    (project / "Solution.lean").write_text(
        "theorem hole : 1 + 1 = 3 := by sorry\n\n#print axioms hole\n"
    )
    ctx = nn.RunContext(tmp_path / "run", knobs={"timeout_s": 60})
    assert lean_ops.check_with_lean(ctx)["verified"] == 0
    recorded = json.loads((tmp_path / "run" / "verify.json").read_text())
    assert recorded["used_sorry"] is True
    assert "sorryAx" in recorded["output"]


# ── the sympy half ─────────────────────────────────────────────────────────
@pytest.mark.toolchain("sympy")
@needs_sympy
def test_the_python_tool_mode_establishes_identities_for_real(tmp_path):
    """sympy does the mathematics; the run's number is how many checked out."""
    seed = _resolve_mode("sympy_pack/exact").evaluate(tmp_path / "seed")
    assert seed.exit_ok, seed.error
    assert seed.score == 0.0, "the seed checks one conjecture per identity, and it is wrong"

    widened = _resolve_mode("sympy_pack/exact", candidates=4).evaluate(tmp_path / "wide")
    assert widened.exit_ok, widened.error
    assert widened.score == 3.0
    written = json.loads((tmp_path / "wide" / "result.json").read_text())
    assert written["established"] == [
        "n*(n+1)/2", "n*(n+1)*(2*n+1)/6", "n**2*(n+1)**2/4"
    ]


@pytest.mark.toolchain("sympy")
@needs_sympy
def test_a_weaker_reduction_route_misses_answers_that_are_there(tmp_path):
    """`route` is a real axis: a sound reduction that does not collect the terms.

    It can never accept a wrong closed form -- every route here is sound -- so the
    lower score is a missed answer rather than a false proof.
    """
    exact = _resolve_mode("sympy_pack/exact", candidates=4).evaluate(tmp_path / "a")
    weak = _resolve_mode(
        "sympy_pack/exact", candidates=4, route="together"
    ).evaluate(tmp_path / "b")
    assert weak.score < exact.score
    assert weak.score >= 1.0, "it still establishes the identity it can reduce"


@pytest.mark.toolchain("sympy")
@needs_sympy
def test_the_outer_loop_improves_the_sympy_run(tmp_path):
    """The seed has a real defect and the search fixes it, through the real engine."""
    loop = Loop(
        "sympy_pack/exact",
        LoopConfig(
            epochs=1, batch_size=4, optimizer="random", seed=5,
            edit_preset="knob_only", runs_root=tmp_path / "runs",
        ),
    )
    report = loop.run()
    # The compared score has the run's size cost taken off it; this preset moves knobs
    # only, so the seed's raw score is the number the mode's own `score()` returns.
    assert report["seed_raw_score"] == 0.0
    assert report["best_score"] > report["seed_score"], (
        "widening `candidates` must establish identities the seed could not"
    )


# ── shared properties of both packages ─────────────────────────────────────
@pytest.mark.parametrize("identifier", ["lean_pack/prover", "sympy_pack/exact"])
def test_validate_state_is_clean_for_every_math_mode(identifier):
    mode = _resolve_mode(identifier)
    issues = nn.validate_state(
        mode.state_dict(), invariants=mode.invariants(), resolve_calls=True
    )
    assert issues == [], [str(issue) for issue in issues]
    assert mode.is_scored() and mode.parameters()


@pytest.mark.parametrize("identifier", ["lean_pack/prover", "sympy_pack/exact"])
def test_the_score_reads_a_run_fact_and_never_a_knob(identifier):
    """A score over a knob would make optimizing circular, so it is held here."""
    mode = _resolve_mode(identifier)
    assert mode.score(nn.RunResult(facts={"verified": 1, "attempts_used": 1})) == 1.0
    assert mode.score(nn.RunResult(facts={"verified": 0, "attempts_used": 1})) == 0.0

    # The same facts under different knobs must score the same, which is what
    # "reads the run, not the input" means in practice.
    moved = _resolve_mode(identifier, **{
        name: (knob.enum[-1] if knob.kind == "enum" else knob.hi)
        for name, knob in mode.parameters().items()
    })
    assert moved.values() != mode.values()
    assert moved.score(nn.RunResult(facts={"verified": 1, "attempts_used": 1})) == 1.0


def test_the_registry_reports_the_toolchain_over_its_command_line(capsys, tmp_path):
    assert registry.main(["tools", "--project", str(tmp_path)]) in (0, 1)
    payload = json.loads(capsys.readouterr().out)
    assert {entry["name"] for entry in payload["lean_pack"]} == {"lean", "lake", "mathlib"}
    assert payload["sympy_pack"][0]["present"] is HAS_SYMPY


# ── the mathlib problem ────────────────────────────────────────────────────
def test_the_mathlib_statement_parses_the_way_this_mathlib_reads_it():
    """A syntax regression guard that needs neither Lean nor mathlib.

    The statement first read `∑ i in Finset.range n, ...`, which is the form most
    introductions show and which this Mathlib rejects: Lean reads `∑ i` as a binder
    and reports `unexpected token 'in'; expected ','`. Every test that used a stub
    passed, so nothing noticed until the statement was run through the real
    checker. This pins the form that does parse, so it cannot drift back.
    """
    problem = lean_problems.by_library("mathlib")
    assert "∈" in problem.statement, (
        "the mathlib statement must use the `∑ i ∈ s, f` form; the `in` form does "
        "not parse against this Mathlib"
    )
    assert " in Finset" not in problem.statement, (
        "the `in` binder notation is back, and it does not parse"
    )
    assert "BigOperators" in problem.preamble, (
        "the summation notation is not opened by the preamble"
    )
    source = lean_problems.statement_source(problem)
    assert "theorem" in source and "sorry" in source, (
        "the statement file must declare the theorem and leave it open"
    )


def test_the_timeout_default_survives_importing_a_large_library():
    """A timeout that abandons the checker reads exactly like a refuted theorem.

    At 60s the mathlib problem scored 0.0 on a machine where the theorem is
    provable and the proof took 22s warm, because the first invocation pays for
    importing the library. The default has to clear that cost.
    """
    mode = _resolve_mode("lean_pack/prover")
    timeout = mode.state_dict()["knobs"]["timeout_s"]
    assert timeout["value"] >= 180, (
        f"the default timeout is {timeout['value']}s, which is under the cost of "
        "importing mathlib once"
    )


@pytest.mark.toolchain("mathlib")
@needs_mathlib
def test_the_mathlib_problem_is_checked_by_the_real_proof_checker(tmp_path):
    """The path that had never run, run for real.

    Both defects this covers were invisible without mathlib installed: the
    statement did not parse, and the timeout abandoned the checker before it
    answered. A stub cannot find either, because a stub accepts whatever it is
    handed and returns immediately.
    """
    result = _resolve_mode("lean_pack/prover", library="mathlib").evaluate(tmp_path / "run")
    assert result.exit_ok, result.error
    assert result.facts["statement_ok"] == 1, "the statement did not elaborate"
    assert result.facts["verified"] == 1, "the proof was not accepted by mathlib"

    written = json.loads((tmp_path / "run" / "result.json").read_text())
    assert written["library"] == "mathlib"
    assert written["verified"] is True, written["output"]
    assert written["used_sorry"] is False
    assert "sorryAx" not in (written["output"] or ""), written["output"]
    assert written["checker_version"], (
        "the record does not name the compiler that decided"
    )
