# Authoring validation acceptance — 2026-09-27

This is the focused acceptance record for #250, #251 and #252. It distinguishes
runtime execution, requirement coverage and final completion. The behavioral
contract and graphs are in [authoring-validation.md](authoring-validation.md).

## Source and fixture

Implementation started in an isolated `codex/authoring-validation` worktree from
`8c3f2a61b738e8305e0d7b1ee10345b7deb91e3d`. The runs used the working-tree
implementation; each saved run includes its prepared graph, declarations, worker
specs and configuration fingerprints. Final controls also cover refinements made
after those launches: clearer audit diagnostics/prompts, initialization of first-pass
feedback files, output-file freshness, full-mode repair resource phases, the maximum
fast repair allowance, and the smoke gate's configured command timeout.

Evidence roots below are relative to that worktree. Raw logs and managed
authentication stay local. `.factory/authoring-acceptance-20260927/VERIFIED-EVIDENCE.json`
records receipt digests, execution order, actual routes, usage and final source hashes.

The synthetic source `authoring_seed/greeting` has one string knob `text`, a
`greeting.txt` invariant and an equality score. Its original operation would write
`ORIGINAL_EXECUTED` and raise. The requested revision must write the knob value plus
one newline, publish matching `greeting`/`expected` facts, preserve its score and
invariant, and include a README documenting `text=welcome`. No dependencies, model
nodes, extra modes or optimization are requested. The original execution marker
remained absent after every run, and no original-smoke directory was created.

## Actual terminal and native GUI evidence

| Entry point | Run identity | Observed result |
|---|---|---|
| Public terminal, `refactory ceo --mode edit_fast` | `.factory/authoring-acceptance-20260927/cli` | Completed. Initial runtime defect failed smoke and returned diagnostics to the coder. Corrected execution then exposed the intentionally missing README to focused QA. The second repair added it; fresh smoke, audit and final report passed. Three distinct smoke directories, two repairs, ten passing audit rows. |
| Native Chrome Modes panel, **Run once** | `.factory/interactive/single-07cc0586-200c-44dd-ab67-65d7628a5e4e` | Runtime-defect and missing-README controls both caused repair. The final package executed successfully, but QA cited a directory for one requirement. The audit gate refused completion at the declared bound. No `create.json` was published. This is a negative acceptance result, not a successful authoring run. |
| Native Chrome CEO chat, `wf_open`/`wf_next`, `default_pack/edit_fast` | `.factory/authoring-acceptance-20260927/chat-fast` | Completed. First audit self-citations were rejected. Its one allowed repair led to fresh execution and a valid audit grounded in individual files. Final output was exactly `hello\n`; score 1; fifteen passing rows and one explicit exclusion for original comparison. Eleven graph nodes completed, none skipped. |
| Native panel, **Read this run**, on that completed chat run | Same `chat-fast` identity | Displayed finished/done, all eleven completed nodes, smoke-before-QA connections, worker receipts and Outputs and validation. This read did not launch another evaluation. |

The terminal and panel briefs explicitly staged two temporary defects: first raise
`ValueError("ACCEPTANCE_RUNTIME_DEFECT")` and omit the README; after runtime feedback,
fix the operation but retain the omission until QA reports it. Neither defect was
an authorized final exclusion. The chat brief requested the correct small artifact
directly. These complementary paths consolidate acceptance without repeating the
same model campaign until it happens to pass.

All three paths set `allow_regression=true`; smoke validated the revised mode.
Their persisted revision records retain `revision_of_state` and its digest, set
`before_score` and `regressed` to null, and explicitly state that original execution
and non-regression measurement were intentionally skipped. The terminal/panel used
two repairs, 30-second smoke and a 600-second run bound. Chat used one repair and
30-second smoke. Token caps were unset. Chat's `wf_open` did not install a shared
elapsed/token ledger; its worker telemetry is reported separately rather than
inventing aggregate usage or an enforced elapsed limit.

Actual terminal and panel workers used DSH `0.1.5-rc.3`,
`deepseek-official/deepseek-flash`, effort `low`. Chat selected that provider/model
and low effort in the native composer, but child receipts record effort as null;
they do not establish an effective child effort of low. The existing permission
default remained `skip`. Credentials stayed in the managed store.

| Run | Reported tokens | Time evidence |
|---|---:|---|
| Terminal | 647,954, measured run ledger | 140.810 seconds |
| Panel | 694,532, measured run ledger | 210.749 seconds |
| Successful chat | 892,734 across four worker receipts; coordinator UI approximately 1.1 million separately | Native chat UI: 4m 34s; no aggregate run ledger |

These are reported token counts, including provider-reported cached input; they
are not monetary cost estimates. Coordinator and child figures have distinct
scopes and are not presented as a deduplicated total.

## Incomplete full-chat attempts and discovered defects

An initial full Edit native chat stopped before its first worker because
`research-review.txt` was a required input without an initial producer. The shared
prepare operations now initialize feedback as **not run**, and strict worker-read
controls cover first-pass Create and Design. No missing file was hand-seeded in the
failed run. Its identity is
`.factory/web/session-09f56067-2ead-42ef-9b4e-4c6565a386d3/default_pack-edit`;
native coordinator UI reported approximately 460,000 tokens.

A fresh full-chat attempt at `.factory/authoring-acceptance-20260927/chat-fixed`
passed initialization but expanded the tiny task into the full research director's
delegated investigations. It was stopped before implementation to avoid an
unnecessary research campaign. The research worker reported 5,743,561 tokens;
the coordinator UI reported approximately 66,000 separately. Native Stop aborted
the chat call but its owned child processes remained, so the two identified
worker processes were terminated and their absence verified. The saved graph
reports exit 143 and the worker reports cancelled. This is **not** completed
full-mode/Overwatch model acceptance. Full-mode audit delivery, role separation,
failure/repair behavior and stale sign-off are covered by the controls below;
actual modeled intent-audit behavior was established with focused QA.

## Controls and limits

The targeted Python suite covers full/fast Create/Edit runtime repair before
review, all three fast repairs, unresolved bounds, missing credentials/tools,
cancellation, timeout, resource exhaustion, missing criteria, reviewer waivers,
explicit user exclusions with current evidence, changed source/intent/output,
stale delivery, native first-pass reads, shared validation reserve, and lowered
gate timeout preservation. Existing false/default comparable and non-comparable
Edit behavior remains covered alongside the no-original-execution spies.

Validation: 186 Python tests passed; 33 optional reference-oracle checks skipped
because `factory` is not installed. The generated API reference and tutorial were
regenerated and verified. Five targeted TypeScript suites passed, 143 tests,
covering the chat tool, panel rendering/graph/declarations and workflow execution.
Build/typecheck and normal commit/push hooks passed. Seven unrelated console-service
expectation failures also reproduced on the unchanged base commit (39 passed,
7 failed in that suite); they were not repaired by this change.

Audit parsing checks traceability and freshness, not the truth of arbitrary model
claims. In particular, a numeric smoke score alone does not establish every user
requirement. The failed panel audit demonstrates that a reviewer-authored PASS
does not override the runtime evidence contract.

The task's localhost server on port 60211 and its owned workers were stopped.
Other tasks' servers, the protected original checkout, `.env.swp`, and historical
campaigns were not changed or restarted.
