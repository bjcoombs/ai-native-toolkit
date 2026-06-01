"""Unit tests for the pure logic inside complexity-treemap.py.

The script imports lizard/matplotlib/numpy at module load (it's a CLI wrapper),
so those are stubbed in sys.modules before import. We only exercise functions
that don't touch the real heavy deps: the build-artifact filter, the plugin
version stamp, and the stats-sidecar enrichment (field naming + hotspot rank).
"""
from __future__ import annotations

import importlib.util
import json
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
    sys.modules.setdefault("numpy", _StubNumpy("numpy"))
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


def test_write_stats_uses_commits_field_and_balanced_rank(treemap, tmp_path):
    """The per-file churn count is emitted as `commits` (what consumers read),
    and the balanced composite ranks a moderately-complex active file above a
    very-complex frozen one (issue #47, observation 2 + 5)."""
    root = tmp_path
    frozen_complex = root / "frozen.go"   # high ccn, barely touched
    active_moderate = root / "active.go"  # moderate ccn, churning
    files = [
        (frozen_complex, 800, 1396.0, "lizard"),
        (active_moderate, 300, 140.0, "lizard"),
    ]
    aux_data = {frozen_complex: 1, active_moderate: 45}
    out = root / "stats.json"
    treemap.write_stats(files, aux_data, "commits (last 12mo)", root, out)

    stats = json.loads(out.read_text())
    assert "plugin_version" in stats
    hotspots = stats["top_hotspots"]
    # Balanced composite: the active moderate-complexity file leads.
    assert hotspots[0]["path"] == "active.go"
    # Field is `commits`, not the legacy `churn`.
    for h in hotspots:
        assert "commits" in h
        assert "churn" not in h
    by_path = {h["path"]: h for h in hotspots}
    assert by_path["active.go"]["commits"] == 45
    assert by_path["frozen.go"]["commits"] == 1


def test_write_stats_commits_none_without_git(treemap, tmp_path):
    """No churn data (no git) -> commits is None, distinct from a real 0."""
    root = tmp_path
    f = root / "a.go"
    out = root / "stats.json"
    treemap.write_stats([(f, 100, 5.0, "lizard")], None, None, root, out)
    stats = json.loads(out.read_text())
    assert stats["top_hotspots"][0]["commits"] is None


def test_write_stats_separates_aggregate_from_per_function_ccn(treemap, tmp_path):
    """Issue #58: the file-level aggregate ccn (sum of per-function complexity)
    must be labelled as an aggregate and never conflated with the per-function
    value a linter threshold gates. A file summing to ccn 136 whose worst
    single function is only 13 is NOT a per-function violation."""
    root = tmp_path
    f = root / "service_modules.go"   # the issue's actual offender shape
    out = root / "stats.json"
    # 13 functions whose complexities sum to 136 (the reported aggregate),
    # worst single function = 13 (under a cyclop:15 threshold).
    fn_ccns = [13.0, 13.0, 12.0, 12.0, 11.0, 11.0, 10.0, 10.0,
               9.0, 9.0, 8.0, 8.0, 10.0]
    assert sum(fn_ccns) == 136.0
    treemap.write_stats(
        [(f, 800, 136.0, "lizard")], {f: 3}, "commits (last 12mo)", root, out,
        fn_ccn_by_path={f: fn_ccns},
    )
    stats = json.loads(out.read_text())

    # The aggregate block self-labels and the per-function block is separate.
    assert stats["ccn"]["basis"] == "file-aggregate"
    assert stats["ccn"]["max"] == 136.0
    assert stats["fn_ccn"]["basis"] == "per-function"
    assert stats["fn_ccn"]["function_count"] == 13
    assert stats["fn_ccn"]["max"] == 13.0   # worst function, not the sum

    row = stats["top_complex"][0]
    assert row["ccn"] == 136.0              # aggregate preserved for the hue
    assert row["ccn_basis"] == "file-aggregate"
    assert row["max_fn_ccn"] == 13.0        # the per-function truth for Layer 3


def test_write_stats_scc_file_has_null_max_fn_ccn(treemap, tmp_path):
    """scc reports file-level complexity with no function breakdown, so a
    scc-scored file carries max_fn_ccn=null - the report must not invent a
    per-function value it never measured."""
    root = tmp_path
    f = root / "report.sql"
    out = root / "stats.json"
    # No fn_ccn_by_path entry for this path -> scc-style, per-function unknown.
    treemap.write_stats([(f, 400, 50.0, "scc")], None, None, root, out,
                        fn_ccn_by_path={})
    stats = json.loads(out.read_text())
    assert stats["top_complex"][0]["max_fn_ccn"] is None
    assert stats["fn_ccn"]["function_count"] == 0


