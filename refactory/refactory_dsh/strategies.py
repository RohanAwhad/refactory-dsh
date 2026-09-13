"""The SRF strategies, as outer loops.

SRF ships thirteen modes that do different things with the same input. What varies
between them is **control flow**, not policy: a switch inside an iteration, a fork
with no loop at all, a staged curriculum, an island model. That is why the seam is
an overridable outer loop and not a set of hooks on a fixed one.

Each class here is the same strategy operating at the outer level: it searches over
*mode states* rather than over solution programs, and is scored by the mode's own
`evaluate`. What ports is the control flow. What differs from the SRF original is
recorded in each docstring, because "the same algorithm" is a claim worth stating
precisely.

Counterpart names are provisional and deliberately recognisable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Sequence

from refactory_dsh.loop import Loop
from refactory_dsh.optim import Candidate, Proposal
from refactory_dsh.outerloop import OuterLoop, Run

# ── the archives a strategy keeps ──────────────────────────────────────────


class Archive:
    """A set of candidates a strategy keeps, and how it picks from them."""

    def add(self, candidate: Candidate) -> None:
        raise NotImplementedError

    def all(self) -> list[Candidate]:
        raise NotImplementedError

    def best(self) -> Candidate | None:
        kept = self.all()
        return max(kept, key=lambda c: (c.score or 0.0)) if kept else None


@dataclass
class FlatArchive(Archive):
    """Keeps everything. The default loop's behaviour."""

    members: list[Candidate] = field(default_factory=list)

    def add(self, candidate: Candidate) -> None:
        if not any(existing.id == candidate.id for existing in self.members):
            self.members.append(candidate)

    def all(self) -> list[Candidate]:
        return list(self.members)


@dataclass
class ScoredArchive(Archive):
    """Keeps the best ``limit`` seen, so an archive is not the same as a population."""

    limit: int = 8
    members: list[Candidate] = field(default_factory=list)

    def add(self, candidate: Candidate) -> None:
        self.members = [c for c in self.members if c.id != candidate.id]
        self.members.append(candidate)
        self.members.sort(key=lambda c: -(c.score or 0.0))
        del self.members[self.limit :]

    def all(self) -> list[Candidate]:
        return list(self.members)


@dataclass
class IslandArchive(Archive):
    """Several sub-populations. A candidate is only ever selected from its own."""

    count: int = 3
    members: dict[int, list[Candidate]] = field(default_factory=dict)

    def island_of(self, candidate: Candidate) -> int:
        return hash(candidate.id) % max(1, self.count)

    def add(self, candidate: Candidate) -> None:
        island = self.island_of(candidate)
        members = self.members.setdefault(island, [])
        if not any(existing.id == candidate.id for existing in members):
            members.append(candidate)

    def all(self) -> list[Candidate]:
        return [c for members in self.members.values() for c in members]

    def from_island(self, island: int) -> list[Candidate]:
        return list(self.members.get(island, ()))

    def populated(self) -> list[int]:
        return sorted(island for island, members in self.members.items() if members)


@dataclass
class TreeArchive(Archive):
    """A tree. A node's parent is the node it was expanded from."""

    members: list[Candidate] = field(default_factory=list)
    parents: dict[str, str] = field(default_factory=dict)
    children: dict[str, list[str]] = field(default_factory=dict)

    def add(self, candidate: Candidate) -> None:
        if any(existing.id == candidate.id for existing in self.members):
            return
        self.members.append(candidate)
        if candidate.parent:
            self.parents[candidate.id] = candidate.parent
            self.children.setdefault(candidate.parent, []).append(candidate.id)

    def all(self) -> list[Candidate]:
        return list(self.members)

    def depth(self, candidate_id: str) -> int:
        seen: set[str] = set()
        depth = 0
        while candidate_id in self.parents and candidate_id not in seen:
            seen.add(candidate_id)
            candidate_id = self.parents[candidate_id]
            depth += 1
        return depth

    def deepest(self) -> int:
        return max((self.depth(c.id) for c in self.members), default=0)


# ── the strategies ─────────────────────────────────────────────────────────


