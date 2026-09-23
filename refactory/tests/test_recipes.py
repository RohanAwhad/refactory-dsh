"""The documented commands are runnable, and the page shows all of them.

`recipes.py` is the one list: the HTML tutorial renders it and `check_recipes` runs
it. This test is the cheap half of that gate — the recipes that make real model calls
run with `check_recipes --tier model`, which is a person's decision, not CI's.
"""

from __future__ import annotations

import html
import subprocess

from refactory_dsh.paths import REPO_ROOT
from refactory_dsh.tools import gen_tutorial, recipes

TUTORIAL = gen_tutorial.OUT


def test_the_page_shows_every_recipe():
    """A command the checker runs but the page never shows helps nobody."""
    page = TUTORIAL.read_text()
    # The page is HTML, so compare the escaped first line: that is what a reader sees.
    missing = [
        recipe.id for recipe in recipes.RECIPES
        if html.escape(recipe.command.split("\n")[0], quote=True) not in page
    ]
    assert missing == [], f"the tutorial does not show: {missing}"


def test_every_recipe_says_what_it_should_produce():
    """`expect` is the check's human half, and it is required."""
    for recipe in recipes.RECIPES:
        assert recipe.expect.strip(), f"{recipe.id} promises nothing"
        assert recipe.tier in (recipes.CHEAP, recipes.MODEL, recipes.MANUAL), recipe.tier
        assert recipe.command.strip(), f"{recipe.id} shows no command"


def test_every_recipe_belongs_to_a_documented_group():
    """A recipe in no group is a command nobody will find."""
    for recipe in recipes.RECIPES:
        assert recipe.group in recipes.GROUP_TITLES, f"{recipe.id}: unknown group {recipe.group}"


def test_the_offline_recipes_still_do_what_the_page_says():
    """The gate: run the cheap tier and fail when a documented command drifted."""
    completed = subprocess.run(
        [str(REPO_ROOT / ".venv" / "bin" / "python"), "-m", "refactory_dsh.tools.check_recipes",
         "--tier", "cheap", "--skip", "drift", "--quiet"],
        cwd=str(REPO_ROOT / "refactory"), capture_output=True, text=True, timeout=900,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "passed at tier cheap" in completed.stdout
