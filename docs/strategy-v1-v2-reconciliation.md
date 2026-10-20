# Strategy: Reconciling remote-factory (v1) and refactory-dsh (v2)

_October 2026_

## Status quo

Two codebases implement overlapping but architecturally divergent workflow-execution systems for agentic software evolution:

| | **remote-factory (v1)** | **refactory-dsh (v2)** |
|---|---|---|
| Runtime | `WorkflowExecutor` (async Python) | DSH (Node.js Cordis composition) |
| Agent backend | Claude Code CLI only (`ClaudeRunner`) | Multi-provider: DSH, Codex, Claude Code |
| IR | `factory/workflow/primitives.py` — 10+ node types incl. `LLMNode`, `SubgraphForkNode`, `Study` | `refactory_dsh/ir.py` — forked subset (6 types) + 3 extension fields (`resource_role`, `command_timeout_s`, `dispatch_command`) |
| Sandboxing | `factory/contained/` — podman, k8s, **OpenShell** (Landlock + seccomp + proxy egress) | `sandbox.py` — process `setrlimit` + env scrub |
| Optimization | `outer_loop/` — MAP-Elites, swarm evaluator | `nn/` + `optim/` — PyTorch-like `state_dict` mutation |
| Plugins | `factory.plugins` entry points (lightwell-factory ships 17 CVE modes) | `refactory_packages/` directory packages |
| Dependencies | Heavy (anthropic, langfuse, networkx, fastapi, mempalace, graphifyy) | Minimal (pydantic, PyYAML, structlog) |
| Package model | Single Python package | pnpm monorepo (8 packages) + Python package |
| UI | Dashboard + MCP server | DSH web UI + Modes panel (React) |

The IR fork is explicit: `refactory_dsh/ir.py:4-5` states "Copied from `factory/workflow/primitives.py` in remote-factory at commit `e7eb24906a`".

### Superseded plan: `dsh-execution-engine.md`

A prior proposal (`docs/design/dsh-execution-engine.md` in remote-factory, 322 lines) described integrating DSH *into* remote-factory as the execution engine via three layers: L1 (`DshRunner` as a `Runner` protocol plugin), L2 (DSH as the default executor), L3 (DoltLite data plane). Status: "proposal. Nothing in this document is implemented."

This strategy supersedes L2 and L3 of that plan by going the opposite direction — pulling v1 into v2 via submodule rather than embedding DSH into v1. L1 (`DshRunner` as a v1 `Runner` plugin) remains independently useful for v1 users who want DSH model diversity without migrating to v2, but is not on the critical path.

---

## 1. Merging the repositories

### Approach: git submodule (not full merge)

A full merge (absorbing v1's source tree into v2's repo) would break lightwell-factory's GitLab mirror of remote-factory and force an immediate lightwell migration.

**Why remote-factory must remain a standalone repo (for now):** lightwell-factory lives on GitLab and mirrors the `remote-factory` GitHub repo at the repository level. GitLab's repository mirroring operates on entire repos — it cannot mirror a subdirectory or branch of a different repo. If remote-factory is absorbed into refactory-dsh, lightwell's mirror breaks and must be reconfigured to track refactory-dsh (pulling in all of v2's Node.js packages, DSH runtime, etc. that lightwell doesn't use). A legacy branch within refactory-dsh would not satisfy GitLab's repo-level mirror either.

Instead, use a **git submodule** to pull v1 into v2 while keeping remote-factory as a standalone repo.

```
git submodule add git@github.com:akashgit/remote-factory.git v1/remote-factory
```

This gives v2 direct import access to v1's code (IR, OpenShell, Package abstractions) while:
- **remote-factory stays canonical** — lightwell's GitLab mirror keeps working unchanged
- **Changes flow naturally** — commits to `factory/` land in remote-factory, v2 bumps its submodule pin
- **The exit ramp is clean** — when lightwell eventually migrates off v1, absorb or remove the submodule

### Why submodule over subtree

