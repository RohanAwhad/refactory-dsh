"""Steering a run while *using* it: the intent ledger, and the transform that adds it.

Two ways to change a run, and they are different acts on different things:

- **Structure** is the outer loop's job. It searches a mode's graph and knobs, and a
  result is adopted as a new mode (see :mod:`refactory_dsh.adopt`).
- **Steering** is the person's, while using a mode. You add context, correct it, or
  forbid something. **The graph does not change.** What changes is what the agents
  know, which is what steering means during use.

**What was here before, and why it is back.** The previous system had a *User Intent
Ledger* at ``.factory/strategy/user-intent.md``: seeded once by an ``init_user_intent``
node, append-only, described as "the user's original idea **and feedback**", read by
research, strategy, builder review, QA and overwatch, and checked by a gate that work
must be "traceable to user intent". It was thrown away with the rest of that system,
and ``default_pack/create``'s intent became a static class attribute.

It was also already broken, in a way worth not repeating. ``2026-09-12-create-v2-run.md``
records BUG-05: ``REFACTORY_INTENT`` never arrived, so the ledger held the literal
default ``"Build something useful."`` -- and the fidelity gate, having nothing to check
against, checked nothing. The user's real words survived only because the CEO quoted
them by hand into subagent briefs. So :meth:`Ledger.seed` refuses a default rather than
accepting one, and :func:`fidelity` checks the precondition rather than pretending to
check the output.

**The layer is inert when unused.** :func:`annotate` returns the workflow it was given
when the ledger is empty, so a run nobody speaks to is the run it would have been.
Without that, the interactive and headless paths could disagree about the same mode and
there would be no way to tell which was wrong.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Mapping

#: Where the ledger lives inside a run directory.
LEDGER = "intent-ledger.md"

#: The kinds an entry can be. A constraint is not information: it has to be
#: *respected*, not merely known, which is why it is a separate kind rather than a
#: note with a different tone.
KINDS: tuple[str, ...] = ("info", "correction", "constraint")

#: The entry kind a gate verdict is recorded under. Separate from the three the
#: person writes, because a verdict is a decision the run routed on, not something
#: the person said about it, and a resumed run has to tell them apart.
VERDICT_KIND = "verdict"

#: How a piece of work names the ledger entry it serves. A line, so it is readable
#: in the artifact and findable without parsing the prose around it.
CITATION = "Serves:"

#: The placeholder the previous system fell back to. Refused rather than accepted:
#: accepting it is what made the fidelity check vacuous, and a caller that reintroduces
#: it should fail here rather than produce a run nobody can account for.
PLACEHOLDER = "Build something useful."


class SteeringError(ValueError):
    """The steering layer cannot be built or trusted. Always names what is wrong."""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class Ledger:
    """The run's record of what was asked for, and everything said since.

    Append-only, timestamped, and written into the run directory so it travels with
    the run rather than with the session that started it. A ledger kept in the session
    would be lost on resume, and re-asking the user is not the same run.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / LEDGER

    # ── writing ────────────────────────────────────────────────────────────
    @classmethod
    def seed(cls, root: Path, request: str) -> "Ledger":
        """Open a ledger with the request that started the run.

        Refuses a blank request and refuses the legacy placeholder. Both are the
        BUG-05 failure: a ledger holding a default is one a fidelity check cannot
        check, and it looks identical to a ledger that was filled in.
        """
        text = (request or "").strip()
        if not text:
            raise SteeringError(
                "a run that can be steered needs the request it is for; seeding with "
                "an empty intent is what makes the fidelity gate vacuous"
            )
        if text == PLACEHOLDER:
            raise SteeringError(
                f"{PLACEHOLDER!r} is the placeholder the previous system defaulted to. "
                "Pass what was actually asked for: a ledger holding a default is "
                "indistinguishable from one that was filled in, which is the bug this "
                "refuses to reintroduce."
            )
        ledger = cls(root)
        if ledger.path.exists() and ledger.path.stat().st_size > 0:
            raise SteeringError(
                f"{ledger.path} already exists; a ledger is seeded once and appended "
                "to thereafter, so seeding it again would put a second origin in it"
            )
        ledger.root.mkdir(parents=True, exist_ok=True)
        ledger.path.write_text(f"# User Intent Ledger\n\n## [{_now()}] Initial Idea\n{text}\n")
        return ledger

    def note(self, kind: str, text: str) -> dict[str, Any]:
        """Append one thing the user said. Returns the entry as it was recorded."""
        if kind not in KINDS:
            raise SteeringError(f"unknown note kind {kind!r}; known: {list(KINDS)}")
        body = (text or "").strip()
        if not body:
            raise SteeringError("an empty note records nothing and dilutes the ledger")
        entry = {"at": _now(), "kind": kind, "text": body}
        self.root.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as handle:
            handle.write(f"\n## [{entry['at']}] {kind}\n{body}\n")
        return entry

    def verdict(self, gate: str, value: str) -> dict[str, Any]:
        """Record the verdict a gate was answered with, so a resume replays it.

        A verdict is a decision the run routed on. Writing it down is what makes a
        resumed run the same run: re-asking the person would produce a second
        answer to a question already answered, and inventing one would produce a
        result nobody can account for. The last verdict for a gate wins, so a
        person may change their mind while the run is paused.
        """
        gate = (gate or "").strip()
        value = (value or "").strip()
        if not gate:
            raise SteeringError("a verdict needs the gate it answers")
        if not value:
            raise SteeringError(f"gate {gate!r} was answered with an empty verdict")
        entry = {"at": _now(), "kind": VERDICT_KIND, "gate": gate, "verdict": value}
        self.root.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as handle:
            handle.write(
                f"\n## [{entry['at']}] {VERDICT_KIND}\ngate: {gate}\nverdict: {value}\n"
            )
        return entry

    def verdicts(self) -> dict[str, str]:
        """Every gate verdict recorded so far, keyed by gate id."""
        found: dict[str, str] = {}
        for entry in self.entries():
            if entry["kind"] != VERDICT_KIND:
                continue
            gate = verdict = ""
            for line in entry["text"].splitlines():
                key, _, value = line.partition(":")
                if key.strip() == "gate":
                    gate = value.strip()
                elif key.strip() == "verdict":
                    verdict = value.strip()
            if gate and verdict:
                found[gate] = verdict
        return found

    # ── reading ────────────────────────────────────────────────────────────
    def exists(self) -> bool:
        return self.path.exists() and self.path.stat().st_size > 0

    def entries(self) -> list[dict[str, Any]]:
        """Every entry, parsed back out: timestamps and kinds, not just the text."""
        if not self.exists():
            return []
        found: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for line in self.path.read_text().splitlines():
            if line.startswith("## ["):
                if current is not None:
                    found.append(current)
                stamp, _, kind = line[4:].partition("] ")
                current = {"at": stamp, "kind": kind.strip(), "text": ""}
            elif current is not None:
                current["text"] = (current["text"] + "\n" + line).strip()
        if current is not None:
            found.append(current)
        return found

    def constraints(self) -> list[str]:
        return [entry["text"] for entry in self.entries() if entry["kind"] == "constraint"]

    def render(self) -> str:
        """The block folded into a prompt. **Empty when there is nothing to say.**

        Empty is the load-bearing case: it is what makes :func:`annotate` inert, and
        therefore what lets a run nobody steered behave exactly as it did before this
        module existed.
        """
        entries = self.entries()
        if not entries:
            return ""
        lines = [
            "## What the user has said",
            "",
            "This is the run's intent ledger: what was asked for, and everything said",
            "since. Entries marked `constraint` must be respected, not merely known.",
            "",
        ]
        for entry in entries:
            lines.append(f"- **[{entry['kind']}]** {entry['text']}")
        return "\n".join(lines)


