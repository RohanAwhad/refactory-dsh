# Choosing a budget for agentic work

A universal cumulative-token cutoff is a poor definition of a successful fast run.
Keep token limits as **optional, explicit resource guardrails**. Bound ordinary fast
work through its small graph, focused QA, repair limit and worker/check timeouts.
A required check must still pass; removing a cutoff does not establish correctness.

The current product already leaves run-wide token and elapsed limits unset by
default. The 150,000-token/360-second settings in the September release checks were
acceptance-test allocations, not product defaults. The initial analysis reused
saved receipts. Subsequent scoped CLI checks are recorded below; they do not
establish a universal allocation or remove any explicitly selected limit.

## What the failed checks measured

The six CLI/native GUI attempts used the same DSH model. Their worker receipts
reconcile with the saved provider usage:

| Usage category | Tokens | Share |
|---|---:|---:|
| Uncached input | 131,098 | 14.1% |
| Cached input | 780,672 | 83.8% |
| Output, including reasoning | 19,523 | 2.1% |
| Total | 931,293 | 100% |

No run reached its six-minute elapsed limit, and no repair loop ran. The
observed failures therefore do not establish that the task scope or repair bound
needed expansion.

These totals count context processed on successive requests. They are neither
novel output nor a dollar amount; actual costs were unavailable. Cached tokens
remain part of honest usage accounting, but raw token totals alone cannot compare
spending or useful progress across runtimes and models.

Five implementation workers made no write/edit tool calls before exhaustion.
Their calls explored framework, source and delivery files despite the prompt
already containing the emission instructions and relevant API/reference text.
The original CLI Create/Edit traces also showed a child Python import failure
followed by source discovery. The selected-Python propagation fix and worker
orientation now supply the interpreter, node identity, exact input/output paths
and delivery boundary directly. Fast prompts give the coder its API/reference
context and give QA the changed-file list plus the executed check result.
Workers are instructed to return after delivering the current node, without
rediscovering runtime protocols or executing later stages. These are implemented
changes; they do not guarantee compliant or efficient model behavior.

CLI Design completed implementation at 69,799 tokens and used another 92,550 for
QA. Its passing QA JSON was written at 133,008 total tokens; two further requests
used 29,341 tokens before handback crossed the limit. The runtime never completed
the gate/report, so that file is partial evidence, not a completed run. A fixed
1,000-token validation reserve was not a meaningful estimate of this QA stage.

See the [release outcomes](release-validation-20260926.md#fast-mode-outcomes) for
all six attempts and their limits. They do not establish a successful minimum
allocation or prove that more tokens would produce a good result.

## Separate the controls

| Control | Purpose | Current behavior |
|---|---|---|
| Small graph and repair bound | Keep the requested work finite and focused | Create/Design use one strategist, coder and QA agent sequentially; Edit uses coder and QA directly. At most one repair by default; required checks remain in the graph |
| Worker/check timeout | Stop a hung or excessively slow operation | Separate from the optional run-wide elapsed limit |
| Optional elapsed/token limit | Honor an explicitly chosen resource boundary | Shared across descendants and validation; exhaustion is reported and does not become success |
| Validation reserve | Retain part of an explicit allocation for required checks | Validation may use the reserve; returning to implementation must use the work allowance |
| Usage and progress evidence | Explain what the run spent and achieved | Inspect worker receipts, completed nodes and artifact/check records; unavailable measurements stay unavailable |

A token limit is checked at usage boundaries. An already admitted request can
overshoot it, so it is not an exact provider-side ceiling. Do not silently raise an
explicit limit or discard cached-input usage to make a run appear compliant.
External workers without complete nested telemetry refuse token-limited launches.

When a user needs a strict monetary ceiling, the current raw-token control cannot
promise one. That would require reliable model-specific pricing and complete
billing coverage. Choosing no token cap is an explicit tradeoff: work remains
subject to its graph/repair bounds and operation timeouts, but total token usage
has no configured ceiling.

## Reserve correction

The investigation found a separate implementation bug: after Design entered final
validation, its sticky resource phase could let a repair inherit access to the
validation reserve. Fast graph nodes now declare that phase independently of their
worker-routing role. A repair returns to `work`; Design QA/reporting retain their
validation phase. Explicit work phases survive native graph serialization.
Legacy nodes without an explicit phase keep their prior behavior.

Deterministic checks exercise both the Python executor and native graph runtime:
with a total allocation of 100 and reserve of 20, work using 60 followed by QA
using 25 may finish a successful report at 85, but failed QA cannot start a repair
from that remaining validation allocation. Reaching the total limit still fails.
This fixes reserve ownership; it does not fix unproductive exploration or prove
successful model-backed fast authoring.

## Scoped acceptance sequence

The revised checks use the changed execution strategy:

1. Verify the actual loaded runtime, selected Python/imports and reference paths
   without a model call. Keep unrelated run histories out of the task context.
2. Use one fixed representative task first. Retain its focused QA and repair bound;
   agree any resource allocation separately before launching paid work.
3. Inspect stage completion and the reason for each tool call, alongside input,
   cached-input and output usage. A larger cap is not the response to repeated
   framework discovery or another environment failure.
4. Continue to the other paths only after that representative path completes and
   the evidence supports the execution strategy. Reuse unchanged interface and
   accounting evidence.

The representative CLI Design task corrected a tiny copied helper and completed
the three sequential roles, focused check, QA gate and final report at 135,727
observed tokens in 31.77 seconds, with no repair. A subsequent CLI Create attempt
emitted its package but reached 156,775 tokens before worker handback or QA;
the configured 150,000-token limit stopped the run. The emitted file is partial
progress, not a completed package or QA result.

An attempted uncapped launch was rejected by approval review before execution;
no uncapped model run occurred. Five additional checks—CLI Create retry and Edit,
then native Create/Edit/Design—are proposed with six-minute elapsed limits and
one repair. Their allocation approval and results remain pending.
[Issue #234](https://github.com/akashgit/refactory-dsh/issues/234) remains open.

Further improvements worth implementing are advisory stage estimates, earlier
checks for repetitive unproductive tool use, restricted stage-specific tool
surfaces, and durable finalization of a completed worker contract before admitting
more paid work. These remain recommendations; the supplied node orientation and
focused prompts are already implemented. A file appearing on disk is never
enough by itself to stop a worker or certify success: its declared contract,
checks and side-effect boundary still need to be resolved.

[Resource settings and recovery](budgets-fast-modes.md) · [Worker capabilities](models.md)
