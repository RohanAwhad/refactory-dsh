from __future__ import annotations

from refactory_dsh.nn import Package

from .mode import Dataset

PACKAGE = Package(
    name="dataset_probe",
    description="a probe mode that iterates a Task's items",
    modes=(Dataset,),
    ops=("refactory_packages.dataset_probe.ops",),
)
