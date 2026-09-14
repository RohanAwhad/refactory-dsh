"""The operations the sympy-packed mode calls.

Each op reads the run directory, writes files it declares, and returns the facts it
declares. ``check_identity`` is the one that decides anything, and it decides with
sympy: it derives the closed form of a sum from the summand, then reduces the
difference between that and each conjectured form, and accepts a conjecture only
when the difference reduces to exactly zero. Python compares the result to zero; the
*mathematics* is sympy's, and a wrong conjecture is caught because the residual is a
genuine symbol in ``n`` rather than a number that happened to agree.

sympy is imported through ``toolchain.import_tool`` rather than at the top of this
module, so that the module imports -- and the package lists -- on a machine without
it. The import is then the declared requirement, and its failure is the tool's name
and install command.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from refactory_dsh import toolchain

#: This module is ``refactory_packages.<package>.ops``; see ``lean_pack.ops``.
PACKAGE = __name__.split(".")[-2]

PROBLEMS_FILE = "problems.json"
STATE_FILE = "state.json"
RESULTS_FILE = "results.jsonl"
RESULT_FILE = "result.json"


@dataclass(frozen=True)
class Identity:
    """One identity to establish, and the closed forms proposed for it.

    The conjectures are ordered, and the true one is at a different position for each
    identity. That is deliberate: it is what makes widening the ``candidates`` knob
    reveal the answers one identity at a time, and therefore what gives the outer
    loop a gradient to climb instead of a step.
    """

    name: str
    description: str
    #: The summand, in ``k``.
    summand: str
    candidates: tuple[str, ...]

    def to_state(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "summand": self.summand,
            "candidates": list(self.candidates),
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "Identity":
        return cls(
            name=str(state["name"]),
            description=str(state.get("description", "")),
            summand=str(state["summand"]),
            candidates=tuple(str(candidate) for candidate in state["candidates"]),
        )


#: The three identities. Each asks for the closed form of a sum in ``n``, which is a
#: real question with a real answer, and each offers the confusions that answer is
#: usually made of -- the cube-sum formula offered for the square sum, and so on.
CATALOGUE: tuple[Identity, ...] = (
    Identity(
        name="sum_of_integers",
        description="the sum of the first n positive integers",
        summand="k",
        candidates=("n**2/2", "n*(n+1)/2", "n*(n-1)/2", "n**2"),
    ),
    Identity(
        name="sum_of_squares",
        description="the sum of the squares of the first n positive integers",
        summand="k**2",
        candidates=("n**3/3", "n**2", "n*(n+1)*(2*n+1)/6", "n*(n+1)/2"),
    ),
    Identity(
        name="sum_of_cubes",
        description="the sum of the cubes of the first n positive integers",
        summand="k**3",
        candidates=(
            "n**4/4",
            "n**3/3",
            "n**2*(n+1)*(2*n+1)/6",
            "n**2*(n+1)**2/4",
        ),
    ),
)


def _routes(sympy: Any) -> dict[str, Any]:
    """How a residual is reduced before it is compared to zero.

    Four sound reductions that are not equally strong, which is what makes the
    ``route`` knob a real axis rather than a knob that lies: ``together`` over a
    common denominator does not collect a polynomial difference the way ``expand``
    does, so it accepts fewer correct conjectures. Every route here is *sound* --
    none of them can accept a wrong form -- so a lower score is a missed answer and
    never a false proof.
    """
    return {
        "simplify": lambda difference: sympy.simplify(difference),
        "expand": lambda difference: sympy.expand(difference),
        "cancel": lambda difference: sympy.cancel(difference),
        "together": lambda difference: sympy.together(difference),
    }


ROUTES: tuple[str, ...] = ("simplify", "expand", "cancel", "together")


# ── the pipeline ───────────────────────────────────────────────────────────
def load_problems(ctx: Any) -> dict[str, Any]:
    """Write the identities this run will attempt."""
    wanted = max(1, min(int(ctx.knob("problems", 3)), len(CATALOGUE)))
    selected = CATALOGUE[:wanted]
    ctx.write_json(PROBLEMS_FILE, {
        "count": len(selected),
        "identities": [identity.to_state() for identity in selected],
    })
    return {"problem_count": len(selected)}


def check_identity(ctx: Any) -> dict[str, Any]:
    """Derive one closed form and check the conjectures for it exactly.

    The derivation and the check are separate: sympy computes the sum, and then the
    same engine is asked whether the difference between that and a conjecture is
    zero. Nothing here compares floating point numbers, so a conjecture that agrees
    at a few sample points and is wrong is rejected.
    """
    sympy = toolchain.import_tool(PACKAGE, "sympy")
    catalogue = (ctx.read_json(PROBLEMS_FILE, {}) or {}).get("identities") or []
    state = ctx.read_json(STATE_FILE, {}) or {}
    attempted = int(state.get("attempted", 0))
    verified = int(state.get("verified", 0))
    if attempted >= len(catalogue):
        return {"checked_so_far": verified}

    identity = Identity.from_state(catalogue[attempted])
    symbol_n = sympy.Symbol("n", integer=True, nonnegative=True)
    symbol_k = sympy.Symbol("k", integer=True)
    derived = sympy.simplify(
        sympy.summation(sympy.sympify(identity.summand, locals={"k": symbol_k}), (symbol_k, 1, symbol_n))
    )

    route_name = str(ctx.knob("route", "simplify"))
    reduce_residual = _routes(sympy)[route_name]
    budget = max(1, int(ctx.knob("candidates", 1)))

    tried: list[dict[str, Any]] = []
    accepted = ""
    for text in identity.candidates[:budget]:
        reduced = reduce_residual(derived - sympy.sympify(text, locals={"n": symbol_n}))
        is_zero = bool(reduced == 0)
        tried.append({"candidate": text, "residual": str(reduced), "accepted": is_zero})
        if is_zero:
            accepted = text
            break

    record = {
        "identity": identity.name,
        "description": identity.description,
        "summand": identity.summand,
        "derived": str(derived),
        "route": route_name,
        "tried": tried,
        "closed_form": accepted,
        "verified": bool(accepted),
    }
    state["attempted"] = attempted + 1
    state["verified"] = verified + (1 if accepted else 0)
    state["last"] = record
    ctx.write_json(STATE_FILE, state)
    ctx.append_text(RESULTS_FILE, json.dumps(record) + "\n")
    return {"checked_so_far": int(state["verified"])}


def budget_check(ctx: Any) -> str:
    """Gate: reloop while an identity remains unattempted, then proceed."""
    state = ctx.read_json(STATE_FILE, {}) or {}
    total = int((ctx.read_json(PROBLEMS_FILE, {}) or {}).get("count", 0))
    attempted = int(state.get("attempted", 0))
    ctx.set_fact("verified_so_far", int(state.get("verified", 0)))
    return "reloop" if attempted < total else "proceed"


def _read_results(ctx: Any) -> list[dict[str, Any]]:
    """The run's own append-only record, one JSON object per line."""
    if not ctx.exists(RESULTS_FILE):
        return []
    records: list[dict[str, Any]] = []
    for line in ctx.read_text(RESULTS_FILE).splitlines():
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            records.append(loaded)
    return records


