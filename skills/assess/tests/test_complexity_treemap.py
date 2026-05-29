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
        return [], "complexity", None, None

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
        return [], "complexity", None, None

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
