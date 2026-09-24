# Agent Note: A user gate is answered by the runtime, not the model

Status: implemented

## Problem

`Node.gate_kind` types a gate as `fn`, `user`, or `agent`, and the spine handed a `user` gate to the driving agent with a prompt instruction to present it with `ask_user_question`. Nothing enforced the presentation: `wf_verdict` routed whatever label the model passed. A headless run of a mode with a person's gate therefore invented a verdict — `review_pack/review` reported *approved by the person* with nobody asked — and an invented verdict is indistinguishable in the run's artifacts from one a person gave. The Python walker refused the same gate, so the two runtimes disagreed about what a run had done.

## Decision

The spine asks the person itself. `wf_verdict` for a gate whose `evaluator_type` is `user` calls the composed `user-questions` answerer with the gate's own verdict labels as options, routes the answer the person gave, and ignores the model's `verdict` argument. When the composition mounts no user-questions channel, or no answerer accepts the request, the tool throws a message naming the gate, and the run stops there rather than routing an invented verdict. The CEO prompt section says the runtime asks and that the model must not answer for the person.

The plugin writes its own cursor status to `<workdir>/.refactory/spine-status.json` as `done`, `ready`, `failed`, or `refused`. A run that stopped at a gate is therefore a reported failure naming the gate rather than a run that scored 0; `refactory_dsh.dsh_runtime.evaluate_on_dsh` reads that file and fails the evaluation with the reason.

## How a person answers

The `graph` profile mounts a terminal answerer (`packages/bundle/graph/src/answerer.ts`). It shows the gate and its options on the terminal, records the choice through `python -m refactory_dsh.console steer --verdict <gate>=<value>`, and replays a verdict the run's ledger already holds without asking, so a resumed run is the same run. A captured or piped stdin is not a person: the answerer delegates instead, and the spine refuses. `python -m refactory_dsh.interactive <package>/<mode> --intent "<text>"` lowers the mode with its ledger folded in and starts that profile with the terminal attached.

The ledger is also the resume record on the walker side: `Ledger.verdict` appends the answer and `execute` merges `Ledger.verdicts()` into `RunContext.verdicts`, with an explicit caller-supplied verdict still winning.

## Alternatives considered

**Keep the model's `ask_user_question` and trust that it was used.** Rejected: a model that can route a gate without asking can invent the answer, and the artifacts cannot tell the two apart. That is the defect.

**Observe a `user-questions` answer and require one before routing.** The spine could listen on the scoped waterfall and refuse `wf_verdict` until an answer had been seen. Rejected as weaker: the observation is not bound to the gate, so an answer to any question would unlock it, and the model could still route a different label than the person chose.

**Lower a person's gate to an `fn` gate that reads a verdicts file.** Rejected: the file is writable by anything with filesystem access, a missing file has to be distinguished from an empty answer, and the runtime loses the one thing the kind exists for — pausing for a person instead of deciding for them.

**Record the verdict only in the ledger and let the gate read it.** That is how resume works and it is kept, but it does not answer the first question: the first answer still has to come from a person, so it cannot replace asking.

**Have the CEO present the gate and the spine verify the answer matches.** Rejected: the check would bind the answer to a label only after the model had already chosen one, so an unasked label still routes whenever the person's answer happens to match.

## Consequences

- Both runtimes now refuse a gate nobody answered, and both name it. The DSH refusal reads `the graph did not finish: gate approve is answered by a person and could not be asked: no user-questions answerer accepted the request`.
- The CEO prompt section is model-visible and changed, so the spine README's Model Experience text moved with it.
- The spine takes a peer dependency on `@deepseek-ai/dsh-user-questions`. Every composition that mounts the spine either answers or refuses; `dsh-base` mounts the service, so the live path is the missing-answerer refusal.
- `refactory_packages/review_pack` and `refactory_dsh/interactive.py` exist so the path has a mode and an entry point to run; no shipped mode declared a `user` gate before this.
- `<workdir>/.refactory/spine-status.json` is a new on-disk format, and the Python side depends on its four status words.
- The terminal answerer records through the Python console rather than writing the ledger itself, so the ledger's format stays owned by `refactory_dsh/steering.py`.

## Related

[The workflow spine's executable graph vocabulary](2026-09-09-workflow-spine-named-gate-outcomes.md) owns which labels a gate may route; this note owns who answers one.
