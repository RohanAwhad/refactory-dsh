"""The first-run docs can be followed as written (dm-266).

A walkthrough of the README on a fresh install found the steps a reader is shown failing
or misleading: a knob value the mode refuses, a design check that passes on wrong counts,
GUI commands that leave the token URL unexplained, and settings advice the pinned harness
no longer reads. These hold the README and getting-started to what they now say.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from refactory_dsh import registry
from refactory_dsh.nn.edits import configure

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
GETTING_STARTED = ROOT / "refactory" / "docs" / "getting-started.md"
FIRST_RUN = (README, GETTING_STARTED)


def _commands(document: Path) -> list[str]:
    """Every shell command in a document's bash blocks, continuation lines joined."""
    commands: list[str] = []
    for block in re.findall(r"```bash\n(.*?)```", document.read_text(), re.DOTALL):
        commands += [line.strip() for line in block.replace("\\\n", " ").splitlines() if line.strip()]
    return commands


def test_every_documented_knob_value_is_one_its_mode_accepts():
    """The counting example asked for target=20, above the knob's bound of 9, so the first
    interactive run a reader tried was refused (`EditError: knob 'target': 20 is above the
    upper bound 9`), in the terminal and in GUI chat alike."""
    shown: list[tuple[str, str, str]] = []
    for document in FIRST_RUN:
        for command in _commands(document):
            mode = re.search(r"refactory ceo (\w+/\w+)", command)
            if mode:
                shown += [(mode[1], name, value) for name, value in re.findall(r"--knob (\w+)=(\S+)", command)]
        for mode, pairs in re.findall(r"Open (\w+/\w+) with ((?:\w+=\w+(?:,? and |, )?)+)", document.read_text()):
            shown += [(mode, name, value) for name, value in re.findall(r"(\w+)=(\w+)", pairs)]
    assert len(shown) >= 6, shown
    for mode, name, value in shown:
        state = registry.resolve(ROOT, mode)[1]().state_dict()
        configure(state, name, int(value) if value.isdigit() else value)


def _wordcount_checks() -> list[str]:
    checks = [found for found in re.findall(r"--check '([^']+)'", README.read_text().replace("\\|", "|"))
              if "wordcount" in found]
    checks += [found for command in _commands(GETTING_STARTED) if "wordcount" in command
               for found in re.findall(r"--check '([^']+)'", command)]
    return checks


@pytest.mark.parametrize("counts, passes", [("lines, words, len(text)", True),
                                            ("lines, words + 1, len(text)", False)])
def test_the_design_examples_check_fails_on_wrong_counts(tmp_path, counts, passes):
    """The design example's check only ran the script (`python wordcount.py sample.txt`), so
    a wordcount.py printing wrong counts passed it, and no reviewer noticed. The documented
    check compares the counts with `wc`."""
    checks = _wordcount_checks()
    assert len(checks) == 2, checks
    (tmp_path / "sample.txt").write_text("one two three\nfour five\n\nsix\n")
    (tmp_path / "wordcount.py").write_text(
        "import sys\n"
        "text = open(sys.argv[1], encoding='utf-8').read()\n"
        "lines, words = text.count('\\n'), len(text.split())\n"
        f"print({counts})\n"
    )
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"}
    for check in checks:
        ran = subprocess.run(["bash", "-c", check], cwd=tmp_path, env=env, capture_output=True, text=True)
        assert (ran.returncode == 0) is passes, (check, ran.stdout, ran.stderr)


def test_the_gui_commands_say_how_to_reach_the_page():
    """`refactory ui` and `./refactory-dsh` print a token URL; the docs show `--no-open` and
    say to open that URL, token and all."""
    launches = [command for document in FIRST_RUN for command in _commands(document)
                if "refactory ui" in command or command.startswith("./refactory-dsh")]
    assert len(launches) >= 3, launches
    assert all("--no-open" in command for command in launches), launches
    for document in FIRST_RUN:
        assert "?token=" in document.read_text(), document.name


def test_the_readme_verifies_an_adopted_package_before_smoking_it():
    commands = _commands(README)
    assert commands.index("uv run refactory package verify first_tuned") \
        < commands.index("uv run refactory package smoke first_tuned/adaptive")


def test_the_docs_say_how_a_terminal_run_chooses_its_model():
    for document in FIRST_RUN:
        text = document.read_text()
        assert "CLI runs choose their model with `--provider` and `--model`" in text, document.name
        assert "`uv run refactory runtime`" in text, document.name


def test_the_docs_say_what_delivery_needs_and_where_the_files_are_without_it():
    for document in FIRST_RUN:
        text = document.read_text()
        assert "`gh auth login`" in text and "`delivery.json`" in text, document.name
        assert "the run's branch" in text, document.name


def test_pins_profiles_and_model_settings_are_where_the_pinned_harness_keeps_them():
    """Pins and profiles live under `$DSH_HOME`, which need not be `~/.dsh`; and 0.2.0-rc.2
    keeps model routes in each profile's patch, importing a hand-written `settings.yaml` once
    into whichever profile boots first."""
    docs = ROOT / "refactory" / "docs"
    for document in (GETTING_STARTED, docs / "live-control.md", docs / "models.md"):
        text = document.read_text()
        assert not re.search(r"(?:own|in|at) `~/\.dsh", text), document.name
    assert "graph-pin-<commit12>` profile in your own\n> `$DSH_HOME`" in GETTING_STARTED.read_text()
    models = (docs / "models.md").read_text()
    assert "# ~/.dsh/settings.yaml" not in models
    assert "$DSH_HOME/profiles/headless/cordis.patch.yml" in models
