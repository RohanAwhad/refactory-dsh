"""The documentation is a fact about the code, and these keep it that way.

Writing docs once is easy; the failure mode is that they stay written while the
code moves. These tests bind the two: the API reference must match what the
generator produces from the real signatures, and the tutorials' commands must be
commands that exist and run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from refactory_dsh.tools import gen_api_reference

DOCS = Path(__file__).resolve().parent.parent / "docs"
REFERENCE = DOCS / "api" / "reference.md"
ARCHITECTURE = DOCS / "architecture.md"


# ── the API reference is generated, and current ────────────────────────────


def test_the_api_reference_matches_the_code():
    """A stale reference is a document that lies about the signatures."""
    assert REFERENCE.exists(), (
        "docs/api/reference.md is missing; run "
        "`python -m refactory_dsh.tools.gen_api_reference`"
    )
    assert REFERENCE.read_text() == gen_api_reference.render(), (
        "docs/api/reference.md is stale; run "
        "`python -m refactory_dsh.tools.gen_api_reference`"
    )


def test_the_reference_documents_every_module_a_reader_may_import():
    rendered = gen_api_reference.render()
    for module in gen_api_reference.MODULES:
        assert f"## `{module}`" in rendered, f"{module} is not in the reference"


def test_every_documented_class_has_prose_not_just_a_signature():
    """A dataclass with no docstring renders its own repr, which documents nothing."""
    text = REFERENCE.read_text()
    bare = [
        match.group(1)
        for match in re.finditer(r"- \*\*`(\w+)`\*\* — (\w+)\(", text)
        if match.group(1) == match.group(2)
    ]
    assert bare == [], f"these symbols document only their signature: {bare}"


def test_the_generator_reports_a_stale_file(tmp_path, monkeypatch):
    """`--check` is what a contributor runs, so it must actually fail."""
    monkeypatch.setattr(gen_api_reference, "OUT", tmp_path / "reference.md")
    assert gen_api_reference.main(["--check"]) == 1, "a missing file must fail the check"

    (tmp_path / "reference.md").write_text("not the reference")
    assert gen_api_reference.main(["--check"]) == 1, "a stale file must fail the check"

    (tmp_path / "reference.md").write_text(gen_api_reference.render())
    assert gen_api_reference.main(["--check"]) == 0


# ── the prose documents name things that exist ─────────────────────────────


def test_every_doc_link_resolves():
    """A relative link in a document points at a file this repo has.

    Every document, not just the overview: a tutorial index that links to
    walkthroughs nobody wrote is the first thing a reader hits.
    """
    missing: list[str] = []
    for document in sorted(DOCS.rglob("*.md")):
        text = document.read_text()
        for target in re.findall(r"\]\((?!https?:)([^)#]+)", text):
            if not (document.parent / target).exists():
                missing.append(f"{document.relative_to(DOCS)} -> {target}")
    assert missing == [], "documents link to files that do not exist:\n  " + "\n  ".join(missing)


def test_the_tutorial_index_lists_every_tutorial():
    """The index and the directory agree, so a new tutorial cannot be orphaned."""
    index = (DOCS / "tutorials" / "index.md").read_text()
    linked = set(re.findall(r"\]\((\d\d-[\w-]+\.md)\)", index))
    on_disk = {path.name for path in (DOCS / "tutorials").glob("[0-9][0-9]-*.md")}
    assert linked == on_disk, (
        f"only in the index {sorted(linked - on_disk)}, "
        f"only on disk {sorted(on_disk - linked)}"
    )


#: Modules the documentation names **as removed**. The architecture overview
#: records what the replaced system was built on, and a reader is not meant to
#: import these; naming them is the point. Anything else a document names must
#: import, so a typo is a failure rather than a dead end.
REMOVED_MODULES = frozenset({"refactory_dsh.ops"})


@pytest.mark.parametrize("path", ["architecture.md", "api/reference.md"])
def test_the_documents_name_only_modules_that_import(path):
    """A module path in a code span is a claim a reader will try to run."""
    text = (DOCS / path).read_text()
    named = {
        module
        for module in re.findall(r"`(refactory_dsh(?:\.\w+)+)`", text)
        # A removed name and anything under it: the overview names
        # `refactory_dsh.ops.scaffold` among the modules this replaced.
        if not any(module == gone or module.startswith(gone + ".") for gone in REMOVED_MODULES)
    }
    for module in named:
        __import__(module)


def test_the_architecture_overview_states_the_rule_codes_it_claims():
    """The doc's rule table is the code's rule table, in both directions."""
    from refactory_dsh.nn import RULES

    text = ARCHITECTURE.read_text()
    documented = set(re.findall(r"^\| `(V\d+|I:)` \|", text, re.MULTILINE))
    assert documented == set(RULES), (
        "docs/architecture.md and nn/validate.py disagree about the rules: "
        f"only in the doc {sorted(documented - set(RULES))}, "
        f"only in the code {sorted(set(RULES) - documented)}"
    )
