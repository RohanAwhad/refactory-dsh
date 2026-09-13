"""Packages of modes.

Each subpackage is one package: a manifest in ``__init__.py`` naming its modes,
and Python modules holding the operations those modes call. The library in
``refactory_dsh`` never imports anything from here, and nothing in here is
reachable from the library by name.
"""