**Subtree** copies the tree in and requires `git subtree push` to sync changes back upstream. This gets messy when both repos actively receive commits, and it obscures which changes need to flow back to remote-factory for lightwell to pick up.

**Submodule** keeps the boundary explicit. remote-factory is its own repo with its own history. refactory-dsh pins to a commit and bumps it. lightwell tracks remote-factory independently. No sync ambiguity.

### Python packaging boundary

A submodule checkout does not make v1's code importable — `v1/remote-factory/factory/` is a directory on disk, not an installed package. Three options:

1. **`sys.path` adapter** (recommended for Phase 0). A thin `_v1_compat.py` module in v2 that adds the submodule path to `sys.path` and re-exports the specific modules needed (IR primitives, OpenShell). No `pip install` of v1, no dependency contamination. v2's `pyproject.toml` gains no new deps.
2. **Editable install with extras.** `pip install -e "v1/remote-factory[contained-openshell]"` — pulls v1's full dependency set (anthropic, langfuse, networkx, fastapi, etc.) into v2's environment. Unacceptable for v2's minimal-deps goal.
3. **Refactor v1 into optional extras.** Make v1's IR and OpenShell modules installable independently via new extras in v1's `pyproject.toml` (e.g., `pip install "remote-factory[ir-only]"`). Cleaner long-term but requires upstream v1 changes.

Start with option 1. Move to option 3 if the `sys.path` adapter becomes unwieldy or if more than a handful of modules need sharing.

### Estimated effort: ~1 day

| Task | Effort |
|---|---|
| `git submodule add`, configure CI to init submodules | ~2 hours |
| Write `_v1_compat.py` adapter, replace v2's forked `ir.py` with re-exports | ~2 hours |
| Delete cross-repo test oracle (`tests/reference_ir.py`, `uv sync --group reference`) | ~1 hour |
| Validate: v2 tests pass with submodule-sourced IR via supported install path | ~1 hour |

### What this enables