def _is_agent_node(node: Any) -> bool:
    """A node that makes a model call, on either side of the lowering.

    The transform runs on the lowered IR (``AgentNode``) and on the walker's own
    graph (``nn.Node`` with ``kind == "agent"``), so it duck-types instead of
    importing either: the two runtimes must carry the same ledger, and a mode's
    author must not be the thing that decides whether they do.
    """
    return node.__class__.__name__ == "AgentNode" or getattr(node, "kind", "") == "agent"


def _citation_block(ledger: Ledger) -> str:
    """The instruction that makes the chain from ledger to work checkable.

    It names the ledger's **path** as well as its contents, and tells the node to
    read it live: the lowered graph is inlined at boot, so a note the person adds
    *while the run goes* is not in the prompt this node received. Re-reading is what
    makes "a note added mid-run reaches the nodes that run afterwards" true.
    """
    listed = ", ".join(f"`{entry['at']}`" for entry in ledger.entries())
    return (
        "\n\n## Trace your work to the ledger\n\n"
        f"Read the live ledger at `{ledger.path}` before you act. It is appended to while "
        "the run goes, so it may say more than this prompt does, and the newest entry wins.\n\n"
        "End your output with a line naming the ledger entry you are serving:\n\n"
        f"    {CITATION} <timestamp>\n\n"
        f"The entries at the time this prompt was written are: {listed}. Work that names "
        "no entry, or one that does not exist, is reported as not traceable to what the "
        "person asked for."
    )


def annotate(workflow: Any, ledger: Ledger) -> Any:
    """Fold the ledger into every agent node. A no-op when the ledger is empty.

    Every agent node, not one the author remembered to wire: a mode declares *what* it
    does, and the steering layer is not its business, so a mode author cannot be the
    thing that makes the layer work. Applied to the lowered workflow rather than to the
    mode, which is what keeps the mode Pythonic and unchanged.
    """
    block = ledger.render()
    if not block:
        return workflow
    block += _citation_block(ledger)
    for node in workflow.nodes.values():
        if not _is_agent_node(node):
            continue
        existing = getattr(node, "prompt_template", "") or ""
        node.prompt_template = f"{existing}\n\n{block}" if existing else block
    return workflow


