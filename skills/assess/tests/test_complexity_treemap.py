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
