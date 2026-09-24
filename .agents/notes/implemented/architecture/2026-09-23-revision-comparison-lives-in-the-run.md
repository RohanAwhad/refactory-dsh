# Agent Note: A revision is compared, or it says why it was not

Status: implemented

## Problem

`refactory ceo "…" --mode edit` documents a revision that "refuses a regression". Running it showed that it does not. The run recorded `revision_of` in `facts.json` and nothing else: no before score, no after score, and the package's `.origin.json` said only `origin: create-mode`. The comparison, the refusal, and the digest of the state the revision came from all lived in `edit.edit()` — a function the console script never called. Only `python -m refactory_dsh.edit` reached it, and no document told anybody to run that.

The same run showed two smaller untruths. The graph runner printed `"best_score": 0` for an authoring mode that has no `score()` at all, which reads as "scored zero" where the truth is "not measured". And nothing in the output said where the package went or what it scored, so the run's own facts were visible only to a reader who opened `facts.json` afterwards.

The recipe that documents `--mode edit` had been asserting the output contains `before_score` and `after_score` — strings the run could not produce. It is a `model`-tier recipe, so no gate ran it; it would have failed the first time somebody spent the twenty minutes.

## Decision

**The comparison belongs to the run's `smoke` node.** It is the step that has just scored the new mode, so it is the only place that has both numbers without passing them around; for a revision it also scores the mode being revised and records the pair. `--mode edit` on DSH now refuses a regression by failing the run with a message naming both scores, and `python -m refactory_dsh.edit` reaches the same decision from its own comparison because `EditConfig.allow_regression` travels into the graph as a knob.

**Two modes are compared only when one smoke run measures both.** A source mode with no `score()`, or one whose graph calls a model, is recorded as not compared, with the reason, and the run continues. One smoke sample of a model call is not a measurement, and refusing a revision on one sample would refuse the wrong revisions; the dangerous direction is the other one — a silent "no regression" that was never checked. So the fact is `before_score: null` plus a note, never an implied pass.

**Provenance is written next to the package, not only in the run.** `revision_of`, `revision_of_state`, its digest, `before_score`, `after_score` and `regressed` go into the package's `.origin.json` in the workspace, because "what this came from, at which state, and what it scored" is a fact about the package that has to outlive the run directory it was made in. The run directory gets the same record in `revision.json` so a refused revision still leaves its evidence behind.

**The escape hatch is a knob, reachable from the command line.** `allow_regression` is declared on the authoring mode and settable with `ceo --knob allow_regression=1`. A person may intend to trade score for something the score does not measure, and before this there was no way to say so from the CLI at all — the flag lived on the Python entry point only.

**A finished run says what it recorded.** `interactive.run` prints one line of the run's own facts after a successful finish: where the package went, what it scored, and, for a revision, both scores and whether it regressed. DSH's report is the agent's last message; the facts a person acts on should not require opening a file.

**`best_score: null` means not measured.** The graph runner's `bestScore` returned `0` when no state file recorded a score, which is indistinguishable from a real zero. It returns `null` now, matching the `score: null` the smoke path already reports for a mode that could not be measured.

## Consequences

`--mode edit` does what it says on the path a person takes, and the `edit-mode` recipe in `tools/recipes.py` passes at the `model` tier that runs it. The tutorial says what "not compared" means where it claims the refusal, and `revision.json` is a new declared write of the smoke node, which the validator now enforces.

The comparison is one smoke run of the source mode, so a revision costs one more sandboxed run than a creation of the same size. For a revision of a mode whose graph calls a model it costs nothing, because that comparison is skipped by decision rather than by accident.

A refused revision still leaves its package in the workspace: `register` runs before `smoke`, so the run stops after the package exists. That is the same order the Python entry point had, and it is the reason the refusal names the knob that would accept it — the package is there to inspect, and the run is the thing that failed.

`python -m refactory_dsh.edit` keeps its own comparison for its own callers. It is now the second place that decides the same thing, which is a duplication worth removing the next time that entry point is touched; it is not removed here because its return value is what its callers print.

## Alternatives considered

**Document the gap instead of closing it.** The README and the tutorial could have been rewritten to say that `ceo --mode edit` records provenance and that the refusal lives on the Python entry point. Rejected: refusing a regression is the reason a revision is a mode of its own rather than a prompt, and the fix is one node reading two numbers it already has.

**A separate graph node between `register` and `smoke`.** Rejected on two counts: the smoke step is where the new mode's score exists, so a separate node would need it handed over; and the create graph's edges are wired once, with a non-gate node allowed exactly one out-edge, so inserting a node means moving that edge rather than adding a check.

**Compare the scores of two modes that call a model.** Rejected: a single smoke run of a model-calling mode is one sample of a distribution, and a refusal computed from it would be a coin toss reported as a regression.

**Fix the zero score where the number is printed.** The Python side could rewrite `0` to `null` on its way out. Rejected: the number is read from the run directory's own state files by the graph runner, and that is where the difference between "recorded zero" and "recorded nothing" is available.