def test_hotspot_rank_favours_per_function_offender(treemap, tmp_path):
    """Issue #115: for a class-per-file language the hotspot composite must rank
    on the worst single function, not the file aggregate, so a broad coordinator
    class can't bury a genuinely complex single method.

    Real shape from the first Java/JVM run: a coordinator at aggregate ccn 107
    whose worst method is only 14 (not a violation) out-ranked a DAO at ccn 28
    that is one complex method. With equal churn the DAO must now lead."""
    root = tmp_path
    coordinator = root / "Coordinator.java"  # broad: ccn 107, worst method 14
    dao = root / "Dao.java"                   # one genuinely complex method
    files = [
        (coordinator, 600, 107.0, "lizard"),
        (dao, 200, 28.0, "lizard"),
    ]
    # Many small methods summing to 107, worst single = 14.
    coordinator_fns = [14.0, 13.0, 12.0, 11.0, 10.0, 10.0, 9.0,
                       9.0, 8.0, 6.0, 5.0]
    assert sum(coordinator_fns) == 107.0
    dao_fns = [28.0]  # the single complex method is the whole file's ccn
    aux_data = {coordinator: 5, dao: 5}  # equal churn isolates the ccn re-weight
    out = root / "stats.json"
    treemap.write_stats(
        files, aux_data, "commits (last 12mo)", root, out,
        fn_ccn_by_path={coordinator: coordinator_fns, dao: dao_fns},
    )
    stats = json.loads(out.read_text())
    hotspots = stats["top_hotspots"]

    # The true per-function offender ranks at or above the coordinator class.
    assert hotspots[0]["path"] == "Dao.java"
    # The aggregate is still reported faithfully - only the ranking changed.
    by_path = {h["path"]: h for h in hotspots}
    assert by_path["Coordinator.java"]["ccn"] == 107.0
    assert by_path["Coordinator.java"]["max_fn_ccn"] == 14.0
    # The complexity-only rank (treemap hue) stays aggregate-driven.
    assert stats["top_complex"][0]["path"] == "Coordinator.java"


def test_hotspot_rank_unchanged_for_single_function_per_file(treemap, tmp_path):
    """Must-not-regress guard (issue #115): for single-function-per-file
    languages (Python/Go) the aggregate is the worst function, so the
    per-function re-weight is a no-op and the existing ranking is preserved -
    the more-complex-and-equally-churned file still leads."""
    root = tmp_path
    complex_go = root / "complex.go"   # one big function, ccn 40
    simple_go = root / "simple.go"     # one small function, ccn 8
    files = [
        (complex_go, 300, 40.0, "lizard"),
        (simple_go, 120, 8.0, "lizard"),
    ]
    # aggregate == worst function: the per-function weight collapses to aggregate.
    aux_data = {complex_go: 10, simple_go: 10}
    out = root / "stats.json"
    treemap.write_stats(
        files, aux_data, "commits (last 12mo)", root, out,
        fn_ccn_by_path={complex_go: [40.0], simple_go: [8.0]},
    )
    stats = json.loads(out.read_text())
    hotspots = stats["top_hotspots"]

    # Ranking is unchanged: the genuinely complex file still leads.
    assert hotspots[0]["path"] == "complex.go"
    # And it matches the aggregate-only rank - no per-function divergence here.
    assert stats["top_complex"][0]["path"] == "complex.go"


def test_effective_ccn_collapses_to_aggregate_without_per_function_data(treemap):
    """`_effective_ccn` returns the raw aggregate when there is no per-function
    signal (scc files: max_fn_ccn is None), and when the worst function already
    equals the aggregate (single-function file) - the two no-regression paths."""
    assert treemap._effective_ccn(50.0, None) == 50.0   # scc: no breakdown
    assert abs(treemap._effective_ccn(40.0, 40.0) - 40.0) < 1e-9  # single fn
    # A coordinator (aggregate >> worst fn) is pulled below its aggregate but
    # never below the worst function itself.
    eff = treemap._effective_ccn(107.0, 14.0)
    assert 14.0 < eff < 107.0


