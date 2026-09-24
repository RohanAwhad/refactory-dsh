"""The java_build_pack harness: one class of Java source trees, scored per item.

The package is a shipped example of the product's canonical claim, "build me a harness that writes the build config for any Java package", so these tests drive it offline with `FakeModel` on every item its Task lists. They check the two things the rest of the system demands of a harness for a class of work: the Task's per-item verdicts land in `item_scores`, and `score()` is their mean. They also check the input port stays open, because a harness whose items are baked into the graph is a harness for exactly those items.
"""

from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path

import pytest

import refactory_packages.java_build_pack as shipped
from refactory_dsh import create_ops, registry
from refactory_dsh.nn import RunResult
from refactory_dsh.task import TaskInstance
from refactory_packages.java_build_pack.mode import TASK, BuildPack
from refactory_packages.java_build_pack.tasks import JavaBuildTask, java_bin
from tests_support import FakeModel

PACKAGE = "java_build_pack"

#: The shipped package directory, which is what a person copies when they point the
#: checks at a project of their own.
SOURCE = Path(shipped.__file__).resolve().parent

#: The three shipped trees, one per shape the class of work has to cover.
SHIPPED_IDS = ("args", "flat", "nested")


def _stub_script(instance: TaskInstance) -> str:
    """A script that prints the recorded output without compiling anything.

    The scoring and aggregation tests must not depend on a JDK, so the model client
    answers with a script whose output is already the expected one.
    """
    expected = (Path(instance.path or "") / "expected.txt").read_text().strip()
    return f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(expected)}\n"


def _javac_script(instance: TaskInstance, release: int = 17) -> str:
    """A real build script: compile every source with javac, then run the entry point."""
    main_class = str(instance.metadata["main_class"])
    args = str(instance.metadata["main_args"])
    run = " ".join(part for part in ("java -cp out", main_class, args) if part)
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        "rm -rf out\n"
        "mkdir -p out\n"
        f"javac --release {release} -d out $(find src -name '*.java')\n"
        f"{run}\n"
    )


def _run(tmp_path: Path, answers: list[str], name: str) -> RunResult:
    """One harness run over every item its Task lists, answered by `FakeModel`."""
    return BuildPack().evaluate(tmp_path / name, model=FakeModel(answers))


def test_the_package_imports_and_describes_its_mode():
    """The package loads by name and names the one mode it owns."""
    package = registry.load(PACKAGE)
    assert package.name == PACKAGE
    described = package.describe()
    assert described["description"]
    assert described["modes"] == [{
        "id": f"{PACKAGE}/build",
        "name": "build",
        "description": BuildPack.description,
    }]


def test_the_task_lists_three_source_trees_that_differ():
    """Three items, each a real tree with an entry point and its recorded output."""
    items = JavaBuildTask().instances()
    assert len(items) >= 3
    assert tuple(item.id for item in items) == SHIPPED_IDS
    assert len({item.id for item in items}) == len(items)
    for item in items:
        root = Path(item.path or "")
        assert list((root / "src").rglob("*.java")), f"{item.id} holds no Java source"
        assert (root / "expected.txt").read_text().strip(), f"{item.id} has no output"
        assert str(item.metadata.get("main_class") or ""), f"{item.id} names no entry point"
    # The three differ in the way that matters: a default-package class, a package
    # split across two directories, and an entry point that takes arguments.
    assert items[0].metadata["main_args"] == "3 4 5"
    assert items[1].metadata["main_class"] == "Hello"
    assert items[2].metadata["main_class"] == "com.example.app.Main"


def test_check_package_accepts_the_shipped_package(tmp_path, monkeypatch):
    """The validation the rest of the system runs finds nothing wrong with it.

    `check_package` reads a project's writable package root, so the shipped package is
    staged into one: the checks are the same ones a generated package passes through.
    """
    destination = registry.workspace_root(tmp_path) / PACKAGE
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE, destination, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    assert create_ops.check_package(PACKAGE) == []


