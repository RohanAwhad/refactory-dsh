"""Independent graph contract and real native portable operation boundaries."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from refactory_dsh.create_fidelity import CreateFidelityMode
from refactory_dsh import create_fidelity_ops as ops
from refactory_dsh.create_fidelity_prompts import reference, load_prompt
from refactory_dsh.nn import Graph, Executor, Node, RunContext, execute, validate_state

FIXTURE = Path(__file__).parent / "fixtures/create_fidelity/v1/reference.json"


def test_exact_source_topology_roles_gates_and_original_artifact_contracts():
    source = json.loads(FIXTURE.read_text())
    mode = CreateFidelityMode()
    graph = mode.graph()
    assert graph.start == source["graph"]["start_node"]
    assert len(graph.nodes) == source["node_count"] == 29
    assert len(graph.edges) == source["edge_count"] == 33
    assert set(graph.nodes) == set(source["graph"]["nodes"])
    assert {edge.key() for edge in graph.edges} == {(e["source"], e["target"], e["condition"] or "") for e in source["graph"]["edges"]}
    for identifier, original in source["graph"]["nodes"].items():
        node = graph.nodes[identifier]
        assert set(original["reads"]) <= set(node.reads)
        assert set(original["writes"]) <= set(node.writes)
        assert node.blocking == original["blocking"]
        if original.get("timeout"):
            assert node.timeout_s == original["timeout"]
        if original.get("role"):
            assert node.role == original["role"]
        if original.get("evaluator_type") in ("agent", "user"):
            assert node.gate_kind == original["evaluator_type"]
            assert original["gate_prompt"] in node.prompt_template
        if identifier.endswith("_director"):
            assert node.kind == "op"
            assert node.call.endswith(":" + identifier)
            assert node.timeout_s == 3600
    assert graph.nodes["fork_qa"].targets == graph.nodes["join_qa"].sources
    assert not validate_state(mode.state_dict(), invariants=mode.invariants())


def test_production_prompts_preserve_full_source_and_native_mapping():
    frozen = json.loads(FIXTURE.read_text())
    shipping = reference()
    assert shipping["source_commit"] == frozen["source_commit"]
    assert shipping["source_graph_sha256"] == frozen["graph_sha256"]
    for original_path, original_text in frozen["role_prompts"].items():
        role = Path(original_path).stem
        assert original_text in load_prompt(role)
    for role in ("builder", "health_checker", "code_reviewer", "researcher", "strategist"):
        prompt = load_prompt(role, role if role not in ("researcher", "strategist") else "research_director")
        assert "No score override or Task" not in prompt  # supplied by real native API at render time
        assert "artifact-only local authoring run" in prompt
        assert "runtime knobs" in prompt


def test_substantive_input_delivery_preserves_template_looking_data_bytes(tmp_path):
    ctx = RunContext(tmp_path, facts={"name": "authored_name"})
    raw = "# Complete child evidence\n\nCode example keeps literal {{name}} and {{native_api}} as input data.\n"
    ctx.write_text("report.md", raw)
    node = Node("synthesis", "agent", prompt_template="Author {{name}} from actual evidence", reads=("report.md",), writes=("strategy.md",))
    prompt = ops.agent_prompt(ctx, node)
    assert prompt.startswith("Author authored_name from actual evidence")
    assert raw in prompt
    assert "literal authored_name" not in prompt


def test_unsupported_variant_rejected_before_any_dispatch(tmp_path):
    class NoCalls:
        def complete(self, *args, **kwargs):
            pytest.fail("unsupported variant must fail before model I/O")
    for variant in ("plugin", "update-existing", "provider-swap"):
        workdir = tmp_path / variant
        result = execute(CreateFidelityMode().graph(), workdir,
                         knobs={"name": "portable_test", "brief": "Produce a reusable local mode", "variant": variant}, model=NoCalls())
        assert not result.exit_ok
        assert "unsupported" in result.error
        assert not (workdir / ".factory/create-request.json").exists()


class BootstrapIO:
    """Only external model responses are scripted; real graph/ops/delivery run."""
    def __init__(self):
        self.calls = []
    def complete(self, prompt, *, system="", workdir=None):
        contract = json.loads(prompt.split("<!-- refactory worker execution -->", 1)[1].split("<!-- end refactory worker execution -->", 1)[0].split("\n", 2)[2])
        node = contract["node"]
        self.calls.append(node)
        output = Path(contract["outputs"][0])
        if node == "create_factory_md":
            output.write_text("# Factory\n\n## Goal\nAuthor the reusable requested package.\n\n## Scope\nNative local artifacts.\n\n## Guards\nPreserve user intent.\n")
        elif node == "graph_explorer":
            output.write_text("# Graph context\n\nThe project main.py module imports json and defines main. The module is a direct CLI entry point. This conclusion is grounded in graph.json and the inspected source.\n")
        else:
            pytest.fail("unexpected bootstrap model node " + node)
        return "Delivered the requested artifact."


def test_bootstrap_crosses_real_branch_study_and_delivery_boundaries(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("import json\ndef main():\n    return json.dumps({'ready': True})\n")
    run = tmp_path / "run"
    original = CreateFidelityMode().graph()
    identifiers = {"init_user_intent", "gate_has_factory", "discover", "gate_factory_md_exists", "create_factory_md", "factory_init", "graph_update", "study", "graph_explorer", "concat_study"}
    graph = Graph.from_nodes([n for n in original.nodes.values() if n.id in identifiers], [e for e in original.edges if e.source in identifiers and e.target in identifiers], original.start)
    model = BootstrapIO()
    ctx = RunContext(run, knobs={"name": "portable_test", "brief": "Read a runtime JSON file and produce a reusable output", "project_path": str(source)}, model=model)
    executor = Executor(graph, ctx)
    result = executor.run()
    assert result.exit_ok, result.error
    assert result.facts["create_fidelity"] is True
    assert model.calls == ["create_factory_md", "graph_explorer"]
    assert [(step.node, step.verdict) for step in executor.steps if step.kind == "gate"] == [("gate_has_factory", "halt"), ("gate_factory_md_exists", "halt")]
    knowledge = json.loads((run / "graph.json").read_text())
    assert any(n["id"] == "main:main" for n in knowledge["nodes"])
    assert any(e["source"] == "main" and e["target"] == "json" for e in knowledge["edges"])
    assert "direct CLI entry point" in (run / ".factory/strategy/study-combined.md").read_text()
    assert (run / ".refactory/agent-delivery").is_dir()
    assert not (source / "factory.md").exists()
    assert not (source / ".factory").exists()


def make_package(project: Path):
    root = project / ".factory/packages/portable_probe"
    root.mkdir(parents=True, exist_ok=True)
    (root / "__init__.py").write_text("from refactory_dsh import nn\nfrom .mode import Probe\nPACKAGE=nn.Package(name='portable_probe',description='Read a runtime document',modes=(Probe,),ops=('refactory_packages.portable_probe.ops',))\n")
    (root / "mode.py").write_text("""from refactory_dsh import nn
