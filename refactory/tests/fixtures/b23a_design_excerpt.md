# Supported commands for correcting stuck design runs

## Summary

When a design run gets stuck — a wrong setup/check/generate command (dm-65), a dead owner leaving an `executing` checkpoint (dm-70), a headless owner that lingers after graph failure (dm-93), or a resume consumed and silently lost during owner exit (dm-94) — the only recovery is hand-editing `.refactory/facts.json` and `.refactory/spine-checkpoint.json`. This change adds three commands (`amend-command`, `acknowledge`, `withdraw`) and four lifecycle fixes so that every correction has a supported command.

## Rule and sites

**Rule:** A person corrects a stuck run through supported commands that record an explicit audit trail, invalidate through the existing machinery, and never require hand-editing facts.json, the checkpoint, or delivery markers.

### facts.json readers and writers

- `refactory/refactory_dsh/run.py:32` — defines `FACTS = "facts.json"`, the node runner writes to it after each node completes. [left: the node runner writes facts produced by the mode's own ops; the new `command_history` is written by `graph_control.py`, not by the runner]
- `refactory/refactory_dsh/interactive.py:422-435` — `run_summary()` reads facts.json to print what a finished run recorded. [left: read-only summary path; does not write facts]
- `refactory/refactory_dsh/console.py:417` — `_authoring()` reads `facts.json` for the spine status panel: name, validation, files, scores. [changed: also reads `command_history` from facts.json and includes it in the activity dict]
- `refactory/refactory_dsh/console.py:457` — `_item_results()` reads `facts.json` for item_scores. [left: read-only scoring path]
- `refactory/refactory_dsh/delivery.py:1366-1391` — `_base_evidence()` reads facts for inherited_failures and test-guardedness. [changed: also reads `command_history` and emits a line per correction in the Verification section]
- `refactory/refactory_dsh/graph_control.py` — does not currently read or write facts.json. [changed: the new `amend-command` action reads facts.json, updates the command field, appends to `command_history`, and writes the file back]

## Docs

- `refactory/docs/live-control.md` — Update to document new "Correct a stuck run" section.
- `refactory/docs/cli.md` — Update to list the new actions.

## Changes

- `refactory/refactory_dsh/graph_control.py`
- `refactory/refactory_dsh/interactive.py`
- `refactory/refactory_dsh/console.py`
