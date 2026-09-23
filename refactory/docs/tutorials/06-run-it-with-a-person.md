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
an agent; pass one in RunContext.verdicts (an interactive run collects it, a resumed
run replays it) or make the gate `fn`."}
```

That refusal is the point: an invented verdict is indistinguishable in the artifacts
from one a person gave, so it must not be invented.

## Run it with a person

```bash
uv run refactory run review_pack/review \
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

## The answer is durable

```bash
uv run refactory steer --run .factory/interactive/review_pack-review
```

The ledger shows the intent and the answer, and `verdicts` reports
`{"approve": "proceed"}`. Run the same command again and it **replays** that answer
rather than asking: a resumed run is the same run, not a second question.

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

## What to take away

- **Who answers a gate is part of the architecture**, not a convention: the IR
  carries it, the runtime enforces it, and a headless run refuses rather than
  guessing.
- The person's answer is durable, so resuming and replaying are the same act.
- Steering is context; structure is the outer loop's job. During use the graph does
  not change.

Back to the [tutorial index](index.md).