def test_assess_dir_is_self_excluded_by_default(treemap):
    """A prior run's run-context.json must not be scored on the next run -
    the script's own output directory is in EXCLUDE_DIRS. Otherwise re-runs
    pollute the heatmap with their own past output (issue #50 bonus)."""
    assert ".assess" in treemap.EXCLUDE_DIRS


def test_is_user_excluded_matches_dir_name(treemap):
    """A plain dir name in the user excludes filters every file under it,
    at any depth."""
    extra_dirs = {"regulatory-raw"}
    assert treemap._is_user_excluded(
        Path("regulatory-raw/2024-Q1/data.csv"), extra_dirs, []
    ) is True
    assert treemap._is_user_excluded(
        Path("src/data/sub/regulatory-raw/file.txt"), extra_dirs, []
    ) is True
    # A different directory must not be filtered.
    assert treemap._is_user_excluded(
        Path("src/data/file.txt"), extra_dirs, []
    ) is False


def test_is_user_excluded_matches_glob_pattern(treemap):
    """A glob pattern matches by basename, not by full path."""
    extra_patterns = ["*.csv", "seed-*.json"]
    assert treemap._is_user_excluded(
        Path("data/reference.csv"), set(), extra_patterns
    ) is True
    assert treemap._is_user_excluded(
        Path("fixtures/seed-orders.json"), set(), extra_patterns
    ) is True
    # A glob that doesn't match the basename must not filter.
    assert treemap._is_user_excluded(
        Path("src/main.py"), set(), extra_patterns
    ) is False
    # No globs at all => no excludes.
    assert treemap._is_user_excluded(Path("anything.txt"), set(), []) is False


def test_is_user_excluded_dir_and_pattern_combine(treemap):
    """Dir excludes and pattern excludes are independent - either match
    is enough to exclude. Mirrors how the built-in defaults already work."""
    extra_dirs = {"vetted-context"}
    extra_patterns = ["*.parquet"]
    # Dir hit
    assert treemap._is_user_excluded(
        Path("vetted-context/note.md"), extra_dirs, extra_patterns
    ) is True
    # Pattern hit
    assert treemap._is_user_excluded(
        Path("data/silver/events.parquet"), extra_dirs, extra_patterns
    ) is True


def test_cli_exclude_classifies_glob_vs_dir(treemap, monkeypatch, tmp_path):
    """The CLI's `--exclude X` argument routes globby patterns to
    extra_patterns and plain strings to extra_dirs, transparently to the
    caller. Verified by capturing what collect() receives."""
    captured = {}

    def fake_collect(*args, **kwargs):
        captured["extra_dirs"] = kwargs.get("extra_exclude_dirs")
        captured["extra_patterns"] = kwargs.get("extra_exclude_patterns")
        # Return an empty result so main bails out early but cleanly.
        return [], "complexity", None, None, {}

    monkeypatch.setattr(treemap, "collect", fake_collect)
    monkeypatch.setattr(
        sys, "argv",
        ["complexity-treemap.py", str(tmp_path),
         "--exclude", "regulatory-raw",
         "--exclude", "*.csv",
         "--exclude", "seed-data",
         "--exclude", "data-*.json"],
    )
    rc = treemap.main()
    assert rc == 1  # "no scoreable files" - expected with empty collect()
    assert captured["extra_dirs"] == {"regulatory-raw", "seed-data"}
    assert sorted(captured["extra_patterns"]) == ["*.csv", "data-*.json"]


def test_argparse_help_builds_on_current_python(treemap, monkeypatch, capsys):
    """Regression for the Python 3.14 crash: argparse now eagerly validates help
    strings and rejects a bare ``%`` (it must be escaped ``%%``). Building the
    parser via ``--help`` must raise SystemExit (help printed), never ValueError
    ('badly formed help string'). Runs under whatever Python the suite is on, so
    a 3.14 CI job catches a reintroduced bare ``%`` in any help text."""
    monkeypatch.setattr(sys, "argv", ["complexity-treemap.py", "--help"])
    with pytest.raises(SystemExit) as exc:
        treemap.main()
    assert exc.value.code == 0
    assert "--test-pressure" in capsys.readouterr().out


