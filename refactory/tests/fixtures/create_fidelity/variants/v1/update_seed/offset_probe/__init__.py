"""Intended original update input; not a completed revised deliverable."""
from refactory_dsh import nn
from .mode import Offset, Identity

PACKAGE = nn.Package(name="offset_probe", description="Original +1 transform and independent identity", modes=(Offset, Identity), ops=("refactory_packages.offset_probe.ops",))
