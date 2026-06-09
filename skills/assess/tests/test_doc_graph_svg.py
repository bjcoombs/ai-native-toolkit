"""The doc-graph SVG honours the same excludes as the scorer (issue #177).

`doc-graph-svg.py` is a CLI wrapper that imports matplotlib/numpy at load, so
those are stubbed before import (same approach as test_complexity_treemap). We
then drive `main()` with `build_doc_graph` / `analyze_doc_staleness` / `render`
patched to capture the excludes they receive, proving the SVG path resolves
`.assess/config.toml` + `--exclude` and threads the union into both scans - so
the SVG and `lib.doc_graph` compute over the identical doc set.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "doc-graph-svg.py"


def _load_svg():
    for name in ("matplotlib", "matplotlib.pyplot", "numpy"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["matplotlib"].pyplot = sys.modules["matplotlib.pyplot"]
    spec = importlib.util.spec_from_file_location("doc_graph_svg", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def svg():
    return _load_svg()


class _FakeResult:
    available = True
    doc_count = 1
    graph = object()
    doc_to_code_edges: list = []
    entry_points: list = []
    unreachable: list = []
    orphans: list = []
    pagerank: dict = {}
    broken_links: list = []
    island_count = 0
    orphan_rate = 0.0
    reachability_pct = 0.0


def test_svg_threads_config_and_cli_excludes(svg, tmp_path, monkeypatch, capsys):
    # A repo with a durable config exclude plus an ad-hoc CLI exclude.
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        'exclude_dirs = ["_archive"]\nexclude_patterns = ["*.csv"]\n',
        encoding="utf-8",
    )

    captured: dict = {}

    def fake_build(root, *, extra_exclude_dirs=None, extra_exclude_patterns=None):
        captured["graph_dirs"] = extra_exclude_dirs
        captured["graph_patterns"] = extra_exclude_patterns
        return _FakeResult()

    def fake_staleness(root, *, doc_to_code_edges=None,
                       extra_exclude_dirs=None, extra_exclude_patterns=None):
        captured["stale_dirs"] = extra_exclude_dirs
        captured["stale_patterns"] = extra_exclude_patterns
        return {"docs": []}

    monkeypatch.setattr(svg, "build_doc_graph", fake_build)
    monkeypatch.setattr(svg, "analyze_doc_staleness", fake_staleness)
    monkeypatch.setattr(svg, "render", lambda *a, **k: None)
    monkeypatch.setattr(
        sys, "argv",
        ["doc-graph-svg.py", str(tmp_path), "-o", str(tmp_path / "out.svg"),
         "--exclude", "_jira", "--exclude", "*.tmp"],
    )

    assert svg.main() == 0

    # config dir + CLI dir merged; config glob + CLI glob merged.
    assert captured["graph_dirs"] == {"_archive", "_jira"}
    assert captured["graph_patterns"] == ["*.csv", "*.tmp"]
    # The staleness scan (drives the SVG's colour) gets the identical excludes,
    # so colour and structure speak about the same doc set.
    assert captured["stale_dirs"] == captured["graph_dirs"]
    assert captured["stale_patterns"] == captured["graph_patterns"]
