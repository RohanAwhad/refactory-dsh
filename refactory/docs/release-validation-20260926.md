# Bounded release validation — 26 September 2026

Implementation started from main `a339605c69` in an isolated clone. These checks concern execution, controls and reported evidence. They are not broad repository certification or a claim about generated-artifact quality. Historical evaluation campaigns stayed stopped. The original checkout and its user-owned swap file were left untouched.

## Issue and interface evidence

| Issue | CLI evidence | Native GUI evidence | Remaining acceptance |
|---|---|---|---|
| [#234](https://github.com/akashgit/refactory-dsh/issues/234) | Create/Edit/Design fast graphs launched with shared resources; all exhausted tokens before completion. Focused Design check and a QA artifact ran, but the graph did not finish. | All three variants selected and launched; actual routes, resource consumption and failed states displayed. | Successful complete fast paths through QA and bounded repair in both interfaces. Issue remains open. |
| [#235](https://github.com/akashgit/refactory-dsh/issues/235) | Actual live `status --follow`, boundary resume, persisted questions and control receipts; checkpoint admission/restoration and failure refusal have separate deterministic evidence. | Pending gate discovery and verdict, pause/resume while waiting, reviewed invalidation/replay, ownership across concurrent runs, and owned stop. | Complete registered dead-owner recovery through both public interfaces. Issue remains open. |
| [#236](https://github.com/akashgit/refactory-dsh/issues/236) | Versioned profiles, exact configuration, 2 candidates × 2 trials, failed required-artifact control, separated holdout audit, adoption and replay. | Saved-version selection, profile creation and objective edit into a new version, same bounded search, candidate comparison, actual keep/reject/resume, exact adoption and replay. | No stochastic improvement is claimed; missing cost/usage remains unavailable. |
| [#237](https://github.com/akashgit/refactory-dsh/issues/237) | DSH passed; corrected native Codex and Claude Code dispatch both passed artifact and graph checks with explicit native edit permissions. | DSH passed. Actual native Codex/Claude route and version observed, but default permissions prevented required output; graph failure was visible. | Positive native GUI completion with explicit edit permissions. Issue remains open. |

## Fast-mode outcomes

Each approved fast check selected a 150,000-token allocation, 360-second limit and at most one repair. Allocation is enforced at admission/completion boundaries: an in-flight completion can take the observed total beyond the threshold. This is not a strict provider-side token ceiling.

| Interface | Mode | Observed tokens | Outcome |
|---|---|---:|---|
| CLI | Create fast | 149,410 | Working allocation exhausted; no completed emission/QA |
| CLI | Edit fast | 147,289 | Working allocation exhausted; no completed emission/QA |
| CLI | Design fast | 162,349 | Focused check passed; QA artifact written; exhaustion before final gate/report |
| Native GUI | Create fast | 145,594 | Exhaustion during emission |
| Native GUI | Edit fast | 156,722 | Exhaustion during emission |
| Native GUI | Design fast | 169,929 | Exhaustion during emission |

The six runs consumed 931,293 observed tokens, including completion-boundary overshoot of the requested aggregate allocation. Earlier 60,000-token failures and a zero-model preflight failure are retained separately. No automatic polishing, artifact repair campaign or further fast-mode rerun followed. Resource exhaustion is failure evidence, not successful authoring.

## Profiles and live controls

The `bounded-search` version 1 profile used random proposals, the walker evaluator, knob-only changes, one epoch, two candidate evaluations, two trials each, seed 9, and a 30-second shared allocation with three seconds reserved for validation. Both interfaces retained the same effective search constraints and seed-state digest. The CLI request omitted default knobs while the GUI explicitly supplied those identical defaults, so their raw request context hashes differ. The exact adopted winner `83ebe63e958b` had raw score 0.875 and adjusted score 0.795. CLI and native replay retained best score 0.875; no improvement was found. A separate Task control kept training score 1.0 and held-out score 0.5 distinct and refused ordinary adoption of the overfit result. An invalid required artifact made the candidate fail rather than receive a fabricated score.

Native profile versioning preserved the prior objective when a new version was saved. An additional zero-model search exercised actual keep/reject/resume at a generation boundary with two already measured candidates. Its acceptance driver held that boundary long enough to operate each UI control; it did not fabricate candidates or result receipts.

The live graph check used the real Cordis spine, shell execution and user-question bridge with deterministic operations. An explicit verdict was consumed once. Pausing did not invent a verdict. Correcting a completed node required a revision-bound preview; prior owned artifacts/facts were archived, dependent checks invalidated and unrelated input retained. The new draft reached a fresh gate rather than reusing the prior answer. A separate native ownership check ran a 15-second deterministic one-shot A while watching B at that gate: A completed successfully and B retained its identity, pending question and steering ledger. Fixture-only setup mistakes and their honest refusal receipts are retained separately from product evidence. Actual checkpoint restoration reused the completed checkpoint without rerunning commands; this does not substitute for the outstanding public dead-owner recovery path.

## Native workers and permissions

Earlier apparent Codex/Claude successes were withdrawn: the named-provider patch had left DSH active. The dispatch fix disables that entry before inserting the selected native worker and refuses DSH fallback. A composition regression verifies the actual installed loader's match semantics.

| Corrected CLI backend | Native runtime/model | Permission mode | Elapsed | Usage | Result |
|---|---|---|---:|---|---|
| Codex | 0.153.4 / `gpt-6-astra`, OpenAI, high | `approve-for-me` | 8.24 s | 44,964 measured native-thread tokens; nested coverage unverified | Exact `42\n` and graph-owned Python assertion passed |
| Claude Code | Agent SDK 0.3.263 / `claude-opus-4-6[1m]` | `acceptEdits` | 72.84 s | 454,673 estimated SDK-pipeline tokens; helper calls excluded | Exact `42\n` and graph-owned Python assertion passed |

Both checks were single attempts with 120-second limits and no retry. External whole-tree token caps remain unsupported. Claude's own Python tool was denied by its native sandbox; its alternative byte check and the independent graph Python check passed. The native GUI default-permission attempts reported 44,405 Codex measured tokens and 46,668 Claude estimated tokens, but delivered no required file. Those are useful permission-failure controls and are not positive GUI completion evidence.

## Verification limits

Focused integrated Python checks passed (168 tests), followed by the relevant worker and primary-CLI regressions. Host/client TypeScript checks, builds and normal commit/push hooks passed. Two deferred-stream UI regressions fail on the preceding implementation and pass with the fix that keeps watched run B's state and gates after run A completes. Existing unrelated JavaScript test failures were reproduced on the comparison baseline and remain outside this change; no clean full-suite claim is made.

Raw receipts, run directories and local acceptance reports remain in the isolated task checkout. Credential files, token-bearing server URLs and private runtime logs are not published.