def test_cli_exclude_merges_with_config_toml(treemap, monkeypatch, tmp_path):
    """`.assess/config.toml` and `--exclude` both layer onto the defaults;
    neither replaces the other. Config-supplied dirs join CLI dirs, and
    glob patterns merge across both sources."""
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        'exclude_dirs = ["vetted-context", "regulatory-raw"]\n'
        'exclude_patterns = ["*.parquet"]\n',
        encoding="utf-8",
    )
    captured = {}

    def fake_collect(*args, **kwargs):
        captured["extra_dirs"] = kwargs.get("extra_exclude_dirs")
        captured["extra_patterns"] = kwargs.get("extra_exclude_patterns")
        return [], "complexity", None, None, {}

    monkeypatch.setattr(treemap, "collect", fake_collect)
    monkeypatch.setattr(
        sys, "argv",
        ["complexity-treemap.py", str(tmp_path),
         "--exclude", "seed-data",
         "--exclude", "*.csv"],
    )
    rc = treemap.main()
    assert rc == 1  # no scoreable files
    assert captured["extra_dirs"] == {
        "vetted-context", "regulatory-raw", "seed-data",
    }
    assert sorted(captured["extra_patterns"]) == ["*.csv", "*.parquet"]


def test_config_loader_missing_file_is_empty(tmp_path):
    """A repo with no .assess/config.toml degrades silently - no warning,
    no error, just an empty config (the common case)."""
    from lib.assess_config import load_excludes

    dirs, pats = load_excludes(tmp_path)
    assert dirs == set()
    assert pats == []


def test_config_loader_malformed_toml_returns_empty(tmp_path, capsys):
    """A broken TOML file must never block the assessment - the loader
    returns empty excludes and prints a one-line warning."""
    from lib.assess_config import load_excludes

    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        "this is not valid = = toml\n", encoding="utf-8",
    )
    dirs, pats = load_excludes(tmp_path)
    assert dirs == set()
    assert pats == []
    captured = capsys.readouterr()
    assert "could not read" in captured.err


def test_config_loader_drops_non_string_entries(tmp_path):
    """A schema violation in one entry doesn't poison the rest - e.g.
    `exclude_dirs = ["regulatory-raw", 42]` keeps the string and drops
    the integer."""
    from lib.assess_config import load_excludes

    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        'exclude_dirs = ["regulatory-raw", 42, "vetted-context"]\n'
        'exclude_patterns = ["*.csv", true]\n',
        encoding="utf-8",
    )
    dirs, pats = load_excludes(tmp_path)
    assert dirs == {"regulatory-raw", "vetted-context"}
    assert pats == ["*.csv"]


def test_config_loader_no_legacy_section_needed(tmp_path):
    """The schema is top-level - no `[treemap]` or `[exclude]` wrapper.
    The file is already namespaced by living under `.assess/config.toml`."""
    from lib.assess_config import load_excludes

    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        'exclude_dirs = ["regulatory-raw"]\n'
        'exclude_patterns = ["*.csv"]\n',
        encoding="utf-8",
    )
    dirs, pats = load_excludes(tmp_path)
    assert dirs == {"regulatory-raw"}
    assert pats == ["*.csv"]


def test_config_loader_scalar_string_degrades_to_empty(tmp_path):
    """`exclude_dirs = "regulatory-raw"` (string, not list) used to iterate
    character-by-character, silently producing single-char "dir names"
    that match unexpectedly. The loader now rejects non-list values."""
    from lib.assess_config import load_excludes

    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        'exclude_dirs = "regulatory-raw"\n',
        encoding="utf-8",
    )
    dirs, pats = load_excludes(tmp_path)
    assert dirs == set()
    assert pats == []


def test_config_loader_scalar_int_does_not_raise(tmp_path):
    """`exclude_dirs = 5` is valid TOML but the wrong type. It used to
    raise `TypeError` (int not iterable), propagate through `load_excludes`,
    and abort the whole assessment - the opposite of "degrade silently"."""
    from lib.assess_config import load_excludes

    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        'exclude_dirs = 5\nexclude_patterns = true\n',
        encoding="utf-8",
    )
    # The test passes if this call returns without raising.
    dirs, pats = load_excludes(tmp_path)
    assert dirs == set()
    assert pats == []


# ── survivor-density overlay (task 5) ─────────────────────────────────────────


@pytest.mark.parametrize("density,expected", [
    (None, ""),       # unknown -> no overlay
    (0.0, ""),
    (0.30, ""),       # boundary: must exceed, not equal
    (0.31, "diag"),
    (0.50, "diag"),   # boundary: cross only above 0.5
    (0.51, "cross"),
    (0.95, "cross"),
])
def test_hatch_for_density_thresholds(treemap, density, expected):
    assert treemap._hatch_for_density(density) == expected


