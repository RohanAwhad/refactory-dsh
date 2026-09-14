"""What a mode declares about itself: its memory with retention, and its ports.

The authoring-surface issue: `srf/packaging` can declare memory with a retention
policy and the ports a mode publishes; we had no equivalent, so cross-run memory was
an ad-hoc file and a run's outputs were inferred from file writes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from refactory_dsh import nn
from refactory_dsh.memory import MemoryStore
from refactory_dsh.nn import Memory
from refactory_dsh.memory import MemoryError_ as MemoryFailure
from refactory_dsh.loop import Loop, LoopConfig

MODE = "circle_packing/adaptive"


# ── the declarations ───────────────────────────────────────────────────────
def test_a_memory_namespace_must_be_named_and_known():
    with pytest.raises(nn.DeclarationError, match="namespace"):
        nn.Memory("Bad Name")
    with pytest.raises(nn.DeclarationError, match="unknown memory kind"):
        nn.Memory("a.b", kind="nonsense")
    with pytest.raises(nn.DeclarationError, match="unknown retention"):
        nn.Memory("a.b", retention="forever")


def test_a_port_must_name_a_path_and_a_type():
    with pytest.raises(nn.DeclarationError, match="no path"):
        nn.Port("best", "")
    with pytest.raises(nn.DeclarationError, match="media type"):
        nn.Port("best", "best.json", media_type="")
    with pytest.raises(nn.DeclarationError, match="valid name"):
        nn.Port("Best Result", "best.json")


def test_declarations_survive_a_spec_round_trip():
    for item in (nn.Memory("a.b", "log", "project"), nn.Port("best", "best.json")):
        assert type(item).from_state(item.to_state()) == item


# ── the store, and retention ───────────────────────────────────────────────
def _store(tmp_path: Path, retention: str, *, run: str = "run-a") -> MemoryStore:
    return MemoryStore(
        [nn.Memory("m.things", "log", retention)],
        run_root=tmp_path / "runs" / run,
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
    )


def test_a_run_memory_is_scoped_to_its_own_run(tmp_path):
    first = _store(tmp_path, "run", run="run-a")
    first.append("m.things", {"score": 1})
    assert first.read("m.things") == [{"score": 1}]

    other = _store(tmp_path, "run", run="run-b")
    assert other.read("m.things") == [], (
        "a run-retention memory leaked into another run"
    )


def test_a_project_memory_is_visible_to_the_next_run(tmp_path):
    _store(tmp_path, "project").append("m.things", {"score": 2})
    assert _store(tmp_path, "project", run="run-b").read("m.things") == [{"score": 2}]


def test_a_global_memory_is_visible_too(tmp_path):
    _store(tmp_path, "global").append("m.things", {"score": 3})
    assert _store(tmp_path, "global", run="run-b").read("m.things") == [{"score": 3}]


def test_forgetting_is_explicit_and_names_what_it_dropped(tmp_path):
    store = _store(tmp_path, "run")
    store.append("m.things", 1)
    assert store.forget_run_scoped() == ["m.things"]
    assert store.read("m.things") == []
    assert store.forget_run_scoped() == [], "forgetting twice should be quiet"


def test_a_project_memory_is_not_forgotten_with_the_run(tmp_path):
    store = _store(tmp_path, "project")
    store.append("m.things", 1)
    assert store.forget_run_scoped() == []
    assert store.read("m.things") == [1]


def test_a_kind_must_be_used_as_declared(tmp_path):
    store = MemoryStore([nn.Memory("m.log", "log", "run")], run_root=tmp_path)
    with pytest.raises(MemoryFailure, match="not a map"):
        store.set("m.log", "k", 1)
    with pytest.raises(MemoryFailure, match="not a value"):
        store.write("m.log", 1)


def test_an_undeclared_namespace_is_refused(tmp_path):
    store = MemoryStore([nn.Memory("m.log", "log", "run")], run_root=tmp_path)
    with pytest.raises(MemoryFailure, match="was not declared"):
        store.read("m.other")


def test_a_map_memory_keeps_keys(tmp_path):
    store = MemoryStore([nn.Memory("m.map", "map", "project")], run_root=tmp_path,
                        project_root=tmp_path / "p")
    store.set("m.map", "grid", 18)
    assert store.get("m.map", "grid") == 18
    assert store.get("m.map", "missing", "fallback") == "fallback"


# ── ports are checked against the graph ────────────────────────────────────
class Ported(nn.Mode):
    name = "ported"

    @classmethod
    def declare(cls) -> dict[str, nn.Knob]:
        return {}

    def build(self) -> nn.Graph:
        graph = nn.Graph()
        graph.add(nn.Node("only", "op", call="loop_ops:write_best",
                          writes=("best.json",), produces=("best",)))
        return graph

    def score(self, run: nn.RunResult) -> float:
        return float(run.facts.get("best", 0.0))

    def ports(self) -> tuple[nn.Port, ...]:
        return (nn.Port("best", "best.json", "application/json"),)

    def invariants(self) -> tuple[nn.Invariant, ...]:
        return (nn.fact_produced("best"),)


class LyingPorted(Ported):
    def ports(self) -> tuple[nn.Port, ...]:
        return (nn.Port("missing", "never-written.json"),)


def test_a_declared_port_that_no_node_writes_is_refused():
    state = LyingPorted().state_dict()
    issues = nn.validate_state(state)
    named = [i for i in issues if i.code == "V11"]
    assert named, [str(i) for i in issues]
    assert "never-written.json" in named[0].message


def test_a_declared_port_that_a_node_writes_is_accepted():
    state = Ported().state_dict()
    assert nn.validate_state(state) == []


def test_ports_and_memory_travel_in_the_state():
    state = Ported().state_dict()
    assert state["ports"][0]["name"] == "best"
    assert "memory" in state
    assert Ported(state=state).state_dict() == state


def test_the_report_names_what_the_mode_publishes(tmp_path):
    report = Loop(
        MODE,
        LoopConfig(epochs=1, batch_size=2, optimizer="random", seed=1,
                   runs_root=tmp_path / "runs", memory_global_root=tmp_path / "global"),
        project=Path("."),
    ).run()
    assert "ports" in report and "memory" in report
    markdown = (tmp_path / "runs" / "report.md").read_text()
    assert "## What the mode publishes" in markdown
    assert "## What the mode may remember" in markdown


def test_an_inner_run_records_which_ports_it_produced(tmp_path):
    loop = Loop(
        MODE,
        LoopConfig(epochs=1, batch_size=2, optimizer="random", seed=1,
                   runs_root=tmp_path / "runs", memory_global_root=tmp_path / "global"),
        project=Path("."),
    )
    loop.init()
    loop.forward()
    for candidate in loop.population:
        if not candidate.evaluated:
            continue
        payload = __import__("json").loads(
            (tmp_path / "runs" / "runs" / candidate.id / "candidate.json").read_text()
        )
        assert "ports" in payload


def test_the_validator_rules_cover_the_new_port_rule():
    assert "V11" in nn.RULES
    assert "port" in nn.RULES["V11"]


# ── retention is behaviour, not a declaration ──────────────────────────────
#
# Issue #24 declared three retention policies and enforced none of them:
# `MemoryStore.forget_run_scoped` existed and was called nowhere, so a `run`-
# scoped namespace accumulated exactly like a project one. And memory did not
# cross to DSH at all — the lowering dropped it and the node runner built the
# context without a store — so a mode that keeps a cross-run prior worked in the
# walker and quietly kept nothing on the runtime that ships.


def test_a_run_scoped_memory_is_gone_when_the_inner_run_ends(tmp_path):
    """The policy's whole content: it dies with the run it was scoped to."""
    declaration = Memory("scratch", kind="log", retention="run")
    store = MemoryStore([declaration], run_root=tmp_path / "runs" / "abc")
    store.append("scratch", {"tried": 1})
    assert store.path("scratch").exists(), "the write did not land"

    forgotten = store.forget_run_scoped()
    assert forgotten == ["scratch"], forgotten
    assert not store.path("scratch").exists(), (
        "a run-scoped memory survived the run, so it is not run-scoped"
    )