def test_the_data_node_declares_no_inline_items():
    """The input port is open: the items come from the Task, never from the graph."""
    graph = BuildPack().graph()
    data_nodes = [node for node in graph.nodes.values() if node.kind == "data"]
    assert len(data_nodes) == 1
    assert data_nodes[0].inline_items == ()
    assert data_nodes[0].task_ref == TASK
    assert create_ops.baked_item_problems(SOURCE) == []
    assert create_ops.unfeedable_data_node_problems(SOURCE) == []


def test_an_end_to_end_run_produces_item_scores_and_aggregates_them(tmp_path):
    """Every item is scored, and `score()` is the mean of what the Task decided."""
    items = JavaBuildTask().instances()
    result = _run(tmp_path, [_stub_script(item) for item in items], name="all")
    assert result.exit_ok, result.error
    rows = result.facts["item_scores"]
    assert [row["id"] for row in rows] == [item.id for item in items]
    assert [row["passed"] for row in rows] == [True] * len(items)
    assert result.score == pytest.approx(1.0)

    partial = _run(
        tmp_path,
        [*[_stub_script(item) for item in items[:-1]], "#!/bin/sh\nprintf 'wrong\\n'\n"],
        name="partial",
    )
    partial_rows = partial.facts["item_scores"]
    mean = sum(row["score"] for row in partial_rows) / len(partial_rows)
    assert partial.score == pytest.approx(mean)
    assert mean == pytest.approx((len(items) - 1) / len(items))
    assert partial_rows[-1]["passed"] is False


def test_verify_records_the_observed_output_in_details(tmp_path):
    """A failed pass carries what the program printed, not only that it failed."""
    task = JavaBuildTask()
    item = task.instances()[0]
    workspace = tmp_path / "run"
    task.setup(item, workspace)
    (workspace / "build.sh").write_text("#!/bin/sh\nprintf '%s\\n' 'an unexpected line'\n")
    verdict = task.verify(item, workspace)
    assert verdict.passed is False
    assert verdict.score == 0.0
    assert "an unexpected line" in verdict.details


def test_a_missing_script_is_a_failed_pass(tmp_path):
    task = JavaBuildTask()
    item = task.instances()[0]
    task.setup(item, tmp_path / "run")
    verdict = task.verify(item, tmp_path / "run")
    assert verdict.passed is False and verdict.score == 0.0
    assert "no build.sh" in verdict.details


def test_the_task_takes_a_different_item_directory_at_run_time(tmp_path, monkeypatch):
    """Pointing `JAVA_BUILD_ITEMS` elsewhere feeds the harness a tree it never shipped."""
    root = tmp_path / "trees"
    item = root / "solo"
    (item / "src").mkdir(parents=True)
    (item / "src" / "Solo.java").write_text(
        "public class Solo {\n"
        "    public static void main(String[] args) {\n"
        '        System.out.println("solo");\n'
        "    }\n"
        "}\n"
    )
    (item / "item.json").write_text(json.dumps({"main_class": "Solo", "main_args": ""}))
    (item / "expected.txt").write_text("solo\n")
    monkeypatch.setenv("JAVA_BUILD_ITEMS", str(root))

    items = JavaBuildTask().instances()
    assert [entry.id for entry in items] == ["solo"]
    result = _run(tmp_path, [_stub_script(entry) for entry in items], name="solo-run")
    assert [row["id"] for row in result.facts["item_scores"]] == ["solo"]
    assert result.score == pytest.approx(1.0)


def test_a_named_item_directory_that_does_not_exist_is_refused(tmp_path, monkeypatch):
    """An unbound source and an empty selection must not look alike."""
    monkeypatch.setenv("JAVA_BUILD_ITEMS", str(tmp_path / "nowhere"))
    with pytest.raises(FileNotFoundError, match="JAVA_BUILD_ITEMS"):
        JavaBuildTask().instances()


@pytest.mark.skipif(java_bin() is None, reason="this machine has no JDK")
def test_a_real_build_and_run_scores_every_shipped_item(tmp_path):
    """The produced script compiles each tree and prints the recorded output."""
    items = JavaBuildTask().instances()
    result = _run(tmp_path, [_javac_script(item) for item in items], name="jdk")
    assert result.exit_ok, result.error
    rows = result.facts["item_scores"]
    assert all(row["passed"] for row in rows), rows
    assert result.score == pytest.approx(1.0)
