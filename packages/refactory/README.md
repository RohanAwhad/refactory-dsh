---
description: "The refactory group map: the model-facing refactory tool over the Python outer-loop optimizer, for users and maintainers navigating the family."
kind: "package-group"
---

# packages/refactory

## Summary

The refactory group holds the DeepSeek Harness side of a refactory outer-loop run: the optimizer that proposes edits to a Python mode, evaluates them, and keeps what improves the score. Its two packages are the two DSH-side readers of that console: `tool-refactory` registers the model-facing `refactory` tool, which lists the modes a checkout can run, starts a run in its own process, watches that run, and reads back its leaderboard, candidates, and artifacts; `refactory-console` exposes the same console over Typert Remote as the described choices the Web picker offers. A preset row mounts the tool, and the Web view renders each call as a run card instead of a generic tool row. The run itself is a Python program outside this group: the group owns the call across the process boundary and the JSON it returns, while the loop's own repository owns everything inside a run directory.

## Table of Contents

- [Packages](#packages)
- [Related documentation](#related-documentation)
- [Dev Note](#dev-note)

-----

<a id="packages"></a>
## Packages

Every package under `packages/refactory/` is mounted into a composition; the group has no runtime of its own.

| Package | Role | ctx keys |
|---|---|---|
| [`tool-refactory`](tool-refactory/README.md) | Lets the model and the Web view run and read a refactory outer loop: list modes, start a run, watch it, and read its candidates, diffs, and artifacts | `ctx.tools`, `ctx.subprocess`, `ctx.systemPrompt` |
| [`refactory-console`](refactory-console/README.md) | The console over Typert Remote: the described choices a Web picker offers — modes, strategies and packages — plus starting, reading and stopping a run | `ctx.refactoryConsole` |

-----

<a id="related-documentation"></a>
## Related documentation

- [Refactory subsystem](../../docs/subsystems/refactory.md) — the console process boundary, the described choices a picker offers, and the `refactoryConsole` Remote refusals.
- [The refactory system, as built](../../refactory/docs/architecture.md) — the loop's steps, the state model, the validator's rules, and the edit algebra. [Decisions](../../refactory/docs/plans/pythonic-modes.md) records why it is shaped that way; the `refactory/` project's own `README.md` holds its install path and launcher.
- [Generated tool catalog](../../docs/tool-catalog.md) — the exhaustive reference for the tools the model receives, generated from their source.
- [ceo preset](../preset/agent-presets/presets/ceo/agent.cordis.yml) — the row that mounts `tool-refactory`.

-----

<a id="dev-note"></a>
## Dev Note

<details>
<summary>Working context for maintainers — click to expand</summary>

None.

</details>