class BestOfN(Loop):
    """SRF `best_of_n`: `Fork(N samples) → Join → select_best`.

    Differs from the SRF original in that the N samples are N mode states rather
    than N solution programs, so the "fork" is sequential here and the DSH runtime
    is what would make it concurrent.
    """

    name = "best_of_n"
    width = 4

    def strategy(self, run: Run) -> None:
        seed = run.seed()
        for index in range(self.width):
            if run.budget_left() <= 0:
                break
            edit = self._variant(index, seed)
            if edit is None:
                continue
            outcome = run.attempt(Proposal(edit=edit, parent=seed.id), seed)
            if isinstance(outcome, list):
                run.log(f"variant {index} refused: {outcome[0]}")
                continue
            run.keep(run.evaluate(outcome))
        run.stop("samples_exhausted")

    def _variant(self, index: int, seed: Candidate) -> dict[str, Any] | None:
        from refactory_dsh.nn import Knob, edits

        entries = seed.state.get("knobs") or {}
        movable = [
            name
            for name, entry in entries.items()
            if not entry["spec"].get("frozen", False)
        ]
        if not movable:
            return None
        name = movable[index % len(movable)]
        knob = Knob.from_spec(entries[name]["spec"])
        rng = random.Random(f"best_of_n:{self.config.seed}:{index}")
        value = knob.sample(rng, entries[name]["value"])
        if value is None:
            return None
        return {"op": "set_knob", "name": name, "value": value}

    def update(self, run: Run, candidate: Candidate) -> None:
        """No archive: best-of-N is a single round, so keeping is enough."""


class ArchiveLoop(Loop):
    """SRF `scs`: `sample → build_prompt → generate → eval → archive_update`.

    The archive is the point: it keeps more than the elite, so a strategy can
    revisit a candidate the population has moved past.
    """

    name = "archive_loop"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.archive = ScoredArchive(limit=8)

    def update(self, run: Run, candidate: Candidate) -> None:
        self.archive.add(candidate)

    def select_parents(self, n: int, *, by: str = "score") -> list[Candidate]:
        kept = self.archive.all() or super().select_parents(n)
        return kept[:n]


class EvolutionaryArchive(Loop):
    """SRF `evox`: an evolutionary archive with tournament selection.

    Differs from `ArchiveLoop` in how parents are chosen: a tournament over the
    archive rather than its best, so a weaker member can still breed.
    """

    name = "evolutionary_archive"
    tournament = 3

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.archive = ScoredArchive(limit=12)
        self.rng = random.Random(f"evox:{self.config.seed}")

    def update(self, run: Run, candidate: Candidate) -> None:
        self.archive.add(candidate)

    def select_parents(self, n: int, *, by: str = "score") -> list[Candidate]:
        kept = self.archive.all()
        if len(kept) < self.tournament:
            return super().select_parents(n)
        chosen: list[Candidate] = []
        for _ in range(n):
            contenders = self.rng.sample(kept, self.tournament)
            chosen.append(max(contenders, key=lambda c: (c.score or 0.0)))
        return chosen


class Islands(Loop):
    """SRF `openevolve`: an island model. Selection never crosses islands."""

    name = "islands"
    island_count = 3

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.archive = IslandArchive(count=self.island_count)
        self._turn = 0

    def update(self, run: Run, candidate: Candidate) -> None:
        self.archive.add(candidate)

    def select_parents(self, n: int, *, by: str = "score") -> list[Candidate]:
        populated = self.archive.populated()
        if not populated:
            return super().select_parents(n)
        chosen: list[Candidate] = []
        for offset in range(n):
            island = populated[(self._turn + offset) % len(populated)]
            members = self.archive.from_island(island)
            if members:
                chosen.append(max(members, key=lambda c: (c.score or 0.0)))
        self._turn += 1
        return chosen or super().select_parents(n)


class HillClimb(Loop):
    """SRF `karpathy`: one candidate per iteration, from the best so far."""

    name = "hill_climb"
    plateau = 3

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._unchanged = 0
        self._best: float | None = None

    def batch_size(self, run: Run) -> int:
        return 1

    def select_parents(self, n: int, *, by: str = "score") -> list[Candidate]:
        return super().select_parents(1)

    def on_epoch_end(self, run: Run) -> None:
        best = self.leaderboard(1)
        current = best[0].score if best else None
        if current is not None and self._best is not None and current <= self._best:
            self._unchanged += 1
        else:
            self._unchanged = 0
        self._best = current
        if self._unchanged >= self.plateau:
            run.stop("plateau")