class Probe(nn.Mode):
    name='read'
    description='Copy runtime JSON through the native invocation'
    unscored_reason='Produces a document with no optimization metric'
    @classmethod
    def declare(cls):
        return {'document_path': nn.Knob('', 'str', description='Runtime JSON file')}
    def build(self):
        g=nn.Graph()
        g.add(nn.Node('read','op',call='refactory_packages.portable_probe.ops:read',knobs=('document_path',),writes=('result.json',)))
        return g
    def invariants(self):
        return (nn.artifact_produced('result.json',by='read'),)
""")
    (root / "ops.py").write_text("""import json
from pathlib import Path
def read(ctx):
    data=json.loads(Path(ctx.knob('document_path')).read_text())
    if not isinstance(data, dict): raise ValueError('input must be an object')
    ctx.write_json('result.json',data)
""")
    (root / "README.md").write_text("# Portable probe\n\nSupply document_path at runtime; each native invocation writes result.json under its own output directory.\n")
    return root


def test_real_native_admission_export_and_two_distinct_invocations(tmp_path):
    project = tmp_path / "artifact-project"
    root = make_package(project)
    report = ops.admit_native_package(project, "portable_probe", requires_input=("document_path",))
    assert report["ok"], report
    assert report["modes"][0]["id"] == "portable_probe/read"
    assert report["modes"][0]["unscored_reason"]
    assert (root / "SKILL.md").is_file()
    assert "document_path" in (root / "SKILL.md").read_text()
    before = ops.package_digest(root)
    outputs = []
    for i in (1, 2):
        source = tmp_path / f"input-{i}.json"
        source.write_text(json.dumps({"value": i}))
        workdir = tmp_path / f"invocation-{i}"
        result = ops.run_native_mode(project, "portable_probe/read", workdir, {"document_path": str(source)})
        assert result["exit_ok"], result
        assert result["raw_evidence"]["returncode"] == 0
        outputs.append(json.loads((workdir / "result.json").read_text()))
    assert outputs == [{"value": 1}, {"value": 2}]
    assert ops.package_digest(root) == before
    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]")
    failed_run = tmp_path / "invalid-invocation"
    result = ops.run_native_mode(project, "portable_probe/read", failed_run, {"document_path": str(invalid)})
    assert not result["exit_ok"]
    assert "input must be an object" in result["error"]
    assert not (failed_run / "result.json").exists()


def test_native_validator_refuses_unfeedable_input_broken_calls_and_stale_precheck(tmp_path):
    root = make_package(tmp_path)
    required = ops.validate_native_package(tmp_path, "portable_probe", requires_input=("missing_input",))
    assert not required["ok"]
    assert any("missing runtime input" in p for p in required["problems"])
    admitted = ops.admit_native_package(tmp_path, "portable_probe")
    assert admitted["ok"]
    (root / "ops.py").write_text((root / "ops.py").read_text() + "\n# Package modified after admission\n")
    ctx = RunContext(tmp_path, facts={"name": "portable_probe", "artifact_project": str(tmp_path), **{g + "_verdict": "proceed" for g in ("gate_build", "gate_qa", "gate_overwatch", "gate_doc_freshness")}})
    assert ops.gate_precheck(ctx) == "halt"
    assert ctx.facts["create_fidelity_precheck_ok"] is False
    assert "stale" in ctx.facts["create_fidelity_precheck_error"]
    (root / "mode.py").write_text((root / "mode.py").read_text().replace("ops:read", "ops:missing"))
    broken = ops.validate_native_package(tmp_path, "portable_probe")
    assert not broken["ok"]
    assert any("missing" in p for p in broken["problems"])


def test_synthesis_keeps_full_reports_and_promotes_independent_duplicate_issues(tmp_path):
    ctx = RunContext(tmp_path)
    reports = []
    issue = "The native CLI is missing the required runtime document_path option and violates the original acceptance contract."
    for i, task_id in enumerate(("custom-one", "custom-two", "workflow-validate", "cli-integration")):
        body = f"# {task_id}\n\nActual command: validation check {i}\nActual output: specific original evidence {i}.\n" + (issue if i < 2 else "Native graph and discovery checks passed with command evidence.")
        output = f".factory/reviews/testers/{task_id}.md"
        ctx.write_text(output, body)
        reports.append({"task_id": task_id, "status": "completed", "body": body, "output": output, "output_digest": hashlib.sha256(body.encode()).hexdigest()})
    ctx.write_json(".factory/qa_director-findings.json", {"status": "completed", "reports": reports})
    ctx.write_text(".factory/qa_director-findings.md", "\n\n".join(report["body"] for report in reports))
    for review in ("health-check.md", "code-review.md"):
        ctx.write_text(".factory/reviews/" + review, "# Review\n\nActual evidence: inspected runtime input surface and command output, identifying the required missing option.\n")
    ops.synthesize_qa(ctx)
    output = ctx.read_text(".factory/reviews/qa-synthesized.md")
    assert "HIGH (custom-one, custom-two)" in output
    assert all(report["body"] in output for report in reports)
    ctx.write_text(reports[0]["output"], "modified stale report")
    with pytest.raises(ValueError, match="stale"):
        ops.synthesize_qa(ctx)