- **IR drift stops.** v2 imports from v1's canonical `primitives.py` via the adapter. v2's 3 extension fields are added upstream to remote-factory (lightwell doesn't use them, so no disruption).
- **OpenShell becomes importable.** Same adapter exposes `factory.contained.openshell` — no standalone extraction needed.
- **Shared abstractions without forced unification.** v2 can use v1's `Package` type directly if it's useful, or ignore it. No pressure to reconcile `Package` and `Mode` on a timeline.
- **Cross-repo test oracle disappears.** The `reference_ir.py` plumbing that pulls remote-factory via `uv sync` is replaced by a local submodule import.

### Coordination tax

Changes to the shared IR land in remote-factory first (the submodule source), then bump the pin in refactory-dsh. This is a small tax — one extra `git submodule update` step. If it becomes annoying, it's a signal to absorb the submodule into a full merge. By that point, lightwell may already be migrated, making the full merge painless.

### When to absorb the submodule (full merge)

The submodule is a stepping stone, not the end state. Absorb it when:
- Lightwell has migrated off v1 (the main reason remote-factory needs to stay standalone)
- The IR coordination tax outweighs the benefit of keeping repos separate
- v1's `factory` CLI is fully deprecated

At that point: `git submodule deinit v1/remote-factory`, move the code in or delete it.

---

## 2. Maintaining remote-factory as v1, adopting refactory-dsh as v2

**What it means:** remote-factory enters maintenance mode (security fixes, critical bugs only). All new feature development happens in refactory-dsh. v1 users are encouraged to migrate on their own timeline.

### Pros
- **Clean break.** v2's architectural choices (DSH runtime, multi-provider, PyTorch-like optimization, minimal deps) reflect deliberate design decisions. Not contaminating it with v1's legacy keeps it coherent.
- **v2 can evolve freely.** The IR fork already extends beyond v1 (3 new fields). Treating v2 as the successor removes the obligation to keep the IR in sync.
- **Reduced maintenance burden.** One actively developed codebase is easier than two.
- **v2 already has its own docs, CLI, UI, and optimization loop.** It's not a prototype — it's a working system with its own install path (`install.sh`), profiles, and `refactory doctor` command.

### Cons
- **OpenShell gap.** v2 has no kernel-level sandboxing. Benchmark cheating prevention (current branch topic) and production safety both need more than `setrlimit`. Without OpenShell, v2 is unsuitable for untrusted agent execution.
- **Lightwell orphaned.** lightwell-factory's 17 CVE modes run on v1's workflow engine. If v1 goes to maintenance-only, lightwell must either port all modes to v2's `Mode` abstraction or remain frozen on a dying platform.
- **Contributed benchmarks.** v1 has 8 contributed benchmark workflows (`swebench`, `featurebench`, `legacybench`, `devopsgym`, `terminalbench`, `programbench`, `tomswe`, `salitrap`). These would need porting.
- **Plugin ecosystem.** v1's `factory.plugins` entry-point system has no equivalent in v2. Any existing plugins beyond lightwell are stranded.
- **User migration friction.** v1's CLI (`factory ceo`, `factory run`, `factory agent`) and config model (`~/.factory/config.toml`, `~/.factory/registry.json`) differ entirely from v2's (`refactory`, DSH profiles, `refactory.workers.json`). No automated migration path exists.

### Verdict
The submodule approach (section 1) gives the best of both: v2 is the primary development target, v1 stays alive as its own repo for lightwell's GitLab mirror, and v2 can import v1's code (IR, OpenShell) directly. v1 enters maintenance without orphaning lightwell.

---

## 3. Sharing code: OpenShell sandboxing between v1 and v2

This is the most pressing shared-code question. v1 has mature kernel-level sandboxing; v2 needs it.

### Option A: Extract OpenShell into a standalone package

**What exists today:**
- `factory/contained/openshell.py` — Landlock/seccomp confinement, sandbox lifecycle
- `factory/contained/openshell_claude_code.yaml` — provider profile
- Optional dependency: `openshell>=0.1` (gRPC/protobuf)
- Already declared as an optional extra in v1's `pyproject.toml:41` (`contained-openshell`)

**What v2 would need:**
- A `pip install openshell` (or similar) that provides the kernel confinement layer without pulling in the rest of remote-factory
- An adapter in `refactory_dsh/` that maps DSH's worker dispatch to the OpenShell sandbox lifecycle (launch → run → collect facts → teardown)
- Provider profile management compatible with DSH's credential store (v1 holds credentials at the gateway; v2 holds them in DSH's Settings → Models)

**Pros:** Clean separation. Both v1 and v2 depend on the same sandboxing package. OpenShell improvements benefit both.

**Cons:** OpenShell currently relies on v1's `ContainedRunner` and provider routing (`factory/contained/providers.py`). Extracting it means refactoring the gateway credential model, which is tightly coupled to v1's `factory contained` CLI.

### Option B: Port OpenShell concepts into v2's sandbox.py

Implement Landlock/seccomp directly in v2's existing `sandbox.py`, using v1 as a reference but not sharing code.

**Pros:** No cross-repo dependency. v2 can adapt the approach to DSH's execution model natively.

**Cons:** Duplicated security-critical code. Two implementations to audit and maintain. v1's OpenShell has been hardened through production use; a fresh implementation starts from zero.

### Option C: v2 shells out to v1's `factory contained` CLI

Run v2's agent nodes inside v1's containment by invoking `factory contained --target openshell -- <v2 worker command>`.

**Pros:** Reuses v1's battle-tested sandbox with zero porting effort.

**Cons:** Couples v2's runtime to a v1 installation. Adds subprocess overhead. Credential model mismatch (v1 gateway vs. v2 DSH provider registry).

### OpenShell execution boundary

The confinement layer's transitive dependency chain has been verified:

```
factory/contained/openshell.py
├── factory.contained.errors      — no imports beyond __future__
├── factory.contained.provenance  — stdlib only (hashlib, dataclasses, pathlib)
└── factory.podman                — stdlib only (hashlib, os, shlex, dataclasses, pathlib)
```

