"""Generator-directive pages leave the doc-staleness share (#816)."""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
from pathlib import Path

import pytest

from lib.docs.doc_directives import (
    MAX_BYTES, directive_counts, is_directive_only, rst_directive_counts,
)
from lib.docs.doc_staleness import DocStalenessRow, analyze_doc_staleness, staleness_summary

# The issue's evidence page: a heading, a setup link and one directive.
STUB = """# Cohere

To use this model, install the [cohere extra](../../install.md#extras).

::: pydantic_ai.models.cohere
"""

# A hand-written guide that embeds one directive among its prose.
GUIDE = """# Using the models

Pick a model by name when you build the agent. The name selects the provider
and the provider reads its key from the environment.

Each provider has its own retry policy, described below, and every call is
logged with the model name so a failure can be traced.

::: pkg.mod

The table above is generated; the rest of this page is not.
"""


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("text", [
    STUB,
    "::: pkg.mod\n",
    "---\ntitle: API\n---\n# API\n\n::: pkg.a\n::: pkg.b\n",
    "# API\n\n::: pkg.mod\n    options:\n      show_source: false\n      members:\n        - run\n",
    "<!-- generated reference -->\n# API\n\n:::pkg.mod\n",
    # Many directives may carry an intro line per directive.
    "# API\n\n" + "".join(f"Module {i}.\n\n::: pkg.m{i}\n\n" for i in range(5)),
])
def test_a_directive_page_is_directive_only(tmp_path: Path, text: str) -> None:
    _write(tmp_path, "api.md", text)
    assert is_directive_only(tmp_path / "api.md")


@pytest.mark.parametrize("text", [
    GUIDE,
    "# Plain page\n\nNo directive here at all.\n",
    "# Note\n\n:::note\nA Docusaurus admonition.\n:::\n",
    "# Warn\n\n::: warning\nA fenced-div admonition.\n:::\n",
    "# Div\n\n::: {.callout}\nA pandoc fenced div.\n:::\n",
    "# Example\n\n```markdown\n::: pkg.mod\n```\n",   # a directive shown in a code fence
])
def test_other_pages_are_not_directive_only(tmp_path: Path, text: str) -> None:
    _write(tmp_path, "page.md", text)
    assert not is_directive_only(tmp_path / "page.md")


def test_the_counts_follow_the_rules() -> None:
    assert directive_counts(STUB) == (1, 1, 0)
    assert directive_counts(GUIDE) == (1, 5, 0)


def test_only_an_options_block_belongs_to_a_directive() -> None:
    options = "::: pkg.mod\n    options:\n      show_source: false\n      members:\n        - run\n"
    assert directive_counts(options) == (1, 0, 0)
    swallowed = ("::: pkg.mod\n    This module is deprecated; use pkg.v2.\n    It keeps one shim.\n"
                 "    The shim logs a warning.\n    It goes in 4.0.\n"
                 "    Never call reset() in production.\n")
    assert directive_counts(swallowed) == (1, 5, 0)
    # Indented prose after an options block is prose too.
    after = options + "  A note under the options.\n"
    assert directive_counts(after) == (1, 1, 0)


@pytest.mark.parametrize("name", ["notes", "columns", "raw"])
def test_a_one_word_div_with_a_closer_is_not_directive_only(tmp_path: Path, name: str) -> None:
    _write(tmp_path, "div.md", f"# Div\n\n::: {name}\nText.\n:::\n")
    assert directive_counts((tmp_path / "div.md").read_text(encoding="utf-8"))[2] == 1
    assert not is_directive_only(tmp_path / "div.md")


def test_a_page_of_headings_and_one_directive_is_directive_only(tmp_path: Path) -> None:
    _write(tmp_path, "api.md", "# API\n\n## Models\n\n### Cohere\n\n::: pkg.mod\n")
    assert is_directive_only(tmp_path / "api.md")


def test_non_markdown_unreadable_and_oversized_pages_are_not_directive_only(tmp_path: Path) -> None:
    _write(tmp_path, "api.rst", "::: pkg.mod\n")
    _write(tmp_path, "big.md", "::: pkg.mod\n" + "\n" * MAX_BYTES)
    assert not is_directive_only(tmp_path / "api.rst")
    assert not is_directive_only(tmp_path / "big.md")
    assert not is_directive_only(tmp_path / "missing.md")


# A Sphinx autodoc stub (#840): a title, a module target and the directive.
RST_STUB = """pkg.mod
=======

.. currentmodule:: pkg

.. automodule:: pkg.mod
   :members:
   :undoc-members:
"""


