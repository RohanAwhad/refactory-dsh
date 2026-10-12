"""Versioned Create source prompts with the reviewed native DSH mapping.

The production resource is independent of the test oracle. Source obligations
remain readable; the native mapping has precedence over framework spellings.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

NATIVE_INSTRUCTIONS = """\
## Reviewed native mapping (takes precedence over source framework spellings)
You are operating refactory-dsh Create fidelity, authoring a reusable native
Package/Mode. Keep every substantive source obligation; translate framework
operations as follows. Do not try to run remote-factory or modify its source.

The project source for study is {{project_path}}; declared .factory artifacts
are relative to this invocation's run directory. The output project is
{{artifact_project}} and the package directory is {{output_folder}}.
The requested package name is {{name}}. The complete original request is in
.factory/strategy/user-intent.md. Runtime inputs: {{requires_input}}.

* Workflow/AgentNode/FnNode/GateNode/ForkNode/JoinNode mean nn.Mode and native
  nn.Node kinds agent/op/gate/fork/join; Workflow means nn.Graph. The public API
  is refactory_dsh.nn. Agent gate ownership and human approvals stay unchanged.
* definitions.py/primitives.py and contributed workflow study mean
  refactory_dsh/nn/{graph,mode,executor,validate}.py and refactory_packages.
  graph.json contains the native code dependency graph. Inspect its module,
  symbol and import records directly, or explore the actual project source.
* New portable workflows live at <output_folder>/__init__.py, mode.py, ops.py,
  README.md, and any requested companion CLI/tests, with PACKAGE=nn.Package
  and reusable nn.Mode classes. Calls use refactory_packages.<name>.ops:fn.
  Do not patch the framework, registry wiring, or shipped package directory.
* A portable mode receives each new input through declared runtime knobs or
  actual runtime source ports. It must support distinct invocations without
  editing its package. A Task, DataNode, numeric score or optimization knob is
  needed only if requested. An intentionally unscored mode declares an honest
  unscored_reason. Never bake the benchmark instance or expected outputs in code.
* `factory workflow validate <mode>` and validate_graph() map to
  `python -m refactory_dsh.create_fidelity_ops validate <name> --project <artifact_project>`
  which imports in a contained subprocess and runs native validate_state with
  the generated mode's own invariants. Report the actual command and output.
* `factory workflow list/show` map to `python -m refactory_dsh.registry list
  --project <artifact_project> --json` and `python -m refactory_dsh.registry show
  <name>/<mode> --project <artifact_project>` (or candidate inspect for all modes).
* Skill export maps to `python -m refactory_dsh.create_fidelity_ops export <name>
  --project <artifact_project>`. This renders SKILL.md from the real admitted
  graph and its runtime declarations inside <output_folder>/SKILL.md.
* Before publishing its builder report the builder runs candidate `admit <name>
  --project <artifact_project>`: native validation, discovery/inspection and
  skill export; write raw results and commands in builder-latest.md. This is
  structural admission, not proof of behavior. Run genuine acceptance tests.
* Native mode execution is `python -m refactory_dsh.create_fidelity_ops run
  <name>/<mode> --project <artifact_project> --workdir <new-run-dir>
  --knob input_name=value`; this calls the real native executor through a
  contained subprocess. Standalone CLI and every requested input/output
  contract must also be tested independently. Propagate failed execution.
* For authoring smoke tests and verification, allocate fresh, separate run
  directories and output paths under <artifact_project> or an actually supplied
  private TMPDIR that permits both writing and reading the results. Respect
  the invocation's filesystem boundary; do not assume global /tmp is accessible.
  This governs test outputs, not user-supplied external runtime input paths.
  Reuse the same unchanged package with each invocation's declared inputs.
* Source factory study/discover/init/precheck map to the candidate's native
  bootstrap, AST graph study, and final validation/discovery/evidence checks.
  Source eval and PR diff mean the actual local tests and local artifact diff.
* CLAUDE.md conventions map to any actual AGENTS.md, CLAUDE.md and README in
  the source project. If absent, record absence; never invent conventions.
* Commit, issue, GitHub PR, merge and deployment instructions are overridden:
  this is an artifact-only local authoring run. Do not publish or modify the
  framework. Review the real package files and preserved baseline digests.
* Child dispatch is owned by native composite directors, always through DSH.
  Plans are strict JSON; quality summaries are separate prose artifacts.
  Reports live in strategy/research/, strategy/perspectives/, reviews/testers/
  under .factory. Every report body and provenance is embedded in each stage's
  .factory/<stage>-findings.json and .md. Consume substantive reports in full.
* Archival writes remain nonblocking. The build archive must read precheck.json
  and diagnose a failed precheck, preserving failure honestly.
* Source AI gate feedback appended to the user-intent file maps to the separate
  .factory/reviews/repair-feedback.md appendix. It is model-authored critique,
  supplied in full to builder, QA and gates. It cannot edit or redefine human
  intent. Original human strategy rejection feedback remains in user-intent.md
  and follows the original re-entry through strategy and fresh human approval.
  Builder repairs preserve approval only while strategy and human intent bytes
  are unchanged. Material changes require new digest-bound human approval.

## Native package API supplied by this invocation
{{native_api}}
"""


@lru_cache(maxsize=1)
def reference() -> dict[str, Any]:
    """Read the shipping prompt resource, never the independent test fixture."""
    path = Path(__file__).with_name("data") / "create_fidelity" / "v1" / "prompts.json"
    return json.loads(path.read_text())


def role_prompt(role: str) -> str:
    prompts = reference()["role_prompts"]
    if role not in prompts:
        raise ValueError(f"no frozen Create role prompt for {role!r}")
    return str(prompts[role])


def node_prompt(node_id: str) -> str:
    return str(reference()["node_prompts"].get(node_id, ""))


def load_prompt(role: str, node_id: str = "") -> str:
    """Full inherited role and node duties, followed by explicit native rules."""
    sections = [role_prompt(role)]
    if node_id:
        sections.append("## Source stage duties\n" + node_prompt(node_id))
    sections.append(NATIVE_INSTRUCTIONS)
    return "\n\n".join(sections)


# Load versioned source data once at module import. Mode.build() then assembles
# its graph from fixed in-memory instructions, without doing filesystem I/O.
reference()
