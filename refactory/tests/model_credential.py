"""Whether this machine has a model credential the authoring sandbox accepts.

The authoring path refuses before it does any work when the DSH home has no model
credential. That is a fact about the machine, not about the code under test, so the
tests that exercise the model path skip with the fix named rather than failing --
and the ``toolchain`` marker beside ``needs_model_credential`` makes
``--toolchain-gate`` report the skip instead of counting a green suite as proof the
path ran.

The probe is deliberately the same ``sandbox.model_readiness(require_ready=False)``
the failing tests hit, not a direct ``DshModelClient().readiness()``: the sandbox
scrubs ``HOME`` and may launch the installed harness, which is exactly where the
credential is missing.

It is *deferred*, not computed at import: the probe can spawn the CLI, and a run
that selects none of the marked tests should not pay for it. A pytest hook does the
work once, at collection, and only when something marked is being collected. The
hook lives here rather than in ``conftest.py`` because pytest loads a module
``conftest`` imports before it applies that module's decorators, which is exactly
the ordering this needs.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from refactory_dsh import sandbox
from refactory_dsh.model import DshModelClient

#: The statuses that mean the authoring run would be refused. Every other status --
#: configured, or a diagnostic the sandbox accepts -- lets the marked test run.
NO_CREDENTIAL = ("missing-credential", "invalid-configuration")

#: ``status`` -> whether this machine can run the marked tests. Filled in once, by
#: the collection hook below, and only when a marked test is actually being
#: collected. Unset means "no marked test was collected", which is also the answer
#: for the marker: a test that was never collected cannot need the probe's answer.
_PROBE: dict[str, str] = {}


def probe() -> str:
    """Ask the sandbox whether a model credential is configured, once per session."""
    if "status" not in _PROBE:
        try:
            with tempfile.TemporaryDirectory(prefix="refactory-credential-") as work:
                readiness = sandbox.model_readiness(
                    Path(work),
                    model_context=DshModelClient().context(),
                    require_ready=False,
                )
            _PROBE["status"] = str(readiness.get("status") or "")
        except Exception as exc:  # a probe that cannot run is not a missing credential
            _PROBE["status"] = f"probe failed: {type(exc).__name__}: {exc}"
            _PROBE["error"] = f"{type(exc).__name__}: {exc}"
    return _PROBE["status"]


#: The decorator the seven model-path tests carry, beside their `toolchain` marker.
#: It adds nothing itself -- the collection hook above is what skips them -- but it
#: is the marker the hook finds, and `test_toolchain_gate.NEEDS` names it so a test
#: that skips on a missing credential cannot skip silently.
needs_model_credential = pytest.mark.needs_model_credential


def _marked(items: list[pytest.Item]) -> list[pytest.Item]:
    """The items that asked for this marker, in either of the two ways it arrives.

    A module in this suite is a plugin, not a conftest, so its hook is registered
    late -- after `conftest.pytest_collection_modifyitems` has already run. A
    decorator applied at import is therefore found via `own_markers`, while a marker
    this hook itself adds is found via the plain lookup; reading only one of the two
    would silently collect the tests unskipped.
    """
    found: list[pytest.Item] = []
    for item in items:
        if item.get_closest_marker("needs_model_credential") is not None or any(
            marker.name == "needs_model_credential" for marker in item.own_markers
        ):
            found.append(item)
    return found


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Answer the marker for this run, and skip the marked tests when it says no.

    A hook (not a ``skipif`` evaluated at import) so the probe is paid only by a run
    that collected something that needs it. Skipping here rather than in the marker
    keeps the reason the same one the gate prints.
    """
    marked = _marked(items)
    if not marked:
        return
    status = probe()
    if status in NO_CREDENTIAL:
        skip = pytest.mark.skip(reason=reason(status))
        for item in marked:
            item.add_marker(skip)


def reason(status: str) -> str:
    """Why the marked tests cannot run here, naming the fix."""
    return (
        f"the authoring sandbox has no usable model credential (status: {status}); "
        "configure a model in DSH settings → Models (for example the "
        "deepseek-official provider) and re-run, or run these tests where a "
        "credential is configured"
    )
