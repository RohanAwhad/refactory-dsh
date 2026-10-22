"""Test that agent_delivery.finish() raises on stale install before the receipt is written.

Exercises the check at agent_delivery.py:397-403: when the worktree record
exists but no worktree-state entry point is installed, finish() raises before
writing receipt.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


_EP_EMPTY_PREAMBLE = textwrap.dedent("""\
    import importlib.metadata as _im
    _real_ep = _im.entry_points
    def _empty_ep(**kwargs):
        group = kwargs.get("group", "")
        if group.startswith("refactory."):
            return _im.EntryPoints()
        return _real_ep(**kwargs)
    _im.entry_points = _empty_ep
""")


def test_stale_install_finish_raises_before_receipt(tmp_path: Path):
    """finish() raises RuntimeError on stale install before receipt.json is written."""
    workdir = tmp_path / 'run'
    workdir.mkdir()
    # Create the worktree record so the check triggers
    refactory_dir = workdir / '.refactory'
    refactory_dir.mkdir()
    (refactory_dir / 'worktree.json').write_text('{}')

    # Create a fake pending delivery record so finish() can find it
    delivery_dir = refactory_dir / 'agent-delivery'
    delivery_dir.mkdir()
    node_name = 'test_node'
    pending = delivery_dir / f'{node_name}.pending.json'
    record_id = 'test-id'
    record = {
        'id': record_id,
        'node': node_name,
        'writes': [],
        'before': {},
    }
    pending.write_text(json.dumps(record))
    (delivery_dir / record_id).mkdir()

    # Run in a subprocess with entry points emptied
    script = _EP_EMPTY_PREAMBLE + textwrap.dedent(f"""\
        import sys, json
        from pathlib import Path

        workdir = Path('{workdir}')
        try:
            from refactory_dsh.agent_delivery import finish
            finish(workdir, '{node_name}')
        except RuntimeError as e:
            msg = str(e)
            if 'worktree-state entry point' not in msg:
                print(f"unexpected RuntimeError: {{msg}}", file=sys.stderr)
                sys.exit(2)
            # Verify receipt was NOT written
            receipt_path = workdir / '.refactory' / 'agent-delivery' / '{record_id}' / 'receipt.json'
            if receipt_path.exists():
                print("receipt.json should not exist", file=sys.stderr)
                sys.exit(3)
            sys.exit(0)
        except Exception as e:
            print(f"unexpected exception: {{type(e).__name__}}: {{e}}", file=sys.stderr)
            sys.exit(4)
        print("finish() should have raised", file=sys.stderr)
        sys.exit(5)
    """)
    result = subprocess.run(
        [sys.executable, '-c', script],
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"stale-install finish test failed (exit {result.returncode}):\n{result.stderr.decode()}"
    )