@pytest.mark.parametrize("text", [
    RST_STUB,
    ".. automodule:: pkg.mod\n",
    ".. _api:\n\n=====\nAPI\n=====\n\n.. autoclass:: pkg.A\n.. autofunction:: pkg.f\n",
    "API\n---\n\n.. autosummary::\n   :toctree: generated\n\n   pkg.a\n   pkg.b\n",
    ".. comment about the page\n   continued\n\nAPI\n===\n\nIntro line.\n\n.. py:automodule:: pkg\n",
])
def test_a_sphinx_autodoc_page_is_directive_only(tmp_path: Path, text: str) -> None:
    _write(tmp_path, "docs/api.rst", text)
    _write(tmp_path, "docs/api.txt", text)
    assert is_directive_only(tmp_path / "docs/api.rst")
    assert is_directive_only(tmp_path / "docs/api.txt")


@pytest.mark.parametrize("text", [
    "Guide\n=====\n\nFirst line.\nSecond line.\nThird line.\nFourth line.\n\n"
    ".. automodule:: pkg.mod\n",
    "Plain\n=====\n\nNo directive at all.\n",
    "Note\n====\n\n.. note::\n\n   Not autodoc.\n",
])
def test_other_rst_pages_are_not_directive_only(tmp_path: Path, text: str) -> None:
    _write(tmp_path, "page.rst", text)
    assert not is_directive_only(tmp_path / "page.rst")


def test_the_rst_counts_follow_the_rules() -> None:
    assert rst_directive_counts(RST_STUB) == (1, 0, 0)
    assert rst_directive_counts("Title\n=====\n\n.. note::\n\n   body\n   more\n\nprose\n") == (0, 2, 0)


def _row(path: str, ratio: float, directive_only: bool = False) -> DocStalenessRow:
    row: DocStalenessRow = {
        "path": path, "last_commit_days": 1, "doc_churn_in_window": 1,
        "code_churn_in_window": 9, "subject_code_count": 1,
        "subject_method": "explicit-links", "subject_roots": [], "base_doc_dir": None,
        "subject_paths": [], "ratio": ratio, "window_ratio": ratio,
        "code_churn_since_doc_change": 9, "confidence": "high"}
    if directive_only:
        row["directive_only"] = True
    return row


def test_the_summary_leaves_directive_pages_out_and_counts_them() -> None:
    rows = [_row("api.md", 0.0, directive_only=True),
            *(_row(f"guide{i}.md", 9.0) for i in range(20))]
    assert staleness_summary(rows, reliable=True) == {
        "measured_docs": 20, "stale_docs": 20, "stale_pct": 100, "generator_directive_docs": 1}
    assert staleness_summary(rows[:1], reliable=True) == {
        "measured_docs": 0, "stale_docs": None, "stale_pct": None,
        "generator_directive_docs": 1}


# -- end to end, over a real history -----------------------------------------

def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", check=True,
                   env={**os.environ, **(env or {})})


def _commit(repo: Path, message: str, days_ago: int) -> None:
    stamp = (_dt.datetime.now() - _dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message,
         env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})


def test_a_stub_beside_heavy_churn_is_not_stale_and_a_guide_still_is(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _write(tmp_path, "docs/api/mod.md", STUB.replace("pydantic_ai.models.cohere", "pkg.mod"))
    _write(tmp_path, "docs/guide.md", GUIDE)
    _write(tmp_path, "docs/sourced.md", "---\nsource: ../pkg/mod.py\n---\n::: pkg.mod\n")
    _write(tmp_path, "pkg/mod.py", "x = 0\n")
    _write(tmp_path, "pkg/other.py", "y = 0\n")
    _commit(tmp_path, "init", 200)
    for i in range(1, 9):
        _write(tmp_path, "pkg/mod.py", f"x = {i}\n")
        _commit(tmp_path, f"edit {i}", 200 - 10 * i)

    edges = [{"doc": d, "code": "pkg/mod.py"}
             for d in ("docs/api/mod.md", "docs/guide.md", "docs/sourced.md")]
    block = analyze_doc_staleness(tmp_path, doc_to_code_edges=edges, generated_sources=[])
    rows = {row["path"]: row for row in block["docs"]}

    stub = rows["docs/api/mod.md"]
    assert stub["directive_only"] is True
    assert stub["ratio"] == 0.0 and stub["window_ratio"] > 0
    guide = rows["docs/guide.md"]
    assert "directive_only" not in guide and guide["ratio"] > 2
    # A declared source keeps the provenance verdict, not the directive rule.
    assert "directive_only" not in rows["docs/sourced.md"]
    assert rows["docs/sourced.md"]["provenance"]["method"] == "frontmatter"
    # The guide (churn) and the sourced page (its source is newer) are stale;
    # the stub is neither measured nor stale.
    assert block["summary"] == {"measured_docs": 2, "stale_docs": 2, "stale_pct": None,
                                "generator_directive_docs": 1,
                                "stale_pct_reason": "too_few_measured_docs"}
