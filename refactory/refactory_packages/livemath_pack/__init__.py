"""A harness for LiveMathematicianBench, comparable with SkillOpt's LiveMath setup."""

from __future__ import annotations

from refactory_dsh.nn import Package

from .mode import SolvePack

PACKAGE = Package(
    name="livemath_pack",
    description="answer LiveMathematicianBench questions with one model call and a tunable skill",
    modes=(SolvePack,),
    ops=("refactory_packages.livemath_pack.ops",),
)
