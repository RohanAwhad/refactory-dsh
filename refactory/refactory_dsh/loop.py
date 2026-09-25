"""The optimizer loop, named like one.

    init      - build the starting population from the mode's seed state
    forward   - run each candidate in the batch
    loss      - turn the batch into a number
    backward  - read the traces and compute the gradient
    optimizer - propose the next update, validating before anything is run
    step      - apply it, producing offspring
    scheduler - decide whether the epoch is over, and say what bound it
    report    - the visibility surface

A candidate is a state dict plus its lineage. Evaluating one is a forward pass:
materialize the mode from the state, execute its graph, and score the run. An
invalid candidate never reaches ``forward`` at all, so it costs no budget — it is
refused by the optimizer's loop with a reason, and that reason is reported.

Two things shape what a candidate is worth. It is evaluated on the part of its
Task's items the run directory names, never on all of them, so the search cannot be
examined on the items it was tuned against; and what it scores is the evaluator's
number minus ``parsimony`` per node of its graph, so a search with no size cost does
not grow the harness for free. Both numbers are recorded, and once the search is
over the winner is re-evaluated on the items no candidate saw.
"""

from __future__ import annotations

import dataclasses
import json
import random
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from refactory_dsh import history as run_history
from refactory_dsh import events
from refactory_dsh import observe as progress_file
from refactory_dsh import task as task_module
from refactory_dsh.evaluators import create as create_evaluator
from refactory_dsh import registry
from refactory_dsh.nn import Knob, PackageError
from refactory_dsh.nn import edits as nn_edits
from refactory_dsh.nn import validate_state
from refactory_dsh.optim import (
    Candidate,
    Optimizer,
    Proposal,
    ProposalContext,
    Rejection,
    RandomOptimizer,
)
from refactory_dsh.optim import create as create_optimizer
from refactory_dsh.optim.base import summarise_reasons
from refactory_dsh.outerloop import OuterLoop, Run
from refactory_dsh.paths import project_root

#: The append-only record of everything a run did. Resume replays it.
JOURNAL = run_history.JOURNAL


#: The offspring a proposal node found, waiting for the node that installs them.
PENDING = "pending.json"

#: The train-minus-holdout gap above which the audit calls a winner overfit. The
#: reference's number, so a run here and a run there agree about what overfit means.
OVERFIT_THRESHOLD = 0.15

#: Where the run writes how the winner did on the items no candidate saw.
HOLDOUT_FILE = "holdout.json"


class LoopError(RuntimeError):
    """The run cannot proceed. Always names what is wrong with the mode."""



def _item_sources(state: Mapping[str, Any]) -> dict[str, tuple[Any, ...]]:
    """Which items each data node of a state would read.

    The fields a harness's class of work lives in: the Task it binds and the source it
    reads. Nothing else in the graph says what the run is examined on.
    """
    graph = state.get("graph") or {}
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    if isinstance(nodes, dict):
        entries = list(nodes.values())
    elif isinstance(nodes, list):
        entries = nodes
    else:
        entries = []
    found: dict[str, tuple[Any, ...]] = {}
    for node in entries:
        if not isinstance(node, dict) or node.get("kind") != "data":
            continue
        found[str(node.get("id"))] = (
            node.get("task_ref"), node.get("source_path"), node.get("source_format"),
        )
    return found


@dataclass
class LoopConfig:
    """How a run is shaped."""

    epochs: int = 2
    batch_size: int = 4
    optimizer: str = "autoresearch"
    edit_preset: str = "broad"
    seed: int = 0
    elite: int = 3
    #: A hard ceiling on inner runs. ``epochs`` normally binds first; whichever
    #: does is reported, so a run never ends for an unstated reason.
    max_evaluations: int = 64
    #: The fraction of a Task's instances held out of the search and used to audit
    #: the winner. One instance cannot be split, which the audit records as a fact
    #: instead of failing the run.
    holdout: float = task_module.DEFAULT_HOLDOUT
    #: What each node of a candidate's graph costs its score. The reference's
    #: coefficient: without a size cost a search has no reason not to grow the
    #: harness, and the report would not say whether a candidate won on merit or on
    #: size.
    parsimony: float = 0.01
    runs_root: Path = Path(".factory/loop")
    #: Wall-clock ceiling for one inner run, enforced by the executor's step cap.
    max_steps: int = 2000
    #: Attempts the optimizer gets per batch before it stops asking.
    attempts: int = 3
    #: Knobs the operator freezes for this run, whatever the candidate says.
    frozen: tuple[str, ...] = ()
    #: What this run is *for*, as the person asking put it. Seeded into the run's
    #: intent ledger, which every agent node in an interactive run reads. Empty means
    #: nobody said, and the ledger is left alone rather than filled with a default --
    #: that default is BUG-05, and a ledger holding one is a ledger a fidelity check
    #: cannot check. See :mod:`refactory_dsh.steering`.
    intent: str = ""
    #: Knob values the search starts from, as `NAME=VALUE`. A mode whose inputs are
    #: frozen knobs (`source_root`, `test_command`) cannot be searched at all without
    #: this: every candidate would run with the declared defaults, score the same
    #: thing, and the run would report a search that found nothing.
    knobs: dict[str, Any] = dataclasses.field(default_factory=dict)
    #: How many evaluated candidates the optimizer is shown. Bounded on purpose:
    #: an unbounded history in a prompt is how a search stops thinking.
    history_limit: int = 200
    #: How many previous runs to summarise as a prior.
    prior_runs: int = 3
    #: Where a ``global``-retention memory lives. Injected so a test can put it
    #: somewhere temporary instead of the home directory.
    memory_global_root: Path | None = None
    #: Which runtime runs a candidate: "walker" (fast, for search) or "dsh" (the
    #: runtime that ships). Both score identically; the equivalence tests keep it so.
    evaluator: str = "walker"
    #: Wall-clock ceiling for one inner run when the evaluator is DSH.
    dsh_timeout_s: int = 900
    #: Carry on from the journal in the run directory instead of starting over.
    #: A run reproduces what an uninterrupted one would have done exactly when its
    #: optimizer is deterministic, which is stated rather than assumed.
    resume: bool = False
    #: Write ``progress.jsonl`` as the run goes. Off for a run nobody is watching:
    #: the file is written per candidate, not per epoch, so it is the one artifact
    #: whose cost scales with the batch.
    progress: bool = True
    #: Print the run's events while it goes. Off for a library caller (a test, an
    #: embedded loop): the command line turns it on, because a search that says
    #: nothing until it finishes reads as a hang.
    print_events: bool = False
    #: Print every event, not only the meaningful ones, and pass a candidate's own
    #: run output through. A caller debugging a search wants the firehose.
    verbose: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["runs_root"] = str(self.runs_root)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LoopConfig":
        """Rebuild a configuration from one a run wrote.

        Json turned the tuple fields into lists and the path into a string. Both
        are restored here rather than at each reader, so one place knows what the
        on-disk form of a config is.
        """
        known = {field.name for field in dataclasses.fields(cls)}
        restored: dict[str, Any] = {}
        for key, value in data.items():
            if key not in known:
                continue
            if key == "runs_root":
                restored[key] = Path(value)
            elif key == "frozen":
                restored[key] = tuple(value or ())
            elif key == "memory_global_root":
                restored[key] = Path(value) if value else None
            else:
                restored[key] = value
        return cls(**restored)


@dataclass
class EpochSummary:
    """One epoch, as the report and the scheduler describe it.

    Kept as a record rather than recomputed from the population: what an epoch
    did is a fact about that epoch, and the population at the end of a run can no
    longer answer how many candidates a middle epoch proposed.
    """

    epoch: int
    evaluated: int
    best: float | None
    mean: float | None
    improved: bool
    proposed: int
    accepted: int
    rejected: int
    structural_accepted: int


