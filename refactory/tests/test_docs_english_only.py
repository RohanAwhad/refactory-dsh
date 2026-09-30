"""Every document this repository owns is English-only.

The harness copy carried a bilingual pairing gate: `scripts/translation-pairing.manifest.json`
listed documents with a `.zh.md` counterpart and failed when the two sides drifted.
Deleting the copied harness deleted that gate, and with it the only thing that kept a
translation honest. The eight packages and `refactory/` no longer publish translations,
so the rule is now the simpler one this test enforces: no `.zh.md` side and no
`README.i18n.yaml` alignment record. A stale translation is worse than none, because a
reader cannot tell it has fallen behind.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

#: The trees whose documents are English-only: the glue and the eight packages we ship.
ENGLISH_ONLY = (
    ROOT / "refactory",
    ROOT / "packages" / "workflow" / "workflow-spine",
    ROOT / "packages" / "bundle" / "graph",
    ROOT / "packages" / "bundle" / "refactory-presets",
    ROOT / "packages" / "bundle" / "refactory-web",
    ROOT / "packages" / "refactory" / "refactory-console",
    ROOT / "packages" / "refactory" / "tool-refactory",
    ROOT / "packages" / "client" / "ui-refactory",
    ROOT / "packages" / "client" / "ui-brand-refactory",
)


def _counterparts(tree: Path) -> list[Path]:
    return sorted(
        path
        for path in tree.rglob("*")
        if path.is_file() and (path.name.endswith(".zh.md") or path.name.endswith(".i18n.yaml"))
    )


def test_no_english_only_tree_carries_a_translation_side() -> None:
    """A `.zh.md` or `README.i18n.yaml` here has no gate keeping it current."""
    found = [path for tree in ENGLISH_ONLY for path in _counterparts(tree)]
    assert not found, (
        "these documents are English-only; delete the translation side rather than "
        "letting it drift: " + ", ".join(str(path.relative_to(ROOT)) for path in found)
    )


def test_every_package_we_ship_still_has_its_english_readme() -> None:
    """The rule is English-only, not readme-less."""
    for tree in ENGLISH_ONLY[1:]:
        assert (tree / "README.md").is_file(), f"{tree.relative_to(ROOT)} has no README.md"
