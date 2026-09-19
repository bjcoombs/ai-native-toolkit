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
    # The composite now includes a sqrt(est_tokens) size axis. Hold size equal
    # so this test isolates the churn axis it is about (issue #47); otherwise
    # frozen.go's larger size would confound the ranking.
    tokens = {frozen_complex: 1000, active_moderate: 1000}
    treemap.write_stats(files, aux_data, "commits (last 12mo)", root, out,
                        tokens_by_path=tokens)

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


def test_write_stats_stamps_schema_and_tool_versions(treemap, tmp_path, monkeypatch):
    """The sidecar stamps the stats schema version and the complexity backend
    versions so a later run can detect a schema or tool change and void a
    non-comparable diff. scc_version is present only when scc scored files."""
    monkeypatch.setattr(treemap, "_lizard_version", lambda: "1.23.0")
    monkeypatch.setattr(treemap, "_scc_version", lambda: "3.7.0")
    root = tmp_path
    lz = root / "a.go"
    sc = root / "b.rb"
    out = root / "stats.json"

    # lizard-only: no scc_version key.
    treemap.write_stats([(lz, 100, 5.0, "lizard")], None, None, root, out)
    stats = json.loads(out.read_text())
    assert stats["schema_version"] == treemap.STATS_SCHEMA_VERSION
    assert stats["lizard_version"] == "1.23.0"
    assert "scc_version" not in stats

    # A file scored by scc adds scc_version.
    treemap.write_stats(
        [(lz, 100, 5.0, "lizard"), (sc, 80, 4.0, "scc")], None, None, root, out
    )
    stats = json.loads(out.read_text())
    assert stats["lizard_version"] == "1.23.0"
    assert stats["scc_version"] == "3.7.0"


def test_tool_versions_omits_scc_when_not_scored(treemap, monkeypatch):
    """_tool_versions always carries lizard; scc appears only when a file was
    scored by scc AND scc is resolvable."""
    monkeypatch.setattr(treemap, "_lizard_version", lambda: "1.23.0")
    monkeypatch.setattr(treemap, "_scc_version", lambda: "3.7.0")
    assert treemap._tool_versions([(Path("a.go"), 1, 1.0, "lizard")]) == {
        "lizard": "1.23.0"
    }
    assert treemap._tool_versions([(Path("b.rb"), 1, 1.0, "scc")]) == {
        "lizard": "1.23.0", "scc": "3.7.0"
    }


def test_tool_versions_drops_scc_when_binary_absent(treemap, monkeypatch):
    """When scc scored files but the binary can't report a version, scc is
    omitted rather than stamped as a false 'unknown'."""
    monkeypatch.setattr(treemap, "_lizard_version", lambda: "1.23.0")
    monkeypatch.setattr(treemap, "_scc_version", lambda: None)
    assert treemap._tool_versions([(Path("b.rb"), 1, 1.0, "scc")]) == {
        "lizard": "1.23.0"
    }