`factory/__init__.py` imports only `logging`, `os`, `sys`, and `structlog`. Since v2 already depends on `structlog`, the `sys.path` adapter will not introduce any new dependencies. The heavy deps (anthropic, langfuse, networkx, fastapi) are not triggered.

What sits where in v1's OpenShell stack:

| Layer | What it does | Location in v1 | Reusable by v2? |
|---|---|---|---|
| Kernel confinement | Landlock filesystem allowlists, seccomp syscall filtering | `factory/contained/openshell.py` | Yes — pure sandbox lifecycle, no v1-specific deps |
| Plan composition | Assembles confinement plans from profiles | `factory/contained/openshell.py` | Yes |
| Provisioning + workspace transfer | Sets up sandbox filesystem, copies project files in | `factory/cli/contained_openshell.py` | Needs adapter — v2's workdir layout differs |
| Credential gateway | Holds provider keys outside the sandbox, proxies API calls | `factory/contained/providers.py` | Needs adapter — v2 uses DSH provider registry, not v1's gateway |
| Provenance + cancellation | Tracks sandbox provenance, handles cleanup on abort | `factory/cli/contained_openshell.py` | Likely reusable with minor adaptation |
| Artifact collection | Reads results out of sandbox post-execution | `factory/cli/contained_openshell.py` | Needs adapter — v2 reads `facts.json`, not v1's result format |

The kernel confinement and plan composition layers are directly importable. The CLI layer (provisioning, credentials, artifacts) needs adapter work to map to v2's DSH execution model.

### Recommendation

**With the submodule approach (section 1):** import the confinement and plan-composition layers from the submodule via the `_v1_compat.py` adapter. Write a thin v2-specific shim for provisioning, credential bridging, and artifact collection.

The credential adapter (DSH provider registry → gateway model) is the hardest piece — ~3-5 days of focused work touching security-sensitive code. An integration spike (see Phase 0 below) should validate the boundary before committing to this estimate.

---

## 4. Support model for legacy v1 users

### Who are v1 users?
- **lightwell-factory / refactory-midstream**: The largest downstream consumer. 17 CVE-remediation modes, active migration plan (`docs/midstream-package-migration.md`).
- **Contributed benchmark authors**: 8 benchmark workflows in `factory/workflow/contributed/`.
- **Direct CLI users**: Anyone using `factory ceo`, `factory run`, `factory agent` directly.

### Proposed support tiers

