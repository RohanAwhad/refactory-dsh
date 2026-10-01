"""Every command this repository documents, in one place, with a check.

The tutorial used to *describe* commands. A described command is a claim nobody
tests, and the first one to break is the one a newcomer runs first. A recipe here
is the command, the sentence saying what it should produce, and a declarative check
of what it did produce — so the HTML tutorial, the prose docs and a checker all
read the same source, and `check_recipes` runs them.

Three tiers, because they cost different things:

- ``CHEAP`` — deterministic, offline, seconds. `check_recipes` runs these by
  default and `tests/test_recipes.py` runs them in the suite.
- ``MODEL`` — real model calls, minutes. Run with ``--model`` when you want to
  prove the authoring path against the provider you actually use.
- ``MANUAL`` — a browser or a person. Not runnable; the recipe is the instruction.

Nothing here decides what the system does; it decides what the docs claim, and
then checks the claim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

#: The tiers, cheapest first.
CHEAP = "cheap"
MODEL = "model"
MANUAL = "manual"

#: Scratch space the checks share, so a rerun starts clean and never writes into
#: the checkout's own run directories.
SCRATCH = "/tmp/refactory-recipes"


@dataclass(frozen=True)
class Recipe:
    """One documented command and what it must produce."""

    #: Stable id, used in the checker's report and the page's anchors.
    id: str
    #: Which part of the tutorial this belongs to.
    group: str
    #: What the command is for, in the reader's terms.
    title: str
    #: The command, exactly as a reader types it.
    command: str
    #: What it should produce, in one sentence.
    expect: str
    #: `cheap`, `model` or `manual`.
    tier: str = CHEAP
    #: Substrings that must appear in the combined output.
    contains: tuple[str, ...] = ()
    #: `dotted.path` → expected JSON value, read from the output.
    json_fields: tuple[tuple[str, object], ...] = ()
    #: Files that must exist after the run, relative to the repository root.
    files: tuple[str, ...] = ()
    #: Run this instead, when the shown command is a fragment or needs a shell.
    script: str = ""
    #: Where the script runs: the repository root, or the refactory package.
    cwd: str = "root"
    #: Exit codes the recipe may return and still have done what the page says. A
    #: diagnostic reports a verdict as data: on a machine with local problems it
    #: exits nonzero without the documented command having failed at its job.
    exit_ok: tuple[int, ...] = (0,)

    def runnable(self, tier: str) -> bool:
        """Whether `check_recipes` may run this at the given tier."""
        order = {CHEAP: 0, MODEL: 1, MANUAL: 2}
        return order[self.tier] <= order[tier]


def _refactory(verb: str) -> str:
    """The entry point, as a reader on a fresh clone invokes it.

    `uv run refactory …` and not an interpreter path: uv owns the environment, the
    lockfile pins it, and the console script is the contract. A reader never has to
    know that `.venv` exists.
    """
    return f"uv run refactory {verb}"


RECIPES: tuple[Recipe, ...] = (
    Recipe(
        id="install-doctor",
        group="install",
        title="Is this machine ready?",
        command="uv run refactory doctor",
        expect="python, uv, node, the vendored language, and the DSH profile the modes run on",
        # A verdict depends on the machine: a developer's DSH home can hold a stale
        # attached profile or a different serving `dsh`, which doctor reports rather
        # than swallows. The recipe checks the rows and that a verdict is stated,
        # and lets the verdict itself be 1 ("problems") instead of failing the page.
        contains=("python", "uv", "node", "DSH profile 'graph'", "refactory doctor: "),
        exit_ok=(0, 1),
    ),
    Recipe(
        id="install-packages",
        group="install",
        title="What did the install put here?",
        command="uv run refactory list",
        expect="every package of modes, each mode with a one-line description",
        contains=("default_pack/", "circle_packing/", "dataset_probe/"),
    ),
    Recipe(
        id="use-smoke",
        group="use",
        title="Run one deterministic mode once",
        command="uv run refactory package smoke circle_packing/adaptive --json",
        expect="exit_ok true, and a score the mode's own score() produced",
        contains=('"exit_ok": true',),
        json_fields=(("mode", "circle_packing/adaptive"),),
    ),
    Recipe(
        id="use-inner",
        group="use",
        title="Run a mode in this process, and see the number",
        command=(
            "uv run python - <<'PY'\n"
            "from pathlib import Path\n"
            "from refactory_dsh import registry\n"
            "mode = registry.resolve(Path('.'), 'circle_packing/adaptive')[1]()\n"
            "result = mode.evaluate(Path('/tmp/refactory-recipes/inner'))\n"
            "print('score', result.score, 'exit_ok', result.exit_ok)\n"
            "PY"
        ),
        expect="the score, and whether the run exited cleanly",
        contains=("score ", "exit_ok True"),
    ),
    Recipe(
        id="use-agent-mode",
        group="use",
        title="Use a mode whose graph calls a model",
        command='uv run refactory ceo counting_pack/fixed_step --intent "count up to five"',
        expect="the mode runs on DSH under the CEO, leaving facts.json, trace.json and the intent ledger",
        tier=MODEL,
        contains=("best_score", "session_id"),
        files=(".factory/interactive/counting_pack-fixed_step/facts.json",),
    ),
    Recipe(
        id="use-smoke-a-model-mode",
        group="use",
        title="Score a mode that calls a model",
        command="uv run refactory package smoke default_pack/create --model --json",
        expect="a real score, because the smoke supplies the model the mode needs",
        tier=MODEL,
        contains=('"exit_ok"',),
    ),
    Recipe(
        id="use-smoke-refuses-a-model-mode",
        group="use",
        title="Smoke says so, rather than failing deep inside the run",
        command="uv run refactory package smoke default_pack/create",
        expect="a refusal naming the agent node and the command that does supply a model",
        contains=("calls a model", "refactory ceo default_pack/create"),
        script="uv run refactory package smoke default_pack/create || true",
    ),
    Recipe(
        id="optimize-loop",
        group="optimize",
        title="Search a mode: one epoch of four candidates",
        command=(
            "uv run refactory optimize counting_pack/fixed_step \\\n"
            "  --epochs 1 --batch-size 4 --optimizer random --seed 5 --runs-root /tmp/refactory-recipes/run"
        ),
        expect="a seed score, a best score, the rejections it did not pay for, and a report",
        contains=('"seed_score"', '"rejection_codes"', "report.md"),
    ),
    Recipe(
        id="read-leaderboard",
        group="read",
        title="Read the candidates, with what each one changed",
        command=f"uv run refactory leaderboard --run {SCRATCH}/run",
        expect="one row per candidate: id, parent, score, and the edits against the parent",
        contains=('"parent"', '"edits"', '"diff"'),
    ),
    Recipe(
        id="read-best",
        group="read",
        title="The winner, with the state that scored it",
        command=f"uv run refactory best --run {SCRATCH}/run",
        expect="the winning candidate id and score",
        contains=('"candidate"', '"score"'),
    ),
    Recipe(
        id="read-status",
        group="read",
        title="Where the run is",
        command=f"uv run refactory status --run {SCRATCH}/run",
        expect="the run's state and the events it went through",
        contains=('"state"', '"events"'),
    ),
    Recipe(
        id="again-adopt",
        group="again",
        title="Keep the winner as a mode of its own",
        command=(
            f"uv run refactory adopt --run {SCRATCH}/run --as check_tuned\n"
            "uv run refactory package smoke check_tuned/fixed_step --json\n"
            "rm -rf .factory/packages/check_tuned"
        ),
        expect="the adopted package smokes at the winner's score, and the run's input is untouched",
        contains=('"adopted_from"', '"exit_ok": true'),
    ),
    Recipe(
        id="steer-ledger",
        group="steer",
        title="Tell a run what it is for, while it runs",
        command=f'uv run refactory steer --run {SCRATCH}/run --note "keep the step small" --kind constraint',
        expect="the note is in the run's intent ledger, marked as a constraint",
        contains=("entries", "constraint"),
    ),
    Recipe(
        id="steer-verdict",
        group="steer",
        title="Answer a gate a person owns, so a resume replays it",
        command=(
            "uv run python -m refactory_dsh.console open review_pack/review \\\n"
            f"  --workdir {SCRATCH}/verdict-run\n"
            f"uv run refactory steer --run {SCRATCH}/verdict-run --verdict approve=proceed"
        ),
        expect="the verdict is scoped to the reviewed draft and recorded under the gate that owns it",
        contains=("gate: approve", "verdict: proceed", "scope:"),
    ),
    Recipe(
        id="dataset-walker",
        group="dataset",
        title="A mode that iterates a Task's items",
        command="uv run refactory package smoke dataset_probe/dataset --json",
        expect="one pass per item, and the mean of the Task's own verdicts",
        json_fields=(("exit_ok", True), ("score", 0.75)),
    ),
    Recipe(
        id="dataset-equivalence",
        group="dataset",
        title="The same items on the runtime that ships",
        command="uv run python -m pytest tests/test_equivalence.py -q --run-integration -k data_node",
        expect="the walker and DSH produce the same facts for the same data-driven mode",
        tier=MODEL,
        cwd="refactory",
        contains=("1 passed",),
    ),
    Recipe(
        id="create-mode",
        group="create",
        title="Create a harness for one run of one thing",
        command=(
            'uv run refactory ceo "one agent node drafts a blurb, one op checks it" \\\n'
            "  --mode create --name my_pack --single --approve gate_strategy=proceed"
        ),
        expect="a package under .factory/packages, registered and smoke-scored",
        tier=MODEL,
        contains=("package", "smoke_score"),
    ),
    Recipe(
        id="create-harness",
        group="create",
        title="Create a harness for a class of work",
        command=(
            'uv run refactory ceo "for any item a counting Task lists, a harness that records '
            'the item and scores what it did" \\\n'
            "  --mode create --name my_harness \\\n"
            "  --task refactory_packages.dataset_probe.tasks:CountingTask \\\n"
            "  --approve gate_strategy=proceed"
        ),
        expect=(
            "a package whose mode binds a data node to that Task, runs its subgraph once "
            "per item and scores per item"
        ),
        tier=MODEL,
        contains=("package", "smoke_score"),
    ),
    Recipe(
        id="design-artifact",
        group="create",
        title="Design an artifact, checked by a command you name",
        command=(
            'uv run refactory ceo "a CLI that counts words, with tests" \\\n'
            '  --mode design --name wordcount_tool --check "pytest -q" --approve gate_strategy=proceed'
        ),
        expect="the artifact, and the exit status of your check command",
        tier=MODEL,
        contains=("artifact", "check"),
    ),
    Recipe(
        id="edit-mode",
        group="create",
        title="Revise a mode, refusing a regression",
        command=(
            "uv run refactory ceo \"ask for fewer steps\" --mode edit \\\n"
            "  --revise counting_pack/fixed_step --name counting_revised \\\n"
            "  --approve gate_strategy=proceed"
        ),
        expect="a revision with a before/after score, refused when it gets worse",
        tier=MODEL,
        contains=("before_score", "after_score"),
    ),
    Recipe(
        id="gui-launch",
        group="gui",
        title="The GUI, from this checkout",
        command="uv run refactory ui --port 3081 --no-open",
        expect="prints http://127.0.0.1:3081/?token=… — open that URL, token and all",
        tier=MANUAL,
        contains=("token=",),
    ),
    Recipe(
        id="gui-ask",
        group="gui",
        title="Ask the CEO for a run",
        command='"Start a run of counting_pack/fixed_step with the random optimizer, then tell me the best score."',
        expect="the CEO calls the `refactory` tool: modes → start → status → best, and answers in words",
        tier=MANUAL,
    ),
    Recipe(
        id="gui-steer",
        group="gui",
        title="Steer it in the conversation",
        command='"This run is for making the count land exactly; keep the step small."',
        expect="the CEO calls steer, and the note reaches the run's intent ledger",
        tier=MANUAL,
    ),
    Recipe(
        id="gui-panel",
        group="gui",
        title="Or click instead: the panel",
        command="Open the sidebar → Modes",
        expect="described choices for the mode, the outer loop, the optimizer and the packages; Start names the run",
        tier=MANUAL,
    ),
    Recipe(
        id="drift-suites",
        group="drift",
        title="The suites that keep the two runtimes honest",
        command="uv run python -m pytest tests/test_equivalence.py -q --run-integration",
        expect="the same mode scored on the walker and on DSH, agreeing",
        tier=MODEL,
        cwd="refactory",
        contains=("passed",),
    ),
    Recipe(
        id="drift-generated-docs",
        group="drift",
        title="The documents that are generated from the code",
        command=(
            "uv run python -m refactory_dsh.tools.gen_api_reference --check && "
            "uv run python -m refactory_dsh.tools.gen_tutorial --check"
        ),
        expect="the API reference and the HTML tour both match the code",
        contains=("up to date",),
    ),
    Recipe(
        id="drift-recipes",
        group="drift",
        title="The documented commands, executed",
        command="uv run python -m refactory_dsh.tools.check_recipes --skip drift",
        expect="every command this page shows did what the page says it does",
        contains=("passed at tier",),
    ),
)

#: Group titles for the page and the checker's report.
GROUP_TITLES: dict[str, str] = {
    "install": "Install and check",
    "use": "Use a mode",
    "optimize": "Optimize it",
    "read": "Read the run",
    "again": "Use it again",
    "steer": "Steer it",
    "dataset": "Datasets, Tasks and data nodes",
    "create": "Create, revise and design (model-driven)",
    "gui": "In the GUI",
    "drift": "Keeping it honest",
}


def by_group() -> dict[str, list[Recipe]]:
    """The recipes, grouped in the order the tutorial presents them."""
    grouped: dict[str, list[Recipe]] = {}
    for recipe in RECIPES:
        grouped.setdefault(recipe.group, []).append(recipe)
    return {group: grouped[group] for group in GROUP_TITLES if group in grouped}


def json_field(output: str, path: str) -> object:
    """Read `a.b` out of the last JSON document in `output`.

    Console commands print one JSON document; some print a line of progress before
    it. Anything else is a failed check rather than a crash, so a malformed answer
    is a reported problem.
    """
    document = None
    decoder = json.JSONDecoder()
    for index, character in enumerate(output):
        if character not in "[{":
            continue
        try:
            value, _end = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        document = value
    if document is None:
        raise ValueError("no JSON in the output")
    for step in path.split("."):
        if isinstance(document, list):
            document = document[int(step)]
        elif isinstance(document, dict):
            document = document[step]
        else:
            raise KeyError(path)
    return document


@dataclass
class Result:
    """One recipe's outcome."""

    recipe: Recipe
    ok: bool
    problems: list[str] = field(default_factory=list)
    output: str = ""

    def describe(self) -> str:
        """A line for the checker's report."""
        mark = "ok  " if self.ok else "FAIL"
        detail = "" if self.ok else "  " + "; ".join(self.problems)
        return f"[{mark}] {self.recipe.id:<22} {self.recipe.title}{detail}"


def check(recipe: Recipe, output: str, root: Path) -> Result:
    """Whether one run of a recipe produced what its `expect` claims.

    Declarative on purpose: the check is data next to the command, so adding a
    documented command means adding a recipe rather than writing a test.
    """
    problems: list[str] = []
    for needle in recipe.contains:
        if needle not in output:
            problems.append(f"output does not contain {needle!r}")
    for path, expected in recipe.json_fields:
        try:
            found = json_field(output, path)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            problems.append(f"cannot read {path!r} from the output ({exc})")
            continue
        if found != expected:
            problems.append(f"{path} is {found!r}, expected {expected!r}")
    for relative in recipe.files:
        if not (root / relative).exists():
            problems.append(f"{relative} was not written")
    return Result(recipe=recipe, ok=not problems, problems=problems, output=output)
