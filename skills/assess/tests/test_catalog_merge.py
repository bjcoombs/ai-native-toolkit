"""Two PRs that each add a different check merge cleanly in every generated catalog file (#1239).

``docs/checks.html``, ``references/measures.json``, ``docs/what-we-measure.md`` and
``references/counts-only.md`` give each check its own lines, sorted by a stable key, with no
count or summary line that every addition rewrites. This test adds two probe checks (a
collector, a signal, a condition, a maturity-model row and a finding each) on two branches cut
from the same base, merges both with git, and asserts that no generated file conflicts and that
the merged files equal what a regeneration of both checks together writes, so ``make regen``
after the merge leaves no diff.

Two checks whose keys sort next to each other in a list still insert at the same spot and
conflict; the probes are named so they land apart, as most new checks do. A JSON list with one
element is the other limit: two additions to it always meet, because a new element changes the
separator on its neighbour's line (the "same shared tags" case).
"""
from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import lib.catalog as catalog_mod
import lib.counts_only.content_free as content_free
import lib.manifest.check_manifest as cm
from lib.catalog import build_catalog, render_json
from lib.catalog_html import render_html
from lib.catalog_md import render_markdown
from lib.counts_only.counts_only_rules import RULES
from lib.scoring.level_signals import load_default_levels
from lib.signals import known_scans

TEMPLATE = "skill_script_tests"  # the collector each probe is copied from
FILES = ("checks.html", "measures.json", "what-we-measure.md", "counts-only.md")


class Probe:
    """A new check named ``name``: the template collector's entries and row, renamed, plus a finding."""

    def __init__(self, name: str, *tags: str) -> None:
        self.name, self.tags = name, list(tags)

    def _renamed(self, value: Any) -> Any:
        text = json.dumps(value).replace(TEMPLATE, self.name).replace(
            TEMPLATE.replace("_", "-"), self.name.replace("_", "-"))
        return json.loads(text.replace("Skill script tests", f"Probe {self.name}"))

    def entries(self, data: dict[str, Any]) -> list[Any]:
        own = [e for e in data["entries"] if TEMPLATE in e["id"] or TEMPLATE.replace("_", "-") in e["id"]]
        finding = copy.deepcopy(next(e for e in data["entries"] if e["kind"] == "finding"))
        finding.update(id=f"core.finding.{self.name}_gap", name=f"{self.name}_gap",
                       title=f"Probe {self.name} gap", inputs=[self.name],
                       order=900 + len(self.name), why=f"Why {self.name} matters.")
        return [*self._renamed(own), finding]

    def row(self, model: Any) -> Any:
        row = copy.deepcopy(next(r for r in model["rows"] if r["id"] == TEMPLATE.replace("_", "-")))
        renamed = self._renamed(row)
        renamed["levels"][0]["truth"] = self.tags
        return renamed


def _render(probes: list[Probe], monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    data = copy.deepcopy(cm.read_manifest())
    model = copy.deepcopy(load_default_levels())
    for probe in probes:
        data["entries"] += probe.entries(cm.read_manifest())
        model["rows"].append(probe.row(load_default_levels()))
    rules = dict(RULES)
    for probe in probes:
        rules[probe.name] = {k.replace(TEMPLATE, probe.name): v for k, v in RULES[TEMPLATE].items()}
    with monkeypatch.context() as m:
        m.setattr(catalog_mod, "known_scans", lambda: [*known_scans(), *(p.name for p in probes)])
        m.setattr(content_free, "RULES", rules)
        catalog = build_catalog(cm.parse_manifest(data), model)
        counts_only = content_free.render_doc()
    return dict(zip(FILES, (render_html(catalog, "9.9.9"), render_json(catalog),
                            render_markdown(catalog), counts_only)))


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          cwd=repo, capture_output=True, encoding="utf-8", errors="replace", check=False)


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8", newline="\n")
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "-q", "-m", message).returncode == 0


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Path]:
    assert _git(tmp_path, "init", "-q", "-b", "main").returncode == 0
    yield tmp_path


# case -> (the two probes, the files expected to conflict). Two conditions shared by the same
# pair of tags both join that pair's JSON list, which holds one id today: in a one-element list
# every insertion meets the same line (the only element, or its separator), so measures.json
# conflicts there and `make regen` resolves it. The Markdown and the page give each shared id
# its own line and merge.
CASES: dict[str, tuple[tuple[Probe, Probe], set[str]]] = {
    "different tags": ((Probe("doc_probe", "docs"), Probe("review_probe", "governance")), set()),
    "same tag": ((Probe("doc_probe", "behaviour"), Probe("trace_probe", "behaviour")), set()),
    "same shared tags": ((Probe("doc_probe", "agent_context", "docs"),
                          Probe("trace_probe", "agent_context", "docs")), {"measures.json"}),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_two_branches_adding_different_checks_merge_cleanly(case: str, repo: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    (a, b), expected = CASES[case]
    _commit(repo, _render([], monkeypatch), "base")
    assert _git(repo, "branch", "check-b").returncode == 0
    _commit(repo, _render([a], monkeypatch), f"add {a.name}")
    assert _git(repo, "checkout", "-q", "check-b").returncode == 0
    _commit(repo, _render([b], monkeypatch), f"add {b.name}")
    assert _git(repo, "checkout", "-q", "main").returncode == 0
    merged = _git(repo, "merge", "--no-edit", "check-b")
    conflicted = set(_git(repo, "diff", "--name-only", "--diff-filter=U").stdout.split())
    assert conflicted == expected and (merged.returncode == 0) == (not expected), merged.stdout
    regenerated = _render([a, b], monkeypatch)
    for name in sorted(set(FILES) - expected):  # make regen after the merge leaves no diff
        assert (repo / name).read_text(encoding="utf-8") == regenerated[name], name
