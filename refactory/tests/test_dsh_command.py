"""One resolver decides how the Python glue launches DSH.

The defect this locks down: `dsh_runtime.py`, `interactive.py` and `model.py` each
spelled `node --import tsx/esm apps/cli/src/bin.ts` themselves, so changing `NODE_BIN`
or the working directory could not point the runtime at a published harness. The
resolver answers with a source checkout's CLI, an installed `dsh`, or the executable
`DSH_CLI` names, and every call site appends its own DSH arguments to that answer.
"""

from __future__ import annotations

from pathlib import Path

from refactory_dsh import paths
from refactory_dsh.paths import INSTALLED_CLI_ENTRY, SOURCE_CLI_ENTRY, dsh_command, node_bin

#: The production modules that spawn DSH. Each must ask the resolver.
LAUNCHERS = (
    Path("refactory") / "refactory_dsh" / "dsh_runtime.py",
    Path("refactory") / "refactory_dsh" / "interactive.py",
    Path("refactory") / "refactory_dsh" / "model.py",
)


def _source_checkout(root: Path) -> Path:
    """A directory that looks like a source checkout, and returns its CLI entry."""
    entry = root / SOURCE_CLI_ENTRY
    entry.parent.mkdir(parents=True)
    entry.write_text("")
    return entry


def test_a_source_checkout_launches_its_own_cli_through_the_bootstrap(tmp_path, monkeypatch):
    """The branch every checkout used before the pin: the source entry is there."""
    monkeypatch.delenv("DSH_CLI", raising=False)
    monkeypatch.setenv("DSH_ROOT", str(tmp_path))
    entry = _source_checkout(tmp_path)
    bootstrap = Path(paths.__file__).with_name("source_bootstrap.mjs").as_uri()
    assert dsh_command() == [node_bin(), "--import", bootstrap, str(entry.resolve())]


def test_an_install_runs_its_entry_on_the_chosen_node(tmp_path, monkeypatch):
    """Once the source copy is gone, the pinned install is the only launcher left.

    Its entry runs on `node_bin()` rather than through `node_modules/.bin/dsh`,
    which takes whichever `node` is first on PATH: that is how workers ran on a Node
    DSH aborts under while `node_bin()` named a supported one.
    """
    monkeypatch.delenv("DSH_CLI", raising=False)
    monkeypatch.setenv("DSH_ROOT", str(tmp_path))
    entry = tmp_path / INSTALLED_CLI_ENTRY
    entry.parent.mkdir(parents=True)
    entry.write_text("")
    assert dsh_command() == [node_bin(), str(entry)]


def test_an_install_without_its_entry_falls_back_to_the_launcher(tmp_path, monkeypatch):
    """A layout with only the `.bin` launcher still names something to run."""
    monkeypatch.delenv("DSH_CLI", raising=False)
    monkeypatch.setenv("DSH_ROOT", str(tmp_path))
    binary = tmp_path / "node_modules" / ".bin" / "dsh"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    assert dsh_command() == [str(binary)]


def test_the_environment_can_name_the_cli_outright(tmp_path, monkeypatch):
    """`DSH_CLI` reaches an install the checkout does not resolve by itself."""
    monkeypatch.setenv("DSH_ROOT", str(tmp_path))
    _source_checkout(tmp_path)
    monkeypatch.setenv("DSH_CLI", "/opt/dsh/node_modules/.bin/dsh")
    assert dsh_command() == ["/opt/dsh/node_modules/.bin/dsh"]


def test_every_module_that_spawns_dsh_asks_the_resolver():
    """A module that spells the source argv itself cannot reach the pin.

    Read as text rather than by importing: the assertion is about the call site, and
    the spelling that broke the pin was a literal argument list.
    """
    root = Path(__file__).resolve().parents[2]
    for relative in LAUNCHERS:
        text = (root / relative).read_text()
        assert "dsh_command(" in text, f"{relative} does not ask dsh_command(...)"
        assert "tsx/esm" not in text, f"{relative} still hardcodes the tsx launch"
        assert "apps/cli/src/bin.ts" not in text, f"{relative} still hardcodes the entry"


def _node_install(root: Path, relative: str) -> Path:
    """A fake Node binary at `relative` under `root`, as a version manager lays one out."""
    binary = root / relative
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    return binary


def _isolated_node_search(tmp_path, monkeypatch, path_node_version: str | None = None) -> None:
    """Point the search at an empty home, no system Nodes, and a PATH whose `node` reports a version."""
    monkeypatch.delenv("NODE_BIN", raising=False)
    monkeypatch.delenv("NVM_DIR", raising=False)
    monkeypatch.delenv("FNM_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(paths, "SYSTEM_NODES", ())
    path_dir = tmp_path / "path-bin"
    path_dir.mkdir()
    if path_node_version is not None:
        node = path_dir / "node"
        node.write_text(f"#!/bin/sh\necho {path_node_version}\n")
        node.chmod(0o755)
    monkeypatch.setenv("PATH", str(path_dir))
    paths._binary_version.cache_clear()


def test_node_22_is_chosen_over_a_newer_default_node(tmp_path, monkeypatch):
    """DSH aborts intermittently under Node 24.20 and not under 22, so a newer PATH default loses."""
    _isolated_node_search(tmp_path, monkeypatch, path_node_version="v24.20.0")
    _node_install(tmp_path, ".nvm/versions/node/v22.20.0/bin/node")
    newest = _node_install(tmp_path, ".local/share/fnm/node-versions/v22.23.1/installation/bin/node")
    assert node_bin() == str(newest)


def test_a_node_22_older_than_dsh_supports_is_not_chosen(tmp_path, monkeypatch):
    """DSH needs 22.19; an older 22 falls through to PATH."""
    _isolated_node_search(tmp_path, monkeypatch, path_node_version="v24.20.0")
    _node_install(tmp_path, ".nvm/versions/node/v22.18.0/bin/node")
    assert node_bin() == str(tmp_path / "path-bin" / "node")


def test_a_node_22_on_path_counts_as_installed(tmp_path, monkeypatch):
    """A Node 22 that only PATH knows about (volta, a distro package) is still Node 22."""
    _isolated_node_search(tmp_path, monkeypatch, path_node_version="v22.23.1")
    assert node_bin() == str(tmp_path / "path-bin" / "node")


def test_node_bin_still_wins_outright(tmp_path, monkeypatch):
    """An explicit `NODE_BIN` is the operator's choice, Node 22 or not."""
    _isolated_node_search(tmp_path, monkeypatch)
    _node_install(tmp_path, ".nvm/versions/node/v22.23.1/bin/node")
    monkeypatch.setenv("NODE_BIN", "/opt/node24/bin/node")
    assert node_bin() == "/opt/node24/bin/node"


def test_without_node_22_the_path_node_is_used(tmp_path, monkeypatch):
    """No Node 22 anywhere: the default `node` is what there is."""
    _isolated_node_search(tmp_path, monkeypatch, path_node_version="v24.20.0")
    assert node_bin() == str(tmp_path / "path-bin" / "node")
