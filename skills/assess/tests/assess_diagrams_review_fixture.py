"""The fixture blocks and golden renders of the doc-drift map and the review strip (#943).

``DOC_STALENESS`` has 17 docs mapped to code of their own (more than ``DRIFT_TOP``, with a
tie, a zero, a generated doc whose source is newer and a doc with several subject roots)
and two ``repo-baseline`` docs. ``REVIEW`` is a GitLab-shaped block with every strip state,
including an undecided merge request (so its ``reviewed_share`` is None) and bot marks.
``test_assess_diagrams_review.py`` fails when a committed golden differs from a fresh
render. Regenerate with ``uv run --locked python tests/assess_diagrams_review_fixture.py``
from ``skills/assess``, then review the diff.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.assess_diagrams import diagrams_section  # noqa: E402
from lib.assess_diagrams_review import DRIFT_FILE, STRIP_FILE, review_diagrams  # noqa: E402
from lib.run_context_types import JsonDict  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "assess_diagrams"


def _doc(path: str, ratio: float, roots: list[str], method: str = "base-doc",
         **extra: object) -> JsonDict:
    return {"path": path, "subject_method": method, "subject_roots": roots, "ratio": ratio, **extra}


_RATIOS = [14.0, 9.5, 7.0, 7.0, 4.25, 3.0, 2.5, 2.0, 1.5, 1.0, 0.75, 0.5, 0.5, 0.25, 0.0, 0.0]
DOCS: list[JsonDict] = [
    _doc("services/billing/README.md", 14.0, ["services/billing"]),
    _doc("baseline/OVERVIEW.md", 12.0, [], method="repo-baseline"),
    *[_doc(f"packages/module-{i:02d}/docs/a-rather-long-document-name.md", r,
           [f"packages/module-{i:02d}/src"]) for i, r in enumerate(_RATIOS[1:], start=1)],
    _doc("docs/api/generated.md", 0.0, ["api"], method="explicit-links",
         provenance={"method": "frontmatter", "sources": ["api/spec.yaml"], "generated_by": None,
                     "source_newer": True}),
    _doc("docs/shared.md", 0.0, ["lib/a", "lib/b", "lib/c"], method="parallel-docs"),
    _doc("CONTRIBUTING.md", 0.0, [], method="repo-baseline"),
]

DOC_STALENESS: JsonDict = {"available": True, "churn_degenerate": False, "history_truncated": False,
                           "docs": DOCS}

REVIEW: JsonDict = {
    "available": True, "merged_count": 10, "reviewed_share": None, "approved_share": 0.3,
    "bot_review_share": None,
    "sequence": ["approved", "reviewed", "unreviewed", "unreviewed", "unreviewed",
                 "unknown", "reviewed", "approved", "unreviewed", "approved"],
    "bot_sequence": ["none", "bot", "bot", "none", "none", "unknown", "none", "bot", "none", "none"],
}

CTX: JsonDict = {"doc_staleness": DOC_STALENESS, "review_reality": REVIEW}


def goldens() -> dict[str, str]:
    """The golden renders: ``expected-<file>`` -> text."""
    diagrams = review_diagrams(CTX)
    files = {f"expected-{d['file']}": d["svg"] for d in diagrams if d["svg"] is not None}
    assert set(files) == {f"expected-{DRIFT_FILE}", f"expected-{STRIP_FILE}"}
    return {**files, "expected-review-section.md": diagrams_section(diagrams) + "\n"}


if __name__ == "__main__":
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, content in goldens().items():
        (FIXTURES / name).write_text(content, encoding="utf-8", newline="\n")
