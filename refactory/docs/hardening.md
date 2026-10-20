# Hardening

## Pin integrity

### Repository guard

The `repo_guard` pytest plugin (loaded via `conftest.py`) snapshots the
repository's worktree list, hooks and git config at session start and compares
at session end. Any change attributed to the test session fails the suite.

Pin tests use temporary git repositories created with `git init` in `tmp_path`.
No test creates a worktree in the real repository under test, so the guard never
fires for pin operations.

### Hooks isolation during pin preparation

A pin's `pnpm install` runs prepare scripts (such as `lefthook install`) that
can rewrite the driver's shared hooks. Pin creation isolates hooks:

1. `extensions.worktreeConfig` is enabled on the driver (the same extension
   run worktrees already enable, and the canary checks already expect).
2. `core.hooksPath` is set to `/dev/null` as a per-worktree config on the pin,
   so hooks write to the pin's own git dir, not the driver's shared hooks.
3. The `LEFTHOOK=0` and `HUSKY=0` environment variables are set during
   `pnpm install` and `pnpm build`.

### Dirty driver pinning

When a driver has uncommitted changes to machinery paths at launch, the unified
diff is recorded in the driver record (`dirty_diff`). When a pin is created,
`git apply` applies that diff to the pin's worktree. If the diff conflicts with
the pin's commit (the checkout has moved and the changes overlap), pin creation
refuses with a `RuntimeError` naming the commit and the conflicting paths.

### File lock for concurrent pin creation

`create_pin()` acquires an exclusive `flock` on
`<refactory_home>/pins/<commit>.lock` before staging a pin. Concurrent callers
block until the first one finishes, then find the prepared pin and return it.

### Atomic pin preparation

The pin is staged in `<commit>.preparing/` and atomically renamed to
`<commit>/` only after all preparation steps succeed. A failure at any point
removes the staging directory and propagates the error.
