"""The authoring API's example must resolve in the generated-package namespace."""

from importlib import import_module
from textwrap import dedent

import pytest
import refactory_packages

from refactory_dsh import create_ops, nn, registry
from refactory_dsh.nn.resolve import resolve


@pytest.fixture
def example_package(tmp_path, monkeypatch):
    # Compile the supplied example itself, without correcting its import strings.
    source = dedent(create_ops.API.split("\n\n", 1)[1].split("\nFor required", 1)[0])
    first = dedent(
        create_ops.API.split("An operation is a plain function of the run context:\n\n", 1)[1]
        .split("\nThe context", 1)[0]
    )
    package_dir = registry.workspace_root(tmp_path) / "my_pkg"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text(source)
    (package_dir / "ops.py").write_text(
        first + "\n" + dedent("""\
        def check(ctx):
            return "proceed"

        def done(ctx):
            return {"best": ctx.read_json("a.json")["value"]}

        def observe(ctx):
            return {"spread": 0}
        """)
    )
    monkeypatch.setattr(refactory_packages, "__path__", list(refactory_packages.__path__))
    previous = getattr(refactory_packages, "my_pkg", None)
    try:
        yield registry.load("my_pkg", tmp_path), package_dir
    finally:
        registry._evict_workspace("my_pkg", tmp_path)
        if previous is None:
            vars(refactory_packages).pop("my_pkg", None)
        else:
            refactory_packages.my_pkg = previous


def test_api_manifest_imports_generated_operation_module(example_package):
    package, package_dir = example_package
    assert package.ids() == ["my_pkg/shortname"]
    assert len(package.ops) == 1
    assert import_module(package.ops[0]).__file__ == str(package_dir / "ops.py")


@pytest.mark.parametrize("node_id", ["first", "check", "done", "observe"])
def test_api_node_references_resolve_in_workspace(example_package, node_id, tmp_path):
    package, _ = example_package
    mode = package.mode("shortname")()
    nodes = {**mode.graph().nodes, **{node.id: node for node in mode.insertable()}}
    node = nodes[node_id]
    operation = resolve(node.call or node.evaluator)
    assert operation.__name__ == node_id
    if node_id == "first":
        ctx = nn.RunContext(tmp_path, knobs={"rounds": 5}, facts={})
        assert operation(ctx) == {"a": 5}
        assert ctx.read_json("a.json") == {"value": 5}
