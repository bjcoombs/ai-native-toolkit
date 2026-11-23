"""Headless observability, doc_graph, doc_staleness and archetype blocks (#699)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import jsonschema
import pytest

import assess_signals
from lib import headless_static as hs
from lib import scan_registry as reg
from lib import signals
from lib.counts_only import counts_only_scans, reason_code
from lib.doc_staleness import DocStalenessRow, _safe_rel, staleness_summary
from lib.level_signals import load_default_levels
from lib.levels import evaluate
from lib.signals_schema import SCHEMA_PATH

BLOCKS = ("observability", "doc_graph", "doc_staleness", "archetype")
# Blocks still assembled only by a full run: #697 marks them full-run-only by this code.
FULL_RUN_ONLY = ("dead_code", "understanding", "runtime")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="surrogateescape", check=True)


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str = "c") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


def _init(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    return repo


@pytest.fixture
def instrumented(tmp_path: Path) -> Path:
    """A service with committed telemetry config and a runbook, plus a module doc."""
    repo = _init(tmp_path / "svc")
    _write(repo, "pyproject.toml", '[project]\nname = "svc"\ndependencies = ["opentelemetry-sdk"]\n')
    _write(repo, "docs/runbook.md", "# Runbook\n\nDashboards and logs for the service.\n")
    _write(repo, "pkg/__init__.py", "")
    _write(repo, "pkg/core.py", "def run():\n    return 1\n")
    _write(repo, "pkg/README.md", "# pkg\n\nSee [core](core.py).\n")
    _write(repo, "README.md", "# svc\n")
    _commit(repo)
    return repo


def _run(repo: Path, out: Path, *extra: str) -> dict[str, Any]:
    assert assess_signals.main([str(repo), "--out", str(out), *extra]) == 0
    doc: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return doc


def _row(scans: dict[str, Any], row_id: str) -> dict[str, Any]:
    result = evaluate(load_default_levels(), {"name": "g", "settings": None, "repos": {"r": scans}})
    return next(r for r in result["rows"] if r["id"] == row_id)


# -- the table ---------------------------------------------------------------

def test_the_signal_table_reports_the_four_static_blocks() -> None:
    reg.validate(signals.SIGNAL_SCANS)
    reported = {spec.key for spec in signals.SIGNAL_SCANS if signals._reported(spec)}
    assert set(BLOCKS) <= reported
    assert not set(BLOCKS) & set(signals.UNREGISTERED_SCANS)
    assert hs.__name__ not in signals.CORE_MODULES
    assert all(spec.fn.__module__ == hs.__name__ for spec in hs.HEADLESS_SCANS)


def test_the_costly_blocks_stay_not_registered(instrumented: Path, tmp_path: Path) -> None:
    assert set(FULL_RUN_ONLY) <= set(signals.UNREGISTERED_SCANS)
    doc = _run(instrumented, tmp_path / "o.json", "--scans", ",".join(FULL_RUN_ONLY),
               "--counts-only")
    for name in FULL_RUN_ONLY:
        assert doc["scans"][name] == {"available": False, "reason": "not_registered"}, name


# -- a whole run -------------------------------------------------------------

@pytest.mark.parametrize("counts_only", [False, True])
def test_a_headless_run_reports_the_static_blocks(
    instrumented: Path, tmp_path: Path, counts_only: bool,
) -> None:
    doc = _run(instrumented, tmp_path / "o.json", "--scans", ",".join(BLOCKS),
               *(["--counts-only"] if counts_only else []))
    jsonschema.validate(doc, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    scans = doc["scans"]
    for name in BLOCKS:
        assert scans[name].get("available") is not False, (name, scans[name])
        assert reason_code(scans[name].get("reason")) != "not_registered", name
    assert scans["observability"]["rung"] >= 2   # instrumented + discoverable
    assert scans["archetype"]["archetype"] == "software"
    assert scans["doc_graph"]["doc_count"] >= 1
    assert set(scans["doc_staleness"]["summary"]) == {
        "measured_docs", "stale_docs", "stale_pct", "generator_directive_docs"}


def test_layer_one_gets_a_level_from_committed_telemetry(instrumented: Path, tmp_path: Path) -> None:
    doc = _run(instrumented, tmp_path / "o.json", "--scans", "observability")
    row = _row(doc["scans"], "layer-1-liveness")
    assert (row["status"], row["level"]) == ("level", 1)


def test_an_uninstrumented_repository_is_level_zero_not_unknown(tmp_path: Path) -> None:
    repo = _init(tmp_path / "plain")
    _write(repo, "main.py", "print(1)\n")
    _commit(repo)
    doc = _run(repo, tmp_path / "o.json", "--scans", "observability")
    assert doc["scans"]["observability"]["rung"] == 0
    assert _row(doc["scans"], "layer-1-liveness")["level"] == 0


# -- failure markers ---------------------------------------------------------

def test_doc_staleness_outside_git_is_not_git_root(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
    block = hs.doc_staleness(tmp_path, {"available": True}, (set(), []))
    assert set(block) == {"available", "reason"}
    assert reason_code(block["reason"]) == "not_git_root"
    kept = counts_only_scans({"doc_staleness": block})
    assert kept["doc_staleness"] == {"available": False, "reason": "not_git_root"}
    assert _row({"doc_staleness": block}, "doc-freshness")["status"] == "unknown"


@pytest.mark.parametrize(("name", "target", "call"), [
    ("observability", "scan_observability", lambda root: hs.observability(root, (set(), []))),
    ("doc_graph", "build_doc_graph", lambda root: hs.doc_graph(root, (set(), []))),
    ("doc_staleness", "analyze_doc_staleness",
     lambda root: hs.doc_staleness(root, {}, (set(), []))),
    ("archetype", "analyze_archetype", hs.archetype),
])
def test_a_raising_scan_is_a_named_marker(
    instrumented: Path, monkeypatch: pytest.MonkeyPatch, name: str, target: str, call: Any,
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("planted")

    monkeypatch.setattr(hs, target, boom)
    block = call(instrumented)
    assert set(block) == {"available", "reason"}, name
    assert reason_code(block["reason"]) == "scan_failed"
    assert "planted" in block["reason"]


def test_doc_graph_without_networkx_is_no_networkx(
    instrumented: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Unavailable:
        def as_dict(self) -> dict[str, Any]:
            return {"available": False, "reason": "networkx not installed; doc link-graph not assessed",
                    "doc_count": 0}

    monkeypatch.setattr(hs, "build_doc_graph", lambda *_a, **_k: Unavailable())
    block = hs.doc_graph(instrumented, (set(), []))
    assert set(block) == {"available", "reason"}
    assert reason_code(block["reason"]) == "no_networkx"


# -- the summary and the doc-freshness row ------------------------------------

def _doc(method: str, ratio: float) -> DocStalenessRow:
    return {"path": "d.md", "last_commit_days": 1, "doc_churn_in_window": 1,
            "code_churn_in_window": 1, "subject_code_count": 1, "subject_method": method,
            "subject_roots": [], "base_doc_dir": None, "subject_paths": [], "ratio": ratio,
            "window_ratio": ratio, "code_churn_since_doc_change": 0, "confidence": "high"}


def test_the_summary_counts_mapped_docs_only_and_rounds_up() -> None:
    rows = [_doc("nearest-ancestor", 2.5), _doc("nearest-ancestor", 2.0),
            _doc("explicit-link", 0.0), _doc("repo-baseline", 90.0)]
    assert staleness_summary(rows, reliable=True) == {
        "measured_docs": 3, "stale_docs": 1, "stale_pct": 34,   # 33.3% rounds up
        "generator_directive_docs": 0}


@pytest.mark.parametrize(("rows", "reliable"), [
    ([_doc("nearest-ancestor", 5.0)], False),   # truncated or degenerate history
    ([_doc("repo-baseline", 5.0)], True),       # no doc with a subject of its own
    ([], True),
])
def test_an_unmeasurable_summary_is_null(rows: list[DocStalenessRow], reliable: bool) -> None:
    summary = staleness_summary(rows, reliable=reliable)
    assert (summary["stale_docs"], summary["stale_pct"]) == (None, None)
    row = _row({"doc_staleness": {"available": True, "summary": summary}}, "doc-freshness")
    assert (row["status"], row["level"]) == ("unknown", None)


@pytest.mark.parametrize(("pct", "level"), [(0, 2), (20, 2), (21, 1), (50, 1), (51, 0)])
def test_the_doc_freshness_thresholds(pct: int, level: int) -> None:
    scans = {"doc_staleness": {"available": True,
                               "summary": {"measured_docs": 100, "stale_docs": pct, "stale_pct": pct}}}
    assert _row(scans, "doc-freshness")["level"] == level


def test_a_shallow_clone_reads_doc_freshness_unknown(instrumented: Path, tmp_path: Path) -> None:
    for n in range(3):
        _write(instrumented, "pkg/core.py", f"def run():\n    return {n}\n")
        _commit(instrumented, f"c{n}")
    clone = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{instrumented}", str(clone)],
                   capture_output=True, check=True)
    doc = _run(clone, tmp_path / "o.json", "--scans", "doc_staleness")
    block = doc["scans"]["doc_staleness"]
    assert block["history_truncated"] is True
    assert block["summary"]["stale_pct"] is None
    assert _row(doc["scans"], "doc-freshness")["status"] == "unknown"


def test_doc_paths_are_posix(tmp_path: Path) -> None:
    assert _safe_rel(tmp_path / "a" / "b.md", tmp_path) == "a/b.md"


def test_a_generated_doc_with_a_source_verdict_is_measured() -> None:
    generated = _doc("repo-baseline", 0.0)
    generated["provenance"] = {"method": "frontmatter", "sources": ["api.py"],
                               "generated_by": None, "source_newer": True}
    summary = staleness_summary([generated, _doc("repo-baseline", 50.0)], reliable=True)
    assert summary == {"measured_docs": 1, "stale_docs": 1, "stale_pct": 100,
                       "generator_directive_docs": 0}


def test_a_directory_nested_in_a_checkout_is_not_git_root(instrumented: Path) -> None:
    assert hs.is_git_work_tree(instrumented)
    nested = instrumented / "pkg"
    assert not hs.is_git_work_tree(nested)
    assert reason_code(hs.doc_staleness(nested, {}, (set(), []))["reason"]) == "not_git_root"
