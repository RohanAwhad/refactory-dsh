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
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from refactory_dsh import sandbox
from refactory_dsh.model import DshModelClient


def _probe() -> str:
    try:
        with tempfile.TemporaryDirectory(prefix="refactory-credential-") as work:
            readiness = sandbox.model_readiness(
                Path(work),
                model_context=DshModelClient().context(),
                require_ready=False,
            )
    except Exception as exc:  # a probe that cannot run is not a missing credential
        return f"probe failed: {type(exc).__name__}: {exc}"
    return str(readiness.get("status") or "")


#: ``missing-credential``/``invalid-configuration`` are the two statuses that mean
#: the authoring run would be refused; every other status (configured, or a
#: diagnostic the sandbox accepts) lets the test run.
_STATUS = _probe()
HAS_MODEL_CREDENTIAL = _STATUS not in ("missing-credential", "invalid-configuration")

needs_model_credential = pytest.mark.skipif(
    not HAS_MODEL_CREDENTIAL,
    reason=(
        f"the authoring sandbox has no usable model credential (status: {_STATUS}); "
        "configure a model in DSH settings → Models (for example the "
        "deepseek-official provider) and re-run, or run these tests where a "
        "credential is configured"
    ),
)
