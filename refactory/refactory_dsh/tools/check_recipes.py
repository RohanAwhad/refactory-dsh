"""Run the documented commands and report which ones no longer do what they say.

This is the tutorial as a diagnostic: every command the documentation shows has a
recipe (`refactory_dsh.tools.recipes`), and this executes them and compares the
result with the sentence that promises what it produces. A doc that lies fails
here.

    python -m refactory_dsh.tools.check_recipes              # the cheap tier
    python -m refactory_dsh.tools.check_recipes --tier model # real model calls
    python -m refactory_dsh.tools.check_recipes --only loop  # one recipe, by id

Exit status is 0 when every recipe it ran passed, 1 otherwise, and 2 when a recipe
could not be run at all — a missing interpreter is not a documentation defect.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from refactory_dsh.paths import REPO_ROOT
from refactory_dsh.tools import recipes as recipes_module
from refactory_dsh.tools.recipes import CHEAP, MANUAL, MODEL, Recipe, Result, SCRATCH


def _cwd(recipe: Recipe) -> Path:
    return REPO_ROOT / "refactory" if recipe.cwd == "refactory" else REPO_ROOT


def _shell(recipe: Recipe) -> str:
    """The script to run: the shown command, or the fuller one when it needs it."""
    return recipe.script or recipe.command


def run(recipe: Recipe, *, timeout: int) -> Result:
    """Execute one recipe and check what it produced."""
    interpreter = REPO_ROOT / ".venv" / "bin" / "python"
    if not interpreter.exists() and ".venv/bin/python" in _shell(recipe):
        result = Result(recipe=recipe, ok=False, problems=["no .venv/bin/python; run ./install.sh"])
        result.problems.append("skipped: this machine has no venv")
        return result
    completed = subprocess.run(
        ["bash", "-lc", _shell(recipe)],
        cwd=str(_cwd(recipe)),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = completed.stdout + completed.stderr
    result = recipes_module.check(recipe, output, _cwd(recipe))
    if completed.returncode != 0:
        result.ok = False
        result.problems.append(f"exit code {completed.returncode}")
    return result


def main(argv: list[str] | None = None) -> int:
    """Run the recipes the caller asked for and print what happened."""
    parser = argparse.ArgumentParser(description="Check the documented commands.")
    parser.add_argument(
        "--tier", choices=[CHEAP, MODEL, MANUAL], default=CHEAP,
        help="how expensive a recipe may be: cheap runs offline, model makes real calls",
    )
    parser.add_argument("--only", default="", help="run the recipes whose id contains this")
    parser.add_argument("--skip", default="", help="skip the recipes whose id contains this")
    parser.add_argument("--timeout", type=int, default=900, help="seconds per recipe")
    parser.add_argument("--quiet", action="store_true", help="print only failures")
    args = parser.parse_args(argv)

    chosen = [
        recipe for recipe in recipes_module.RECIPES
        if recipe.runnable(args.tier)
        and (not args.only or args.only in recipe.id)
        and (not args.skip or args.skip not in recipe.id)
    ]
    if not chosen:
        print(f"check-recipes: no recipes at tier {args.tier!r} match {args.only!r}")
        return 2

    shutil.rmtree(SCRATCH, ignore_errors=True)
    Path(SCRATCH).mkdir(parents=True, exist_ok=True)

    results: list[Result] = []
    for recipe in chosen:
        try:
            result = run(recipe, timeout=args.timeout)
        except subprocess.TimeoutExpired:
            result = Result(recipe=recipe, ok=False, problems=[f"timed out after {args.timeout}s"])
        results.append(result)
        if not args.quiet or not result.ok:
            print(result.describe())

    failed = [result for result in results if not result.ok]
    print(f"\ncheck-recipes: {len(results) - len(failed)}/{len(results)} passed at tier {args.tier}")
    for result in failed:
        print(f"\n--- {result.recipe.id}: {result.recipe.command}")
        print(result.output[-1500:])
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    raise SystemExit(main())