class Loop(OuterLoop):
    """The default outer loop: the eight steps over one mode.

    Every step a different strategy would change is a method here, so a strategy
    subclasses this and overrides one of them rather than copying the loop.
    """

    name = "default"

    def __init__(
        self,
        mode_id: str,
        config: LoopConfig | None = None,
        *,
        project: Path | None = None,
        model: Any = None,
        optimizer: Optimizer | None = None,
    ) -> None:
        self.mode_id = mode_id
        self.config = config or LoopConfig()
        self.project = Path(project or Path("."))
        self.root = Path(self.config.runs_root)
        self.model = model
        # One printer for the run: a caller that asked for events sees them while
        # the search goes, and a library caller prints nothing at all.
        self._printer = events.Printer(verbose=self.config.verbose) if self.config.print_events else None

        try:
            package, self.mode_class = registry.resolve(self.project, mode_id)
        except PackageError as exc:
            # A mode that cannot be resolved is a loop that cannot be set up, so
            # it is reported as one rather than as a registry error.
            raise LoopError(str(exc)) from exc
        self.package = package
        self.mode = self.mode_class()
        self.invariants = self.mode.invariants()
        self.parameters = self.mode.parameters()

        if optimizer is not None:
            self.optimizer: Optimizer = optimizer
        elif self.config.optimizer == "autoresearch":
            self.optimizer = create_optimizer(
                self.config.optimizer, client=model, k=self.config.attempts
            )
        else:
            self.optimizer = create_optimizer(
                self.config.optimizer, seed=self.config.seed, k=self.config.attempts
            )

        # How a candidate is run, and therefore how it is scored. Built here from
        # this loop's own settings rather than switched on inside `_evaluate`, so a
        # third evaluator is a new class in `evaluators.py` and not a new branch.
        self.evaluator = create_evaluator(
            self.config.evaluator,
            config=self.config,
            model=model,
            project=self.project,
        )
        self.population: list[Candidate] = []
        self.rejections: list[Rejection] = []
        self.proposals: dict[str, dict[str, Any]] = {}
        self.epoch = 0
        self.generation = 0
        self._epoch_open = False
        self.evaluations = 0
        self.bound = ""
        self.attempts_used: list[int] = []
        self._refusal_pressure = 0.0
        self.loss: dict[str, Any] = {}
        self.gradient: dict[str, Any] = {}
        #: What the outer loop decided this iteration, read by `_context()`. A
        #: plain `Loop` decides nothing beyond the bound; a strategy that branches
        #: writes what it chose here, so the choice reaches the proposal instead of
        #: only the log.
        self.strategy_decision: dict[str, Any] = {}
        self.epochs: list[EpochSummary] = []
        #: The held-out audit of the winner, computed once by :meth:`audit_holdout`
        #: and reported by :meth:`report`. ``None`` means it has not been computed.
        self.holdout_audit: dict[str, Any] | None = None

    # ── init ───────────────────────────────────────────────────────────────
    def _seed_ledger(self) -> None:
        """Write the run's intent ledger, when somebody said what the run is for.

        Nothing is written when ``intent`` is empty: a ledger holding a default is one
        a fidelity check cannot check, which is BUG-05, and a run nobody steered needs
        no ledger rather than an empty one that looks like a statement. Refuses a
        placeholder here rather than at the gate, because the person who typed it is
        the one who can fix it.
        """
        if not self.config.intent.strip():
            return
        from refactory_dsh.steering import Ledger

        ledger = Ledger(self.root)
        if ledger.exists():
            # A resumed run keeps what it was already told, including anything said
            # since. Re-seeding would put a second origin in the ledger.
            return
        Ledger.seed(self.root, self.config.intent)

    def init(self) -> None:
        """Seed the population and refuse to search a mode that cannot be run."""
        seed_state = self.mode.state_dict()
        for name, value in self.config.knobs.items():
            # `configure`, not the editor: the search may not move a frozen knob, but
            # the caller deciding what this run operates on is not the search.
            seed_state = nn_edits.configure(seed_state, name, value)
        if self.config.knobs:
            # The loop's own mode is what the report and the seed candidate's parent
            # read, so it has to carry the same configuration as the population.
            self.mode.load_state_dict(seed_state)
        issues = validate_state(
            seed_state, invariants=self.invariants, resolve_calls=True
        )
        if issues:
            raise LoopError(
                f"the seed state of {self.mode_id} is not runnable; "
                f"first problem: {issues[0]}"
            )
        unreachable = self.unreachable_knobs()
        if unreachable:
            # Loud, because the failure is silent: the run reports full batches over
            # a surface smaller than the mode declared.
            print(
                json.dumps({
                    "warning": "optimizer_cannot_reach_some_knobs",
                    "optimizer": self.optimizer.name,
                    "knobs": unreachable,
                    "detail": "declared and movable, but this optimizer cannot produce "
                              "a different value for them",
                }),
                file=sys.stderr,
            )
        if not self.mode.is_scored():
            raise LoopError(
                f"{self.mode_id} does not override score(), so the loop has no "
                "signal to search"
            )

        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "config.json").write_text(
            json.dumps({"mode": self.mode_id, **self.config.to_dict()}, indent=2, default=str)
        )
        self._seed_ledger()
        resumed = self._replay_if_asked()
        (self.root / run_history.RUN_FILE).write_text(
            json.dumps(
                {
                    "version": run_history.RUN_VERSION,
                    "mode": self.mode_id,
                    "resumed": resumed,
                    "journal": JOURNAL,
                },
                indent=2,
            )
        )
        if resumed:
            self._write_state()
            self._progress("init", resumed=True, population=len(self.population))
            return

        self._journal("start", mode=self.mode_id, config=self.config.to_dict())
        seed = Candidate.from_state(seed_state)
        self.population = [seed]
        self._journal_candidate(seed)
        self._seed_population(seed)
        self._write_state()
        self._progress(
            "init",
            resumed=False,
            population=len(self.population),
            epochs=self.config.epochs,
            batch_size=self.config.batch_size,
            optimizer=self.optimizer.name,
        )

    def _seed_population(self, seed: Candidate) -> None:
        """Fill the starting batch with neighbours of the seed.

        A population of one would spend the first epoch evaluating a single point
        and look like a search while doing almost nothing. It is also swept rather
        than sampled: one candidate per movable knob, each at a value the default
        is not, so the first epoch produces a gradient with *contrast* in it. A
        purely structural starting population gives the optimizer plenty of
        candidates and no evidence about which value of anything is worth having.
        """
        wanted = max(0, int(self.config.batch_size) - 1)
        if not wanted:
            return
        sampler = RandomOptimizer(seed=self.config.seed)
        edits = self._exploration_edits(seed, wanted)
        insertable = [node.to_state() for node in self.mode.insertable()]
        rng = random.Random(f"{self.config.seed}:sweep")
        while len(edits) < wanted:
            edit = nn_edits.random_edit(
                seed.state,
                preset=self.config.edit_preset,
                insertable=insertable,
                rng=rng,
            )
            if edit is None:
                break
            edits.append(edit)

        for edit in edits:
            proposal = Proposal(edit=edit, parent=seed.id, rationale="starting population")
            outcome = sampler.attempt(
                proposal, seed, self.invariants, True, self.config.frozen, self._tried()
            )
            if isinstance(outcome, Candidate):
                self.population.append(outcome)
                self._journal_candidate(outcome)
                self.proposals.setdefault(outcome.id, {
                    "id": outcome.id,
                    "parent": outcome.parent,
                    "edits": outcome.edits,
                    "rationale": "swept for the starting population",
                    "prediction": "",
                    "confidence": 0.0,
                    "origin": "seed_population",
                })
            else:
                self.rejections.append(Rejection(1, edit, outcome))

    def _exploration_edits(self, seed: Candidate, wanted: int) -> list[dict[str, Any]]:
        """One parameter move per movable knob, at a value the default is not."""
        rng = random.Random(self.config.seed)
        entries = seed.state.get("knobs") or {}
        frozen = set(self.config.frozen)
        edits: list[dict[str, Any]] = []
        for name in sorted(entries):
            if len(edits) >= wanted:
                break
            knob = Knob.from_spec(entries[name]["spec"])
            if knob.frozen or name in frozen:
                continue
            value = _other_value(knob, entries[name]["value"], rng)
            if value is None:
                continue
            edits.append({"op": "set_knob", "name": name, "value": value})
        return edits

    # ── forward ────────────────────────────────────────────────────────────
    def forward(self) -> int:
        """Evaluate every candidate in the batch that has not run yet."""
        batch = [candidate for candidate in self.population if not candidate.evaluated]
        for index, candidate in enumerate(batch):
            if self.evaluations >= self.config.max_evaluations:
                break
            self._evaluate(candidate)
            self._progress(
                "forward",
                candidate=candidate.id,
                index=index,
                of=len(batch),
                score=candidate.score,
                exit_ok=candidate.exit_ok,
                error=candidate.error,
                duration_s=candidate.duration_s,
            )
        return len(batch)

    def _carry_ledger(self, workdir: Path) -> None:
        """Put the run's ledger where the candidate's own nodes look for it.

        The run's ledger lives at the run root; a candidate is evaluated in
        ``runs/<id>/``; and the steering transform folds in the ledger of the work
        directory it is handed. Without this the ledger a person seeded with
        ``--intent``, or appended to with ``refactory steer --note`` while the search
        was going, was written, reported by ``steer``, and read by no node — in the one
        place steering is most wanted, because a search is what runs for hours.

        Copied rather than searched for up the tree, so each candidate directory stays
        a self-contained record of what the person had said when it was evaluated. A
        note added while a candidate is running reaches the next candidate, exactly as
        it reaches the next node of an interactive run.
        """
        import shutil

        from refactory_dsh.steering import LEDGER

        source = self.root / LEDGER
        if not source.exists():
            return
        workdir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, workdir / LEDGER)

    def _write_subset(self, workdir: Path, part: str) -> None:
        """Name the part of the items this candidate is evaluated on.

        Written into the candidate's own run directory before the evaluator starts,
        and read by the item loader of either runtime. It is not a knob: a search
        that could set it would choose its own exam, and there would be no item left
        for the audit to hold out.
        """
        task_module.write_subset(workdir, part, self.config.holdout)

    def _evaluate(self, candidate: Candidate) -> None:
        """Run one candidate through the evaluator, and record what it scored.

        This is the loop's forward pass and the evaluator is its loss: what the
        candidate scored is whatever the evaluator says it scored. Which evaluator
        ran it is not this method's business -- see :mod:`refactory_dsh.evaluators`
        for the two that ship and what makes them agree.

        The evaluator runs the train part of the item selection, and the loop takes
        the size cost off afterwards. ``raw_score`` keeps the evaluator's number, so
        the report can say whether a candidate won on merit or on size.
        """
        workdir = self.root / "runs" / candidate.id
        self._carry_ledger(workdir)
        self._write_subset(workdir, task_module.TRAIN)
        mode = self.mode_class(state=candidate.state)
        result = self.evaluator.evaluate(mode, candidate.state, workdir)
        nodes = node_count(candidate.state)
        candidate.raw_score = result.score
        candidate.score = result.score - self.config.parsimony * nodes
        candidate.mean_score = result.score
        candidate.facts = _trim(result.facts)
        candidate.exit_ok = result.exit_ok
        candidate.error = result.error
        candidate.duration_s = result.duration_s
        candidate.model_calls = result.model_calls
        candidate.evaluated = True
        self.evaluations += 1
        self._journal(
            "score",
            id=candidate.id,
            score=candidate.score,
            raw_score=candidate.raw_score,
            nodes=nodes,
            parsimony=self.config.parsimony,
            exit_ok=candidate.exit_ok,
            error=candidate.error,
            duration_s=candidate.duration_s,
            model_calls=candidate.model_calls,
            facts=candidate.facts,
        )
        self._write_candidate(candidate, workdir, mode=mode)

    def _write_candidate(
        self, candidate: Candidate, workdir: Path, *, mode: Any = None
    ) -> None:
        workdir.mkdir(parents=True, exist_ok=True)
        nodes = node_count(candidate.state)
        (workdir / "candidate.json").write_text(
            json.dumps(
                {
                    "id": candidate.id,
                    "parent": candidate.parent,
                    # Both numbers, so a reader of one candidate directory can tell
                    # what the evaluator measured from what the search compared.
                    "score": candidate.score,
                    "raw_score": candidate.raw_score,
                    "nodes": nodes,
                    "parsimony": self.config.parsimony,
                    "size_cost": round(self.config.parsimony * nodes, 6),
                    "exit_ok": candidate.exit_ok,
                    "error": candidate.error,
                    "duration_s": candidate.duration_s,
                    "edits": candidate.edits,
                    "facts": candidate.facts,
                    "model_calls": candidate.model_calls,
                    # What the run published, against what the mode declared: a port
                    # that is missing is a promise the mode did not keep. Written for
                    # every candidate whichever evaluator ran it, because a reader
                    # comparing two runs should not have to know how they were run.
                    "ports": {
                        port.name: (workdir / port.path).exists()
                        for port in (mode.port_declarations() if mode is not None else ())
                    },
                },
                indent=2,
                default=str,
            )
        )

    # ── loss ───────────────────────────────────────────────────────────────
    def loss_step(self) -> dict[str, Any]:
        evaluated = self._evaluated()
        scored = [c for c in evaluated if c.exit_ok and c.score is not None]
        scores = [c.score for c in scored]
        raw_scores = [c.raw_score for c in scored if c.raw_score is not None]
        best = max(scores) if scores else None
        mean = (sum(scores) / len(scores)) if scores else None
        previous = self.loss.get("best")
        self.loss = {
            "epoch": self.epoch,
            "batch": len(evaluated),
            # The scores the search compared, and the evaluator's own numbers beside
            # them, so an epoch's numbers can be read either way.
            "scores": scores,
            "raw_scores": raw_scores,
            "mean": mean,
            "best": best,
            "raw_best": max(raw_scores) if raw_scores else None,
            "parsimony": self.config.parsimony,
            "best_ever": max([s for s in (previous, best) if s is not None], default=None),
            "improved": bool(best is not None and previous is not None and best > previous),
            "run_failures": [c.id for c in self.population if c.evaluated and not c.exit_ok],
        }
        self._write("loss.json", self.loss)
        self._journal("loss", **self.loss)
        self._progress("loss", **self.loss)
        return self.loss

    # ── backward ───────────────────────────────────────────────────────────
    def backward(self) -> dict[str, Any]:
        """Turn the traces into the gradient the optimizer reads."""
        evaluated = [c for c in self._evaluated() if c.exit_ok and c.score is not None]
        self.gradient = {
            "epoch": self.epoch,
            "scoring": self.scoring(),
            "knob_attribution": self._knob_attribution(evaluated),
            "cost": self._cost(),
            "run_failures": self.loss.get("run_failures", []),
            "rejection_codes": self._rejection_codes(),
            "rejection_reasons": [r for rej in self.rejections[-8:] for r in rej.reasons],
            "accepted_op_mix": self._op_mix(),
            "structural": self._structural_stats(),
            "leaderboard": [self._row(c) for c in self.leaderboard(8)],
        }
        self._write("gradient.json", self.gradient)
        self._progress(
            "backward",
            knobs=len(self.gradient["knob_attribution"]),
            failures=len(self.gradient["run_failures"]),
        )
        return self.gradient

    def _knob_attribution(self, evaluated: Sequence[Candidate]) -> dict[str, Any]:
        """What each parameter value was worth, over everything evaluated so far.

        The score attributed here is the one the search compares -- the evaluator's
        number minus the run's size cost -- so a value is credited with what choosing
        it did to the search's objective rather than to the raw measurement.

        ``untried`` is part of the gradient, not decoration: an optimizer reading
        only the values that have run has no way to tell a value that failed from
        one nobody has tried, and will keep re-proposing what it already knows.
        """
        buckets: dict[str, dict[str, list[float]]] = {}
        for candidate in evaluated:
            for name, entry in (candidate.state.get("knobs") or {}).items():
                buckets.setdefault(name, {}).setdefault(str(entry["value"]), []).append(
                    float(candidate.score)
                )
        attribution: dict[str, Any] = {}
        for name, values in sorted(buckets.items()):
            attribution[name] = {
                "tried": {
                    value: {
                        "n": len(scores),
                        "mean": round(sum(scores) / len(scores), 6),
                        "max": round(max(scores), 6),
                    }
                    for value, scores in sorted(values.items())
                },
                "untried": self._untried(name, values),
            }
        return attribution

    def _untried(self, name: str, tried: dict[str, list[float]]) -> list[Any]:
        """The declared values of a knob that have not been evaluated.

        A knob with a finite set -- an enum, or a ``str`` with declared values --
        enumerates it. An ``int`` with both bounds declared is just as finite and
        just as readable, so it enumerates its range too. An open-ended numeric
        knob has no set to enumerate and answers empty.
        """
        knob = self.parameters.get(name)
        if knob is None:
            return []
        finite = knob.accepted()
        if finite:
            return [member for member in finite if str(member) not in tried]
        if knob.kind == "int" and knob.lo is not None and knob.hi is not None:
            return [
                value
                for value in range(int(knob.lo), int(knob.hi) + 1)
                if str(value) not in tried
            ]
        return []

    def _efficiency(self) -> dict[str, Any]:
        """How many attempts it took per candidate that could actually run."""
        proposed = len(self.proposals)
        attempts = sum(self.attempts_used)
        return {
            "rounds": attempts,
            "batches": len(self.attempts_used),
            "effective_k": sorted(set(self.attempts_used)),
            "candidates": proposed,
            "attempts_per_candidate": (
                round(attempts / proposed, 3) if proposed else None
            ),
            "why": "k starts at the configured attempts and rises by one when the "
                   "previous batch was refused more than it was accepted, because "
                   "that is the signal that the optimizer is close to something "
                   "legal rather than out of ideas",
        }

    def _proposer_health(self) -> dict[str, Any]:
        """Whether the proposer answered at all, said where a reader will see it.

        A failed round is caught and the run continues, which is right: a model
        that is down should not kill a run. But the run then looks exactly like one
        where the model answered and had nothing useful, because both end with a
        score that did not move and no rejections. That reads as a finding about
        the mode when it is a fact about the machine, so the count is reported and
        a run that never got an answer says so in one line.
        """
        failures = list(getattr(self.optimizer, "proposal_failures", []))
        rounds = int(getattr(self.optimizer, "proposal_rounds", 0))
        all_failed = bool(rounds) and len(failures) >= rounds
        if all_failed:
            finding = (
                f"every one of the {rounds} proposal round(s) failed, so this run is "
                "not a search result: nothing was proposed, and the score did not "
                "move because the proposer never answered."
            )
        elif failures:
            finding = (
                f"{len(failures)} of {rounds} proposal round(s) failed; the rest "
                "were proposed and evaluated normally."
            )
        else:
            finding = ""
        return {
            "optimizer": self.optimizer.name,
            "rounds": rounds,
            "failures": failures[:5],
            "failure_count": len(failures),
            "all_failed": all_failed,
            "finding": finding,
        }

    def _dominant_refusal(self) -> dict[str, Any]:
        """The commonest refusal, and what it says about the catalogue.

        A rule that refuses most of what the optimizer proposes is a finding about
        the ops it was offered, not about the optimizer. Counting the codes and
        stopping there leaves that unsaid.
        """
        counts = self._rejection_codes()
        if not counts:
            return {"code": None, "count": 0, "share": 0.0, "finding": ""}
        code, count = max(counts.items(), key=lambda item: item[1])
        total = sum(counts.values())
        share = round(count / total, 3)
        finding = ""
        if share >= 0.5 and code.startswith("E-"):
            finding = (
                f"{share:.0%} of refusals were {code}. That is a statement about the "
                "operations the optimizer was offered, not about the optimizer: the "
                "catalogue asks for something these proposals cannot satisfy, so the "
                "field descriptions or the preconditions are the thing to fix."
            )
        elif share >= 0.5 and code.startswith("V"):
            finding = (
                f"{share:.0%} of refusals were {code}. The optimizer is proposing edits "
                "that break a rule it does not have to guess at -- the rules are in its "
                "prompt -- so either the mode's graph invites that edit or the rule needs "
                "stating more plainly."
            )
        return {"code": code, "count": count, "share": share, "finding": finding}

    def _convergence(self) -> dict[str, Any]:
        """Whether the optimizer has run out of ideas within the surface it sees.

        A no-op or a duplicate means the same thing: every edit it can think of
        lands on a point that is already the case. That is a convergence symptom,
        not one more refusal.
        """
        counts = self._rejection_codes()
        spent = {code: counts.get(code, 0) for code in ("E-noop", "E-duplicate")}
        total = sum(spent.values())
        if not total:
            return {"reason": None, "detail": ""}
        accepted = sum(1 for c in self.population if c.edits)
        if total > accepted:
            return {
                "reason": "surface_exhausted",
                "detail": (
                    f"{total} proposal(s) landed on a state that already exists and "
                    f"{accepted} produced something new, so the optimizer is out of "
                    "ideas within the surface it can see; the surface needs widening, "
                    "not more attempts"
                ),
            }
        return {"reason": None, "detail": "", "noops": total}

    def _cost(self) -> dict[str, Any]:
        durations = [c.duration_s for c in self._evaluated()]
        if not durations:
            return {"n": 0}
        calls = sum(c.model_calls for c in self._evaluated())
        return {
            "n": len(durations),
            "mean_s": round(statistics.fmean(durations), 4),
            "max_s": round(max(durations), 4),
            "total_s": round(sum(durations), 4),
            "inner_model_calls": calls,
        }

    def _rejection_codes(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rejection in self.rejections:
            for code in rejection.codes():
                counts[code] = counts.get(code, 0) + 1
        return dict(sorted(counts.items()))

    def _op_mix(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for candidate in self.population:
            for edit in candidate.edits:
                op = str(edit.get("op", "?"))
                counts[op] = counts.get(op, 0) + 1
        return dict(sorted(counts.items()))

    def _structural_stats(self) -> dict[str, Any]:
        structural = [c for c in self.population if c.is_structural()]
        knob_only = [c for c in self.population if not c.is_structural()]
        structural_rejections = [
            r for r in self.rejections if nn_edits.is_macro(r.edit.get("op", ""))
        ]
        return {
            "structural_candidates": len(structural),
            "knob_candidates": len(knob_only),
            "structural_refusals": len(structural_rejections),
            "structural_yield": _yield(len(structural), len(structural_rejections)),
            "accepted_ops": self._op_mix(),
        }

    # ── optimizer ──────────────────────────────────────────────────────────
    def optimizer_step(
        self, run: Run | None = None
    ) -> tuple[list[Candidate], list[Rejection]]:
        """Propose a batch and turn it into candidates, through the overridable steps.

        Going through ``propose`` and ``install`` rather than straight to the
        optimizer is what lets a strategy change one of them and keep the other.
        """
        run = run or Run(loop=self)
        # The pair a proposer is handed, and why it is the whole population rather
        # than the elite: `describe()` carries a score and the edits that produced
        # it, not the state, so a candidate the loop does not hand over cannot be
        # bred from. Passing only the elite made parent selection impossible inside
        # an optimizer, which is why every SRF method that selects parents had to be
        # a loop instead. Selection is part of "how to search", so the optimizer gets
        # the pool and chooses from it (issue #32).
        parents = [candidate for candidate in self.population if candidate.evaluated]
        if not parents:
            parents = self.select_parents(self.config.elite)
        if not parents:
            return [], []
        before = len(self.rejections)
        proposals = self.propose(run, parents, self.batch_size(run))
        candidates = self.install(run, proposals, parents)
        rejections = self.rejections[before:]
        for rejection in rejections:
            self._journal(
                "rejection",
                attempt=rejection.attempt,
                edit=rejection.edit,
                reasons=rejection.reasons,
            )
        for candidate in candidates:
            self._journal_candidate(candidate)
            self.proposals.setdefault(candidate.id, {
                "id": candidate.id,
                "parent": candidate.parent,
                "edits": candidate.edits[-1:],
                "rationale": "",
                "prediction": "",
                "confidence": 0.0,
                "origin": "optimizer",
                "diagnosis": getattr(self.optimizer, "last_diagnosis", ""),
            })
        self._write_analysis()
        self._append_rejections(rejections)
        self._progress(
            "optimizer",
            proposed=len(proposals),
            accepted=len(candidates),
            refused=len(rejections),
            # The running tally, so a watcher sees which rule is dominating without
            # opening the run directory. Same derivation as the report's.
            refusal_codes=self._rejection_codes(),
            children=[c.id for c in candidates],
        )
        return candidates, rejections

    def _write_analysis(self) -> None:
        diagnosis = str(getattr(self.optimizer, "last_diagnosis", "") or "")
        if not diagnosis:
            return
        path = self.root / "analysis" / f"generation-{self.generation}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# Generation {self.generation} — {self.optimizer.name}\n\n{diagnosis}\n"
        )

    def _append_rejections(self, rejections: Sequence[Rejection]) -> None:
        if not rejections:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "rejections.jsonl").open("a") as handle:
            for rejection in rejections:
                handle.write(
                    json.dumps(
                        {
                            "attempt": rejection.attempt,
                            "edit": rejection.edit,
                            "rationale": rejection.rationale,
                            "reasons": rejection.reasons,
                            "codes": rejection.codes(),
                        },
                        default=str,
                    )
                    + "\n"
                )

    def _exam_moved(self, candidate: Candidate) -> str | None:
        """Whether a candidate changed which items it would be graded on.

        The seed names the class of work. A candidate that rebinds a data node's Task
        or source chooses a different exam, and the held-out split is derived from the
        items a candidate actually has, so it would be graded on its own choice. A
        person's route to other items is a run's ``--source``.
        """
        if not self.population:
            return None
        before = _item_sources(self.population[0].state)
        after = _item_sources(candidate.state)
        changed = sorted(
            node for node in set(before) | set(after) if before.get(node) != after.get(node)
        )
        if not changed:
            return None
        return (
            f"E-exam: the candidate changed which items it would be graded on "
            f"({', '.join(changed)}); the class of work is the seed's, and a run points "
            f"a harness at other items with --source"
        )

    # ── step ───────────────────────────────────────────────────────────────
    def step(
        self, candidates: Sequence[Candidate], run: Run | None = None
    ) -> list[Candidate]:
        """Add offspring that are genuinely new points in the search."""
        known = {candidate.id for candidate in self.population}
        added: list[Candidate] = []
        for candidate in candidates:
            moved = self._exam_moved(candidate)
            if moved is not None:
                self.rejections.append(
                    Rejection(
                        attempt=0,
                        edit=candidate.edits[-1] if candidate.edits else {},
                        reasons=[moved],
                    )
                )
                continue
            if candidate.id in known:
                # The optimizer's loop refuses these now, so reaching here means a
                # strategy bypassed it. Record it rather than losing the slot in
                # silence: a batch that tried fewer points than it claimed is a
                # result nobody can trust.
                self.rejections.append(
                    Rejection(
                        attempt=0,
                        edit=candidate.edits[-1] if candidate.edits else {},
                        reasons=[
                            "E-duplicate: the candidate's state already exists, so its "
                            "batch slot was lost"
                        ],
                    )
                )
                continue
            known.add(candidate.id)
            self.population.append(candidate)
            added.append(candidate)
        self.generation += 1
        self._journal(
            "step",
            generation=self.generation,
            # Journaled, not derived: the adaptive k depends on it, so a resumed
            # run that guessed it would propose something else.
            refusal_pressure=round(self._refusal_pressure, 6),
        )
        self._write_state()
        self._progress("step", added=len(added), population=len(self.population))
        return added

    # ── scheduler ──────────────────────────────────────────────────────────
    def scheduler(self, *, evaluated: int = 0, proposed: int = 0) -> bool:
        """Close the epoch, and say whether there is another.

        The epoch is opened by the loop, not here: a resume has to know which
        iteration it is in, and an epoch that is only counted once it is finished
        cannot say that.
        """
        batch = [c for c in self._evaluated() if c.score is not None]
        scores = [c.score for c in batch if c.exit_ok and c.score is not None]
        summary = EpochSummary(
            epoch=self.epoch,
            evaluated=evaluated,
            best=max(scores) if scores else None,
            mean=(sum(scores) / len(scores)) if scores else None,
            improved=bool(self.loss.get("improved")),
            proposed=proposed,
            accepted=len(self.population),
            rejected=len(self.rejections),
            structural_accepted=sum(1 for c in self.population if c.is_structural()),
        )
        self.epochs.append(summary)
        self._write_state()

        if self.epoch >= self.config.epochs:
            self.bound = "epochs"
        elif self.evaluations >= self.config.max_evaluations:
            self.bound = "evaluations"
        if self.bound:
            # Journaled, not derived: the report states what bound the run, and a
            # run whose steps are separate processes reads that from the log.
            self._journal("bound", bound=self.bound)
        self._progress(
            "scheduler",
            epoch=self.epoch,
            best=summary.best,
            mean=summary.mean,
            improved=summary.improved,
            bound=self.bound,
            another=not self.bound,
        )
        return not self.bound

    # ── the loop ───────────────────────────────────────────────────────────
    # ── the steps a strategy may override ──────────────────────────────────
    def select_parents(self, n: int, *, by: str = "score") -> list[Candidate]:
        """The candidates a proposal is made against. Overridable, not fixed.

        Falls back to the population when nothing has been scored yet: a strategy
        that asks for parents before the first epoch must get the seed, not an empty
        list, or the only way to write a strategy is to know the order of the loop.
        """
        chosen = self.leaderboard(n)
        if chosen:
            return chosen
        return self.population[:n]

    def propose(self, run: Run, parents: Sequence[Candidate], n: int) -> list[Proposal]:
        """One round of proposals. The default delegates to the optimizer.

        A failed round is survived and recorded rather than raised. The run is the
        only place the evaluations so far are reported, so letting a model
        exception escape loses the whole run to a fact about the machine -- and a
        run that ends with no report looks, from outside, exactly like one that
        never started. What was refused and why is the optimizer's business;
        whether the optimizer answered at all is the loop's, and it is recorded in
        ``Optimizer.proposal_failures`` so the report can say which happened.
        """
        self.optimizer.proposal_rounds += 1
        try:
            return self.optimizer.propose(self._context(), n, parents)
        except Exception as exc:  # a proposer that is down is not a crash
            detail = f"{type(exc).__name__}: {exc}"
            self.optimizer.proposal_failures.append(detail)
            run.log(f"{self.optimizer.name} could not propose: {detail}")
            return []

    def batch_size(self, run: Run) -> int:
        return int(self.config.batch_size)

    def install(
        self, run: Run, proposals: Sequence[Proposal], parents: Sequence[Candidate]
    ) -> list[Candidate]:
        """Turn proposals into candidates, asking again with the reasons.

        This is the validating loop the whole design rests on: apply, refuse with a
        coded reason, feed the reasons back, and only ever hand a candidate to
        evaluation. Overriding it is how a strategy changes how many times it asks.
        """
        accepted: list[Candidate] = []
        accepted_ids: set[str] = set()
        wanted = self.batch_size(run)
        # `k` adapts: one more round when the last batch was refused more than it was
        # accepted, because that is the signal that the optimizer is close to
        # something legal rather than out of ideas.
        cap = max(1, self.config.attempts) + (1 if self._refusal_pressure >= 0.5 else 0)
        rounds = 0
        before_all = len(self.rejections)
        for _attempt in range(1, cap + 1):
            rounds += 1
            if len(accepted) >= wanted:
                break
            before = len(self.rejections)
            for proposal in proposals:
                if len(accepted) >= wanted:
                    break
                parent = next((p for p in parents if p.id == proposal.parent), parents[0])
                outcome = run.attempt(proposal, parent)
                if isinstance(outcome, Candidate):
                    if outcome.id in accepted_ids:
                        # Two proposals in one batch that reach the same state. The
                        # population does not hold the first one yet, so the attempt
                        # above could not see it, and `step` would drop the second
                        # without saying why. Refused here, where the optimizer is
                        # still listening.
                        self.rejections.append(
                            Rejection(
                                attempt=rounds,
                                edit=proposal.edit,
                                reasons=[
                                    "E-duplicate: this state was already accepted from "
                                    "the same batch, so evaluating it again would spend "
                                    "a batch slot on a point already scored"
                                ],
                                rationale=proposal.rationale,
                            )
                        )
                        continue
                    accepted_ids.add(outcome.id)
                    accepted.append(outcome)
            if len(accepted) >= wanted or len(self.rejections) == before:
                break
            proposals = self.propose(run, parents, wanted - len(accepted))
            if not proposals:
                break
        refused = len(self.rejections) - before_all
        self._refusal_pressure = refused / max(1, refused + len(accepted))
        self.attempts_used.append(rounds)
        return accepted

    def strategy(self, run: Run) -> None:
        """The default control flow: repeated epochs, each one step of the loop.

        `should_stop` is consulted first so a strategy can end for a reason of its
        own -- a plateau, a convergence -- without replacing the loop.
        """
        while True:
            reason = self.should_stop(run)
            if reason:
                run.stop(reason)
                return
            if not self.epoch_step(run):
                return

    def should_stop(self, run: Run) -> str | None:
        """A reason to stop before the bound, or ``None``. The bound is the default."""
        return run.stopped

    def accepts(self, run: Run, candidate: Candidate) -> bool:
        """Whether a validated candidate is kept. A loser can still be archived."""
        return True

    def update(self, run: Run, candidate: Candidate) -> None:
        """Record a kept candidate. This is where an archive lives."""
        self.archive.add(candidate) if hasattr(self, "archive") else None

    def on_epoch_end(self, run: Run) -> None:
        """Called once per iteration, after the scheduler."""

    def open_epoch(self) -> None:
        """Open the epoch the following steps belong to. Idempotent.

        Public because the eight steps are also the nodes of a graph: an executor
        that runs one node per process has to open the epoch in the node that
        begins it, and reaching for the flag would make the graph depend on private
        state instead of on this method.
        """
        if self._epoch_open:
            return
        self.epoch += 1
        self._journal("epoch_open", epoch=self.epoch)
        self._epoch_open = True

    def close_epoch(self) -> None:
        """Close the epoch, so the next step opens the next one."""
        self._epoch_open = False
        self._journal("epoch_end", epoch=self.epoch)

    def epoch_step(self, run: Run | None = None) -> bool:
        """One iteration, and whether there is another.

        The epoch is opened here and closed by the scheduler, and both are journaled.
        A run that died inside an iteration therefore resumes *into* it rather than
        opening it again, which would count the same epoch twice.
        """
        self.open_epoch()
        evaluated = self.forward()
        self.loss_step()
        self.backward()
        candidates, rejections = self.optimizer_step(run)
        kept = self.step([c for c in candidates if self.accepts(run, c)], run=run)
        for candidate in kept:
            self.update(run, candidate)
        another = self.scheduler(
            evaluated=evaluated, proposed=len(candidates) + len(rejections)
        )
        self.on_epoch_end(run)
        self.close_epoch()
        return another

    # ── report ─────────────────────────────────────────────────────────────
    def leaderboard(self, limit: int | None = None) -> list[Candidate]:
        ordered = sorted(
            [c for c in self._evaluated() if c.score is not None],
            key=lambda c: (-(c.score or 0.0), c.id),
        )
        return ordered[:limit] if limit else ordered

    def _evaluated(self) -> list[Candidate]:
        return [c for c in self.population if c.evaluated]

    def _unevaluated(self) -> list[str]:
        """Candidates the run kept but never ran, because it stopped at its bound.

        The pipeline proposes the next epoch's batch at the end of an epoch, so a run
        that stops on the epoch bound holds one batch that was installed and never
        evaluated. Named in the report so it cannot be read as "all of this ran", and
        so a model-driven proposal round whose output was dropped is visible.
        """
        return [c.id for c in self.population if not c.evaluated]

    def _row(self, candidate: Candidate) -> dict[str, Any]:
        """One candidate as a reader sees it: both scores, and what its size cost.

        The leaderboard, the lineage and the records the optimizer reads all go
        through here, so none of them reports a score without the cost that was
        taken off it.
        """
        nodes = node_count(candidate.state)
        return {
            **candidate.describe(),
            "nodes": nodes,
            "size_cost": round(self.config.parsimony * nodes, 6),
        }

    def scoring(self) -> dict[str, Any]:
        """How a candidate's score is built, said where the numbers are recorded."""
        return {
            "compared": "penalized",
            "parsimony": self.config.parsimony,
            "detail": (
                "candidates are ranked by raw_score - parsimony * nodes; every row "
                "carries both, so a candidate that won on merit is told from one "
                "that won on size"
            ),
        }

    def report(self) -> dict[str, Any]:
        # The audit runs before the payload is built: it needs the winner, and the
        # report is where its numbers are read.
        audit = self.audit_holdout()
        board = self.leaderboard()
        best = board[0] if board else None
        payload = {
            "mode": self.mode_id,
            "bound": self.bound,
            "epochs": self.config.epochs,
            "epochs_completed": self.epoch,
            "batch_size": self.config.batch_size,
            "optimizer": self.optimizer.name,
            "evaluator": self.config.evaluator,
            "evaluations": self.evaluations,
            "population": len(self.population),
            "unevaluated": self._unevaluated(),
            "best": self._row(best) if best else None,
            "best_score": best.score if best else None,
            "raw_best_score": best.raw_score if best else None,
            "seed_score": self.population[0].score if self.population else None,
            "seed_raw_score": self.population[0].raw_score if self.population else None,
            "scoring": self.scoring(),
            "leaderboard": [self._row(c) for c in board],
            "lineage": self._lineage(best),
            "structural_wins": [
                self._row(c) for c in board if c.is_structural() and c.exit_ok
            ],
            "accepted_op_mix": self._op_mix(),
            "structural": self._structural_stats(),
            "rejection_codes": self._rejection_codes(),
            "rejection_reasons": [r for rej in self.rejections for r in rej.reasons][:40],
            "knob_attribution": self.gradient.get("knob_attribution", {}),
            "ports": self.mode.describe()["ports"],
            "memory": self.mode.describe()["memory"],
            "cost": self._cost(),
            "efficiency": self._efficiency(),
            "dominant_refusal": self._dominant_refusal(),
            "convergence": self._convergence(),
            "knob_coverage": {
                "unreachable": self.unreachable_knobs(),
                "detail": "declared and movable, but the selected optimizer cannot "
                          "produce a different value for it",
            },
            "proposal_outcomes": self._proposal_outcomes(),
            "epochs_summary": [asdict(summary) for summary in self.epochs],
            "proposer": self._proposer_health(),
            "holdout": audit,
        }
        self._write("report.json", payload)
        self._write_text("report.md", self._render_report(payload))
        self._write("manifest.json", progress_file.artifact_manifest(self.root))
        self._progress(
            "report",
            best_score=payload["best_score"],
            raw_best_score=payload["raw_best_score"],
            overfit=bool(audit.get("overfit")),
            evaluations=self.evaluations,
            bound=self.bound,
        )
        return payload

    # ── the held-out audit ─────────────────────────────────────────────────
    def audit_holdout(self) -> dict[str, Any]:
        """Re-evaluate the winner on the items no candidate saw, once.

        The search evaluated every candidate on the train part, so this is the only
        place the harness meets the held-out part. The train score is the winner's own
        recorded score and is not penalised: the size cost is a search preference,
        while this is a measurement of the same mode on items it was not tuned
        against. A winner that only works on its training items is reported rather
        than rewarded.
        """
        if self.holdout_audit is not None:
            return self.holdout_audit

        board = self.leaderboard()
        winner = board[0] if board else None
        items, data_nodes = self._audit_items(
            winner.state if winner is not None else {}
        )
        instances: dict[str, int | None] = (
            dict(task_module.counts(items, self.config.holdout))
            if items
            else {task_module.TRAIN: None, task_module.HOLDOUT: None}
        )
        train = winner.raw_score if winner is not None else None
        holdout: float | None = None
        note = ""
        if winner is None:
            note = "the run evaluated no candidate, so there is no winner to audit"
        elif not data_nodes:
            note = (
                "the mode has no data node, so it iterates no items and there is "
                "nothing to hold out"
            )
        elif not items:
            note = (
                "the items this mode iterates could not be read outside a candidate's "
                "run directory, so no holdout was scored"
            )
        elif instances[task_module.HOLDOUT] == 0:
            note = (
                "the selection holds one item or none, so it cannot be split; the "
                "winner was searched on every item it has and no holdout exists"
            )
        else:
            holdout, note = self._holdout_score(winner)

        gap = (
            train - holdout
            if train is not None and holdout is not None
            else None
        )
        audit: dict[str, Any] = {
            "train": train,
            "holdout": holdout,
            "gap": gap,
            "overfit": bool(gap is not None and gap > OVERFIT_THRESHOLD),
            "threshold": OVERFIT_THRESHOLD,
            "instances": instances,
            "winner": winner.id if winner is not None else None,
        }
        if note:
            audit["note"] = note
        self.holdout_audit = audit
        self._write(HOLDOUT_FILE, audit)
        self._journal("holdout", **audit)
        self._progress(
            "holdout",
            train=train,
            holdout=holdout,
            gap=gap,
            overfit=audit["overfit"],
        )
        return audit

    def _holdout_score(self, winner: Candidate) -> tuple[float | None, str]:
        """Run the winner on the held-out part, in a directory of its own.

        Its own directory rather than the candidate's, so the score the search
        recorded stays what it was and a reader can still see both runs.
        """
        workdir = self.root / "holdout" / winner.id
        try:
            workdir.mkdir(parents=True, exist_ok=True)
            self._carry_ledger(workdir)
            self._write_subset(workdir, task_module.HOLDOUT)
            mode = self.mode_class(state=winner.state)
            result = self.evaluator.evaluate(mode, winner.state, workdir)
        except Exception as exc:  # a failed audit is recorded, not raised
            return None, f"the holdout run failed: {type(exc).__name__}: {exc}"
        if not result.exit_ok:
            return None, f"the holdout run failed: {result.error}"
        return float(result.score), ""

    def _audit_items(self, state: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        """The whole item selection, and how many data nodes the mode has.

        The whole selection is loaded rather than the part this run evaluates, so the
        counts do not depend on which part the call is made from. The first data node
        whose items can be read from the run root supplies them; a node bound to a
        source this directory does not have is skipped, and a mode with no data node
        answers with no items and no nodes, which are two different facts.
        """
        if not state:
            return [], 0
        try:
            graph = self.mode_class(state=state).build()
        except Exception:  # a mode that cannot build has no items to count
            return [], 0
        from refactory_dsh.nn.executor import full_items

        nodes = sorted(
            (node for node in graph.nodes.values() if node.kind == "data"),
            key=lambda node: node.id,
        )
        for node in nodes:
            try:
                items = full_items(node, self.root)
            except Exception:  # a data node bound to a source this directory lacks
                continue
            if items:
                return items, len(nodes)
        return [], len(nodes)

    def _lineage(self, candidate: Candidate | None) -> list[dict[str, Any]]:
        if candidate is None:
            return []
        by_id = {c.id: c for c in self.population}
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        while candidate is not None and candidate.id not in seen:
            seen.add(candidate.id)
            chain.append(self._row(candidate))
            candidate = by_id.get(candidate.parent or "")
        return list(reversed(chain))

    def _proposal_outcomes(self) -> list[dict[str, Any]]:
        """Every accepted proposal, with what actually happened to the score.

        Both the compared score and the evaluator's number are recorded, with their
        deltas against the parent, so a proposal that grew the graph shows the size
        cost beside the gain it bought.
        """
        by_id = {c.id: c for c in self.population}
        out: list[dict[str, Any]] = []
        for candidate_id, record in sorted(self.proposals.items()):
            candidate = by_id.get(candidate_id)
            if candidate is None or not candidate.evaluated:
                continue
            parent = by_id.get(candidate.parent or "")
            parent_score = parent.score if parent is not None else None
            parent_raw = parent.raw_score if parent is not None else None
            delta = (
                round((candidate.score or 0.0) - parent_score, 6)
                if parent_score is not None and candidate.score is not None
                else None
            )
            raw_delta = (
                round((candidate.raw_score or 0.0) - parent_raw, 6)
                if parent_raw is not None and candidate.raw_score is not None
                else None
            )
            out.append(
                {
                    **record,
                    "score": candidate.score,
                    "raw_score": candidate.raw_score,
                    "parent_score": parent_score,
                    "parent_raw_score": parent_raw,
                    "delta": delta,
                    "raw_delta": raw_delta,
                    "exit_ok": candidate.exit_ok,
                    "error": candidate.error,
                }
            )
        return out

    def _render_report(self, payload: dict[str, Any]) -> str:
        lines = [
            f"# Outer loop — {payload['mode']}",
            "",
            f"Optimizer `{payload['optimizer']}`, {payload['epochs_completed']} epoch(s) "
            f"of {payload['epochs']}, batch {payload['batch_size']}, "
            f"{payload['evaluations']} inner run(s). "
            f"Stopped because **{payload['bound']}** bound the run.",
            "",
            f"Seed score **{payload['seed_score']}** → best **{payload['best_score']}**"
            f" (raw {payload['seed_raw_score']} → {payload['raw_best_score']}). "
            f"The compared score is the raw one minus "
            f"{payload['scoring']['parsimony']} per node of the candidate's graph.",
            "",
            "## Held-out audit",
            "",
            _holdout_sentence(payload.get("holdout") or {}),
            "",
            "## Leaderboard",
            "",
            _md_table(
                ["id", "parent", "score", "raw", "nodes", "structural", "edits", "ok"],
                [
                    [
                        c["id"], c.get("parent"), c.get("score"), c.get("raw_score"),
                        c.get("nodes"), c.get("structural"),
                        json.dumps(c.get("edits"), default=str), c.get("exit_ok"),
                    ]
                    for c in payload["leaderboard"]
                ],
            ),
            "",
            "## Structural candidates that scored",
            "",
            _md_table(
                ["id", "score", "raw", "nodes", "edits"],
                [[c["id"], c.get("score"), c.get("raw_score"), c.get("nodes"),
                  json.dumps(c.get("edits"), default=str)]
                 for c in payload["structural_wins"]],
            ),
            "",
            "## Why proposals were refused",
            "",
            payload["dominant_refusal"]["finding"] or "_No dominant refusal._",
            "",
            *(
                [
                    "## Did the proposer answer?",
                    "",
                    payload["proposer"]["finding"],
                    "",
                ]
                if payload["proposer"]["failure_count"]
                else []
            ),
            "## Did the search run out of ideas?",
            "",
            payload["convergence"]["detail"] or "_No: proposals were still producing "
            "new points._",
            "",
            f"Attempts per candidate that could run: "
            f"{payload['efficiency']['attempts_per_candidate']} "
            f"(effective k {payload['efficiency']['effective_k']}).",
            "",
            "## Refusals by rule",
            "",
            _md_table(
                ["rule", "count"], [[code, count] for code, count in payload["rejection_codes"].items()]
            ),
            "",
            "## Accepted edits by operation",
            "",
            _md_table(
                ["op", "count"],
                [[op, count] for op, count in payload["accepted_op_mix"].items()],
            ),
            "",
            f"Structural yield: {payload['structural']['structural_candidates']} "
            f"candidate(s) from {payload['structural']['structural_refusals']} "
            f"refusal(s) — {payload['structural']['structural_yield']}.",
            "",
            "## What the mode publishes",
            "",
            _md_table(
                ["port", "path", "media type", "description"],
                [[p["name"], p["path"], p["media_type"], p.get("description", "")]
                 for p in payload["ports"]],
            ),
            "",
            "## What the mode may remember",
            "",
            _md_table(
                ["namespace", "kind", "retention", "description"],
                [[m["namespace"], m["kind"], m["retention"], m.get("description", "")]
                 for m in payload["memory"]],
            ),
            "",
            "## Inner runs",
            "",
            f"{payload['evaluations']} candidate(s) evaluated, total inner wall clock "
            f"{payload['cost'].get('total_s')}s, and "
            f"{payload['cost'].get('inner_model_calls')} model call(s) made inside the "
            "graphs themselves (an inserted agent node costs these).",
            "",
            *(
                [
                    f"{len(payload['unevaluated'])} candidate(s) were proposed for the "
                    "next epoch and never evaluated, because the run stopped at its "
                    "bound: "
                    + ", ".join(f"`{candidate}`" for candidate in payload["unevaluated"])
                    + ".",
                    "",
                ]
                if payload.get("unevaluated")
                else []
            ),
            "## Proposal versus outcome",
            "",
            _md_table(
                ["id", "origin", "parent_score", "score", "delta", "raw_score",
                 "raw_delta", "rationale"],
                [
                    [
                        p["id"], p.get("origin"), p.get("parent_score"), p.get("score"),
                        p.get("delta"), p.get("raw_score"), p.get("raw_delta"),
                        (p.get("rationale") or "")[:80],
                    ]
                    for p in payload["proposal_outcomes"]
                ],
            ),
            "",
            "## Lineage of the best",
            "",
            _md_table(
                ["id", "score", "raw", "nodes", "edits"],
                [[c["id"], c.get("score"), c.get("raw_score"), c.get("nodes"),
                  json.dumps(c.get("edits"), default=str)]
                 for c in payload["lineage"]],
            ),
            "",
        ]
        return "\n".join(lines)

    # ── context ────────────────────────────────────────────────────────────
    def _context(self) -> ProposalContext:
        described = self.mode.describe()
        board = self.leaderboard(8)
        return ProposalContext(
            epoch=self.epoch,
            generation=self.generation,
            mode_name=self.mode_id,
            best_score=(board[0].score if board else None),
            mean_score=self.loss.get("mean"),
            knobs=[
                {"name": name, **spec, "reachable": self._reachable(name, spec)}
                for name, spec in described["knobs"].items()
            ],
            nodes=described["nodes"],
            edges=described["edges"],
            leaderboard=[self._row(c) for c in board],
            history=self._history(),
            tried=self._tried(),
            elite=int(self.config.elite),
            history_truncated=self._history_truncated(),
            prior_runs=run_history.prior_summaries(
                self.root.parent, limit=self.config.prior_runs, exclude=self.root
            ),
            traces=[self.gradient] if self.gradient else [],
            catalogue=nn_edits.catalogue(self.config.edit_preset),
            insertable=[node.to_state() for node in self.mode.insertable()],
            frozen=list(self.config.frozen),
            rejections=summarise_reasons(
                [
                    reason
                    for rejection in self.rejections[-8:]
                    for reason in rejection.reasons
                ]
            ),
            preset=self.config.edit_preset,
            strategy=dict(self.strategy_decision),
        )

    def _reachable(self, name: str, spec: dict[str, Any]) -> bool:
        """Whether the selected optimizer can move this knob at all."""
        knob = Knob.from_spec(self.mode.state_dict()["knobs"][name]["spec"])
        if knob.frozen or name in self.config.frozen:
            return False
        return self.optimizer.can_move(knob, spec.get("value"))

    def unreachable_knobs(self) -> list[str]:
        """Movable knobs the selected optimizer cannot move, named.

        Reported rather than tolerated: a search over four of five declared knobs
        looks exactly like a search over five.
        """
        described = self.mode.describe()["knobs"]
        return sorted(
            name
            for name, spec in described.items()
            if not spec.get("frozen")
            and name not in self.config.frozen
            and not self._reachable(name, spec)
        )

    def state_dict(self) -> dict[str, Any]:
        """The search's own state: where it is, and what it is searching.

        Distinct from a mode's `state_dict`, which is the thing being searched. A
        strategy that wants to inspect the search gets this.
        """
        return {
            "mode": self.mode_id,
            "epoch": self.epoch,
            "generation": self.generation,
            "evaluations": self.evaluations,
            "bound": self.bound,
            "evaluator": self.config.evaluator,
            "optimizer": self.optimizer.name,
            "seed": self.mode.state_dict(),
            "scoring": self.scoring(),
            "population": [self._row(candidate) for candidate in self.population],
        }

    def _tried(self) -> list[str]:
        """Every state id that exists, so a duplicate can be refused."""
        return sorted(candidate.id for candidate in self.population)

    def _history(self) -> list[dict[str, Any]]:
        """The evaluated candidates, newest last, bounded."""
        evaluated = [self._row(c) for c in self.population if c.evaluated]
        return evaluated[-self.config.history_limit :]

    def _history_truncated(self) -> bool:
        return sum(1 for c in self.population if c.evaluated) > self.config.history_limit

    # ── handing state between processes ────────────────────────────────────
    def write_pending(self, candidates: Sequence[Candidate]) -> Path:
        """Write the offspring one step produced, for the step that installs them.

        A graph node that proposes and a graph node that installs are two
        processes, so what the first found has to be written down. That is a cost
        of expressing the loop as a graph; the alternative is one node that does
        both, which hides the seam rather than removing it.
        """
        return self._write(
            PENDING,
            [
                {
                    "id": candidate.id,
                    "parent": candidate.parent,
                    "edits": candidate.edits,
                    "state": candidate.state,
                }
                for candidate in candidates
            ],
        )

    def read_pending(self) -> list[Candidate]:
        """The offspring the proposal node left, as candidates again."""
        path = self.root / PENDING
        if not path.exists():
            return []
        return [
            Candidate.from_state(
                record["state"], parent=record.get("parent"), edits=record.get("edits") or ()
            )
            for record in json.loads(path.read_text())
        ]

    def config_from_root(self) -> "LoopConfig":
        """This run's own configuration, as the run directory recorded it."""
        return LoopConfig.from_dict(json.loads((self.root / "config.json").read_text()))

    # ── the journal, and resume ────────────────────────────────────────────
    def _journal(self, event: str, **payload: Any) -> None:
        """Append one event. The journal is the run's durable state.

        Append-only and replayed rather than snapshotted: a snapshot of the
        population has to be kept consistent with the counters, and the old glue's
        rehydrate did exactly that and produced real bugs. One log means one source
        of truth, and the same one the report already reads.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / JOURNAL).open("a") as handle:
            handle.write(json.dumps({"event": event, **payload}, default=str) + "\n")

    def _journal_candidate(self, candidate: Candidate) -> None:
        self._journal(
            "candidate",
            id=candidate.id,
            parent=candidate.parent,
            edits=candidate.edits,
            state=candidate.state,
        )

    def _replay_if_asked(self) -> bool:
        """Rebuild the run from its journal, if there is one and resume was asked for."""
        journal = self.root / JOURNAL
        if not self.config.resume:
            if journal.exists():
                journal.unlink()
            return False
        if not journal.exists():
            raise LoopError(
                f"resume was asked for but {journal} does not exist, so there is "
                "nothing to carry on from"
            )
        self._replay(journal)
        return True

    def _replay(self, journal: Path) -> None:
        """Rebuild the population, the counters and the refusals from the log."""
        by_id: dict[str, Candidate] = {}
        order: list[str] = []
        for line in journal.read_text().splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # A half-written last line is what a crash looks like. Stopping at
                # it is the point of an append-only log.
                break
            event = record.get("event")
            if event == "candidate":
                candidate = Candidate.from_state(
                    record["state"],
                    parent=record.get("parent"),
                    edits=record.get("edits") or (),
                )
                if candidate.id not in by_id:
                    by_id[candidate.id] = candidate
                    order.append(candidate.id)
            elif event == "score":
                candidate = by_id.get(record.get("id"))
                if candidate is None:
                    continue
                candidate.score = record.get("score")
                # A journal written before the size cost existed has no `raw_score`;
                # its `score` is the evaluator's own number, which is then the raw
                # one too.
                raw = record.get("raw_score", record.get("score"))
                candidate.raw_score = None if raw is None else float(raw)
                candidate.exit_ok = bool(record.get("exit_ok", True))
                candidate.error = record.get("error")
                candidate.duration_s = float(record.get("duration_s") or 0.0)
                candidate.model_calls = int(record.get("model_calls") or 0)
                candidate.facts = dict(record.get("facts") or {})
                candidate.evaluated = True
            elif event == "rejection":
                self.rejections.append(
                    Rejection(
                        attempt=int(record.get("attempt") or 0),
                        edit=dict(record.get("edit") or {}),
                        reasons=list(record.get("reasons") or []),
                    )
                )
            elif event == "epoch":
                self.epoch = int(record.get("epoch") or self.epoch)
            elif event == "epoch_open":
                self.epoch = int(record.get("epoch") or self.epoch)
                self._epoch_open = True
            elif event == "epoch_end":
                self.epoch = int(record.get("epoch") or self.epoch)
                self._epoch_open = False
            elif event == "step":
                # The generation is what the proposal stream is derived from, so it
                # is journaled rather than inferred: guessing it makes a resumed run
                # propose something else.
                self.generation = int(record.get("generation") or self.generation)
                self._refusal_pressure = float(record.get("refusal_pressure") or 0.0)
            elif event == "bound":
                self.bound = str(record.get("bound") or self.bound)
            elif event == "loss":
                self.loss = {
                    key: value
                    for key, value in record.items()
                    if key != "event"
                }

        if not order:
            raise LoopError(f"{journal} holds no candidate, so there is nothing to resume")
        self.population = [by_id[node_id] for node_id in order]
        self.evaluations = sum(1 for c in self.population if c.evaluated)
        self.proposals = {
            candidate.id: {
                "id": candidate.id,
                "parent": candidate.parent,
                "edits": candidate.edits,
                "rationale": "replayed from the journal",
                "prediction": "",
                "confidence": 0.0,
                "origin": "seed_population" if candidate.parent else "seed_population",
            }
            for candidate in self.population
        }

    # ── persistence ────────────────────────────────────────────────────────
    def _write(self, name: str, payload: Any) -> Path:
        return self._write_text(name, json.dumps(payload, indent=2, default=str))

    def _write_text(self, name: str, text: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def _progress(self, step: str, **fields: Any) -> None:
        """One line of "where the run is", readable while it is still running.

        The journal already records everything, but it is replayed rather than
        tailed and a step is only interesting once you know which one it was. This
        is the same facts at the granularity a reader watches: one line per
        candidate evaluated, not one per epoch, because an epoch can be an hour.
        """
        if not self.config.progress:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        record = {
            "step": step,
            "epoch": self.epoch,
            "generation": self.generation,
            "evaluations": self.evaluations,
            "time": round(time.time(), 3),
            **fields,
        }
        with (self.root / progress_file.PROGRESS).open("a") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
        if self._printer is not None:
            self._printer(record)

    def _write_state(self) -> None:
        self._write(
            "state.json",
            {
                "mode": self.mode_id,
                "epoch": self.epoch,
                "generation": self.generation,
                "evaluations": self.evaluations,
                "scoring": self.scoring(),
                "population": [self._row(candidate) for candidate in self.population],
            },
        )


def _other_value(knob: Knob, current: Any, rng: random.Random) -> Any:
    """A value a knob accepts that is not the one it currently holds.

    One sampler for the whole system, so the starting sweep and the random
    optimizer agree about what is reachable.
    """
    return knob.sample(rng, current)


def node_count(state: dict[str, Any]) -> int:
    """How many nodes a candidate's graph holds, which is what the size cost counts."""
    graph = state.get("graph") or {}
    return len(graph.get("nodes") or {})


def _holdout_line(audit: dict[str, Any]) -> str:
    """One line saying what the held-out audit found, for `refactory optimize`."""
    if not audit:
        return "holdout: this run recorded no audit of the winner"
    instances = audit.get("instances") or {}
    train = audit.get("train")
    holdout = audit.get("holdout")
    split = (
        f"{instances.get('train')} train / {instances.get('holdout')} holdout instance(s)"
    )
    if holdout is None or train is None:
        return f"holdout: {audit.get('note') or 'no holdout score'} ({split})"
    verdict = "overfit" if audit.get("overfit") else "holds up"
    return (
        f"holdout: train {train:.4f}, holdout {holdout:.4f}, "
        f"gap {audit.get('gap'):.4f} against threshold {audit.get('threshold')} "
        f"— {verdict} ({split})"
    )


def _holdout_sentence(audit: dict[str, Any]) -> str:
    """The audit as one paragraph for the report, saying both numbers and the gap."""
    if not audit:
        return "_This run recorded no audit of the winner._"
    line = _holdout_line(audit)
    if audit.get("overfit"):
        return (
            f"{line}. Every candidate was searched on the train part, so the winner "
            "may be a harness that only works on the items it was tuned against."
        )
    return f"{line}."


def _yield(accepted: int, refused: int) -> str:
    total = accepted + refused
    if not total:
        return "n/a"
    return f"{100.0 * accepted / total:.0f}%"


def _trim(facts: dict[str, Any], limit: int = 400) -> dict[str, Any]:
    """Keep the facts, but not an unbounded model answer inside the trace."""
    out: dict[str, Any] = {}
    for key, value in facts.items():
        if isinstance(value, str) and len(value) > limit:
            out[key] = value[:limit] + "…"
        elif key.endswith("_verdict"):
            continue
        else:
            out[key] = value
    return out


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_(none)_"
    head = "| " + " | ".join(headers) + " |"
    rule = "|" + "|".join("---" for _ in headers) + "|"
    body = [
        "| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in row) + " |"
        for row in rows
    ]
    return "\n".join([head, rule, *body])


def run(
    mode_id: str,
    *,
    config: LoopConfig | None = None,
    project: Path | None = None,
    model: Any = None,
) -> dict[str, Any]:
    """Build a loop and run it to its bound."""
    loop = Loop(mode_id, config, project=project, model=model)
    return loop.run()


def main(argv: list[str] | None = None) -> int:
    """``python -m refactory_dsh.loop <package>/<mode> [options]``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m refactory_dsh.loop",
        description="Run the optimizer loop on a mode.",
    )
    parser.add_argument("mode", help="the mode to optimize, as <package>/<name>")
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=4)
    from refactory_dsh import optim, strategies

    # The registry is the list of what can be selected. Hardcoding it here is how
    # the two buckets drifted apart in the first place: five optimizers could be
    # registered and still not be reachable from the command line.
    parser.add_argument("--optimizer", default="autoresearch", choices=optim.names())
    parser.add_argument(
        "--strategy", default="default", choices=strategies.names(),
        help="which outer loop drives the search; the default is the harness's own",
    )
    parser.add_argument(
        "--evaluator", default="walker", choices=["walker", "dsh"],
        help="which runtime runs a candidate: walker (fast, for search) or dsh "
             "(the runtime that ships)",
    )
    parser.add_argument("--preset", default="broad", help="which edits the optimizer may use")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--elite", type=int, default=3, help="how many parents to propose against")
    parser.add_argument("--max-evaluations", type=int, default=64)
    parser.add_argument(
        "--holdout", type=float, default=task_module.DEFAULT_HOLDOUT,
        help="the fraction of the Task's items held out of the search and used to "
             "audit the winner; one item cannot be split, which the audit records",
    )
    parser.add_argument(
        "--parsimony", type=float, default=0.01,
        help="what each node of a candidate's graph costs its score; the search "
             "compares raw_score - parsimony * nodes",
    )
    parser.add_argument("--attempts", type=int, default=3, help="retries per batch after a refusal")
    parser.add_argument(
        "--verbose", action="store_true",
        help="print every event, and each candidate's own run output, not only the "
             "meaningful ones",
    )
    parser.add_argument(
        "--runs-root",
        default=".factory/loop",
        help=(
            "the run directory to write, read back with `console --run <this path>`; "
            "the console's --runs-root is a parent of runs, not this"
        ),
    )
    parser.add_argument(
        "--intent", default="",
        help="what this run is for, in the asker's words; seeds the run's intent "
             "ledger, which every agent node of an interactive run reads",
    )
    parser.add_argument("--project", default=str(project_root()))
    parser.add_argument(
        "--knob", action="append", default=[], metavar="NAME=VALUE",
        help="a knob value the search starts from (repeatable); required for a mode "
             "whose inputs are frozen knobs, which the search itself may not move",
    )
    parser.add_argument(
        "--model", default="",
        help="model name for this run's completions: the reasoning optimizer's "
             "proposals, and the agent nodes of the mode being evaluated",
    )
    parser.add_argument(
        "--provider", default="", help="provider route that model is on (default: the settings default)"
    )
    args = parser.parse_args(argv)

    # A client for whatever needs a model: the reasoning optimizer when it is the one
    # proposing, and *any* mode whose graph has an agent node, because the evaluator
    # has to supply one to run those nodes. Building it only for `autoresearch` made
    # every agent mode unevaluable under every other optimizer — each candidate failed
    # with `agent node 'propose' needs a model client` and the run reported a search
    # that found nothing. Constructing it is free: it spawns nothing until asked.
    from refactory_dsh.model import DshModelClient

    model = DshModelClient(model=args.model, provider=args.provider)

    config = LoopConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        optimizer=args.optimizer,
        evaluator=args.evaluator,
        edit_preset=args.preset,
        seed=args.seed,
        elite=args.elite,
        max_evaluations=args.max_evaluations,
        # A search a person started prints what it is doing while it does it.
        print_events=True,
        verbose=bool(args.verbose),
        attempts=args.attempts,
        runs_root=Path(args.runs_root),
        intent=args.intent,
        holdout=args.holdout,
        parsimony=args.parsimony,
        knobs={name.strip(): value.strip() for pair in args.knob
               for name, _, value in [pair.partition("=")] if name.strip()},
    )
    try:
        loop = strategies.create(
            args.strategy, args.mode, config, project=Path(args.project), model=model
        )
        report = loop.run()
    except LoopError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    audit = report.get("holdout") or {}
    # One line a person reads without opening the run directory. On stderr, because
    # everything this command prints on stdout is the one JSON object a caller parses.
    print(_holdout_line(audit), file=sys.stderr)
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "optimizer": report["optimizer"],
                "evaluator": loop.config.evaluator,
                "bound": report["bound"],
                "evaluations": report["evaluations"],
                "seed_score": report["seed_score"],
                "best_score": report["best_score"],
                "raw_best_score": report["raw_best_score"],
                "scoring": report["scoring"],
                "holdout": audit,
                "structural_wins": len(report["structural_wins"]),
                "rejection_codes": report["rejection_codes"],
                "accepted_op_mix": report["accepted_op_mix"],
                "report": str(Path(args.runs_root) / "report.md"),
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    raise SystemExit(main())
