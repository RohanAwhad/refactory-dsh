"""Source-derived timing contract and actual transport receipt verification."""
import json
import sys
from pathlib import Path

import pytest

from benchmarks.create_fidelity.deadline_evidence import dispatch_deadline_row, source_idle_allowance
from refactory_dsh.create_fidelity import CreateFidelityMode
from refactory_dsh.create_fidelity_timing import configure_client
from refactory_dsh.lowering import node_declarations, to_workflow
from refactory_dsh.model import DshModelClient
from refactory_dsh.workers import lower_workflow

FIXTURES = Path(__file__).parent / "fixtures/create_fidelity/v1"


def test_all_source_owners_keep_idle_precedence_and_separate_wall(tmp_path):
    pinned = json.loads((FIXTURES / "source-timing.json").read_text())
    reference = json.loads((FIXTURES / "reference.json").read_text())
    assert pinned["source_commit"] == reference["source_commit"]
    assert pinned["source_graph_sha256"] == reference["graph_sha256"]
    assert "max_timeout: float = 14400.0" in pinned["source_sections"]["subprocess_signature"]["text"]
    state = CreateFidelityMode().state_dict()
    declarations = node_declarations(state)
    workflow = lower_workflow(to_workflow(state), tmp_path, DshModelClient().context(), declarations)
    for node, expected in {**pinned["agent_nodes"], **pinned["agent_gates"]}.items():
        timing = declarations[node]["source_timing"]
        assert timing == {"policy": pinned["policy"], "wall_timeout_s": pinned["source_hard_wall_s"],
                          "inactivity_timeout_s": expected["idle_timeout_s"]}
        assert source_idle_allowance(node, expected["role"]) == expected["idle_timeout_s"]
        assert workflow.nodes[node].command_timeout_s >= pinned["source_hard_wall_s"]
    for stage, role in (("research_director", "researcher"), ("strategy_director", "strategist"),
                        ("qa_director", "adversarial_tester")):
        assert source_idle_allowance(stage + ":v1:child:a1", role) == pinned["director_child_cli_idle_s"]


@pytest.fixture
def actual_transport(tmp_path):
    cli = tmp_path / "controlled-cli"
    cli.write_text(f"#!{sys.executable}\nprint('actual complete output line')\n")
    cli.chmod(0o700)
    client = configure_client(DshModelClient(dsh_cli=str(cli), attempts=1).for_node(
        "graph_explorer", role="researcher"), 600)
    assert client.complete("Controlled local transport; no external model.", workdir=tmp_path)
    path = next((tmp_path / ".refactory/workers").glob("*.json"))
    return [(str(path), json.loads(path.read_text()))]


def test_actual_transport_records_pass_independent_clock_validation(actual_transport):
    score = dispatch_deadline_row(actual_transport)
    assert score["status"] == "passed", score


@pytest.mark.parametrize("mutation", ["missing", "policy", "idle", "wall", "activity", "time", "early-expiry"])
def test_incomplete_or_forged_activity_never_passes(actual_transport, mutation):
    worker = actual_transport[0][1]
    record = worker["transport_timing"][0]
    if mutation == "missing":
        del worker["transport_timing"]
    elif mutation == "policy":
        record["policy"] = "invented"
    elif mutation == "idle":
        record["inactivity_timeout_s"] = 1
    elif mutation == "wall":
        record["wall_timeout_s"] = 20000
    elif mutation == "activity":
        record["activity_observations"] = 100000
    elif mutation == "time":
        record["last_activity_unix"] = record["finished_unix"] + 1
    else:
        record["end_reason"] = "idle"
    assert dispatch_deadline_row(actual_transport)["status"] == ("unavailable" if mutation == "missing" else "failed")
