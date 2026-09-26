"""Permission mappings reach the pinned native adapters without model calls."""
import subprocess
from pathlib import Path

import pytest

from refactory_dsh import workers
from refactory_dsh.model import DshModelClient


def test_public_worker_permissions_reach_native_protocols_without_model_calls(tmp_path):
    root = Path(__file__).resolve().parents[2]
    if any(not (root / 'node_modules' / package).exists() for package in workers.PACKAGES.values()):
        pytest.skip('optional native worker packages not installed')
    for backend in ('codex', 'claude_code'):
        for permission in ('skip', 'default', 'implicit'):
            arguments = {'permission_mode': permission} if permission != 'implicit' else {}
            route = DshModelClient(worker_backend=backend, **arguments).route()
            assert route['permission_mode'] == ('skip' if permission == 'implicit' else permission)
            workers.write_patch(tmp_path / f'{backend}-{permission}.json', root, route)
    subprocess.run(['node', '--experimental-vm-modules',
                    str(Path(__file__).with_name('worker_permissions_test.mjs')),
                    str(root), str(tmp_path)], check=True, timeout=30)
