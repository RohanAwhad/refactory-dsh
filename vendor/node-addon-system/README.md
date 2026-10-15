# @deepseek-ai/node-addon-system 0.1.2, vendored

A copy of the published package with one change, in `lib/flock.js`: it tells glibc
from musl by the C library mapped into the process (`/proc/self/maps`) instead of
`process.report.getReport()`. Generating a diagnostic report walks every libuv handle
and aborts the process when a socket is closing, which is how DSH workers died with
`exit -6` mid-step (session persistence and the bash sandboxes load `flock` while a
worker streams from its model); a report also takes seconds on many-CPU hosts.

`pnpm-workspace.yaml` points the dependency here with an override. A pnpm patch was
not enough: an install whose store already holds the package reuses the unpatched
copy. The native binaries still come from the registry as this package's
`optionalDependencies`.

Delete this directory and its override once the upstream package stops calling
`process.report.getReport()`.
