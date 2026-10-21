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

## A headless run stops

```bash
uv run refactory package smoke review_pack/review
```

```
{"error": "approve: ExecutionError: gate 'approve' is answered by 'user' and this run
has no verdict for it. A headless run cannot answer a gate meant for a person or for
an agent; pass one in RunContext.verdicts (an interactive run collects a fresh
answer) or make the gate `fn`."}
```

That stop is the point: an invented verdict is indistinguishable in the artifacts
from one a person gave, so it must not be invented.

## Run it with a person

```bash
uv run refactory ceo review_pack/review \
  --knob 'topic=circle packing' \
  --intent "draft a note about circle packing and let me approve it"
```

The frozen `topic` knob supplies the draft's subject. The intent describes how to
run and review it; it does not substitute input for a deterministic operation.
In the Modes panel, fill the `topic` field. In CEO chat, ask it to launch
`review_pack/review` with `topic=circle packing`. Programmatic callers may still
pass `task={"topic": "circle packing"}` (or `brief`); an explicit task takes
precedence over the knob.


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
> **stops** at the gate, naming it. A walker run raises ``GateStop``; a headless
> DSH run stops with `the graph did not finish: gate approve is answered by a person
> and could not be asked: no user-questions answerer accepted the request` instead of
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
explicit Python `RunContext.verdicts` mapping. A ledger-backed upfront approval
persists across later invocations in that same work directory; use a new directory
when a fresh approval policy is required. A consumed or legacy unscoped ledger
answer does not substitute for that authorization.

## Reusing a work directory

`ceo` and `wf_open` start a fresh graph invocation, including when you explicitly
reuse a work directory. Prior declared outputs, current facts, lowering and runtime
receipts move into `.refactory/invocations/<id>/artifacts/`; `archive.json` lists
what moved. A rejected second review therefore has no current `published_chars`
or old `final.md` left over from a first approval.

The input selection (`items.json`), source inputs, other files the runtime does not
own, and `intent-ledger.md` remain in place. This preserves the documented standing
upfront approval policy; fresh execution does not silently revoke that approval.
An old owned output used as a declared external input by the new mode is retained
and listed in the receipt. Undeclared files are not assumed to be runtime outputs.
Explicit `wf_redo` still uses its separate invalidation/archive contract.

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

A finished search has no next boundary. New Keep, Reject, Pause, Resume and Stop
requests are refused without queuing; the panel disables those controls. **Adopt
best now** and `steer --adopt` instead apply guarded adoption immediately and show
an applied or refused receipt, including the resulting package directory. Reading
`steer --run <run>` preserves that receipt across refreshes.
If the search is explicitly resumed with more budget, controls follow the active
optimizer. Its previous terminal report is retained under
`.refactory/previous-reports/`; a report-only replay does not start that lifecycle.
Only one optimizer may own a run directory at a time. A concurrent launch is
refused before changing its state; process exit releases ownership automatically.

Older versions could leave a Keep or Reject queued after completion. Both adoption
routes recover that choice, including its lineage, into the journal and selection
state before choosing a winner. The receipt identifies the recovery; it does not
claim a new evaluation. If rejection changes the winner, adoption refuses an audit
for the old winner. An unknown queued candidate is preserved with an error.

To audit the current winner without proposing candidates or opening another epoch,
replay the saved configuration and call the public report step directly. Run this
from the original project, using the same model route as the original search when
its evaluator needs a model (the example uses DeepSeek V4.1 Flash):

```python
import json
from pathlib import Path
from refactory_dsh.loop import Loop, LoopConfig
from refactory_dsh.model import DshModelClient

root = Path(".factory/loop/<run>")
saved = json.loads((root / "config.json").read_text())
config = LoopConfig.from_dict(saved)
config.runs_root = root
config.resume = True
loop = Loop(saved["mode"], config, model=DshModelClient(
    provider="deepseek-official", model="deepseek-flash",
))
loop.init()                 # replay; does not evaluate or propose
report = loop.report()       # audit the current winner; may call the model
```

The saved epoch/evaluation bounds remain unchanged. Inspect the resulting audit,
then repeat adoption. This does not waive failed evaluations or an overfit warning.

## What to take away

- **Who answers a gate is part of the architecture**, not a convention: the IR
  carries it, the runtime enforces it, and a walker run stops (a headless DSH run
  refuses at the spine level) rather than guessing.
- The decision history is durable. Interactive approval applies once to the
  reviewed inputs; another visit needs a new answer.
- Steering is context; structure is the outer loop's job. During use the graph does
  not change.

Back to the [tutorial index](index.md).
