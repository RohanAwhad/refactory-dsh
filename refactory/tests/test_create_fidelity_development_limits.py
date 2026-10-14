"""The diagnostic cap prevents dispatch, including copied and resumed clients."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from benchmarks.create_fidelity.development_limits import dispatch_limit
from refactory_dsh.model import DshModelClient


def test_concurrent_copies_and_new_clients_share_one_hard_dispatch_cap(tmp_path, monkeypatch):
    launched = []
    def transport(client, *args, **kwargs):
        launched.append(client._node_id)
        return "controlled launch"
    monkeypatch.setattr(DshModelClient, "_run_completion", transport)
    first = DshModelClient()
    def call(number):
        # Native resume constructs another instance; it must not reset the cap.
        client = first.for_node(str(number)) if number % 2 else DshModelClient().for_node(str(number))
        try:
            return client._run_completion([], tmp_path)
        except RuntimeError as error:
            assert "before exceeding 5" in str(error)
            return "refused"
    with dispatch_limit(tmp_path, 5):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(call, range(20)))
    assert len(launched) == results.count("controlled launch") == 5
    assert results.count("refused") == 15
    record=json.loads((tmp_path/"raw/development-dispatches.json").read_text())
    assert [item["number"] for item in record["dispatches"]] == list(range(1,6))
    assert DshModelClient._run_completion is transport


def test_ordinary_trials_remain_unchanged_and_invalid_cap_fails(tmp_path):
    original=DshModelClient._run_completion
    with dispatch_limit(tmp_path,None):
        assert DshModelClient._run_completion is original
    assert not (tmp_path/"raw").exists()
    with pytest.raises(ValueError):
        with dispatch_limit(tmp_path,True):
            pass
