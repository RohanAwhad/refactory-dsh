# Bounded release validation — 26–27 September 2026

Implementation started from main `a339605c69` in an isolated clone. These checks concern execution, controls and reported evidence. They are not broad repository certification or a claim about generated-artifact quality. Historical evaluation campaigns stayed stopped. The original checkout and its user-owned swap file were left untouched.

## Readiness

**All four release capabilities have completed bounded acceptance:** fast authoring/resources ([#234](https://github.com/akashgit/refactory-dsh/issues/234)), live controls/recovery ([#235](https://github.com/akashgit/refactory-dsh/issues/235)), profiles ([#236](https://github.com/akashgit/refactory-dsh/issues/236)) and worker routing ([#237](https://github.com/akashgit/refactory-dsh/issues/237)). Each has separate CLI and native GUI evidence. All three fast modes completed their required validation; actual structural repairs and explicit unresolved failures were also observed. These results establish the tested contracts, not general artifact quality or token efficiency.

Implementation was merged in [PR #239](https://github.com/akashgit/refactory-dsh/pull/239) and [PR #240](https://github.com/akashgit/refactory-dsh/pull/240). [PR #241](https://github.com/akashgit/refactory-dsh/pull/241) added the requested full-bypass worker default and positive native worker checks; [PR #242](https://github.com/akashgit/refactory-dsh/pull/242) recorded successful public recovery. [PR #243](https://github.com/akashgit/refactory-dsh/pull/243) corrected validation-reserve ownership; [PR #244](https://github.com/akashgit/refactory-dsh/pull/244) finalizes fast stage scope, CLI forwarding, worker context and the package API correction. The records below retain unsuccessful attempts and capability limits as well as the positive evidence.

## Issue and interface evidence

| Issue | CLI evidence | Native GUI evidence | Remaining acceptance |
|---|---|---|---|
| [#234](https://github.com/akashgit/refactory-dsh/issues/234) | All three fast modes completed required QA/checks and reports. Create used one structural repair; Edit retained score 0.5 with no regression. Capped failures remain below. The deterministic known-defect control reached the real unresolved QA gate. | All three modes completed checks, QA and reports. Edit used one structural repair with no measured regression; corrected Create completed without repair. Its earlier import failure is retained. The deterministic known-defect control visibly reported the failed assertion and unresolved gate. | Complete for the bounded tasks. Required checks, structural repairs and unresolved outcomes are distinct evidence. |
| [#235](https://github.com/akashgit/refactory-dsh/issues/235) | Actual live `status --follow`, boundary resume, persisted questions and control receipts, and public recovery of a registered run after its owner stopped. | Pending gate discovery and verdict, pause/resume, reviewed invalidation/replay, concurrent ownership, owned stop, and native Resume of a registered stopped owner. | Both interfaces complete for supported boundaries. Unknown in-flight side effects and partial-fork recovery remain explicitly refused. |
| [#236](https://github.com/akashgit/refactory-dsh/issues/236) | Versioned profiles, exact configuration, 2 candidates × 2 trials, failed required-artifact control, separated holdout audit, adoption and replay. | Saved-version selection, profile creation and objective edit into a new version, same bounded search, candidate comparison, actual keep/reject/resume, exact adoption and replay. | No stochastic improvement is claimed; missing cost/usage remains unavailable. |
| [#237](https://github.com/akashgit/refactory-dsh/issues/237) | DSH passed; corrected native Codex and Claude Code dispatch both passed artifact and graph checks with explicit native edit permissions. | DSH passed. Codex and Claude Code passed the exact artifact and graph checks with the new `skip` default; actual native routes and versions displayed. | Both interfaces complete. Unsupported capabilities and usage coverage remain explicit below. |

## Fast-mode outcomes

The original six fast checks selected a 150,000-token allocation, 360-second limit and at most one repair. Allocation is enforced at admission/completion boundaries: an in-flight completion can take the observed total beyond the threshold. This is not a strict provider-side token ceiling.

| Interface | Mode | Observed tokens | Outcome |
|---|---|---:|---|
| CLI | Create fast | 149,410 | Working allocation exhausted; no completed emission/QA |
| CLI | Edit fast | 147,289 | Working allocation exhausted; no completed emission/QA |
| CLI | Design fast | 162,349 | Focused check passed; QA artifact written; exhaustion before final gate/report |
| Native GUI | Create fast | 145,594 | Exhaustion during emission |
| Native GUI | Edit fast | 156,722 | Exhaustion during emission |
| Native GUI | Design fast | 169,929 | Exhaustion during emission |

These six runs consumed 931,293 observed tokens, including completion-boundary overshoot of the requested aggregate allocation. Earlier 60,000-token failures and a zero-model preflight failure are retained separately. Resource exhaustion is failure evidence, not successful authoring. The [budget strategy analysis](budget-strategy.md) reconciles these receipts to provider usage and explains the subsequent scoped changes.

After the public CLI check/timeout forwarding fix, worker orientation and focused
stage changes, two further CLI checks used the same 150,000-token/360-second
limits and one-repair bound:

| Interface | Mode | Observed tokens | Outcome |
|---|---|---:|---|
| CLI, revised | Design fast | 135,727 | Completed in 31.77 seconds: sequential strategist, coder and QA; actual check passed, gate and report completed; zero repairs |
| CLI, revised | Create fast | 156,775 | Emitted package text, then token exhaustion before handback/QA; no completed authoring result |

The completed Design run changed only the copied tiny helper artifact; its source
was retained. A file emitted by the failed Create worker is not evidence of
structural admission, focused QA or canonical smoke.

The final scoped checks retain the six-minute elapsed limit and one-repair bound.
At the user's explicit request, the subsequent checks omit the optional
cumulative-token cap. Usage remains measured, including cached input; these
completion results do not establish token efficiency or reduced cost.

| Interface | Mode | Observed tokens | Elapsed | Outcome |
|---|---|---:|---:|---|
| CLI | Create fast | 1,210,437 | 143.47 s | Completed after one structural repair; focused QA, canonical smoke and report passed |
| CLI | Edit fast | 831,836 | 132.58 s | Completed with zero repairs; canonical score 0.5 → 0.5, `regressed=false`; default/changed inputs and sibling mode retained their contracts |
| Native GUI | Design fast | 128,567 | 29.02 s | Sequential strategist, coder and QA; focused check, gate and report passed; zero repairs |
| Native GUI | Edit fast | 757,421 | 130.15 s | Completed after one structural repair of a package-name mismatch; focused QA/check, canonical smoke and report passed; score 0.5 → 0.5, no regression |
| Native GUI | Create fast, failed attempt | 723,770 | 101.56 s | Failed after one structural repair on an unqualified package import; no focused QA, canonical smoke or final success report; resources were not exhausted |
| Native GUI | Create fast, corrected example | 655,160 | 99.89 s | Completed with zero repairs; structural admission, focused check/QA, canonical smoke (score 1) and report passed |

Native Create failed after its one repair: the supplied framework API example
used an unqualified package import. That failed run is retained. Five targeted
regressions verified the narrow example correction. The corrected
native Create run completed without repair. The successful capped CLI Design
above supplies the sixth interface path; it was not repeated. Default and changed
inputs were independently checked for the generated packages; Edit retained the
existing contracts and score, and the Design source remained unchanged.

The explicitly registered `fast_defect_acceptance/known_defect` control ran once
through the public CLI with no agent nodes and an impossible model route. The
actual helper assertion failed; production `focused_check` and `gate_qa` recorded
`unresolved` at `repair_attempts=0`, and no `design.json` was written. It exited
in 2.25 seconds within a 30-second/1,000-token allocation. The automatic driver
reported zero model calls; token telemetry remained unavailable. This is
deterministic failure/gate evidence, not model-authored QA or successful repair.
The native GUI **Run once** control independently showed the failed assertion and
unresolved gate with the same zero-repair bound, no `design.json`, and no budget
exhaustion. Its ledger recorded 2.19 seconds; it also used no agent nodes and the
impossible model route. Both interfaces therefore exposed the actual known-defect
failure without claiming a model review.

## Profiles and live controls

The `bounded-search` version 1 profile used random proposals, the walker evaluator, knob-only changes, one epoch, two candidate evaluations, two trials each, seed 9, and a 30-second shared allocation with three seconds reserved for validation. Both interfaces retained the same effective search constraints and seed-state digest. The CLI request omitted default knobs while the GUI explicitly supplied those identical defaults, so their raw request context hashes differ. The exact adopted winner `83ebe63e958b` had raw score 0.875 and adjusted score 0.795. CLI and native replay retained best score 0.875; no improvement was found. A separate Task control kept training score 1.0 and held-out score 0.5 distinct and refused ordinary adoption of the overfit result. An invalid required artifact made the candidate fail rather than receive a fabricated score.

Native profile versioning preserved the prior objective when a new version was saved. An additional zero-model search exercised actual keep/reject/resume at a generation boundary with two already measured candidates. Its acceptance driver held that boundary long enough to operate each UI control; it did not fabricate candidates or result receipts.

The live graph check used the real Cordis spine, shell execution and user-question bridge with deterministic operations. An explicit verdict was consumed once. Pausing did not invent a verdict. Correcting a completed node required a revision-bound preview; prior owned artifacts/facts were archived, dependent checks invalidated and unrelated input retained. The new draft reached a fresh gate rather than reusing the prior answer. A separate native ownership check ran a 15-second deterministic one-shot A while watching B at that gate: A completed successfully and B retained its identity, pending question and steering ledger. Fixture-only setup mistakes and their honest refusal receipts are retained separately from product evidence. Actual checkpoint restoration reused completed work without rerunning commands. The subsequent registered public recovery checks below cover the terminal and native panel separately.

## GUI chat optimization follow-up

On main `6ca5a57a84`, a fresh native CEO chat independently exercised the public
Refactory tool path. Earlier GUI optimization evidence above covered the panel;
it did not establish a chat-launched search.

The chat selected `bounded-search` version 1 and launched exactly one
`circle_packing/adaptive` search, `gui-chat-optimization-20260927`. Its saved
configuration retained random proposals, walker evaluation, one epoch, two
candidate evaluations, two trials, seed 9, 30 elapsed seconds with three reserved
for validation, and no token cap. The search completed four measurements in
0.179 seconds. Winner `83ebe63e958b` tied the seed: raw 0.875, adjusted 0.795,
with no improvement. Holdout was explicitly not applicable to this mode without
a data node; the separate Task holdout controls above remain the holdout evidence.

In that same chat, public tool calls inspected the winner, adopted it as
`issue236_chat_verified`, and smoked `issue236_chat_verified/adaptive`: exit OK,
score 0.875, 21 iterations. The native chat displayed the final result. Session
`session-e34a04ad-45ac-4fed-a2cb-e1fbfca94a86` records the actual start, status,
candidate, adoption and smoke calls; no shell substitute launched the search.
Two package-only registry targets were refused, then corrected to qualified mode
IDs without restarting the search. Source and saved-profile hashes were unchanged.

The chat used DSH / `deepseek-official` / `deepseek-flash`, low effort. Its turn
took 62.13 seconds and 25 model completions: 905,387 measured tokens, including
847,744 cached input, 50,159 uncached input and 7,484 output. The deterministic
search used no model workers. Chat orchestration usage is separate from that
search ledger; no efficiency or monetary-cost claim is made. The chat performed
source discovery to identify profile arguments, which remains observed overhead.

### Chat changes the optimizer to autoresearch

A second request in the same native chat launched one fresh run,
`gui-chat-autoresearch-20260927`, with per-run overrides selecting `autoresearch`,
two epochs, batch/elite size one, two evaluations, one trial, one admission attempt,
no prior-run history, 180 elapsed seconds with 15 reserved for validation, and no
token cap. The selected proposer route was DSH / official DeepSeek Flash / low
effort / skip permissions. The saved profile and source remained unchanged.

The run completed in **29.47 seconds**. Starting with only the seed ensured that
the next evaluated candidate came from the actual model proposer. Candidate
`3e50cd6446e7`, recorded with origin `optimizer`, changed the mode's `budget` knob
from 3 to 8 and was evaluated successfully: raw 0.875, adjusted 0.795, equal to the
seed. Both proposer calls completed with no errors and measured usage of 13,532
and 11,833 tokens (25,365 total). Worker receipts and provider-request records
confirmed the requested route. No improvement is claimed.

The existing pipeline also proposed `e160c2dbcfa1` (`grid` 10 → 24) at the end of
the final epoch. It remained explicitly unevaluated, with no invented score. No
search retry, source correction, additional adoption or replay followed; the first
chat check supplies adoption/replay evidence.

The second chat turn completed in 54.52 seconds and 20 model completions, using
1,294,169 tokens including 1,267,712 cached input. This orchestration usage is
separate from the 25,365 proposer tokens. Repeated status polling contributed to
the chat overhead; the result demonstrates functional configuration and execution,
not efficient chat orchestration. Both searches ended, all proposer workers
completed, resource clocks paused, and the temporary GUI server was stopped.

## Native workers and permissions

Earlier apparent Codex/Claude successes were withdrawn: the named-provider patch had left DSH active. The dispatch fix disables that entry before inserting the selected native worker and refuses DSH fallback. A composition regression verifies the actual installed loader's match semantics.

| Corrected CLI backend | Native runtime/model | Permission mode | Elapsed | Usage | Result |
|---|---|---|---:|---|---|
| Codex | 0.153.4 / `gpt-6-astra`, OpenAI, high | `approve-for-me` | 8.24 s | 44,964 measured native-thread tokens; nested coverage unverified | Exact `42\n` and graph-owned Python assertion passed |
| Claude Code | Agent SDK 0.3.263 / `claude-opus-4-6[1m]` | `acceptEdits` | 72.84 s | 454,673 estimated SDK-pipeline tokens; helper calls excluded | Exact `42\n` and graph-owned Python assertion passed |

Both checks were single attempts with 120-second limits and no retry. External whole-tree token caps remain unsupported. Claude's own Python tool was denied by its native sandbox; its alternative byte check and the independent graph Python check passed. The native GUI default-permission attempts reported 44,405 Codex measured tokens and 46,668 Claude estimated tokens, but delivered no required file. Those are useful permission-failure controls and are not positive GUI completion evidence.

## Worker permission follow-up

At the user's explicit request, new workers now default to full native permission bypass (`skip`). Existing profiles and checkpoints retain their prior policies. The change was verified at `1762d11dca`, based on merged main `6d732dd9e4`. Each additional GUI check used **Run once**, one worker attempt, a 120-second elapsed allocation, no retry or repair, and the same fixed `worker_gui_acceptance/worker_once` task. Both delivered exact `42\n`, passed the independent Python artifact check, and visibly reached **finished / done** with `build` and `verify` complete.

| Native GUI backend | Actual native runtime/model | Elapsed including graph checks | Usage | Run identity |
|---|---|---:|---|---|
| Codex | 0.153.4 / `gpt-6-astra`, OpenAI, high | 9.15 s | 44,016 measured native-thread tokens; nested coverage unverified | `single-4df96cd6-8a2c-4261-844c-061a1e92ea3c` |
| Claude Code | Agent SDK 0.3.263 / `claude-opus-4-6[1m]` | 18.73 s | 77,887 estimated SDK-pipeline tokens; helper calls excluded | `single-48ddda32-0d15-4b20-8992-361ccc16dd9d` |

Requested permission `skip` was saved in each run and worker receipt. Installed-provider contract tests independently verify Codex's full-access sandbox/never-approve mapping and Claude's bypass mapping. DSH uses a scoped runtime patch, with no environment override leaking into explicitly restricted children. Fresh preparation, CLI/console context propagation and historical run/profile preservation are covered by 117 focused Python tests and nine native-controls component tests; host/client typechecks and builds passed. Previous positive CLI evidence is reused for unchanged worker dispatch and artifact contracts. No further worker model calls or old campaign restarts were made. A subsequent recovery fixture unexpectedly used a managed profile pointing to the older graph runtime and consumed 9,929 coordinator tokens before its completion-boundary cap stopped it; no node completed and no retry with model access followed. This failed fixture is retained separately from the successful worker evidence.

## Public recovery follow-up

A registered two-operation package completed `first`, queued a pause before `second`, saved its real graph checkpoint, and exited. Recovery then used the normal public operation against that stopped owner; no checkpoint or success receipt was fabricated. These checks ran against merged main `10cefa309a`.

| Interface | Public action | Run identity | Observed result |
|---|---|---|---|
| CLI | `refactory graph-control resume --run RUN` | `registered-recovery-cli-guarded` | Queued a fresh owner; reached done with first/second complete |
| Native GUI | Existing run directory → Read this run → resume | `registered-recovery-gui-guarded` | Showed paused, first complete and second due; after one resume, streamed finished/done with both complete |

Both runs preserved the first artifact, its exactly-once invocation marker, saved run configuration, invocation identity, knob value, requested route and accumulated resource accounting. Only `second` executed after recovery. A simultaneous public resume attempt while the first owner was active was refused. Reading the native run did not execute it, and completion appeared without a manual refresh.

The actual graph/headless profile packages were pinned to the isolated checkout. Both saved routes used the deliberately unavailable provider `refactory-no-model` and model `no-model`: the installed LLM registry was separately verified to reject it with `NO_ADAPTER`, with no fallback adapter invocation. The deterministic runtime reported `driver: automatic` and `driver_model_calls: 0`; usage remained unavailable instead of inventing measured model tokens. The earlier accidental coordinator call and a filesystem-watcher startup refusal remain separate failed fixture evidence. No model-enabled retry followed them.

## Verification limits

Initial focused integrated Python checks passed (168 tests), followed by the relevant worker regressions. The final fast-mode slice passed 124 focused Python tests, five new API regression cases, 27 native execution/steering tests and 19 documentation tests. One optional external graph-reference oracle was skipped. Host/client TypeScript checks, builds and normal commit/push hooks passed. Two deferred-stream UI regressions fail on the preceding implementation and pass with the fix that keeps watched run B's state and gates after run A completes. Existing unrelated test failures remain outside this change; no clean full-suite claim is made.

Raw receipts, run directories and local acceptance reports remain in the isolated task checkout. Credential files, token-bearing server URLs and private runtime logs are not published.
