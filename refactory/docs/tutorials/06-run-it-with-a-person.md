# 6. Run it with a person

Levels 1 and 2 are the language and the search. This one is a person **using** a
mode: the run pauses at a gate only they can answer, and the runtime refuses to
answer it for them.

## The mode

`review_pack/review` is the smallest real one. Its graph is:

```
task_input -> draft -> check --proceed--> approve --proceed--> publish
                ^         |                  |
                +-reloop--+                  +---halt-------> reject
```

`check` is an `fn` gate — a rule decides it. `approve` is a **`user`** gate: it
carries no evaluator at all, because a program must not be able to answer it. Its IR
says so:

```bash
uv run python -c "
from pathlib import Path
from refactory_dsh import lowering, registry
state = registry.resolve(Path('.'), 'review_pack/review')[1]().state_dict()
workflow = lowering.to_workflow(state, name='review')
for node_id in ('check', 'approve'):
    node = workflow.nodes[node_id]
    print(node_id, node.evaluator_type, repr(node.evaluator_command))"
```

```
check fn 'python -m refactory_dsh.run refactory_packages.review_pack.ops:check'
approve user None
```

## A headless run refuses

```bash
uv run refactory package smoke review_pack/review
```

```
{"error": "approve: ExecutionError: gate 'approve' is answered by 'user' and this run
has no verdict for it. A headless run cannot answer a gate meant for a person or for
an agent; pass one in RunContext.verdicts (an interactive run collects a fresh
answer) or make the gate `fn`."}
```

That refusal is the point: an invented verdict is indistinguishable in the artifacts
from one a person gave, so it must not be invented.

## Run it with a person

```bash
uv run refactory ceo review_pack/review \
  --intent "draft a note about circle packing and let me approve it"
```

It lowers the mode with that intent folded into every agent node and starts the
`graph` profile with your terminal attached. At the gate the author's note is the
question:

```
[gate approve] the person approves the draft, or halts the run
  1. proceed
  2. halt
> 1
```

Answer `1`. The run completes, `final.md` is written, and the verdict is recorded in
the run's ledger.

> A piped or captured stdin is not a person, so the answerer delegates and the run
> **refuses** the gate, naming it. That is why a headless DSH run of the same mode
> stops with `the graph did not finish: gate approve is answered by a person and
> could not be asked: no user-questions answerer accepted the request` instead of
> scoring 0.

## The history is durable; approval is scoped

```bash
uv run refactory steer --run .factory/interactive/review_pack-review
```

The ledger keeps the intent and the answer as history. The interactive answer is
consumed for that gate visit: running the command again asks a fresh question,
even in the same work directory and with the same draft. Changing the draft while
a question is pending also invalidates the old answer.

A public `steer --verdict approve=proceed` is likewise scoped to the declared review
inputs and consumed once. First open the mode and reach its gate so the runtime
knows what is being reviewed; an unknown or ambiguous gate is refused. To grant
standing authorization deliberately, use `ceo --approve approve=proceed` or an
explicit Python `RunContext.verdicts` mapping. A consumed or legacy unscoped ledger
answer does not substitute for that authorization.

## Steering while it runs

What the person says *while using* a mode is the ledger, and it never changes the
graph:

```bash
uv run refactory steer \
  --run .factory/interactive/review_pack-review --note "no numpy" --kind constraint
```

The instruction in every agent node names the live ledger, so a note added mid-run
reaches the nodes that run afterwards rather than only the ones that had already
started.

The same works for a search. `refactory optimize <mode> --intent "…"` seeds the
run's ledger at the run root, and each candidate is evaluated with a copy of it in its
own directory, so `refactory steer --run .factory/loop/<run> --note "no numpy"
--kind constraint` reaches the mode's agent nodes from the next candidate onwards.

## Steering the search itself

A note is context. The *search* is steered with control, which the loop honors at a
generation boundary and reports back on the same stream the scores arrive on:

```bash
uv run refactory leaderboard --run .factory/loop/<run>
uv run refactory steer --run .factory/loop/<run> --reject 3f9a2c --lineage
uv run refactory steer --run .factory/loop/<run> --keep 1b77e0
uv run refactory steer --run .factory/loop/<run> --adopt --as better_adaptive
```

`--reject` takes that candidate and its descendants out of the leaderboard, so the
rest of the budget is not spent around a branch you have read and dismissed. The
score stays in `state.json`, because a run does not rewrite its own history, and
`refactory best` and `refactory adopt` agree with the leaderboard about who won.
`--keep` is the opposite act and the undo for a mistyped reject: that candidate
stays a parent even once a newer batch outscores it, so a good-but-overshadowed
point still gets followed up. `--adopt` writes the run's current winner as a package
*without ending the search*, so a leaderboard that is already good enough does not
have to cost the remaining budget; a refusal (the name already exists, nothing has
scored yet) is reported as an event rather than killing the run.

`--pause`, `--resume` and `--stop` travel the same way. All of it works from a
second terminal while `refactory optimize` is still printing, because control is a
small file in the run directory rather than a message to the running process.

## What to take away

- **Who answers a gate is part of the architecture**, not a convention: the IR
  carries it, the runtime enforces it, and a headless run refuses rather than
  guessing.
- The decision history is durable. Interactive approval applies once to the
  reviewed inputs; another visit needs a new answer.
- Steering is context; structure is the outer loop's job. During use the graph does
  not change.

Back to the [tutorial index](index.md).
