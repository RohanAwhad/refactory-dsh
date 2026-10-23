# Emission path grammar follow-up: malformed-header loopback and literal pathspecs

This follow-up to #494 (the emission path grammar change, tracker dm-201) closes two gaps its independent review found (tracker dm-207). With #494, the parsers accept any non-space character except `=`, backticks and quotes in emitted paths. This follow-up ensures:

1. A malformed header in create and fast modes loops back to emit instead of ending the run.
2. Git reads every emitted path literally, never expanding brackets, globs or colons as pathspec patterns.

## Core

### Fix 1: malformed-header loopback in create and fast modes

`create_ops.materialise` caught `parse_files`' `ValueError` and re-raised it as `AttemptRefused`. Nothing on the create path caught that exception; only `repo_change.materialise` (design mode) turns `AttemptRefused` into the `emission_refused` fact. On the walker, any exception from an op node ends the run.

The fix adopts `repo_change.materialise`'s pattern: `create_ops.materialise` now returns `{"emission_refused": str(exc)}` instead of raising, and clears the fact on a successful materialisation. `create_ops.validate` and `fast_ops.validate_package` check `emission_refused` first. If it is set, they return `Verdict("reloop", detail)` with the offending line quoted in the feedback, without spending an attempt or repair.

### Fix 2: literal pathspec protection for emitted paths

The wider grammar now admits `[`, `]`, `*`, `?` and `:` in paths, but git commands that received emitted paths expanded them as pathspec patterns. `worktree.git()` now sets `GIT_LITERAL_PATHSPECS=1` in the environment of every invocation, protecting all callers: `commit_attempt` (`git rm`, `git add`), `_committed_file` (`git ls-tree`), `amend`, `_restore`, and every caller in `repo_change.py` and `delivery.py`. Direct `subprocess.run` git calls in `base_check.py`, `failfast.py` and `worktree.committed_text` also set the variable. `pin.py`'s intentional pathspec globs are unaffected — they call `subprocess.run` directly, not `worktree.git()`.

## Behaviour changes

- **Emitted paths** may contain any non-space character except `=`, backticks and quotes, and git takes them literally.
- **Malformed or wrong-mode headers** are refused by name. In create and fast modes they now loop back to emit instead of ending the run. In design mode they already looped back (via `repo_change.materialise`'s `emission_refused` fact).
- **Who is affected:** every mode that parses emissions — design, create, create_fast, edit_fast, and design_fast.

## Test count

On #494, 17 of 24 new tests fail without the change, not 24 of 24: T3, T4 and five older cases pin existing behaviour. This follow-up adds 4 new tests (2 loopback, 2 pathspec), all of which fail without the change.

## Both runtimes

The parsers, ops and gates are Python, run directly by the walker and by the spine through `python -m refactory_dsh.run`, so no TypeScript change is needed.
