"""Side effects of adding scc to the treemap and its bands (#1232, #1234, #1235).

- #1232: scc scores a committed SVG as XML, so a large diagram took the biggest
  treemap blocks. ``*.svg`` is a default exclude; ``--include-artifacts`` keeps it.
- #1234: the top lists rank every scored file, so scc's extra files pushed code
  files out of them and cleared ``self_referential_tests``. The finding reads
  ``code_band``, ranked over the files a per-function backend scored.
- #1235: one Python file falling back to scc made ``backend_by_language`` read
  Python as null. Partial coverage now reads as the backend, with the fallback
  files counted in ``file_level_by_language``.
"""
from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path

import pytest
from test_complexity_treemap import stubbed_treemap

from lib.change_coupling import parse_commit_file_sets
from lib.complexity.scc_coverage import backend_by_language, file_level_by_language
from lib.complexity.stats_rows import code_band, code_band_paths
from lib.keyhole.keyhole_signals import _self_referential_test_paths


@pytest.fixture(scope="module")
def treemap():
    with stubbed_treemap() as mod:
        yield mod


def _stub_scc(treemap, monkeypatch, blocks: list[tuple[str, list[tuple[Path, int, int]]]]) -> None:
    """Make ``scc_scores`` read ``blocks`` (language, [(path, code, complexity)])."""
    payload = json.dumps([
        {"Name": lang, "Files": [{"Location": str(p), "Code": code, "Complexity": cx}
                                 for p, code, cx in files]}
        for lang, files in blocks
    ])
    monkeypatch.setattr(treemap.shutil, "which", lambda _: "/usr/bin/scc")
    monkeypatch.setattr(treemap.subprocess, "run",
                        lambda *a, **k: sp.CompletedProcess(a, 0, stdout=payload))


# --- #1232: committed SVG images stay out of the treemap ---------------------

@pytest.mark.parametrize("rel", ["docs/example-heatmap-after.svg", "logo.svg", "a/b/Diagram.svg"])
def test_svg_is_a_default_exclude(treemap, rel):
    assert treemap._is_build_artifact(Path(rel)) is True


def test_large_committed_svg_stays_out_of_the_treemap(treemap, tmp_path, monkeypatch):
    """A large SVG that scc scores as code never reaches the scored files or the
    stats sidecar by default; --include-artifacts keeps it."""
    tmp_path = tmp_path.resolve()
    svg = tmp_path / "docs" / "example-heatmap-after.svg"
    svg.parent.mkdir()
    svg.write_text("<svg>\n" + "<rect/>\n" * 20000 + "</svg>\n", encoding="utf-8")
    app = tmp_path / "src" / "app.py"
    app.parent.mkdir()
    app.write_text("def f(x):\n    return 1 if x else 2\n", encoding="utf-8")
    monkeypatch.setattr(treemap, "lizard_scores",
                        lambda root, **kw: {app.resolve(): (2, 2.0, [2.0])})
    _stub_scc(treemap, monkeypatch, [("SVG", [(svg, 20002, 0)]), ("Python", [(app, 2, 1)])])

    files, *_rest = treemap.collect(tmp_path, by="complexity")
    assert [f[0] for f in files] == [app.resolve()]
    out = tmp_path / "stats.json"
    treemap.write_stats(files, None, None, tmp_path, out, fn_ccn_by_path={app.resolve(): [2.0]})
    stats = json.loads(out.read_text(encoding="utf-8"))
    assert [r["path"] for r in stats["top_large"]] == ["src/app.py"]
    assert [r["path"] for r in stats["files"]] == ["src/app.py"]

    kept, *_rest = treemap.collect(tmp_path, by="complexity", include_artifacts=True)
    assert svg.resolve() in {f[0] for f in kept}


# --- #1234: the code band does not move when scc adds files -------------------

def _scored_repo(git_repo) -> tuple[Path, Path, list[Path]]:
    """A repo where src/target.py and its co-located test were committed
    together, beside twelve large, complex shell scripts lizard cannot read."""
    repo, commit = git_repo
    repo = repo.resolve()
    target = repo / "src" / "target.py"
    target.parent.mkdir()
    target.write_text("def f(x):\n    return 1 if x else 2\n", encoding="utf-8")
    (repo / "src" / "test_target.py").write_text(
        "from target import f\n\ndef test_f():\n    assert f(1) == 1\n", encoding="utf-8")
    commit("add target and its test")
    scripts = []
    for i in range(12):
        sh = repo / "ops" / f"deploy{i:02d}.sh"
        sh.parent.mkdir(exist_ok=True)
        sh.write_text("if true; then echo x; fi\n" * 400, encoding="utf-8")
        scripts.append(sh)
    commit("add ops scripts")
    return repo, target, scripts


