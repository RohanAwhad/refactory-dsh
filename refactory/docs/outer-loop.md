# Outer-loop recovery

## WalkerEvaluator

`WalkerEvaluator` enables `enable_recovery` by default on every `mode.evaluate()`
call.  This means:

- **Op retry.** A transient failure in an FnNode op is retried once with
  fact restore before it is recorded as a failure.
- **Per-op timeout.** Every op and fn gate runs in a child process forked from
  a preloaded forkserver, with a timeout derived from `command_timeout_s()`
  (the same formula lowering uses), clamped to the remaining budget with a 1 s
  floor.  The forkserver preloads the graph's op and gate modules, so per-op
  overhead is ~0.01 s instead of the ~0.28 s a fresh spawn costs.
- **Clean trial boundaries.** The child process is killed at the limit
  via session-grouped SIGTERM→SIGKILL escalation.  No leaked processes or
  state survive between candidates.

## DshEvaluator

`DshEvaluator` runs candidates through the DSH spine, which enforces its own
`command_timeout_s` on each node's subprocess.  It does not use
`enable_recovery`.