def test_survivor_overrides_applies_hatch_per_file(treemap):
    p1, p2, p3 = Path("/repo/a.py"), Path("/repo/b.py"), Path("/repo/c.py")
    files = [(p1, 100, 5.0, "lizard"),
             (p2, 50, 3.0, "lizard"),
             (p3, 10, 1.0, "lizard")]
    density = {p1: 0.6, p2: 0.4, p3: 0.1}
    overrides = treemap._survivor_overrides(files, density)
    assert overrides[p1] == {"hatch": "cross"}
    assert overrides[p2] == {"hatch": "diag"}
    assert p3 not in overrides  # below threshold -> no overlay


def test_survivor_overrides_empty_data_is_silent(treemap):
    """Absent or empty survivor data renders no overlay, no error."""
    files = [(Path("/repo/a.py"), 100, 5.0, "lizard")]
    assert treemap._survivor_overrides(files, None) == {}
    assert treemap._survivor_overrides(files, {}) == {}


def test_write_svg_emits_hatch_overlay_and_legend(render_lib, tmp_path):
    """A hatched node gets a pattern-filled overlay, the <defs> patterns are
    emitted, and the legend explains what the hatch means."""
    node = render_lib.Node(name="a.py", rel_path="a.py", loc=100,
                           metric=5.0, color=(0.8, 0.2, 0.1, 1.0),
                           is_file=True, hatch="diag")
    rects = [(0.0, 0.0, 100.0, 100.0, node)]
    out = tmp_path / "hatched.svg"
    render_lib.write_svg(rects, Path("/repo"), 1600.0, 1000.0, out,
                         False, "ccn", show_survivor_legend=True)
    svg = out.read_text()
    # pattern definition + overlay reference
    assert 'id="survivor-diag"' in svg
    assert 'fill="url(#survivor-diag)"' in svg
    # legend explains the survivor meaning with both thresholds
    assert "survivor density" in svg.lower()
    assert "30%" in svg
    assert "50%" in svg
    # canvas extended by the legend band (1000 + 84)
    assert 'height="1084"' in svg


def test_write_svg_no_overlay_without_survivor_data(render_lib, tmp_path):
    """No hatch and no legend flag -> original full-canvas treemap, untouched:
    no survivor patterns, no <defs>, no extra legend band."""
    node = render_lib.Node(name="a.py", rel_path="a.py", loc=100,
                           metric=5.0, color=(0.8, 0.2, 0.1, 1.0),
                           is_file=True)
    rects = [(0.0, 0.0, 100.0, 100.0, node)]
    out = tmp_path / "plain.svg"
    render_lib.write_svg(rects, Path("/repo"), 1600.0, 1000.0, out,
                         False, "ccn")
    svg = out.read_text()
    assert "survivor-" not in svg
    assert "<defs>" not in svg
    assert 'height="1000"' in svg


def test_load_survivor_density_from_per_file(treemap, tmp_path):
    """Per-file density is survived/total; entries without a total (mutmut)
    are skipped, and paths resolve against the repo root."""
    ctx = {"test_pressure": {"per_file": [
        {"file": "src/a.py", "killed": 2, "survived": 8, "total": 10},
        {"file": "src/b.py", "killed": 9, "survived": 1, "total": 10},
        {"file": "src/c.py", "killed": None, "survived": 4, "total": None},
    ]}}
    j = tmp_path / "run-context.json"
    j.write_text(json.dumps(ctx), encoding="utf-8")
    density = treemap.load_survivor_density(j, tmp_path)
    assert density[(tmp_path / "src/a.py").resolve()] == 0.8
    assert density[(tmp_path / "src/b.py").resolve()] == 0.1
    assert (tmp_path / "src/c.py").resolve() not in density  # no total


def test_load_survivor_density_absent_block_is_empty(treemap, tmp_path):
    j = tmp_path / "run-context.json"
    j.write_text(json.dumps({"doc_graph": {}}), encoding="utf-8")
    assert treemap.load_survivor_density(j, tmp_path) == {}


def test_load_survivor_density_missing_file_is_empty(treemap, tmp_path):
    assert treemap.load_survivor_density(tmp_path / "nope.json", tmp_path) == {}