def _stats(treemap, tmp_path: Path, repo: Path, name: str) -> dict:
    files, _by, _aux, _label, fn_ccn = treemap.collect(repo, by="complexity")
    out = tmp_path / name
    treemap.write_stats(files, None, None, repo, out, fn_ccn_by_path=fn_ccn)
    return json.loads(out.read_text(encoding="utf-8"))


def test_self_referential_tests_same_with_and_without_scc(treemap, tmp_path, monkeypatch, git_repo):
    repo, target, scripts = _scored_repo(git_repo)
    test = repo / "src" / "test_target.py"
    lizard = {target.resolve(): (2, 2.0, [2.0]), test.resolve(): (3, 1.0, [1.0])}
    monkeypatch.setattr(treemap, "lizard_scores", lambda root, **kw: dict(lizard))
    commit_sets = parse_commit_file_sets(repo)

    monkeypatch.setattr(treemap.shutil, "which", lambda _: None)
    without = _stats(treemap, tmp_path, repo, "lizard-only.json")
    _stub_scc(treemap, monkeypatch, [("Shell", [(s, 400, 40) for s in scripts]),
                                     ("Python", [(target, 2, 1), (test, 3, 0)])])
    with_scc = _stats(treemap, tmp_path, repo, "with-scc.json")

    # The full lists lose the code file to the scripts: the shift #1234 reported.
    full = {r["path"] for k in ("top_hotspots", "top_complex", "top_large") for r in with_scc[k]}
    assert "src/target.py" not in full
    assert with_scc["files_scored"] == without["files_scored"] + len(scripts)
    assert with_scc["code_band"] == without["code_band"] == {
        "population": 2, "paths": ["src/target.py", "src/test_target.py"]}
    assert (_self_referential_test_paths(repo, without, commit_sets)
            == _self_referential_test_paths(repo, with_scc, commit_sets)
            == ["src/target.py"])


def test_older_sidecar_without_code_band_reads_the_full_lists(git_repo):
    repo, _target, _scripts = _scored_repo(git_repo)
    stats = {"top_large": [{"path": "src/target.py"}]}
    assert code_band_paths(stats) is None
    assert _self_referential_test_paths(repo, stats, parse_commit_file_sets(repo)) == ["src/target.py"]


@pytest.mark.parametrize("stats", [{}, {"code_band": None}, {"code_band": {"paths": "x"}}])
def test_code_band_paths_none_when_absent_or_malformed(stats):
    assert code_band_paths(stats) is None


def test_code_band_ranks_only_code_rows():
    rows = [{"path": f"doc{i}.md", "loc": 900, "ccn": 0.0, "_score": 50.0} for i in range(12)]
    rows.append({"path": "a.py", "loc": 5, "ccn": 1.0, "_score": 1.0})
    band = code_band(rows, {"a.py"})  # type: ignore[arg-type]
    assert band == {"population": 1, "paths": ["a.py"]}
    assert code_band_paths({"code_band": band}) == ["a.py"]


# --- #1235: partial per-function coverage reads as the backend ----------------

def test_partial_coverage_names_the_backend_and_counts_fallback_files():
    root = Path("/repo")
    a, b, c = root / "a.py", root / "bin" / "tool", root / "run.sh"
    flat = root / "bin" / "flat"
    files = [(a, 10, 2.0, "lizard"), (b, 10, 3.0, "scc"), (flat, 10, 0.0, "scc"), (c, 10, 2.0, "scc")]
    langs = {a: "Python", b: "Python", flat: "Python", c: "Shell"}
    backends = {a: "lizard"}
    assert backend_by_language(files, langs, backends) == {"Python": "lizard", "Shell": None}
    # A fallback file with no decision points loses no per-function figure.
    assert file_level_by_language(files, langs, backends) == {"Python": 1, "Shell": 1}


def test_snapshot_names_lizard_for_python_and_counts_fallback(treemap, tmp_path):
    """The stats sidecar the snapshot commits: Python maps to lizard although one
    shebang script fell back to scc, and the fallback is counted."""
    py, script = tmp_path / "app.py", tmp_path / "bin" / "tool"
    out = tmp_path / "stats.json"
    treemap.write_stats([(py, 20, 8.0, "lizard"), (script, 30, 4.0, "scc")], None, None,
                        tmp_path, out, fn_ccn_by_path={py: [8.0]},
                        languages_by_path={py: "Python", script: "Python"})
    fn = json.loads(out.read_text(encoding="utf-8"))["fn_ccn"]
    assert fn["backend_by_language"] == {"Python": "lizard"}
    assert fn["file_level_by_language"] == {"Python": 1}