def test_forgetting_leaves_project_and_global_memory_alone(tmp_path):
    declarations = [
        Memory("per_run", retention="run"),
        Memory("per_project", retention="project"),
        Memory("everywhere", retention="global"),
    ]
    store = MemoryStore(
        declarations,
        run_root=tmp_path / "run",
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
    )
    for namespace in ("per_run", "per_project", "everywhere"):
        store.append(namespace, {"seen": namespace})

    assert store.forget_run_scoped() == ["per_run"]
    assert not store.path("per_run").exists()
    for survivor in ("per_project", "everywhere"):
        assert store.path(survivor).exists(), f"{survivor} was forgotten too"


def test_the_loop_forgets_run_scoped_memory_after_every_candidate(tmp_path):
    """The enforcement point. Without it a resumed run reads its own leftovers."""
    import inspect
    from refactory_dsh.loop import Loop

    source = inspect.getsource(Loop._evaluate)
    assert "forget_run_scoped" in source, (
        "the inner run ends without applying the run-retention policy"
    )
    assert "finally" in source, (
        "it must be applied when the run crashes or is stopped, not only on success"
    )


def test_memory_declarations_travel_to_the_runtime_that_ships(tmp_path):
    """A mode's declared memory must survive the lowering, or DSH silently lacks it."""
    from refactory_dsh import lowering

    state = {
        "version": 1,
        "graph": {"start": "a", "nodes": {"a": {"id": "a", "kind": "op", "call": "m:f",
                  "reads": [], "writes": [], "produces": [], "knobs": [],
                  "evaluator": "", "prompt_template": "", "role": "ceo", "tools": [],
                  "max_iterations": 1, "notes": "", "blocking": True,
                  "score_effect": "affects"}}, "edges": []},
        "knobs": {},
        "ports": [],
        "memory": [{"namespace": "priors", "kind": "log", "retention": "project",
                    "description": "what previous runs found"}],
    }
    lowering.write_declarations(state, tmp_path)
    read_back = lowering.read_memory_declarations(tmp_path)
    assert [m.namespace for m in read_back] == ["priors"], (
        "the lowering dropped the mode's memory, so DSH would run without it"
    )
    assert read_back[0].retention == "project"


def test_the_node_runner_builds_a_store_from_those_declarations(tmp_path):
    """Declaring it is not enough; the runtime has to hand it to the op."""
    import inspect
    from refactory_dsh import run as runner

    assert "_memory_store" in inspect.getsource(runner), (
        "the node runner builds no memory store, so ops see None on DSH"
    )
