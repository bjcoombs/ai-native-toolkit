"""Unit tests for the pure logic inside complexity-treemap.py.

The script imports lizard/matplotlib/numpy at module load (it's a CLI wrapper),
so those are stubbed in sys.modules before import. We only exercise functions
that don't touch the real heavy deps. This module holds the stubbed loader,
the build-artifact filter, the plugin version stamp, the render summary and
main's CLI paths; the sibling test_complexity_treemap_*.py modules hold the
rest by concern, and version assertions live in
test_complexity_treemap_schema_versions.py.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "complexity-treemap.py"


class _StubNumpy(types.ModuleType):
    """Minimal numpy: only percentile, with numpy's default linear interp."""

    @staticmethod
    def percentile(values, q):
        s = sorted(values)
        if not s:
            return 0.0
        k = (len(s) - 1) * q / 100.0
        f = int(k)
        c = min(f + 1, len(s) - 1)
        return float(s[f] + (s[c] - s[f]) * (k - f))


def _load_treemap():
    """Import complexity-treemap.py with heavy deps stubbed out."""
    for name in ("lizard", "matplotlib", "matplotlib.pyplot", "squarify"):
        sys.modules.setdefault(name, types.ModuleType(name))
    # Another file's loader may have left a bare numpy stub in sys.modules
    # (test_scope, test_doc_graph_svg); this one needs percentile (#831).
    if not hasattr(sys.modules.get("numpy"), "percentile"):
        sys.modules["numpy"] = _StubNumpy("numpy")
    # complexity-treemap does `import matplotlib.pyplot as plt`
    sys.modules["matplotlib"].pyplot = sys.modules["matplotlib.pyplot"]
    spec = importlib.util.spec_from_file_location("complexity_treemap", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def treemap():
    return _load_treemap()


@pytest.fixture(scope="module")
def render_lib(treemap):
    """The shared treemap_render module. Depends on `treemap` so the scripts
    dir is on sys.path and numpy is stubbed before import."""
    import importlib

    return importlib.import_module("lib.treemap_render")


@pytest.mark.parametrize("name", [
    "canvaskit.js",
    "canvaskit/chromium/canvaskit.js",  # nested, basename still matches
    "skwasm.js",
    "skwasm_heavy.js",
    "main.dart.js",                     # pre-existing Flutter artifact
])
def test_flutter_runtime_bundles_are_filtered(treemap, name):
    assert treemap._is_build_artifact(Path(name)) is True


@pytest.mark.parametrize("name", ["app.js", "widget.dart", "canvaskit_helper.dart"])
def test_real_source_is_not_filtered(treemap, name):
    assert treemap._is_build_artifact(Path(name)) is False


def test_plugin_version_is_stamped_from_plugin_json(treemap):
    # Resolves the real .claude-plugin/plugin.json three dirs up; must be a real
    # version string, never the "unknown" fallback.
    version = treemap._read_plugin_version()
    assert version != "unknown"
    assert version[0].isdigit()


# --- Characterization: render's console summary and main's CLI paths -------
# Pin the branches of `render` and `main` so splitting them into named steps
# cannot change what they print, pass on, or reject.


def _stub_render_io(treemap, monkeypatch):
    """Stub the colour map, squarify layout and SVG writer; capture writer args."""
    captured: dict = {}
    monkeypatch.setattr(treemap.plt, "get_cmap",
                        lambda _name: (lambda v: (1.0, 1.0 - v, 0.0, 1.0)), raising=False)
    monkeypatch.setattr(treemap, "layout", lambda *a, **k: None)

    def fake_write_svg(rects, root, W, H, out_path, show_labels, metric_label,
                       show_survivor_legend=False):
        captured.update(show_labels=show_labels, metric_label=metric_label,
                        show_survivor_legend=show_survivor_legend)

    monkeypatch.setattr(treemap, "write_svg", fake_write_svg)
    return captured


def _two_files(tmp_path):
    a = tmp_path / "a.py"
    a.write_text("x = 1\n" * 40, encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text("y = 2\n" * 4, encoding="utf-8")
    return [(a, 40, 12.0, "lizard"), (b, 4, 3.0, "scc")]


def test_render_prints_hue_saturation_and_biggest_files(treemap, tmp_path, monkeypatch, capsys):
    captured = _stub_render_io(treemap, monkeypatch)
    files = _two_files(tmp_path)
    aux = {files[0][0]: 7, files[1][0]: 1}
    treemap.render(files, tmp_path, tmp_path / "o.svg", "t", show_labels=True,
                   aux_data=aux, aux_label="commits (last 12mo)",
                   survivor_density={files[0][0]: 0.6})
    out = capsys.readouterr().out
    assert "(2 files, 1 lizard, 1 scc)" in out
    assert "hue: ccn; range 0-12; cap 12 (max)" in out
    assert "saturation: commits (last 12mo); range 0-7; cap 7 (max)" in out
    assert "biggest files (estimated tokens dominate layout):" in out
    assert "commits (last 12mo)    7  [lizard]  a.py" in out
    assert captured == {"show_labels": True, "metric_label": "ccn",
                        "show_survivor_legend": True}


def test_render_degenerate_churn_drops_saturation(treemap, tmp_path, monkeypatch, capsys):
    captured = _stub_render_io(treemap, monkeypatch)
    files = _two_files(tmp_path)
    aux = {files[0][0]: 1, files[1][0]: 1}
    treemap.render(files, tmp_path, tmp_path / "o.svg", "t", by="churn",
                   aux_data=aux, aux_label="commits", churn_degenerate=True)
    out = capsys.readouterr().out
    assert "hue: commits;" in out
    assert "saturation: churn signal flat" in out
    assert "saturation: commits;" not in out
    # The aux column is dropped from the biggest-files list with the axis.
    rows = [ln for ln in out.splitlines() if "est.tok" in ln]
    assert len(rows) == 2
    assert all(row.count("commits") == 1 for row in rows)
    assert captured["show_survivor_legend"] is False


def test_main_rejects_non_directory(treemap, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["complexity-treemap.py", str(tmp_path / "nope")])
    assert treemap.main() == 1
    assert "is not a directory" in capsys.readouterr().err


def test_main_rejects_missing_scope(treemap, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv",
                        ["complexity-treemap.py", str(tmp_path), "--scope", "absent"])
    assert treemap.main() == 1
    assert "error: scope path" in capsys.readouterr().err


def test_main_rejects_scope_outside_root(treemap, monkeypatch, tmp_path, capsys):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(sys, "argv",
                        ["complexity-treemap.py", str(root), "--scope", str(tmp_path)])
    assert treemap.main() == 1
    assert "is not under" in capsys.readouterr().err


def test_main_no_files_under_scope_names_it(treemap, monkeypatch, tmp_path, capsys):
    (tmp_path / "sub").mkdir()
    monkeypatch.setattr(treemap, "collect",
                        lambda *a, **k: ([], "complexity", None, None, {}))
    monkeypatch.setattr(sys, "argv",
                        ["complexity-treemap.py", str(tmp_path), "--scope", "sub"])
    assert treemap.main() == 1
    err = capsys.readouterr().err
    assert "no scoreable files found under" in err
    assert "excluded as generated" not in err


def test_main_with_no_files_writes_a_zero_file_sidecar(treemap, monkeypatch, tmp_path):
    """#959: with scc on PATH (it reads every language) a tree with nothing to score is a
    measured empty set. The sidecar is written (files_scored 0), so the run reads
    no_scored_files, never a missing sidecar (no_complexity_stats)."""
    from lib import level_tally, run_setup
    from lib.run_scope import load_current_stats

    monkeypatch.setattr(treemap, "collect", lambda *a, **k: ([], "hotspot", {}, "commits", {}))
    out = tmp_path / ".assess" / "complexity-stats.json"
    out.parent.mkdir()
    monkeypatch.setattr(sys, "argv", ["complexity-treemap.py", str(tmp_path), "--stats", str(out)])
    # Without scc nothing outside lizard's languages is read: zero files is not a
    # measurement, so no sidecar is written and the run reads no_complexity_stats.
    monkeypatch.setattr(treemap.shutil, "which", lambda name: None)
    assert treemap.main() == 1 and not out.exists()
    monkeypatch.setattr(treemap.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert treemap.main() == 1  # still an error exit: no treemap is drawn
    current = load_current_stats(out.parent)
    assert current["files_scored"] == 0 and "available" not in current
    stats = run_setup.run_header(
        "id", "2026-11-22", (None, None, ""), {"available": False}, current, None,
        ({}, None, [], [], {}, {}), run_setup.StatsDiff(), (set(), []))["stats_summary"]
    signals = {"stats_summary": stats, "structure_drift": {"tier_0": {"boundary_patterns": 2}}}
    result = level_tally.level_result(level_tally.default_model(), signals)
    reasons = {c["id"]: c.get("reason") for c in result["conditions"]}
    assert reasons["layer-4-architecture.2"] == "no_scored_files"


@pytest.mark.parametrize("scope, out_name, title_suffix", [
    (None, "hotspot-{root}.svg", ""),
    ("sub/deep", "hotspot-{root}-sub-deep.svg", "/sub/deep"),
])
def test_main_threads_scope_into_output_name_title_and_stats(
        treemap, monkeypatch, tmp_path, scope, out_name, title_suffix):
    root = tmp_path / "repo"
    (root / "sub" / "deep").mkdir(parents=True)
    files = _two_files(root)
    aux = {files[0][0]: 3, files[1][0]: 1}
    seen: dict = {}
    monkeypatch.setattr(treemap, "collect",
                        lambda *a, **k: (files, "hotspot", aux, "commits", {"fn": 1}))
    monkeypatch.setattr(treemap, "render",
                        lambda f, r, out, title, **k: seen.update(out=out, title=title, **k))
    monkeypatch.setattr(treemap, "write_stats",
                        lambda *a, **k: seen.update(stats_path=a[4], stats_kwargs=k))
    monkeypatch.chdir(tmp_path)
    argv = ["complexity-treemap.py", str(root), "--labels", "--stats", "s.json"]
    if scope:
        argv += ["--scope", scope]
    monkeypatch.setattr(sys, "argv", argv)
    assert treemap.main() == 0
    assert seen["out"] == Path(out_name.format(root=root.name))
    assert seen["title"] == f"Hotspot: {root.name}{title_suffix}"
    assert seen["show_labels"] is True
    assert seen["by"] == "hotspot"
    assert seen["aux_data"] is aux
    assert seen["survivor_density"] == {}
    assert seen["churn_degenerate"] is False
    assert seen["stats_path"] == Path("s.json")
    assert seen["stats_kwargs"]["fn_ccn_by_path"] == {"fn": 1}
    assert seen["stats_kwargs"]["tokens_by_path"] is seen["tokens_by_path"]


@pytest.mark.parametrize("n, expected", [
    (0, "files"), (1, "file"), (2, "files"),
])
def test_plural_agrees_with_count(render_lib, n, expected):
    assert render_lib.plural(n, "file") == expected


@pytest.mark.parametrize("n", [0.6, 1.0, True])
def test_plural_rejects_a_non_int_count(render_lib, n):
    """A float can print as "1" yet compare unequal to 1, so plural() takes the
    rounded int the caller displays and refuses anything else."""
    with pytest.raises(TypeError):
        render_lib.plural(n, "line")


def test_plural_takes_an_irregular_form(render_lib):
    assert render_lib.plural(2, "child", "children") == "children"
    assert render_lib.plural(1, "child", "children") == "child"


@pytest.mark.parametrize("tokens, text", [
    (1, "1 est. token · 100 loc"),
    (1200, "1,200 est. tokens · 100 loc"),
])
def test_write_svg_token_tooltip_and_label_pluralise(render_lib, tmp_path, tokens, text):
    """The est-token count in the tooltip and the on-block label agrees with
    its number."""
    node = render_lib.Node(name="a.py", rel_path="a.py", loc=100,
                           metric=5.0, color=(0.8, 0.2, 0.1, 1.0),
                           is_file=True, est_tokens=tokens)
    rects = [(0.0, 0.0, 1600.0, 1000.0, node)]
    out = tmp_path / "tok.svg"
    render_lib.write_svg(rects, Path("/repo"), 1600.0, 1000.0, out, True, "ccn")
    svg = out.read_text(encoding="utf-8")
    assert text in svg
    label = text.split(" · ")[0]
    assert f">{label}</text>" in svg
    assert "—" not in svg


def test_render_summary_singular_file(treemap, tmp_path, monkeypatch, capsys):
    _stub_render_io(treemap, monkeypatch)
    files = _two_files(tmp_path)[:1]
    treemap.render(files, tmp_path, tmp_path / "o.svg", "t")
    assert "(1 file, 1 lizard, 0 scc)" in capsys.readouterr().out
