# Benchmark strategy review under actual user delegation

This optional benchmark profile retains the native strategy gate and requires
a specific independent operator decision for every actual gate visit. Production
human gates retain their existing behavior. Delegation grants authority to review;
it supplies no verdict, and says nothing about whether the user inspected the
new strategy bytes. Never use this profile without actual recorded user
authorization for the exact trial, lane set and reviewer.

Before launch, the operator stores the trusted context at
`TRIAL/operator/review-context.json`, outside every lane-writable project and
denied to authoring processes. Populate references from the trusted user channel,
never from generated lane files. The following is an **unfilled** scaffold:

```json
{
  "reviewer_id": null,
  "trusted_user_references": {},
  "trusted_operator_delegations": {
    "ACTUAL_AUTHORIZATION_REFERENCE": {
      "authorization": "operator-strategy-review",
      "trial_id": null,
      "lane_ids": [],
      "gate": "gate_strategy",
      "operator_id": null,
      "user_turn_reference": null,
      "user_message_reference": null,
      "user_thread_reference": null,
      "authorization_text": null
    }
  }
}
```

`trial_id` is the actual trial root basename. `operator_id` equals `reviewer_id`.
List only the lanes actually authorized by the user. A delegation containing
`decision` or `verdict` is rejected. Context is operator supplied provenance,
not platform authentication or a cryptographic signature.

When the lane reaches its real strategy gate, inspect the actual question and
every input named by `LANE/project/approval-required.json`. Read the complete
current inputs and make a specific decision. Save that review under
`TRIAL/operator/reviews/LANE/strategy-REVIEW_VISIT.json`. This unfilled shape
cannot pass validation or authorize continuation:

```json
{
  "schema_version": 1,
  "decision_source": "delegated-operator",
  "human_inspected_strategy": false,
  "trial_id": null,
  "lane_id": null,
  "gate": "gate_strategy",
  "reviewer_id": null,
  "strategy_digest": null,
  "input_paths": [],
  "request_created_unix": null,
  "request_sha256": null,
  "verdict": null,
  "feedback": "",
  "rationale": null,
  "evidence": [
    {"path": null, "sha256": null, "quote": null}
  ]
}
```

Copy the digest, input paths and creation time from the actual pending request;
`request_sha256` hashes that request's exact current bytes. Each declared input
requires its current SHA-256 and a literal supporting excerpt. Rationale must
explain the actual comparison of strategy to intent, including any limitations.
Choose `proceed`, `reloop`, or `halt`; `reloop` requires specific feedback. These
citations bind the reviewed files; they do not automate substantive adequacy.
Baseline inputs are `strategy.json` and `brief.json`; candidate/reference inputs
are the actual `.factory/strategy/current.md` and `user-intent.md` declarations.

Queue that actual review while the controller is alive:

```sh
python -m benchmarks.create_fidelity.harness operator-decision TRIAL dsh-candidate \
  --review TRIAL/operator/reviews/dsh-candidate/strategy-REVIEW_VISIT.json \
  --delegation-reference ACTUAL_AUTHORIZATION_REFERENCE
```

The controller revalidates the external review before sending it through its
owned pipe. Lane-authored delegated answers are rejected. Changed inputs or
request visit, incomplete citations, wrong scope, replay, and a claimed human
inspection fail. A different gate visit needs a new review; no automatic
`proceed` exists. A `reloop` correction is explicitly labeled delegated operator
feedback, not a new human utterance. Candidate continuation exercises actual
`Mode.resume`, native one-use Ledger consumption, and current gate receipts.
The pinned baseline records its documented `Mode.run(start=..., facts=...)`
recovery mapping. Remote continuation retains the source gate decision and
records its source actor; unsupported receipt surfaces remain unavailable.

Raw answers use `source: delegated-operator`. DSH writes a separate
`raw/approvals/native-consumption-*.json` with `decision_actor`, logical gate
owner, exact digest, verdict ID and actual uses. The native receipt's `owner:
human` identifies the original gate's logical ownership; it does not establish
the actor's identity. Actor provenance comes from the archived answer, external
review and independently trusted delegation.

After raw evidence settles, create the ordinary identity review template under
`operator/reviews/LANE/human-approval.json`. For this profile add
`decision_source: delegated-operator`, `delegation_reference`,
`human_inspected_strategy: false`, `decision`, `strategy_digest`, `input_paths`,
`answer_evidence_path`, `request_evidence_path`, `native_receipt_path`, and
`operator_review_path` relative to `operator/reviews`. Match the actual consumed
request, archived answer and current native gate receipt. A passing identity row
reports `authorization_profile: benchmark-delegated-strategy-review/v1`, distinct
from `direct-user-answer`. It proves the bounded authorization/consumption
protocol, not direct human review of these bytes or overall authoring success.

Each harness lane owns a dedicated session and a read-only controller pipe.
The lane marks the descriptor non-inheritable before model/tool dispatch.
Controller EOF, explicit cancellation and the absolute trial wall deadline
unwind normal worker ownership, including strategy waiting. Native subprocesses
in the dedicated lane group are joined or killed within bounded shutdown;
every lane exit checks that group, including internal failures. Cleanup that
cannot be established is recorded as unknown. The pipe closes on controller
SIGKILL without relying on a controller finally block. Standalone adapters have
a finite wall deadline but never signal an unrelated caller process group.
`raw/lifecycle.json` includes `owned_group_cleanup`: the actual lane-exit trigger,
initial residual member PIDs, each attempted signal and its group recheck result,
remaining members and confirmation. `no_residual_members_observed` differs from
`residual_children_signalled`; a successful lane result does not hide forced
residual cleanup. The cancellation reason remains separate from that exit check.
On macOS, membership uses libproc scoped to the owned process group; Seatbelt
rejects launching the system's setuid `ps` binary. The read separation policy
remains in force during inspection and cleanup.

Freeze this adapter and its identity into a fresh trial. Do not retrofit this
authorization or supervision profile into an existing frozen execution.