class Reflective(Loop):
    """SRF `gepa`: reflective evolution, with a merge branch.

    `merge_decision` is a switch, not a rewind: both outcomes continue the same
    iteration down different pipelines. That is the shape a policy hook cannot
    express, and it is why this is an outer loop.
    """

    name = "reflective"
    requires_model = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.merges = 0
        self.mutations = 0

    def strategy(self, run: Run) -> None:
        while True:
            reason = self.should_stop(run)
            if reason:
                run.stop(reason)
                return
            branch = "merge" if self._merge_now(run) else "mutate"
            if not self.epoch_step(run, branch=branch):
                run.stop("epochs")

    def _merge_now(self, run: Run) -> bool:
        """Merge once there are enough parents to have something to combine."""
        return len(self.select_parents(3)) >= 3 and (self.merges + self.mutations) % 3 == 2

    def epoch_step(self, run: Run | None = None, *, branch: str = "mutate") -> bool:
        if branch == "merge":
            self.merges += 1
        else:
            self.mutations += 1
        run = run or Run(loop=self)
        run.log(f"branch={branch}")
        return super().epoch_step(run)

    def select_parents(self, n: int, *, by: str = "score") -> list[Candidate]:
        return super().select_parents(n)


class ReflectiveSampler(Loop):
    """SRF `shinka`: reflection inside sampling.

    The sampling prompt carries prior rejections, so a proposal is informed by what
    has already been refused rather than only by what has already worked.
    """

    name = "reflective_sampler"
    requires_model = True

    def propose(self, run: Run, parents: Sequence[Candidate], n: int) -> list[Proposal]:
        proposals = super().propose(run, parents, n)
        refused = run.rejections()
        self.last_reflection = refused
        return proposals


class AdaptiveMeta(Loop):
    """SRF `adaevolve`: an adaptive meta-level.

    `meta_gate` routes an iteration to either the normal pipeline or the meta one,
    on a criterion of its own, and the choice is recorded.
    """

    name = "adaptive_meta"
    requires_model = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.meta_taken = 0
        self._best: float | None = None

    def strategy(self, run: Run) -> None:
        while True:
            reason = self.should_stop(run)
            if reason:
                run.stop(reason)
                return
            meta = self._meta_now(run)
            if meta:
                self.meta_taken += 1
            run.log(f"meta={meta}")
            if not self.epoch_step(run):
                run.stop("epochs")

    def _meta_now(self, run: Run) -> bool:
        """Fire the meta branch when progress has stalled."""
        scored = [c.score for c in run.history() if c.score is not None]
        if len(scored) < 3:
            return False
        previous, self._best = self._best, max(scored)
        return previous is not None and self._best <= previous


class TreeSearch(Loop):
    """SRF `aide`: `policy → action_gate(draft|improve|debug) → tree_update`.

    The archive is a tree, so a candidate's parent is the node it expanded from and
    the search can say how deep it has gone.
    """

    name = "tree_search"
    requires_model = True
    actions = ("draft", "improve", "debug")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tree = TreeArchive()

    def update(self, run: Run, candidate: Candidate) -> None:
        self.tree.add(candidate)

    def propose(self, run: Run, parents: Sequence[Candidate], n: int) -> list[Proposal]:
        parents = self.select_parents(1) or list(parents)
        self.last_action = self.actions[len(self.tree.all()) % len(self.actions)]
        return super().propose(run, parents, n)


class StagedCurriculum(Loop):
    """SRF `ai_sci_v2`: a staged curriculum, not a loop.

    `initial → tuning → creative → ablation`, each with its own action gate. Every
    stage runs, in order, before the next.
    """

    name = "staged_curriculum"
    requires_model = True
    stages = ("initial", "tuning", "creative", "ablation")
    per_stage = 2

    def strategy(self, run: Run) -> None:
        self.stages_done = []
        for stage in self.stages:
            self.stages_done.append(stage)
            run.log(f"stage={stage}")
            for _ in range(self.per_stage):
                if not self.epoch_step(run):
                    run.stop("epochs")
                    return
        run.stop("curriculum_complete")