def finish(ctx: Any) -> dict[str, Any]:
    """Close the run and publish how many identities were established exactly."""
    state = ctx.read_json(STATE_FILE, {}) or {}
    results = _read_results(ctx)
    attempted = int(state.get("attempted", 0))
    verified = int(state.get("verified", 0))
    ctx.write_json(RESULT_FILE, {
        "identities": attempted,
        "verified": verified,
        "established": [record.get("closed_form", "") for record in results],
    })
    return {"verified": verified, "attempted": attempted}


# ── insertable ─────────────────────────────────────────────────────────────
def numeric_probe(ctx: Any) -> dict[str, Any]:
    """Sample the derived form and the last conjecture at a few values of ``n``.

    A sanity check, and offered as one: agreement at three values of ``n`` is not a
    proof, so the node that carries this declares ``score_effect='none'``. It exists
    so that a structural edit has something honest to add that is not a way of
    scoring better without establishing more.
    """
    sympy = toolchain.import_tool(PACKAGE, "sympy")
    catalogue = (ctx.read_json(PROBLEMS_FILE, {}) or {}).get("identities") or []
    if not catalogue:
        raise ValueError("no identities are written; load_problems has to run first")
    state = ctx.read_json(STATE_FILE, {}) or {}
    index = min(max(int(state.get("attempted", 1)) - 1, 0), len(catalogue) - 1)
    identity = Identity.from_state(catalogue[index])
    symbol_n = sympy.Symbol("n", integer=True, nonnegative=True)
    symbol_k = sympy.Symbol("k", integer=True)
    derived = sympy.summation(
        sympy.sympify(identity.summand, locals={"k": symbol_k}), (symbol_k, 1, symbol_n)
    )
    tried = (state.get("last", {}) or {}).get("tried") or []
    conjecture = str(tried[-1]["candidate"]) if tried else identity.candidates[0]

    samples: list[dict[str, Any]] = []
    agreements = 0
    for value in (1, 3, 7):
        left = int(derived.subs(symbol_n, value))
        right = int(sympy.sympify(conjecture, locals={"n": symbol_n}).subs(symbol_n, value))
        samples.append({"n": value, "derived": left, "conjecture": right})
        agreements += 1 if left == right else 0
    ctx.write_json("numeric_probe.json", {
        "identity": identity.name,
        "conjecture": conjecture,
        "samples": samples,
        "agreements": agreements,
        "note": "agreement at sampled values is evidence, not proof",
    })
    return {"numeric_agreements": agreements}