**Tier 1: Security + critical fixes (indefinite)**
- v1 continues to receive security patches and fixes for data-loss bugs.
- OpenShell sandboxing in v1 remains maintained (it's the production sandbox for existing deployments).
- No new features, no new benchmark modes, no new agent backends.

**Tier 2: Migration tooling (time-bounded, ~3 months)**
- Provide a `refactory migrate` CLI command that:
  - Converts v1 `Workflow` IR → v2 `Mode` state dict (the IR is a strict superset in v1; v2's 6-type subset covers the common case; v1-only types like `LLMNode`, `SubgraphForkNode`, `Study` need manual mapping or deprecation)
  - Converts v1 `~/.factory/config.toml` → v2 DSH profile + `refactory.workers.json`
  - Maps v1 `factory.plugins` entry points → v2 `refactory_packages/` structure
- Provide a v1→v2 mode equivalence test suite (v2 already does this for IR via `tests/reference_ir.py`; extend to full mode execution parity)

**Tier 3: Lightwell-specific migration (see section 5)**
- Dedicated workstream given lightwell's complexity and business importance.

### What to deprecate
- v1's `factory/dashboard/` — replaced by v2's DSH web UI
- v1's MCP server — v2 uses DSH's native MCP integration
- v1's `outer_loop/` MAP-Elites — replaced by v2's `nn/optim/` (more composable, same purpose)
- v1's `ClaudeRunner` as the sole agent backend — v2's multi-provider model subsumes it

### What to preserve
- v1's OpenShell sandboxing (extract per section 3)
- v1's benchmark workflow definitions (port to v2 modes)
- v1's `Package` composition operators where they map cleanly to v2's `Mode` abstraction

---

## 5. Lightwell factories: stay on v1 vs. migrate to DSH

### Current state
- lightwell-factory (`akashgit/refactory-midstream`) is a plugin to v1
- 17 CVE-remediation workflow modes, ~6,500 LOC
- Pinned to v1 commit `c1f1c08` (37 commits behind HEAD at time of doc)
- Uses monkey-patches (`_registry_patch.py`) to integrate with `WorkflowRegistry`
- Active migration plan exists (`docs/midstream-package-migration.md`) to move onto v1's `Package` abstraction — 11 PRs across two repos
- **No lightwell references exist in v2**

### Option A: Complete v1 Package migration, then port to v2

1. Finish the 11-PR migration plan in `docs/midstream-package-migration.md` to move lightwell onto v1's `Package` abstraction
2. Once on `Package`, create a `Package` → `Mode` transpiler (both are graph-with-knobs abstractions; the mapping is mostly mechanical)
3. Port the 17 modes to v2 `refactory_packages/` format
4. Validate via v2's evaluation framework

**Pros:** Lower risk — each step is validated independently. The v1 `Package` migration removes the monkey-patches regardless of v2 adoption.

**Cons:** Two migration steps instead of one. The intermediate `Package` form may not be worth the investment if v1 is going to maintenance.

### Option B: Port directly from raw Workflow to v2 Mode

Skip the v1 `Package` migration entirely. Convert each of the 17 raw `Workflow` graph definitions directly into v2 `Mode` classes.

**Pros:** One migration step. Lightwell lands on the target platform immediately.

**Cons:** Higher risk per mode. The raw `Workflow` graphs use v1-only IR features (`Study` nodes for human checkpoints, `SelectionNode` for multi-candidate evaluation) that have no v2 equivalent. Each must be manually decomposed.

### Option C: Let lightwell stay on v1

Keep lightwell running on v1 indefinitely. v1 enters maintenance but lightwell is the primary (only?) reason it stays alive.

**Pros:** Zero migration cost. Lightwell continues working as-is.

**Cons:** v1 becomes a legacy dependency maintained solely for one plugin. Lightwell misses v2's multi-provider support, DSH web UI, and improved optimization loop. The monkey-patch integration remains fragile. Security-critical sandbox updates must be backported to v1.

### Recommendation

**Option A** (Package migration first, then port). The `Package` migration plan already exists and removes the fragile monkey-patches. The `Package` → `Mode` transpiler is a well-scoped piece of tooling that also serves any other v1 user with custom `Package` definitions. Attempting Option B risks discovering v1-only IR features mid-port with no intermediate fallback.

**With the submodule approach (recommended):** lightwell stays on remote-factory and keeps working via its GitLab mirror. The `Package` migration (11-PR plan) proceeds in remote-factory at its own pace — no coordination with v2 required. When lightwell is ready and the `Package` migration is complete, either:
- Port the modes to v2 `Mode` classes (if `Package`/`Mode` reconciliation has happened by then), or
- Import `Package` directly from the submodule and use it as a first-class abstraction in v2

This removes lightwell migration from the critical path entirely.

---

## Decision matrix

| Decision | Recommended path | Effort | Prerequisite |
|---|---|---|---|
| Repo structure | **Submodule** v1 into v2 (section 1) | ~1 day | — |
| Runtime direction | **v2 (DSH) as primary**, v1 maintenance | Ongoing | Submodule |
| Sandboxing | **Direct import** from submodule (section 3) | Integration spike first, then ~3-5 days for credential adapter | Submodule |
| v1 support | **Tier 1 indefinite, Tier 2 time-bounded** (section 4) | ~3 months for migration tooling | — |
| Lightwell | **Stay on v1**, migrate independently (section 5) | At lightwell's pace | Package migration plan |
| Mechanical repo absorption | **Absorb submodule** when lightwell is migrated | ~1-2 days | Lightwell migration |
| Package/Mode convergence | **Incremental**, decoupled from repo absorption | Unbounded design work | — |
| CLI unification | **After** convergence is underway | TBD | Package/Mode convergence progress |

## Suggested sequence

### Phase 0: Integration spike (~2-3 days)

Validate the packaging approach and OpenShell boundary before committing to later estimates.

1. `git submodule add` remote-factory into `v1/remote-factory`
2. Write `_v1_compat.py` adapter: `sys.path`-based import of IR primitives from submodule
3. Replace v2's forked `ir.py` with re-exports from adapter
4. Run one representative v2 workflow end-to-end on the shared IR, via `uv run` / supported install path
5. Verify IR serialization round-trip: v2-produced graph JSON accepted by v1's reader for the 6 shared node types
6. Confirm `factory.contained.openshell` imports cleanly via the adapter (transitive deps verified as stdlib + `structlog` — both already in v2's environment). Verify the confinement and plan-composition layers are callable without triggering the CLI provisioning layer.

**Exit criteria:** shared IR works through the supported install path; OpenShell confinement layer imports and can be called from v2 code; we have a concrete boundary for what the credential/provisioning adapter must cover. Use this to firm up Phase 2 estimates.

### Phase 1: Submodule + IR unification (~1 day, after spike validates approach)
7. Finalize `_v1_compat.py` based on spike findings
8. Add v2's 3 extension fields (`resource_role`, `command_timeout_s`, `dispatch_command`) upstream to remote-factory
9. Delete cross-repo test oracle (`tests/reference_ir.py`, `uv sync --group reference` plumbing), retain serialization round-trip tests
10. Validate: all v2 tests pass, CI inits submodule correctly

### Phase 2: OpenShell integration (estimate TBD by spike)
11. Import OpenShell confinement + plan-composition layers from submodule via adapter
12. Write v2 shim for provisioning, workspace transfer, and artifact collection
13. Build credential adapter (DSH provider registry → v1 gateway model)
14. Wire into v2's `workers.py` as a sandbox backend option
15. Validate: run a v2 mode inside OpenShell confinement, test cancellation and failure paths

### Phase 3: Benchmark porting (~2-3 days, can overlap with Phase 2)

All 8 v1 benchmarks follow the same 4-node pattern (`FnNode → AgentNode → GateNode → FnNode` with reloop edge) and use only node types present in v2's IR. None use v1-only features (`LLMNode`, `SubgraphForkNode`, `SelectionNode`). Porting is mechanical: translate the pattern to v2's `Mode.build()` graph structure; prompt templates copy verbatim.

16. Port v1's 8 contributed benchmark workflows to v2 `Mode` format
17. Validate: benchmark results match between v1 executor and v2 DSH runtime

### Phase 4: Lightwell migration (at lightwell's pace, independent)
18. Complete the 11-PR `Package` migration plan in remote-factory
19. Remove monkey-patches and pinned-commit dependency
20. When ready, port modes to v2 or import `Package` from submodule

### Phase 5: Mechanical repo absorption (when ready, ~1-2 days)
21. Absorb submodule: `git submodule deinit v1/remote-factory`, move needed code in or delete

### Phase 6: Runtime convergence (unbounded, incremental)
22. Converge `Package`/`Mode` abstractions
23. Unify CLIs, deprecate `factory` entry point

## Key risks

1. **Credential model adapter is the critical path.** v2 cannot use OpenShell without bridging DSH's provider registry to v1's gateway credential model. ~3-5 days of focused work, security-sensitive code.
2. **Submodule pin coordination.** IR changes land in remote-factory first, then bump in v2. Small tax, but can become annoying if IR evolves rapidly. If it does, that's the signal to absorb the submodule.
3. **Lightwell migration timeline is unbounded.** Since lightwell can stay on v1 indefinitely, there's a risk it never migrates and v1 maintenance continues forever. Set a sunset date (e.g., 6 months after v2 reaches feature parity) to create pressure.
4. **Benchmark parity.** v1's 8 contributed benchmarks are used by external contributors. Port early to avoid blocking anyone.