def test_write_stats_records_churn_degenerate_flag(treemap, tmp_path):
    """Issue #172: the stats sidecar carries ``churn_degenerate`` so the report
    and a reader know the saturation axis / commits column is inactive. Default
    is False; passing the flag records True."""
    root = tmp_path
    f = root / "a.go"
    out = root / "stats.json"
    treemap.write_stats([(f, 100, 5.0, "lizard")], {f: 1}, "commits (all-time)",
                        root, out)
    assert json.loads(out.read_text())["churn_degenerate"] is False

    treemap.write_stats([(f, 100, 5.0, "lizard")], {f: 1}, "commits (all-time)",
                        root, out, churn_degenerate=True)
    assert json.loads(out.read_text())["churn_degenerate"] is True


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
    # Equal est_tokens too, so the new sqrt(est_tokens) size axis doesn't
    # confound the per-function re-weight this test is about (issue #115).
    tokens = {coordinator: 1000, dao: 1000}
    treemap.write_stats(
        files, aux_data, "commits (last 12mo)", root, out,
        fn_ccn_by_path={coordinator: coordinator_fns, dao: dao_fns},
        tokens_by_path=tokens,
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


def test_write_svg_emits_a11y_title_and_desc(render_lib, tmp_path):
    """Task 17: the root <svg> is role="img" with a <title>/<desc> pair as its
    first children, so a screen reader announces the image and how it encodes."""
    node = render_lib.Node(name="a.py", rel_path="a.py", loc=100,
                           metric=5.0, color=(0.8, 0.2, 0.1, 1.0),
                           is_file=True)
    rects = [(0.0, 0.0, 100.0, 100.0, node)]
    out = tmp_path / "a11y.svg"
    render_lib.write_svg(rects, Path("/repo"), 1600.0, 1000.0, out, False, "ccn")
    svg = out.read_text()
    assert 'role="img"' in svg
    assert "<title>Complexity Hotspot Heatmap</title>" in svg
    assert "hue indicates cyclomatic complexity" in svg
    assert "saturation indicates git churn" in svg
    # <title>/<desc> are the root's first children (before the <style> block).
    assert svg.index("<title>") < svg.index("<style>")
    assert svg.index("<desc>") < svg.index("<style>")


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


def test_mutmut_junitxml_drives_hatch_overlay(treemap, tmp_path, fixtures_dir):
    """End to end: a real mutmut junitxml parse -> run-context per_file ->
    load_survivor_density -> hatch overrides. Before the junitxml fix the mutmut
    path carried no totals, so this overlay could never render on a Python repo."""
    from lib.test_pressure import _parse_mutmut_junitxml

    per_file = _parse_mutmut_junitxml(fixtures_dir / "mutmut-junitxml.xml")
    ctx = {"test_pressure": {"per_file": per_file}}
    j = tmp_path / "run-context.json"
    j.write_text(json.dumps(ctx), encoding="utf-8")

    density = treemap.load_survivor_density(j, tmp_path)
    calc = (tmp_path / "src/calc.py").resolve()
    util = (tmp_path / "src/util.py").resolve()
    assert density[calc] == 2 / 3      # real density, not skipped
    assert density[util] == 0.0         # all killed -> real 0 density (has a total)

    files = [(calc, 100, 5.0, "lizard"), (util, 50, 3.0, "lizard")]
    overrides = treemap._survivor_overrides(files, density)
    assert overrides[calc] == {"hatch": "cross"}  # 0.67 > 0.5
    assert util not in overrides                   # 0.0 below hatch threshold


def test_load_survivor_density_absent_block_is_empty(treemap, tmp_path):
    j = tmp_path / "run-context.json"
    j.write_text(json.dumps({"doc_graph": {}}), encoding="utf-8")
    assert treemap.load_survivor_density(j, tmp_path) == {}


def test_load_survivor_density_missing_file_is_empty(treemap, tmp_path):
    assert treemap.load_survivor_density(tmp_path / "nope.json", tmp_path) == {}


# --- Estimated tokens as the keyhole size unit (PRD 2026-06) -----------------

def test_est_token_count_is_chars_over_four(treemap, tmp_path):
    """est_tokens = ceil(len(text)/4) for a real on-disk file."""
    f = tmp_path / "a.py"
    f.write_text("x" * 800, encoding="utf-8")  # 800 chars -> 200 tokens
    assert treemap.est_token_count(f, 0) == 200


def test_est_token_count_falls_back_to_loc_when_unreadable(treemap, tmp_path):
    """An unreadable/absent file estimates from loc - a conservative floor that
    can't inflate a benign file's rank."""
    missing = tmp_path / "gone.py"
    assert treemap.est_token_count(missing, 137) == 137


def test_write_stats_emits_est_tokens_per_row_and_uses_real_text(treemap, tmp_path):
    """Each row carries est_tokens; for on-disk files it is the char-based
    estimate, not the loc (a dense file reads higher than its line count)."""
    root = tmp_path
    dense = root / "dense.py"
    dense.write_text("y" * 4000, encoding="utf-8")  # 1000 est tokens
    out = root / "stats.json"
    treemap.write_stats([(dense, 50, 5.0, "lizard")], None, None, root, out)
    stats = json.loads(out.read_text())
    row = stats["top_hotspots"][0]
    assert row["est_tokens"] == 1000          # chars/4, not the 50 loc
    assert row["loc"] == 50                    # loc preserved alongside
    assert stats["est_tokens"]["total"] == 1000


def test_hotspot_score_includes_token_factor(treemap, tmp_path):
    """With ccn and churn held equal, the larger file (more estimated tokens)
    ranks first - the size axis is live in the composite."""
    root = tmp_path
    big = root / "big.go"
    small = root / "small.go"
    files = [(big, 200, 30.0, "lizard"), (small, 200, 30.0, "lizard")]
    aux_data = {big: 10, small: 10}
    tokens = {big: 40000, small: 4000}  # same ccn + churn, 10x size
    out = root / "stats.json"
    treemap.write_stats(files, aux_data, "commits (last 12mo)", root, out,
                        tokens_by_path=tokens)
    hotspots = json.loads(out.read_text())["top_hotspots"]
    assert hotspots[0]["path"] == "big.go"


def test_big_but_simple_stable_file_does_not_top_on_size_alone(treemap, tmp_path):
    """PRD validation guard: a large-but-simple-stable file (a long config or
    data table - low ccn, no churn, huge size) must NOT top the hotspot list.
    The sqrt bounding keeps its single big axis from dominating a genuine
    complex+churning hotspot."""
    root = tmp_path
    data_table = root / "data.json"      # huge, trivial, frozen
    hotspot = root / "engine.go"         # complex AND churning, modest size
    files = [(data_table, 5000, 1.0, "lizard"), (hotspot, 300, 100.0, "lizard")]
    aux_data = {data_table: 0, hotspot: 30}
    tokens = {data_table: 200000, hotspot: 5000}  # data table 40x bigger
    out = root / "stats.json"
    treemap.write_stats(files, aux_data, "commits (last 12mo)", root, out,
                        tokens_by_path=tokens)
    hotspots = json.loads(out.read_text())["top_hotspots"]
    assert hotspots[0]["path"] == "engine.go"  # complexity+churn beats raw size


def test_keyhole_budget_rollup_counts_files_and_subtrees(treemap, tmp_path):
    """The est_tokens.budget block reports the repo total plus how many files
    and top-level subtrees exceed one context-window keyhole."""
    root = tmp_path
    over_file = root / "giant.py"           # single file over budget
    a1 = root / "pkg_a" / "x.py"            # pkg_a subtree over budget in sum
    a2 = root / "pkg_a" / "y.py"
    small = root / "tiny.py"
    files = [
        (over_file, 10, 1.0, "lizard"),
        (a1, 10, 1.0, "lizard"),
        (a2, 10, 1.0, "lizard"),
        (small, 10, 1.0, "lizard"),
    ]
    tokens = {over_file: 250000, a1: 150000, a2: 120000, small: 100}
    out = root / "stats.json"
    treemap.write_stats(files, None, None, root, out, tokens_by_path=tokens)
    budget = json.loads(out.read_text())["est_tokens"]["budget"]
    assert budget["budget"] == treemap.CONTEXT_WINDOW_BUDGET_TOKENS
    assert budget["total"] == 250000 + 150000 + 120000 + 100
    assert budget["files_over_budget"] == 1           # only giant.py alone
    # pkg_a (270k) and giant.py-as-its-own-subtree (250k) both exceed 200k.
    assert budget["subtrees_over_budget"] == 2
    over_names = {s["path"] for s in budget["over_budget_subtrees"]}
    assert "pkg_a" in over_names and "giant.py" in over_names


def test_est_tokens_are_post_artifact_filter(treemap, tmp_path):
    """est_tokens are computed only over the files passed in (already filtered
    by collect), so a filtered bundle never inflates the totals or budget."""
    root = tmp_path
    real = root / "real.py"
    real.write_text("z" * 400, encoding="utf-8")  # 100 tokens
    out = root / "stats.json"
    # The bundle is simply absent from `files` (collect dropped it) - the
    # totals reflect only the surviving file.
    treemap.write_stats([(real, 20, 5.0, "lizard")], None, None, root, out)
    stats = json.loads(out.read_text())
    assert stats["est_tokens"]["total"] == 100


def test_build_tree_size_by_decouples_area_from_loc(render_lib):
    """size_by overrides the block area (estimated tokens) while each leaf keeps
    its real loc and records est_tokens for the tooltip."""
    root = Path("/repo")
    a = root / "a.py"
    files_colored = [(a, 500, 5.0, "lizard", (0.8, 0.2, 0.1, 1.0))]
    tree = render_lib.build_tree(files_colored, root, size_by={a: 1800})
    leaf = tree.children[0]
    assert leaf.size == 1800        # layout area = estimated tokens
    assert leaf.loc == 500          # real loc preserved
    assert leaf.est_tokens == 1800


def test_build_tree_without_size_by_is_unchanged(render_lib):
    """No size_by (the docs-heatmap path) keeps area == loc, est_tokens 0."""
    root = Path("/repo")
    a = root / "a.py"
    files_colored = [(a, 500, 5.0, "lizard", (0.8, 0.2, 0.1, 1.0))]
    leaf = render_lib.build_tree(files_colored, root).children[0]
    assert leaf.size == 500 and leaf.loc == 500 and leaf.est_tokens == 0


def test_write_svg_tooltip_shows_est_tokens_and_loc(render_lib, tmp_path):
    """A code-heatmap node (est_tokens set) leads its tooltip with estimated
    tokens and keeps LOC alongside."""
    node = render_lib.Node(name="a.py", rel_path="a.py", loc=514,
                           est_tokens=8200, metric=12.0,
                           color=(0.8, 0.2, 0.1, 1.0), is_file=True)
    rects = [(0.0, 0.0, 100.0, 100.0, node)]
    out = tmp_path / "tok.svg"
    render_lib.write_svg(rects, Path("/repo"), 1600.0, 1000.0, out, False, "ccn")
    svg = out.read_text()
    assert "8,200 est. tokens" in svg
    assert "514 loc" in svg


def test_write_stats_stamps_run_id_and_schema_version(treemap, tmp_path):
    """The complexity-stats sidecar carries an artifact_schema_version and a
    unique run_id (assess-obey-thyself), so each stats emission is traceable.
    Distinct from the stats-layout `schema_version` (an int, versions the diff
    comparability)."""
    root = tmp_path
    f = root / "a.go"
    out = root / "stats.json"
    treemap.write_stats([(f, 100, 5.0, "lizard")], None, None, root, out)
    stats = json.loads(out.read_text())
    assert stats["artifact_schema_version"] == treemap.ARTIFACT_SCHEMA_VERSION == "1.1.0"
    # The stats-layout schema_version (from #244) still coexists as an int.
    assert stats["schema_version"] == treemap.STATS_SCHEMA_VERSION
    run_id = stats["run_id"]
    stamp, _, suffix = run_id.partition("-")
    assert len(stamp) == 14 and stamp.isdigit()
    assert len(suffix) == 8

    out2 = root / "stats2.json"
    treemap.write_stats([(f, 100, 5.0, "lizard")], None, None, root, out2)
    assert json.loads(out2.read_text())["run_id"] != run_id


# --- generated-file exclusion (header sniff, long lines, filename globs) -----

@pytest.mark.parametrize("name", [
    "types.generated.ts", "schema.generated.sql", "client.gen.ts",
    "database.types.ts",
])
def test_generated_header_free_filename_globs_are_filtered(treemap, name):
    assert treemap._is_build_artifact(Path("src") / name) is True


def _fake_scorers(treemap, monkeypatch, paths):
    monkeypatch.setattr(
        treemap, "lizard_scores",
        lambda root, **kw: {p: (10, 3.0, [3.0]) for p in paths},
    )
    monkeypatch.setattr(treemap, "scc_scores", lambda root, **kw: {})


def test_collect_drops_generated_header_and_long_line_files(
        treemap, tmp_path, monkeypatch):
    root = tmp_path
    (root / "db").mkdir()
    (root / "src").mkdir()
    schema = root / "db" / "schema.sql"
    schema.write_text("-- GENERATED FILE - DO NOT EDIT\nCREATE TABLE t (id int);\n")
    font = root / "src" / "font.ts"
    font.write_text('export const F = "' + "A" * 40000 + '";\n')
    hand = root / "src" / "hand.py"
    hand.write_text("def f():\n    return 1\n" + "# x\n" * 196
                    + "# do not edit the table above\n")
    _fake_scorers(treemap, monkeypatch, [schema, font, hand])

    excluded: list[dict] = []
    files, *_rest, fn_ccn = treemap.collect(
        root, by="complexity", excluded_generated=excluded)
    assert [f[0] for f in files] == [hand]
    assert set(fn_ccn) == {hand}
    assert excluded == [
        {"path": "db/schema.sql", "reason": "generated-header"},
        {"path": "src/font.ts", "reason": "long-lines"},
    ]


def test_collect_include_artifacts_keeps_generated_header_files(
        treemap, tmp_path, monkeypatch):
    schema = tmp_path / "schema.sql"
    schema.write_text("-- @generated\nCREATE TABLE t (id int);\n")
    _fake_scorers(treemap, monkeypatch, [schema])
    excluded: list[dict] = []
    files, *_ = treemap.collect(tmp_path, by="complexity",
                                include_artifacts=True,
                                excluded_generated=excluded)
    assert [f[0] for f in files] == [schema]
    assert excluded == []


def test_write_stats_carries_generated_header_exclusions(treemap, tmp_path):
    root = tmp_path
    f = root / "a.py"
    f.write_text("x = 1\n")
    out = root / "stats.json"
    listed = [{"path": "db/schema.sql", "reason": "generated-header"}]
    treemap.write_stats([(f, 1, 1.0, "lizard")], None, None, root, out,
                        excluded_generated=listed)
    stats = json.loads(out.read_text())
    assert stats["excluded_generated"] == listed
    assert isinstance(stats["schema_version"], int) and stats["schema_version"] > 1
    treemap.write_stats([(f, 1, 1.0, "lizard")], None, None, root, out)
    assert json.loads(out.read_text())["excluded_generated"] == []


def test_generated_header_all_excluded_error_names_the_exclusion(
        treemap, tmp_path, monkeypatch, capsys):
    schema = tmp_path / "schema.sql"
    schema.write_text("-- GENERATED FILE - DO NOT EDIT\nCREATE TABLE t (id int);\n")
    _fake_scorers(treemap, monkeypatch, [schema])
    monkeypatch.setattr(sys, "argv", ["complexity-treemap.py", str(tmp_path)])
    assert treemap.main() == 1
    err = capsys.readouterr().err
    assert "no scoreable files found" in err
    assert "1 excluded as generated" in err
    assert "--include-artifacts" in err


def test_write_stats_paths_match_generated_header_list_separator(treemap, tmp_path):
    """Row paths use forward slashes on every host, the same form as
    excluded_generated, so assess_core can intersect the two sets on Windows
    (where str() of a relative path would use backslashes)."""
    (tmp_path / "db").mkdir()
    f = tmp_path / "db" / "a.py"
    f.write_text("x = 1\n")
    out = tmp_path / "stats.json"
    treemap.write_stats([(f, 1, 1.0, "lizard")], None, None, tmp_path, out)
    paths = [r["path"] for r in json.loads(out.read_text())["top_large"]]
    assert paths == ["db/a.py"]
    src = (Path(treemap.__file__)).read_text()
    rel_body = src[src.index("    def rel(p: Path) -> str:"):][:400]
    assert "as_posix()" in rel_body and "str(p" not in rel_body


# --- generated test reports, code/data maxima, scc-only hint -----------------

@pytest.mark.parametrize("rel", [
    "web/tests/html-report/index.html",
    "e2e/playwright-report/index.html",
    "web/accessibility/lighthouse-report.html",
    "web/accessibility/lighthouse-results.json",
    "security/zap-report.html",
    "security/zap-report.json",
    "security/zap_report.html",
    "mcp/test/fixtures/big/lines.jsonl",
    "a/fixtures/lines.jsonl",
])
def test_report_default_excludes_drop_generated_reports(treemap, rel):
    path = Path(rel)
    in_dir = any(part in treemap.EXCLUDE_DIRS for part in path.parts)
    assert in_dir or treemap._is_build_artifact(path)


@pytest.mark.parametrize("rel", [
    "data/events.jsonl",           # .jsonl outside any fixtures/ directory
    "fixtures/lines.jsonl",        # bare top-level fixtures/ stays scored
    "fixtures/taxonomy/concepts.json",
    "mcp/test/fixtures/big/case.json",  # only .jsonl leaves nested fixtures/
    "src/report.html",
    "security/zap_report.py",  # the script that runs ZAP, not its output
])
def test_report_default_excludes_keep_hand_kept_files(treemap, rel):
    path = Path(rel)
    assert not any(part in treemap.EXCLUDE_DIRS for part in path.parts)
    assert treemap._is_build_artifact(path) is False


def test_report_default_excludes_bypassed_by_include_artifacts(
        treemap, tmp_path, monkeypatch):
    """The nested-fixture .jsonl rule is a filename default like the globs, so
    --include-artifacts scores it."""
    import subprocess as sp

    nested = tmp_path / "mcp" / "fixtures" / "lines.jsonl"
    nested.parent.mkdir(parents=True)
    nested.write_text('{"a": 1}\n')
    payload = json.dumps([{"Name": "JSONL", "Files": [
        {"Location": str(nested), "Code": 1, "Complexity": 0}]}])
    monkeypatch.setattr(treemap.shutil, "which", lambda _: "/usr/bin/scc")
    monkeypatch.setattr(treemap.subprocess, "run", lambda *a, **k:
                        sp.CompletedProcess(a, 0, stdout=payload))
    assert treemap.scc_scores(tmp_path) == {}
    langs: dict = {}
    assert treemap.scc_scores(tmp_path, include_artifacts=True,
                              languages=langs) == {nested.resolve(): (1, 0.0)}
    assert langs == {nested.resolve(): "JSONL"}


def test_code_data_maxima_split_by_scc_language(treemap, tmp_path):
    root = tmp_path
    code = root / "app.py"
    code.write_text("x = 1\n" * 30)
    data = root / "big.json"
    data.write_text('{"k": 1}\n' * 500)
    conf = root / "settings.yaml"
    conf.write_text("k: 1\n" * 100)
    out = root / "stats.json"
    langs = {data: "JSON", conf: "YAML"}
    treemap.write_stats(
        [(code, 30, 4.0, "lizard"), (data, 500, 0.0, "scc"),
         (conf, 100, 0.0, "scc")],
        None, None, root, out, languages_by_path=langs)
    stats = json.loads(out.read_text())
    rows = {r["path"]: r for r in stats["top_large"]}
    assert stats["loc"]["max"] == 500
    assert stats["loc"]["max_code"] == rows["app.py"]["loc"] == 30
    assert stats["loc"]["max_data"] == rows["big.json"]["loc"] == 500
    assert stats["est_tokens"]["max_code"] == rows["app.py"]["est_tokens"]
    assert stats["est_tokens"]["max_data"] == rows["big.json"]["est_tokens"]
    assert stats["schema_version"] >= 3


def test_code_data_maxima_empty_side_reports_zero(treemap, tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    out = tmp_path / "stats.json"
    treemap.write_stats([(f, 1, 1.0, "lizard")], None, None, tmp_path, out)
    stats = json.loads(out.read_text())
    assert stats["loc"]["max_code"] == 1
    assert stats["loc"]["max_data"] == 0
    assert stats["est_tokens"]["max_data"] == 0


def _scc_row(tmp_path, name, loc, ccn=0.0, src="scc"):
    p = tmp_path / name
    return (p, loc, ccn, src)


_LANG_BY_SUFFIX = {".json": "JSON", ".py": "Python", ".ex": "Elixir",
                   ".md": "Markdown"}


def _langs(files):
    return {f[0]: _LANG_BY_SUFFIX[f[0].suffix] for f in files}


def test_scc_only_hint_fires_when_largest_files_are_scc_ccn_zero(
        treemap, tmp_path, capsys):
    files = [_scc_row(tmp_path, f"d{i}.json", 200) for i in range(12)]
    files.append(_scc_row(tmp_path, "tiny.py", 2, 1.0, "lizard"))
    tokens = {f[0]: f[1] * 10 for f in files}
    treemap._hint_if_largest_files_scc_only(files, tokens, _langs(files))
    err = capsys.readouterr().err
    assert ".assess/config.toml" in err
    assert "d0.json" in err


def test_scc_only_hint_silent_when_largest_file_is_code(
        treemap, tmp_path, capsys):
    files = [_scc_row(tmp_path, f"d{i}.json", 200) for i in range(12)]
    files.append(_scc_row(tmp_path, "big.py", 1200, 600.0, "lizard"))
    tokens = {f[0]: f[1] * 10 for f in files}
    treemap._hint_if_largest_files_scc_only(files, tokens, _langs(files))
    assert capsys.readouterr().err == ""


def test_scc_only_hint_silent_on_scored_scc_code(treemap, tmp_path, capsys):
    """An scc-scored file with complexity above 0 is code (an Elixir or Dart
    module), so it keeps the hint quiet."""
    files = [_scc_row(tmp_path, f"m{i}.ex", 200, 3.0) for i in range(12)]
    tokens = {f[0]: f[1] * 10 for f in files}
    treemap._hint_if_largest_files_scc_only(files, tokens, _langs(files))
    assert capsys.readouterr().err == ""


def test_scc_only_hint_silent_on_markdown(treemap, tmp_path, capsys):
    """scc gives Markdown complexity 0 too, but on a docs-first repo those
    blocks are the deliverable, not data to exclude."""
    files = [_scc_row(tmp_path, f"s{i}.md", 200) for i in range(12)]
    tokens = {f[0]: f[1] * 10 for f in files}
    treemap._hint_if_largest_files_scc_only(files, tokens, _langs(files))
    assert capsys.readouterr().err == ""


def test_scc_only_hint_silent_below_top_n(treemap, tmp_path, capsys):
    files = [_scc_row(tmp_path, f"d{i}.json", 200)
             for i in range(treemap.SCC_ONLY_HINT_TOP_N - 1)]
    tokens = {f[0]: f[1] * 10 for f in files}
    treemap._hint_if_largest_files_scc_only(files, tokens, _langs(files))
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("include_artifacts", [False, True])
def test_scc_only_hint_skipped_under_include_artifacts(
        treemap, tmp_path, monkeypatch, capsys, include_artifacts):
    paths = []
    for i in range(treemap.SCC_ONLY_HINT_TOP_N):
        p = tmp_path / f"d{i}.json"
        p.write_text('{"k": 1}\n' * 50)
        paths.append(p)

    def fake_collect(root, **kw):
        kw["scc_languages"].update({p: "JSON" for p in paths})
        return [(p, 50, 0.0, "scc") for p in paths], "hotspot", None, None, {}

    monkeypatch.setattr(treemap, "collect", fake_collect)
    monkeypatch.setattr(treemap, "render", lambda *a, **k: None)
    argv = ["complexity-treemap.py", str(tmp_path), "-o",
            str(tmp_path / "out.svg")]
    if include_artifacts:
        argv.append("--include-artifacts")
    monkeypatch.setattr(sys, "argv", argv)
    assert treemap.main() == 0
    hinted = ".assess/config.toml" in capsys.readouterr().err
    assert hinted is not include_artifacts


# --------------------------------------------------------------------------
# Per-function backend per language and the worst function's name (issue #363)


def test_write_stats_backend_by_language_maps_lizard_and_null(treemap, tmp_path):
    """fn_ccn.source lists the backends that scored a file, as objects, and
    backend_by_language maps each programming language to its backend or to
    null when only scc scored it at file level. Data and markup languages,
    where scc counts no decision points, carry no key."""
    root = tmp_path
    py = root / "src" / "app.py"
    ex = root / "lib" / "router.ex"
    js = root / "data.json"
    md = root / "README.md"
    out = root / "stats.json"
    treemap.write_stats(
        [(py, 20, 8.0, "lizard"), (ex, 9, 2.0, "scc"),
         (js, 50, 0.0, "scc"), (md, 30, 0.0, "scc")],
        None, None, root, out,
        fn_ccn_by_path={py: [1.0, 7.0]},
        fn_name_by_path={py: "gnarly"},
        languages_by_path={py: "Python", ex: "Elixir",
                           js: "JSON", md: "Markdown"},
    )
    fn = json.loads(out.read_text())["fn_ccn"]
    assert fn["source"] == [{"name": "lizard", "approximate": False}]
    assert fn["backend_by_language"] == {"Python": "lizard", "Elixir": None}


def test_write_stats_backend_by_language_partial_coverage_is_null(
        treemap, tmp_path):
    """A language a backend covers only in part maps to null: one lizard file
    must not make the language's scc-only files read as covered. A file with
    no decision points loses no per-function figure and does not downgrade."""
    root = tmp_path
    cpp, ipp = root / "a.cpp", root / "a.ipp"
    js, mjs = root / "a.js", root / "b.mjs"
    out = root / "stats.json"
    treemap.write_stats(
        [(cpp, 20, 8.0, "lizard"), (ipp, 30, 4.0, "scc"),
         (js, 20, 3.0, "lizard"), (mjs, 5, 0.0, "scc")],
        None, None, root, out,
        fn_ccn_by_path={cpp: [8.0], js: [3.0]},
        languages_by_path={cpp: "C++", ipp: "C++",
                           js: "JavaScript", mjs: "JavaScript"},
    )
    fn = json.loads(out.read_text())["fn_ccn"]
    assert fn["backend_by_language"] == {"C++": None, "JavaScript": "lizard"}


def test_write_stats_backend_by_language_empty_source_when_no_backend(
        treemap, tmp_path):
    """An scc-only run lists no backend rather than claiming lizard."""
    root = tmp_path
    ex = root / "router.ex"
    out = root / "stats.json"
    treemap.write_stats([(ex, 9, 2.0, "scc")], None, None, root, out,
                        languages_by_path={ex: "Elixir"})
    fn = json.loads(out.read_text())["fn_ccn"]
    assert fn["source"] == []
    assert fn["backend_by_language"] == {"Elixir": None}


def test_write_stats_max_fn_name_names_worst_function(treemap, tmp_path):
    """Every ranked row carries max_fn_name beside max_fn_ccn; it is null
    wherever max_fn_ccn is null (an scc-scored file, or a lizard file with no
    functions)."""
    root = tmp_path
    py = root / "app.py"
    flat = root / "flat.py"
    ex = root / "router.ex"
    out = root / "stats.json"
    treemap.write_stats(
        [(py, 20, 8.0, "lizard"), (flat, 5, 1.0, "lizard"),
         (ex, 9, 2.0, "scc")],
        None, None, root, out,
        fn_ccn_by_path={py: [1.0, 7.0], flat: []},
        fn_name_by_path={py: "gnarly"},
    )
    stats = json.loads(out.read_text())
    for key in ("top_hotspots", "top_complex", "top_large"):
        rows = {r["path"]: r for r in stats[key]}
        assert rows["app.py"]["max_fn_ccn"] == 7.0
        assert rows["app.py"]["max_fn_name"] == "gnarly"
        assert rows["flat.py"]["max_fn_ccn"] is None
        assert rows["flat.py"]["max_fn_name"] is None
        assert rows["router.ex"]["max_fn_name"] is None


def test_lizard_scores_fills_max_fn_name_with_worst_function(
        treemap, tmp_path, monkeypatch):
    """lizard_scores records the name of each file's highest-ccn function in
    the optional fn_names map, and collect threads it through."""
    src = tmp_path / "app.py"
    src.write_text("def simple(a):\n    return a\n")

    def fn(name, ccn):
        return types.SimpleNamespace(name=name, cyclomatic_complexity=ccn)

    fake = types.SimpleNamespace(
        filename=str(src), nloc=10,
        function_list=[fn("simple", 1), fn("gnarly", 7), fn("tie", 7)],
    )
    monkeypatch.setattr(treemap.lizard, "analyze",
                        lambda **kw: [fake], raising=False)
    monkeypatch.setattr(treemap, "scc_scores", lambda root, **kw: {})
    names: dict = {}
    scores = treemap.lizard_scores(tmp_path, fn_names=names)
    assert scores[src.resolve()][2] == [1.0, 7.0, 7.0]
    assert names == {src.resolve(): "gnarly"}

    via_collect: dict = {}
    treemap.collect(tmp_path, by="complexity", fn_names=via_collect)
    assert via_collect == {src.resolve(): "gnarly"}


def test_stats_schema_version_raised_for_backend_by_language(treemap):
    assert treemap.STATS_SCHEMA_VERSION >= 4


# Approximate per-function backend for Dart (issue #364)


def test_collect_dart_scanner_scores_scc_dart_files(
        treemap, tmp_path, monkeypatch):
    """collect adds the Dart scanner's per-function figures to scc-scored
    .dart files, names the worst function and records the backend; other
    scc files stay without a breakdown."""
    dart = tmp_path / "lib" / "order.dart"
    dart.parent.mkdir()
    dart.write_text("int a(x) { if (x) {} return 1; }\nint b() => 2;\n")
    ex = tmp_path / "router.ex"
    ex.write_text("defmodule R do\nend\n")
    monkeypatch.setattr(treemap, "lizard_scores", lambda root, **kw: {})
    monkeypatch.setattr(
        treemap, "scc_scores",
        lambda root, **kw: {dart.resolve(): (2, 3.0), ex.resolve(): (2, 1.0)})
    names: dict = {}
    backends: dict = {}
    *_, fn_ccn = treemap.collect(tmp_path, by="complexity",
                                 fn_names=names, fn_backends=backends)
    assert fn_ccn == {dart.resolve(): [2.0, 1.0]}
    assert names == {dart.resolve(): "a"}
    assert backends == {dart.resolve(): "dart-scanner"}


def test_write_stats_dart_scanner_marked_approximate(treemap, tmp_path):
    """The Dart scanner appears in fn_ccn.source with approximate true, maps
    Dart in backend_by_language, and fills the Dart row's max_fn_ccn and
    max_fn_name."""
    root = tmp_path
    py = root / "app.py"
    dart = root / "order.dart"
    out = root / "stats.json"
    treemap.write_stats(
        [(py, 20, 8.0, "lizard"), (dart, 40, 30.0, "scc")],
        None, None, root, out,
        fn_ccn_by_path={py: [7.0], dart: [1.0, 12.0]},
        fn_name_by_path={py: "gnarly", dart: "routeOrder"},
        languages_by_path={py: "Python", dart: "Dart"},
        fn_backend_by_path={dart: "dart-scanner"},
    )
    stats = json.loads(out.read_text())
    fn = stats["fn_ccn"]
    assert fn["source"] == [{"name": "dart-scanner", "approximate": True},
                            {"name": "lizard", "approximate": False}]
    assert fn["backend_by_language"] == {"Dart": "dart-scanner",
                                         "Python": "lizard"}
    row = {r["path"]: r for r in stats["top_hotspots"]}["order.dart"]
    assert (row["max_fn_ccn"], row["max_fn_name"]) == (12.0, "routeOrder")


def test_write_stats_version_keys_are_tool_versions_or_listed_non_tools(
        treemap, tmp_path, monkeypatch):
    """Every `*_version` key write_stats emits is either a tool version that
    assess_core._stats_tool_versions reads or a stamp listed in
    _NON_TOOL_VERSION_KEYS, so the writer and the reader cannot drift."""
    import assess_core

    monkeypatch.setattr(treemap, "_scc_version", lambda: "3.7.0")
    root = tmp_path
    py, dart = root / "app.py", root / "order.dart"
    out = root / "stats.json"
    treemap.write_stats(
        [(py, 20, 8.0, "lizard"), (dart, 40, 30.0, "scc")],
        None, None, root, out,
        fn_ccn_by_path={py: [7.0], dart: [12.0]},
        fn_backend_by_path={dart: "dart-scanner"},
    )
    stats = json.loads(out.read_text())
    version_keys = {k for k in stats if k.endswith("_version")}
    tools = {f"{t}_version" for t in assess_core._stats_tool_versions(stats)}
    assert tools == {"lizard_version", "scc_version"}
    assert version_keys - tools == set(assess_core._NON_TOOL_VERSION_KEYS)


def test_collect_dart_scanner_skips_files_with_no_function(
        treemap, tmp_path, monkeypatch):
    """A Dart file the scanner finds no function in stays scc-only, so it
    records no backend and cannot make Dart read as covered."""
    dart = tmp_path / "consts.dart"
    dart.write_text("const a = 1;\n")
    monkeypatch.setattr(treemap, "lizard_scores", lambda root, **kw: {})
    monkeypatch.setattr(treemap, "scc_scores",
                        lambda root, **kw: {dart.resolve(): (1, 1.0)})
    backends: dict = {}
    *_, fn_ccn = treemap.collect(tmp_path, by="complexity",
                                 fn_backends=backends)
    assert fn_ccn == {} and backends == {}


def test_effective_ccn_clamps_dart_scanner_max_to_scc_aggregate(treemap):
    """A Dart row takes ccn from scc and max_fn_ccn from the scanner; a
    scanner figure above the aggregate must not lift the effective value
    past it. Lizard rows, where max <= aggregate, are unchanged."""
    assert treemap._effective_ccn(1.0, 5.0) == 1.0
    assert treemap._effective_ccn(0.0, 3.0) == 0.0
    w = treemap.PER_FUNCTION_WEIGHT
    expected = 10.0 ** w * 100.0 ** (1 - w)
    assert abs(treemap._effective_ccn(100.0, 10.0) - expected) < 1e-9


def test_stats_schema_version_raised_for_dart_scanner(treemap):
    assert treemap.STATS_SCHEMA_VERSION >= 5
