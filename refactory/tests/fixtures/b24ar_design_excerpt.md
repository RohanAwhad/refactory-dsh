# Fix the repository guard's tests and digest compatibility

## Summary

PR #419 introduced per-worktree hook isolation and a session-level repository guard for both pytest and vitest. The hook isolation is correct, but the guard's tests are too weak to catch regressions, the `_shared_state` digest is incompatible with existing records, and the guard silently passes when it cannot take its snapshot. This change fixes seven items.

## Rule and sites

**Rule:** The repository guard detects any change a test session makes to the shared git state (hooks, config, worktree list), attributes changes to the session or to external causes, and fails the suite on session-attributed changes.

- `vitest.global-setup.ts:18-21` — the vitest guard derives the repository path, python binary, and guard script from `import.meta.dirname` `[changed]`: `repo` becomes a module-level `let`, set from `project.config.root` in `setup()`, falling back to `process.cwd()`.
- `vitest.global-setup.ts:26` — `setup()` takes no parameters `[changed]`: accepts an optional `project` parameter (vitest 4's `TestProject`).
- `vitest.global-setup.ts:35-38` — snapshot failure is swallowed with `console.warn` `[changed]`: throws when python exists; prints inactive message when python is missing.
- `refactory/tests/repo_guard.py:100-117` — `_is_session_worktree` compares unresolved paths `[changed]`: resolves both the worktree path and each root before `is_relative_to`.
- `refactory/tests/repo_guard.py:122-123` — stash keys for the guard `[changed]`: adds `_GUARD_INACTIVE_KEY`.
- `refactory/tests/repo_guard.py:130-133` — `pytest_sessionstart` silently returns on snapshot failure `[changed]`: stores the reason in `_GUARD_INACTIVE_KEY`.
- `refactory/tests/repo_guard.py:161-164` — `pytest_sessionfinish` silently returns on snapshot failure `[changed]`: stores the reason in `_GUARD_INACTIVE_KEY`.
- `refactory/tests/repo_guard.py:184-203` — `pytest_terminal_summary` reports failures and warnings `[changed]`: also reports inactive guard with reason.
- `refactory/refactory_dsh/worktree.py:896-917` — `_shared_snapshot` returns config lines and hook file hashes `[changed]`: also returns `hooks_dir` path so `_shared_digest` can re-read raw bytes.
- `refactory/refactory_dsh/worktree.py:920-926` — `_shared_digest` feeds hex-encoded hashes into the outer SHA-256 `[changed]`: feeds raw file bytes instead, matching the old formula.
- `refactory/refactory_dsh/worktree.py:253-337` — `_isolate_hooks` has four inline `subprocess.run` blocks for git config reads `[changed]`: replaced by calls to `_config_value`.
- `refactory/tests/test_worktree.py:1093-1114` — `test_session_guard_catches_worktree_in_tmp` uses tautological assertion `[changed]`: asserts `test_adds_worktree PASSED` and the guard's specific `FAIL:` line.
- `refactory/tests/test_worktree.py:1117-1150` — `test_session_guard_catches_hook_or_config_change` uses tautological assertions `[changed]`: asserts `PASSED` and the specific `FAIL:` line for each variant.
- `refactory/tests/test_worktree.py:1153-1182` — `test_session_guard_catches_subprocess_worktree_in_refactory_home` inherits outer REFACTORY_HOME/TMPDIR `[changed]`: passes isolated TMPDIR and REFACTORY_HOME to the inner pytest so only the guard's override makes the worktree session-owned.
- `refactory/tests/test_worktree.py:1217-1304` — `test_vitest_guard_catches_mismatch` writes an inline copy of the guard setup `[changed]`: points `globalSetup` at the real `vitest.global-setup.ts` with `root` set to the test repo.
- `vitest.config.ts:22` — production globalSetup declaration `[left: unchanged; it already points at ./vitest.global-setup.ts]`.
- `refactory/tests/conftest.py:20` — registers `repo_guard` as a pytest plugin `[left: the module name is unchanged]`.
- `refactory/refactory_dsh/worktree.py:929-931` — `_shared_state` calls `_shared_snapshot` then `_shared_digest` `[left: call site unchanged]`.
- `refactory/refactory_dsh/worktree.py:440-453` — `shared_changed` calls `_shared_state` and compares with stored digest `[left: unchanged; the digest is now backward-compatible]`.
- `refactory/docs/hardening.md:538-572` — documents per-worktree hook isolation and repository guard `[left: no behavioural change to the documented rules]`.
- `refactory/docs/models.md:96` — describes hook isolation, shared-state detection `[left: no change to documented behaviour]`.

## Docs

- `refactory/docs/hardening.md:560-564` — lists the guard tests under "Held by." No update needed.
- `refactory/docs/models.md:96` — describes hook isolation, shared-state detection. No update needed.

## Changes

- `vitest.global-setup.ts`
- `refactory/refactory_dsh/worktree.py`
- `refactory/tests/repo_guard.py`
- `refactory/tests/test_worktree.py`