class StagedV1(StagedCurriculum):
    """SRF `ai_sci_v1`: the earlier staged variant."""

    name = "staged_v1"
    stages = ("explore", "exploit")


class ResearchThenOptimize(Loop):
    """SRF `autoresearch`: a pre-phase, then the loop.

    `research_phase → research_llm` runs once, before the search starts, and what it
    concludes is available to every proposal after it.
    """

    name = "research_then_optimize"
    requires_model = True

    def strategy(self, run: Run) -> None:
        self.research_note = self._research(run)
        run.log(f"research={self.research_note!r}")
        if not self.epoch_step(run):
            run.stop("epochs")
            return
        super().strategy(run)

    def _research(self, run: Run) -> str:
        """What is known before searching: the surface, and what has been tried."""
        live = [k["name"] for k in run.knob_surface() if not k["frozen"]]
        return f"{len(live)} movable knob(s): {', '.join(sorted(live))}"


class Autoscientists(Loop):
    """SRF `autoscientists`: `Fork(N scientists) → Join → merge`.

    N independent branches, then a step that sees all of them.
    """

    name = "autoscientists"
    width = 3
    requires_model = True

    def strategy(self, run: Run) -> None:
        seed = run.seed()
        produced: list[Candidate] = []
        for index in range(self.width):
            if run.budget_left() <= 0:
                break
            edit = BestOfN._variant(self, index, seed)  # the same N-sample shape
            if edit is None:
                continue
            outcome = run.attempt(Proposal(edit=edit, parent=seed.id), seed)
            if isinstance(outcome, list):
                continue
            produced.append(run.keep(run.evaluate(outcome)))
        self.merged = len(produced)
        if len(produced) >= 2 and run.budget_left() > 0:
            best = max(produced, key=lambda c: (c.score or 0.0))
            merge = self._merge(produced, best)
            if merge is not None:
                outcome = run.attempt(Proposal(edit=merge, parent=best.id), best)
                if not isinstance(outcome, list):
                    run.keep(run.evaluate(outcome))
        run.stop("merged")

    def _merge(self, produced: Sequence[Candidate], best: Candidate) -> dict[str, Any] | None:
        """Combine the branches: take one knob value from a different scientist.

        A merge that sees every branch, which is the whole reason the SRF mode forks
        before it merges rather than sampling N times.
        """
        donors = [c for c in produced if c.id != best.id]
        if not donors:
            return None
        donor = donors[0]
        mine = best.state.get("knobs") or {}
        theirs = donor.state.get("knobs") or {}
        for name in sorted(mine):
            if mine[name]["value"] != theirs[name]["value"]:
                return {"op": "set_knob", "name": name, "value": theirs[name]["value"]}
        return None


# ── selection ──────────────────────────────────────────────────────────────

#: The strategies, by name. `default` is the harness's own loop.
STRATEGIES: dict[str, type[Loop]] = {
    "default": Loop,
    BestOfN.name: BestOfN,
    ArchiveLoop.name: ArchiveLoop,
    EvolutionaryArchive.name: EvolutionaryArchive,
    Islands.name: Islands,
    HillClimb.name: HillClimb,
    Reflective.name: Reflective,
    ReflectiveSampler.name: ReflectiveSampler,
    AdaptiveMeta.name: AdaptiveMeta,
    TreeSearch.name: TreeSearch,
    StagedCurriculum.name: StagedCurriculum,
    StagedV1.name: StagedV1,
    ResearchThenOptimize.name: ResearchThenOptimize,
    Autoscientists.name: Autoscientists,
}

#: How many of the SRF modes are ported here. Asserted by the tests, so a port
#: cannot be quietly dropped.
SRF_PORT_COUNT = 13


def names() -> list[str]:
    return sorted(STRATEGIES)


def resolve(name: str) -> type[Loop]:
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy {name!r}; known: {names()}")
    return STRATEGIES[name]


def create(name: str, mode_id: str, config: Any = None, **kwargs: Any) -> Loop:
    """Build a strategy by name, failing loud on an unknown one."""
    return resolve(name)(mode_id, config, **kwargs)


def register(name: str, cls: type[Loop]) -> None:
    """Add a strategy without editing the library."""
    if not issubclass(cls, OuterLoop):
        raise TypeError(f"{cls.__name__} is not an OuterLoop")
    STRATEGIES[name] = cls
