"""One resolver decides how the Python glue launches DSH.

The defect this locks down: `dsh_runtime.py`, `interactive.py` and `model.py` each
spelled `node --import tsx/esm apps/cli/src/bin.ts` themselves, so changing `NODE_BIN`
or the working directory could not point the runtime at a published harness. The
resolver answers with a source checkout's CLI, an installed `dsh`, or the executable
`DSH_CLI` names, and every call site appends its own DSH arguments to that answer.
"""

from __future__ import annotations

from pathlib import Path

from refactory_dsh.paths import SOURCE_CLI_ENTRY, dsh_command, node_bin

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


def test_a_source_checkout_launches_its_own_cli_through_tsx(tmp_path, monkeypatch):
    """The branch every checkout used before the pin: the source entry is there."""
    monkeypatch.delenv("DSH_CLI", raising=False)
    monkeypatch.setenv("DSH_ROOT", str(tmp_path))
    entry = _source_checkout(tmp_path)
    assert dsh_command() == [node_bin(), "--import", "tsx/esm", str(entry)]


def test_an_install_launches_its_own_binary_when_no_source_checkout_is_there(
    tmp_path, monkeypatch
):
    """Once the source copy is gone, the pinned install is the only launcher left."""
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
        assert "dsh_command()" in text, f"{relative} does not ask dsh_command()"
        assert "tsx/esm" not in text, f"{relative} still hardcodes the tsx launch"
        assert "apps/cli/src/bin.ts" not in text, f"{relative} still hardcodes the entry"
