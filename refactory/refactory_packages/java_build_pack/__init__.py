"""A harness that writes a Java build script for each source tree a Task lists."""

from __future__ import annotations

from refactory_dsh.nn import Package

from .mode import BuildPack

PACKAGE = Package(
    name="java_build_pack",
    description="write a javac/java build script for any small Java source tree",
    modes=(BuildPack,),
    ops=("refactory_packages.java_build_pack.ops",),
)
