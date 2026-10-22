"""Discover the refactory.steer entry point.

A core module that returns the module name registered for the
``refactory.steer`` entry-point group, or ``None`` when nothing is
installed.  Used by startup.ts (via ``python -m refactory_dsh.steer_probe``)
and by interactive.py (via ``find_steer_module()`` in-process).
"""
from __future__ import annotations


def find_steer_module() -> str | None:
    """Return the module name from the ``refactory.steer`` entry point, or None."""
    from importlib.metadata import entry_points
    eps = entry_points(group='refactory.steer')
    if len(eps) > 1:
        raise RuntimeError(
            f"expected at most one 'refactory.steer' entry point, "
            f"got {len(eps)}: {[ep.name for ep in eps]}"
        )
    if not eps:
        return None
    return next(iter(eps)).value


if __name__ == '__main__':
    result = find_steer_module()
    if result is not None:
        print(result)
