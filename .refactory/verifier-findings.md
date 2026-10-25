## Verifier findings

- `refactory/refactory_dsh/authoring_evidence.py:1395: require_current_evidence duplicates the evidence-currency checks from current_problems (lines 460–476) instead of being called by it; the correction explicitly says "Do not duplicate the checks" and requires current_problems to call the factored function.`
  fixed: Extracted the four currency checks (identity, settings, receipts, outputs) into `_evidence_currency_problems()`. Both `current_problems` and `require_current_evidence` now call this single function. No duplication.

- `refactory/refactory_dsh/delivery.py:982: on a draft run with a successful merge (merge_commit set, no conflicts, merged check passes), ctx.write_text(BODY, _body(ctx, found, merge=merge)) overwrites the preamble that _write_pr_text wrote; a new PR's description loses "## Unresolved review findings".`
  fixed: After the body rewrite for merge information, when `draft_delivery` is set, `_prepend_draft_preamble(ctx, draft_delivery)` is called to re-prepend the preamble.

- `refactory/refactory_dsh/delivery.py:985: the "refine" action path returns early without any draft handling — no gh pr ready --undo, no preamble prepend — even when draft_delivery is set.`
  fixed: The refine path now checks `if draft_delivery:` and calls `_gh("pr", "ready", "--undo", url)`, fetches the old body, prepends the draft preamble, and calls `_gh("pr", "edit", url, "--body-file", ...)`.
