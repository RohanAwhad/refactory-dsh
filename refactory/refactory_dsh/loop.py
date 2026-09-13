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
"""

from __future__ import annotations

import json
import random
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from refactory_dsh import history as run_history
from refactory_dsh.memory import MemoryStore
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


class LoopError(RuntimeError):
    """The run cannot proceed. Always names what is wrong with the mode."""


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
    runs_root: Path = Path(".factory/loop")
    #: Wall-clock ceiling for one inner run, enforced by the executor's step cap.
    max_steps: int = 2000
    #: Attempts the optimizer gets per batch before it stops asking.
    attempts: int = 3
    #: Knobs the operator freezes for this run, whatever the candidate says.
    frozen: tuple[str, ...] = ()
    #: How many evaluated candidates the optimizer is shown. Bounded on purpose:
    #: an unbounded history in a prompt is how a search stops thinking.
    history_limit: int = 200
    #: How many previous runs to summarise as a prior.
    prior_runs: int = 3
    #: Where a ``global``-retention memory lives. Injected so a test can put it
    #: somewhere temporary instead of the home directory.
    memory_global_root: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["runs_root"] = str(self.runs_root)
        return data


@dataclass
class EpochSummary:
    epoch: int
    evaluated: int
    best: float | None
    mean: float | None
    improved: bool
    proposed: int
    accepted: int
    rejected: int
    structural_accepted: int


class Loop:
    """Drive the eight steps over one mode."""

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

        self.population: list[Candidate] = []
        self.rejections: list[Rejection] = []
        self.proposals: dict[str, dict[str, Any]] = {}
        self.epoch = 0
        self.generation = 0
        self.evaluations = 0
        self.bound = ""
        self.loss: dict[str, Any] = {}
        self.gradient: dict[str, Any] = {}
        self.epochs: list[EpochSummary] = []

    # ── init ───────────────────────────────────────────────────────────────
    def init(self) -> None:
        """Seed the population and refuse to search a mode that cannot be run."""
        seed_state = self.mode.state_dict()
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

        seed = Candidate.from_state(seed_state)
        self.population = [seed]
        self._seed_population(seed)
        self._write_state()

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
        while len(edits) < wanted:
            edit = nn_edits.random_edit(
                seed.state,
                preset=self.config.edit_preset,
                insertable=insertable,
                rng=sampler._rng,  # noqa: SLF001 - one stream for the whole sweep
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
        for candidate in batch:
            if self.evaluations >= self.config.max_evaluations:
                break
            self._evaluate(candidate)
        return len(batch)

    def _evaluate(self, candidate: Candidate) -> None:
        workdir = self.root / "runs" / candidate.id
        mode = self.mode_class(state=candidate.state)
        # One store per inner run: a `run`-retention memory is scoped to this run
        # and no other, which is the enforcement of that policy.
        store = MemoryStore(
            mode.memory_declarations(),
            run_root=workdir,
            project_root=self.project / ".factory",
            global_root=self.config.memory_global_root,
        )
        started = time.time()
        result = mode.evaluate(
            workdir, model=self.model, memory=store, max_steps=self.config.max_steps
        )
        candidate.score = result.score
        candidate.mean_score = result.score
        candidate.facts = _trim(result.facts)
        candidate.exit_ok = result.exit_ok
        candidate.error = result.error
        candidate.duration_s = round(time.time() - started, 4)
        candidate.model_calls = result.model_calls
        candidate.evaluated = True
        self.evaluations += 1
        (workdir / "candidate.json").write_text(
            json.dumps(
                {
                    "id": candidate.id,
                    "parent": candidate.parent,
                    "score": candidate.score,
                    "exit_ok": candidate.exit_ok,
                    "error": candidate.error,
                    "duration_s": candidate.duration_s,
                    "edits": candidate.edits,
                    "facts": candidate.facts,
                    "model_calls": candidate.model_calls,
                    # What the run published, against what the mode declared: a port
                    # that is missing is a promise the mode did not keep.
                    "ports": {
                        port.name: (workdir / port.path).exists()
                        for port in mode.port_declarations()
                    },
                },
                indent=2,
                default=str,
            )
        )

    # ── loss ───────────────────────────────────────────────────────────────
    def loss_step(self) -> dict[str, Any]:
        evaluated = self._evaluated()
        scores = [c.score for c in evaluated if c.exit_ok and c.score is not None]
        best = max(scores) if scores else None
        mean = (sum(scores) / len(scores)) if scores else None
        previous = self.loss.get("best")
        self.loss = {
            "epoch": self.epoch,
            "batch": len(evaluated),
            "scores": scores,
            "mean": mean,
            "best": best,
            "best_ever": max([s for s in (previous, best) if s is not None], default=None),
            "improved": bool(best is not None and previous is not None and best > previous),
            "run_failures": [c.id for c in self.population if c.evaluated and not c.exit_ok],
        }
        self._write("loss.json", self.loss)
        return self.loss

    # ── backward ───────────────────────────────────────────────────────────
    def backward(self) -> dict[str, Any]:
        """Turn the traces into the gradient the optimizer reads."""
        evaluated = [c for c in self._evaluated() if c.exit_ok and c.score is not None]
        self.gradient = {
            "epoch": self.epoch,
            "knob_attribution": self._knob_attribution(evaluated),
            "cost": self._cost(),
            "run_failures": self.loss.get("run_failures", []),
            "rejection_codes": self._rejection_codes(),
            "rejection_reasons": [r for rej in self.rejections[-8:] for r in rej.reasons],
            "accepted_op_mix": self._op_mix(),
            "structural": self._structural_stats(),
            "leaderboard": [c.describe() for c in self.leaderboard(8)],
        }
        self._write("gradient.json", self.gradient)
        return self.gradient

    def _knob_attribution(self, evaluated: Sequence[Candidate]) -> dict[str, Any]:
        """What each parameter value was worth, over everything evaluated so far.

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
        """The enumerated values of a knob that have not been evaluated."""
        knob = self.parameters.get(name)
        if knob is None or knob.kind != "enum":
            return []
        return [member for member in knob.enum if str(member) not in tried]

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
    def optimizer_step(self) -> tuple[list[Candidate], list[Rejection]]:
        parents = self.leaderboard(self.config.elite)
        if not parents:
            return [], []
        ctx = self._context()
        candidates, rejections = self.optimizer.propose_valid(
            ctx,
            self.config.batch_size,
            parents=parents,
            invariants=self.invariants,
            preset=self.config.edit_preset,
            seen=self._tried(),
        )
        self.rejections.extend(rejections)
        for candidate in candidates:
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

    # ── step ───────────────────────────────────────────────────────────────
    def step(self, candidates: Sequence[Candidate]) -> list[Candidate]:
        """Add offspring that are genuinely new points in the search."""
        known = {candidate.id for candidate in self.population}
        added: list[Candidate] = []
        for candidate in candidates:
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
        self._write_state()
        return added

    # ── scheduler ──────────────────────────────────────────────────────────
    def scheduler(self, *, evaluated: int = 0, proposed: int = 0) -> bool:
        """Close the epoch, and say whether there is another."""
        self.epoch += 1
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
            return False
        if self.evaluations >= self.config.max_evaluations:
            self.bound = "evaluations"
            return False
        return True

    # ── the loop ───────────────────────────────────────────────────────────
    def run(self) -> dict[str, Any]:
        self.init()
        while True:
            evaluated = self.forward()
            self.loss_step()
            self.backward()
            candidates, rejections = self.optimizer_step()
            self.step(candidates)
            if not self.scheduler(evaluated=evaluated, proposed=len(candidates) + len(rejections)):
                break
        return self.report()

    # ── report ─────────────────────────────────────────────────────────────
    def leaderboard(self, limit: int | None = None) -> list[Candidate]:
        ordered = sorted(
            [c for c in self._evaluated() if c.score is not None],
            key=lambda c: (-(c.score or 0.0), c.id),
        )
        return ordered[:limit] if limit else ordered

    def _evaluated(self) -> list[Candidate]:
        return [c for c in self.population if c.evaluated]

    def report(self) -> dict[str, Any]:
        board = self.leaderboard()
        best = board[0] if board else None
        payload = {
            "mode": self.mode_id,
            "bound": self.bound,
            "epochs": self.config.epochs,
            "epochs_completed": self.epoch,
            "batch_size": self.config.batch_size,
            "optimizer": self.optimizer.name,
            "evaluations": self.evaluations,
            "population": len(self.population),
            "best": best.describe() if best else None,
            "best_score": best.score if best else None,
            "seed_score": self.population[0].score if self.population else None,
            "leaderboard": [c.describe() for c in board],
            "lineage": self._lineage(best),
            "structural_wins": [
                c.describe() for c in board if c.is_structural() and c.exit_ok
            ],
            "accepted_op_mix": self._op_mix(),
            "structural": self._structural_stats(),
            "rejection_codes": self._rejection_codes(),
            "rejection_reasons": [r for rej in self.rejections for r in rej.reasons][:40],
            "knob_attribution": self.gradient.get("knob_attribution", {}),
            "ports": self.mode.describe()["ports"],
            "memory": self.mode.describe()["memory"],
            "cost": self._cost(),
            "knob_coverage": {
                "unreachable": self.unreachable_knobs(),
                "detail": "declared and movable, but the selected optimizer cannot "
                          "produce a different value for it",
            },
            "proposal_outcomes": self._proposal_outcomes(),
            "epochs_summary": [asdict(summary) for summary in self.epochs],
        }
        self._write("report.json", payload)
        self._write_text("report.md", self._render_report(payload))
        return payload

    def _lineage(self, candidate: Candidate | None) -> list[dict[str, Any]]:
        if candidate is None:
            return []
        by_id = {c.id: c for c in self.population}
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        while candidate is not None and candidate.id not in seen:
            seen.add(candidate.id)
            chain.append(candidate.describe())
            candidate = by_id.get(candidate.parent or "")
        return list(reversed(chain))

    def _proposal_outcomes(self) -> list[dict[str, Any]]:
        """Every accepted proposal, with what actually happened to the score."""
        by_id = {c.id: c for c in self.population}
        out: list[dict[str, Any]] = []
        for candidate_id, record in sorted(self.proposals.items()):
            candidate = by_id.get(candidate_id)
            if candidate is None or not candidate.evaluated:
                continue
            parent = by_id.get(candidate.parent or "")
            parent_score = parent.score if parent is not None else None
            delta = (
                round((candidate.score or 0.0) - parent_score, 6)
                if parent_score is not None and candidate.score is not None
                else None
            )
            out.append(
                {
                    **record,
                    "score": candidate.score,
                    "parent_score": parent_score,
                    "delta": delta,
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
            f"Seed score **{payload['seed_score']}** → best **{payload['best_score']}**.",
            "",
            "## Leaderboard",
            "",
            _md_table(
                ["id", "parent", "score", "structural", "edits", "ok"],
                [
                    [
                        c["id"], c.get("parent"), c.get("score"), c.get("structural"),
                        json.dumps(c.get("edits"), default=str), c.get("exit_ok"),
                    ]
                    for c in payload["leaderboard"]
                ],
            ),
            "",
            "## Structural candidates that scored",
            "",
            _md_table(
                ["id", "score", "edits"],
                [[c["id"], c.get("score"), json.dumps(c.get("edits"), default=str)]
                 for c in payload["structural_wins"]],
            ),
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
            "## Proposal versus outcome",
            "",
            _md_table(
                ["id", "origin", "parent_score", "score", "delta", "rationale"],
                [
                    [
                        p["id"], p.get("origin"), p.get("parent_score"), p.get("score"),
                        p.get("delta"), (p.get("rationale") or "")[:80],
                    ]
                    for p in payload["proposal_outcomes"]
                ],
            ),
            "",
            "## Lineage of the best",
            "",
            _md_table(
                ["id", "score", "edits"],
                [[c["id"], c.get("score"), json.dumps(c.get("edits"), default=str)]
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
            leaderboard=[c.describe() for c in board],
            history=self._history(),
            tried=self._tried(),
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

    def _tried(self) -> list[str]:
        """Every state id that exists, so a duplicate can be refused."""
        return sorted(candidate.id for candidate in self.population)

    def _history(self) -> list[dict[str, Any]]:
        """The evaluated candidates, newest last, bounded."""
        evaluated = [c.describe() for c in self.population if c.evaluated]
        return evaluated[-self.config.history_limit :]

    def _history_truncated(self) -> bool:
        return sum(1 for c in self.population if c.evaluated) > self.config.history_limit

    # ── persistence ────────────────────────────────────────────────────────
    def _write(self, name: str, payload: Any) -> Path:
        return self._write_text(name, json.dumps(payload, indent=2, default=str))

    def _write_text(self, name: str, text: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def _write_state(self) -> None:
        self._write(
            "state.json",
            {
                "mode": self.mode_id,
                "epoch": self.epoch,
                "generation": self.generation,
                "evaluations": self.evaluations,
                "population": [candidate.describe() for candidate in self.population],
            },
        )


def _other_value(knob: Knob, current: Any, rng: random.Random) -> Any:
    """A value a knob accepts that is not the one it currently holds.

    One sampler for the whole system, so the starting sweep and the random
    optimizer agree about what is reachable.
    """
    return knob.sample(rng, current)


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
    parser.add_argument("--optimizer", default="autoresearch", choices=["autoresearch", "random"])
    parser.add_argument("--preset", default="broad", help="which edits the optimizer may use")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--elite", type=int, default=3, help="how many parents to propose against")
    parser.add_argument("--max-evaluations", type=int, default=64)
    parser.add_argument("--attempts", type=int, default=3, help="retries per batch after a refusal")
    parser.add_argument("--runs-root", default=".factory/loop")
    parser.add_argument("--project", default=".")
    parser.add_argument("--model", default="", help="model name for the reasoning optimizer")
    args = parser.parse_args(argv)

    model = None
    if args.optimizer == "autoresearch":
        from refactory_dsh.model import DshModelClient

        model = DshModelClient(model=args.model)

    config = LoopConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        optimizer=args.optimizer,
        edit_preset=args.preset,
        seed=args.seed,
        elite=args.elite,
        max_evaluations=args.max_evaluations,
        attempts=args.attempts,
        runs_root=Path(args.runs_root),
    )
    try:
        loop = Loop(args.mode, config, project=Path(args.project), model=model)
        report = loop.run()
    except LoopError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "optimizer": report["optimizer"],
                "bound": report["bound"],
                "evaluations": report["evaluations"],
                "seed_score": report["seed_score"],
                "best_score": report["best_score"],
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
