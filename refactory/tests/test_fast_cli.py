"""Public fast authoring flags must reach the actual mode's declared knobs."""

import pytest

from refactory_dsh import cli, interactive, registry
from refactory_dsh.paths import REPO_ROOT


@pytest.mark.parametrize("mode,extra", [
    ("create_fast", ["--single"]),
    ("edit_fast", ["--revise", "counting_pack/fixed_step"]),
    ("design_fast", []),
])
def test_fast_check_and_timeout_reach_the_declared_run(monkeypatch, mode, extra):
    seen = []

    def prepared(identifier, **kwargs):
        state = registry.resolve(REPO_ROOT, identifier)[1]().state_dict()
        state = interactive.with_knobs(state, kwargs["knobs"])
        assert state["knobs"]["check"]["value"] == "python -c 'assert 1 + 1 == 2'"
        assert kwargs["timeout_s"] == 60
        assert state["knobs"]["repair_attempts"]["value"] == 1
        seen.append(identifier)
        return 0

    monkeypatch.setattr(interactive, "run", prepared)
    # Bypass first-run model choice detection so the test reaches run().
    from refactory_dsh import dsh_profile
    monkeypatch.setattr(dsh_profile, "first_run_choice", lambda **kw: {"provider": "test", "model": "test"})
    assert cli.main([
        "ceo", "a small change", "--mode", mode, *extra,
        "--check", "python -c 'assert 1 + 1 == 2'", "--timeout", "60",
        "--repair-attempts", "1", "--headless",
    ]) == 0
    assert seen == [f"default_pack/{mode}"]
