"""Pytest configuration for the refactory-dsh test harness."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ops declared by a mode are resolved by module name at run time, so the test ops
# have to be importable by name rather than only by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# PROMPT_MUTATE and expandable-KNOB_MUTATE shell out to `claude` by default.
# For the fast, deterministic harness we point that at /bin/true (empty output),
# which makes `default_prompt_rewriter` fall back to its deterministic prompt
# variants instead of calling a live LLM. The real DSH inner loop test runs the
# actual CEO route, so this only affects the surrogate/unit surface.
os.environ.setdefault("FACTORY_CLAUDE_BIN", "/bin/true")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="run slow tests that spawn a real DSH CEO run",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "integration: slow tests that spawn a real DSH CEO run"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-integration", default=False):
        return
    skip = pytest.mark.skip(reason="pass --run-integration to run DSH inner-loop tests")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