def for_run(workflow: Any, workdir: Path) -> Any:
    """Fold the run directory's ledger into a workflow about to run.

    Called by **both** runtimes. A mode lowered for DSH and one executed by the
    walker have to carry the same ledger; annotating in one path only is a
    difference between the runtimes that nothing would report.
    """
    return annotate(workflow, Ledger(workdir))


def citations(text: str) -> list[str]:
    """The ledger entries a piece of work says it serves, in the order it names them."""
    found: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith(CITATION.lower()):
            continue
        rest = stripped[len(CITATION):].strip()
        for stamp in rest.replace(",", " ").split():
            stamp = stamp.strip("`[]()")
            if stamp and stamp not in found:
                found.append(stamp)
    return found


def chain(ledger: Ledger, outputs: Mapping[str, str]) -> list[str]:
    """Problems with the chain from the ledger to the work, mechanically.

    The cheap half of the fidelity question: whether every agent output names an
    entry that exists. It cannot say the work *satisfies* the intent -- that is a
    judgement and needs a model -- but a node that ignored the ledger is caught, and
    the check costs no model call.
    """
    known = {entry["at"] for entry in ledger.entries()}
    problems: list[str] = []
    for node_id in sorted(outputs):
        cited = citations(outputs[node_id])
        if not cited:
            problems.append(
                f"node {node_id!r} produced work that cites no ledger entry, so it is "
                "not traceable to what was asked for"
            )
            continue
        for stamp in cited:
            if stamp not in known:
                problems.append(
                    f"node {node_id!r} cites {stamp!r}, which is not in the ledger, so "
                    "the citation dangles"
                )
    return problems


def outputs_in(run_root: Path) -> dict[str, str]:
    """What each agent node wrote, read back so the citation chain can be checked.

    Read from the declarations the lowering left beside the run, so it works for a
    run either runtime executed and does not need the state. A file the node never
    wrote is an empty output, which the chain reports as a node that cited nothing.
    """
    from refactory_dsh import lowering

    declared = lowering.read_declarations(Path(run_root))
    outputs: dict[str, str] = {}
    for node_id, entry in declared.items():
        if not isinstance(entry, dict) or entry.get("kind") != "agent":
            continue
        text = ""
        for name in entry.get("writes") or ():
            path = Path(run_root) / str(name)
            if path.exists():
                text += "\n" + path.read_text(errors="replace")
        outputs[str(node_id)] = text
    return outputs


def fidelity(ledger: Ledger, outputs: Mapping[str, str] | None = None) -> list[str]:
    """What is wrong with a ledger a run is about to be judged against.

    Says which half it is, because the halves differ in cost and in what they can
    conclude:

    - **The precondition**, always. A ledger that is missing, empty, or holding the
      placeholder. Deterministic, free. BUG-05 was this half being broken while the
      gate reported a pass.
    - **The chain**, when ``outputs`` is given. Every agent output must cite a ledger
      entry that exists (``chain``). Deterministic, free.
    - **The judgement** -- whether the work *satisfies* the intent -- is **not here**.
      It needs a model to read the work against the ledger, and checking citations
      does not substitute for it. That limit is in `architecture.md` §12.
    """
    problems: list[str] = []
    if not ledger.exists():
        return [f"no intent ledger at {ledger.path}: the run has nothing to be judged against"]
    entries = ledger.entries()
    if not entries:
        return ["the intent ledger has no entries"]
    origin = entries[0]["text"].strip()
    if not origin:
        problems.append("the ledger's origin is empty")
    if origin == PLACEHOLDER:
        problems.append(f"the ledger's origin is the placeholder {PLACEHOLDER!r}")
    if outputs is not None:
        problems.extend(chain(ledger, outputs))
    return problems


def interactive(
    state: dict[str, Any],
    workdir: Path,
    *,
    request: str = "",
    name: str = "",
    description: str = "",
) -> tuple[Any, Ledger]:
    """Lower a mode for a person to *use*, with the steering layer attached.

    The one entry point, and a named function over the IR rather than a branch inside
    :func:`~refactory_dsh.lowering.to_workflow`, so that "does the steering layer
    change the result" is a question one test can ask, and so a mode is not lowered
    differently depending on who is going to run it.
    """
    from refactory_dsh import lowering

    workflow = lowering.to_workflow(state, name=name, description=description)
    ledger = Ledger(workdir)
    if request:
        ledger = Ledger.seed(workdir, request)
    return annotate(workflow, ledger), ledger


__all__ = [
    "CITATION",
    "KINDS",
    "LEDGER",
    "PLACEHOLDER",
    "VERDICT_KIND",
    "Ledger",
    "SteeringError",
    "annotate",
    "chain",
    "citations",
    "fidelity",
    "for_run",
    "interactive",
    "outputs_in",
]
