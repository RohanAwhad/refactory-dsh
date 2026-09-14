"""The statements the Lean mode is asked to prove, and the proofs it may try.

A problem is data: a statement file, and one proof body per tactic strategy. The
strategies are what the outer loop searches, and they are *shared names* across
every problem -- ``omega``, ``induction``, ``simp``, ``decide`` -- so the knob means
the same thing whichever problem is selected, and a candidate's score is comparable
across the population.

Two problems ship. ``three_consecutive_sum`` needs nothing but the Lean prelude, so
the package runs on a machine with a toolchain and no library. ``sum_odd_eq_sq``
needs mathlib, and it is the one that shows why the toolchain is declared: the
statement names ``Finset`` and the proof needs ``ring``, neither of which exists
without the library.

The proof bodies are written for the problem they belong to and are not
interchangeable; a strategy that does not close the goal is a *failed attempt*, not
an error, which is what gives the retry gate something to do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The tactic strategies every problem must offer, in the order the retry gate
#: walks them. A problem that omits one would make the knob mean different things
#: for different candidates, which is exactly what makes a search unreadable.
STRATEGIES: tuple[str, ...] = ("omega", "induction", "simp", "decide")

#: Which library a problem needs. ``core`` is the Lean prelude; ``mathlib`` needs a
#: checkout, which is the tool ``mathlib`` in ``tools.py``.
LIBRARIES: tuple[str, ...] = ("core", "mathlib")


@dataclass(frozen=True)
class Problem:
    """One theorem to prove, and the ways this mode may try to prove it."""

    name: str
    library: str
    summary: str
    preamble: str
    #: The theorem's binder and type, after ``theorem <name>``.
    statement: str
    proofs: tuple[tuple[str, str], ...]

    def proof(self, strategy: str) -> str:
        """The tactic block that strategy uses, indented for ``:= by``."""
        for name, body in self.proofs:
            if name == strategy:
                return body
        known = ", ".join(name for name, _ in self.proofs)
        raise KeyError(f"{self.name} has no strategy {strategy!r}; has: {known}")

    def rotated(self, strategy: str, attempts: int) -> str:
        """Which strategy the ``attempts``-th attempt uses, starting from ``strategy``.

        The retry gate exists because a strategy that fails on one problem may be
        the one that works on another, and because a model-proposed proof is tried
        the same way. Rotation is deterministic -- it never depends on a clock --
        so a run reproduces.
        """
        names = [name for name, _ in self.proofs]
        start = names.index(strategy) if strategy in names else 0
        return names[(start + attempts) % len(names)]

    def to_state(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "library": self.library,
            "summary": self.summary,
            "preamble": self.preamble,
            "statement": self.statement,
            "proofs": [list(pair) for pair in self.proofs],
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "Problem":
        """Rebuild the problem the run wrote down, so an op reads the run's record.

        An op takes the problem from the work directory rather than from this
        module, which is what makes a run's artifacts self-describing: the
        statement the run attempted is in the run.
        """
        return cls(
            name=str(state["name"]),
            library=str(state["library"]),
            summary=str(state.get("summary", "")),
            preamble=str(state.get("preamble", "")),
            statement=str(state["statement"]),
            proofs=tuple((str(name), str(body)) for name, body in state["proofs"]),
        )


THREE_CONSECUTIVE = Problem(
    name="three_consecutive_sum",
    library="core",
    summary="the sum of three consecutive naturals is three times the middle one",
    preamble="",
    statement="(n : Nat) : n + (n + 1) + (n + 2) = 3 * (n + 1)",
    proofs=(
        ("omega", "  omega"),
        (
            "induction",
            "  induction n with\n"
            "  | zero => decide\n"
            "  | succ k _ih => omega",
        ),
        (
            "simp",
            "  simp [Nat.add_assoc, Nat.add_comm, Nat.add_left_comm, Nat.add_mul,\n"
            "    Nat.mul_add, Nat.mul_comm, Nat.mul_one, Nat.one_mul, Nat.two_mul]",
        ),
        ("decide", "  decide"),
    ),
)

SUM_ODD = Problem(
    name="sum_odd_eq_sq",
    library="mathlib",
    summary="the sum of the first n odd numbers is n squared",
    preamble="import Mathlib",
    statement="(n : Nat) : ∑ i in Finset.range n, (2 * i + 1) = n ^ 2",
    proofs=(
        ("omega", "  omega"),
        (
            "induction",
            "  induction n with\n"
            "  | zero => simp\n"
            "  | succ k _ih =>\n"
            "    rw [Finset.sum_range_succ, _ih]\n"
            "    ring",
        ),
        (
            "simp",
            "  induction n with\n"
            "  | zero => simp\n"
            "  | succ k _ih =>\n"
            "    simp [Finset.sum_range_succ, _ih, pow_succ, Nat.mul_add]",
        ),
        ("decide", "  decide"),
    ),
)

CATALOGUE: tuple[Problem, ...] = (THREE_CONSECUTIVE, SUM_ODD)


def by_library(library: str) -> Problem:
    """The problem a ``library`` value selects. One problem per library, for now."""
    for problem in CATALOGUE:
        if problem.library == library:
            return problem
    known = ", ".join(sorted({problem.library for problem in CATALOGUE}))
    raise KeyError(f"no problem needs the library {library!r}; known: {known}")


def statement_source(problem: Problem) -> str:
    """The problem statement as its own Lean file, with the proof left open.

    The statement is the spec, and it stays a ``sorry``: the project has to build
    before anybody has proved anything, and a candidate that could edit the
    statement it is judged against would be proving a different theorem.
    """
    return (
        f"{problem.preamble}\n\n"
        f"/-- {problem.summary} -/\n"
        f"theorem {problem.name} {problem.statement} := by\n"
        "  sorry\n"
    )


def solution_source(problem: Problem, strategy: str) -> str:
    """A candidate's answer: the same statement, closed by ``strategy``'s tactics.

    The trailing ``#print axioms`` is what makes "Lean accepted it" mean what it
    says. A ``sorry`` closes a goal and elaborates cleanly, so an exit code alone
    would accept an unfinished proof; the axiom dump is Lean's own answer to "what
    did this proof assume", and ``sorryAx`` in it is the proof checker reporting the
    hole rather than this module guessing at the text.
    """
    return (
        f"{problem.preamble}\n\n"
        f"-- {problem.summary} ({strategy})\n"
        f"theorem {problem.name} {problem.statement} := by\n"
        f"{problem.proof(strategy)}\n\n"
        f"#print axioms {problem.name}\n"
    )
