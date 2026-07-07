"""Golden-SVG tests: run the REAL renderers and lock their colour encoding.

Unlike ``test_complexity_treemap.py`` / ``test_doc_graph_svg.py`` (which stub
matplotlib to exercise pure logic), these tests drive the *actual* render path:
they synthesize a git history over a tiny fixture repo, invoke the shipped
script via ``uv run --script`` (matplotlib/lizard/squarify resolved from the
script's PEP-723 inline metadata - no stubbing, no mocking), and parse the SVG
the renderer produces on disk. That is the only way to prove the whole pipeline
- lizard -> cap/blend maths -> OrRd colormap -> SVG - actually encodes
``ccn -> hue`` and ``churn -> saturation`` the way the report claims.

The fixtures and their expected values are documented in
``tests/fixtures/golden-svg-repo/README.md`` and
``tests/fixtures/golden-doc-repo/FIXTURE.md``. If the colour maths changes these
assertions fail - that is the point: update the fixture tables and these tests
together, deliberately.

``uv`` drives the isolated script env, so the tests skip when it is absent
(never true in CI, which installs uv). Renders are cached by uv after the first
resolve; the fixtures are deliberately tiny to keep CI runtime low.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"

# uv runs the script in an isolated env from its inline metadata. Skip only when
# uv is missing (a machine that can't run the scripts at all); CI always has it.
_UV = shutil.which("uv")
pytestmark = pytest.mark.skipif(_UV is None, reason="uv not on PATH")


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _chroma(rgb: tuple[int, int, int]) -> int:
    """Saturation proxy: distance between the max and min RGB channel. A vivid
    (saturated) colour has a large spread; a colour blended toward neutral grey
    collapses toward zero chroma."""
    return max(rgb) - min(rgb)


def _git(repo: Path, *args: str, env: dict) -> None:
    subprocess.run(["git", "-C", str(repo), *args],
                   check=True, capture_output=True, text=True, env=env)


def _git_env() -> dict:
    """Ambient-config-free git env, matching conftest's hermetic-git policy, so a
    signing-enabled global config can't break commits in the disposable repo."""
    env = {**os.environ}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    # uv must resolve the script's OWN isolated env from inline metadata, not
    # reuse the pytest runner's active venv.
    env.pop("VIRTUAL_ENV", None)
    return env


def _init_repo(repo: Path, env: dict) -> None:
    _git(repo, "init", "-q", env=env)
    _git(repo, "config", "user.email", "test@example.com", env=env)
    _git(repo, "config", "user.name", "Test", env=env)


def _run_script(script: str, repo: Path, out: Path, env: dict,
                *extra: str) -> str:
    """Run a shipped renderer via ``uv run --script`` and return the SVG text."""
    result = subprocess.run(
        ["uv", "run", "--script", str(_SCRIPTS / script),
         str(repo), "-o", str(out), *extra],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, (
        f"{script} failed (rc={result.returncode})\nstderr:\n{result.stderr}"
    )
    assert out.exists(), f"{script} did not write {out}\nstderr:\n{result.stderr}"
    return out.read_text(encoding="utf-8")


# ── complexity-treemap.py (code heatmap) ──────────────────────────────────────

_RECT_RE = re.compile(
    r'<rect\b[^>]*\bfill="(#[0-9a-f]{6})"[^>]*>\s*<title>(.*?)</title>',
    re.DOTALL,
)


def _parse_rects(svg: str) -> dict[str, tuple[str, str]]:
    """Map ``basename -> (fill_hex, full_title)`` for every leaf rect. The
    tooltip's first line is the file's relative path."""
    out: dict[str, tuple[str, str]] = {}
    for fill, title in _RECT_RE.findall(svg):
        rel = title.splitlines()[0].strip()
        out[Path(rel).name] = (fill, title)
    return out


@pytest.fixture(scope="module")
def treemap_svg(tmp_path_factory) -> str:
    """Render golden-svg-repo through the real complexity-treemap pipeline.

    hot.py + simple_active.py are committed several extra times to create a
    churn gradient; complex_stable.py + simple_stable.py stay at one commit.
    """
    env = _git_env()
    repo = tmp_path_factory.mktemp("golden_svg_repo")
    src = _FIXTURES / "golden-svg-repo"
    # Copy only the scored source; README.md is fixture documentation, not input.
    for name in ("hot.py", "complex_stable.py",
                 "simple_active.py", "simple_stable.py"):
        shutil.copy(src / name, repo / name)

    _init_repo(repo, env)
    _git(repo, "add", "-A", env=env)
    _git(repo, "commit", "-q", "-m", "init", env=env)
    # Churn the two "active" files so churn (saturation) is a live gradient.
    for i in range(4):
        for name in ("hot.py", "simple_active.py"):
            with (repo / name).open("a", encoding="utf-8") as fh:
                fh.write(f"\n# churn {i}\n")
        _git(repo, "add", "-A", env=env)
        _git(repo, "commit", "-q", "-m", f"churn {i}", env=env)

    out = repo / "out.svg"
    return _run_script("complexity-treemap.py", repo, out, env)


def test_treemap_a11y_metadata(treemap_svg):
    """Task 17: the root <svg> is role="img" with a <title>/<desc> pair as its
    first children (the accessible name + description)."""
    assert 'role="img"' in treemap_svg
    assert "<title>Complexity Hotspot Heatmap</title>" in treemap_svg
    assert (
        "<desc>Treemap showing code complexity by file size, hue indicates "
        "cyclomatic complexity, saturation indicates git churn</desc>"
    ) in treemap_svg
    # <title>/<desc> precede the first drawn element (<style>), i.e. they are the
    # root's first children.
    assert treemap_svg.index("<title>") < treemap_svg.index("<style>")
    assert treemap_svg.index("<desc>") < treemap_svg.index("<style>")


def test_treemap_per_rect_titles_carry_path_loc_ccn(treemap_svg):
    """Task 17: every file rect has a <title> naming the path, LOC and CCN."""
    rects = _parse_rects(treemap_svg)
    assert set(rects) == {
        "hot.py", "complex_stable.py", "simple_active.py", "simple_stable.py"
    }
    for name, (_fill, title) in rects.items():
        assert name in title
        assert "loc" in title
        assert "ccn" in title
    # The two complex files scored the known aggregate CCN 21.
    assert "ccn 21" in rects["hot.py"][1]
    assert "ccn 21" in rects["complex_stable.py"][1]


def test_treemap_ccn_maps_to_red_hue(treemap_svg):
    """ccn -> hue: with churn held equal (both high-churn), the high-CCN file is
    dark red while the low-CCN file is pale - red = low green/blue channels."""
    rects = _parse_rects(treemap_svg)
    hot = _hex_to_rgb(rects["hot.py"][0])           # ccn 21, churn high
    simple = _hex_to_rgb(rects["simple_active.py"][0])  # ccn 2, churn high
    # High CCN collapses green and blue toward zero (OrRd dark-red end).
    assert hot[1] < simple[1], "high-CCN green channel must be lower (redder)"
    assert hot[2] < simple[2], "high-CCN blue channel must be lower (redder)"
    assert hot[0] >= 120, "red channel stays high at the dark-red end"
    # Exact golden lock (see fixture README).
    assert rects["hot.py"][0] == "#7f0000"


def test_treemap_churn_maps_to_saturation(treemap_svg):
    """churn -> saturation: with CCN held equal (both aggregate 21), the
    high-churn file is vivid and the low-churn file is blended toward grey."""
    rects = _parse_rects(treemap_svg)
    hot = _hex_to_rgb(rects["hot.py"][0])              # ccn 21, churn high
    stable = _hex_to_rgb(rects["complex_stable.py"][0])  # ccn 21, churn low
    assert _chroma(hot) > _chroma(stable), (
        "high-churn file must be more saturated than the frozen one"
    )
    assert _chroma(hot) > 100 and _chroma(stable) < 50
    # Same axis at the low-CCN end: active file stays more saturated than frozen.
    active = _hex_to_rgb(rects["simple_active.py"][0])
    idle = _hex_to_rgb(rects["simple_stable.py"][0])
    assert _chroma(active) > _chroma(idle)
    # Exact golden lock (see fixture README).
    assert rects["complex_stable.py"][0] == "#c0a7ab"


# ── doc-graph-svg.py (doc navigability graph) ─────────────────────────────────

_CIRCLE_RE = re.compile(r'(<circle\b[^>]*>)\s*<title>(.*?)</title>', re.DOTALL)
_ATTR_RE = re.compile(r'(\S+?)="([^"]*)"')


def _parse_circles(svg: str) -> dict[str, dict]:
    """Map ``doc-basename -> {fill, stroke, stroke-width, title}`` for each node.
    The tooltip's first line is the doc's path."""
    out: dict[str, dict] = {}
    for tag, title in _CIRCLE_RE.findall(svg):
        attrs = dict(_ATTR_RE.findall(tag))
        rel = title.splitlines()[0].strip()
        attrs["title"] = title
        out[Path(rel).name] = attrs
    return out


@pytest.fixture(scope="module")
def doc_graph_svg(tmp_path_factory) -> str:
    """Render golden-doc-repo through the real doc-graph-svg pipeline.

    old.md is committed far in the past (stale); everything else is committed
    "now". src/app.py is churned so the staleness saturation axis is live.
    """
    env = _git_env()
    repo = tmp_path_factory.mktemp("golden_doc_repo")
    src = _FIXTURES / "golden-doc-repo"
    (repo / "src").mkdir()
    # Explicit allowlist: FIXTURE.md is documentation, not a graphed doc.
    for name in ("README.md", "guide.md", "old.md"):
        shutil.copy(src / name, repo / name)
    shutil.copy(src / "src" / "app.py", repo / "src" / "app.py")

    _init_repo(repo, env)
    old = "2020-01-01T00:00:00"
    stale_env = {**env, "GIT_AUTHOR_DATE": old, "GIT_COMMITTER_DATE": old}
    _git(repo, "add", "old.md", env=env)
    _git(repo, "commit", "-q", "-m", "old notes", env=stale_env)
    _git(repo, "add", "README.md", "guide.md", "src/app.py", env=env)
    _git(repo, "commit", "-q", "-m", "docs + code", env=env)
    for i in range(3):
        with (repo / "src" / "app.py").open("a", encoding="utf-8") as fh:
            fh.write(f"\n# churn {i}\n")
        _git(repo, "add", "-A", env=env)
        _git(repo, "commit", "-q", "-m", f"code churn {i}", env=env)

    out = repo / "doc.svg"
    return _run_script("doc-graph-svg.py", repo, out, env)


def test_doc_graph_a11y_metadata(doc_graph_svg):
    """Task 17: root <svg> is role="img" with the doc-graph <title>/<desc> pair
    as its first children."""
    assert 'role="img"' in doc_graph_svg
    assert "<title>Documentation Navigability Graph</title>" in doc_graph_svg
    assert (
        "<desc>Graph showing documentation structure, reachability from entry "
        "point, and staleness indicators</desc>"
    ) in doc_graph_svg
    assert doc_graph_svg.index("<title>") < doc_graph_svg.index("<style>")
    assert doc_graph_svg.index("<desc>") < doc_graph_svg.index("<style>")


def test_doc_graph_per_node_labels_carry_path_and_staleness(doc_graph_svg):
    """Task 17: every node's <title> is an accessible label with the doc path
    and its staleness."""
    circles = _parse_circles(doc_graph_svg)
    assert {"README.md", "guide.md", "old.md"} <= set(circles)
    for name in ("README.md", "guide.md", "old.md"):
        title = circles[name]["title"]
        assert name in title
        assert "stale" in title


def test_doc_graph_entry_node_marked(doc_graph_svg):
    """The entry point (README.md) carries the blue entry ring - the navigation
    root stays obvious even though colour now encodes staleness."""
    circles = _parse_circles(doc_graph_svg)
    entry = circles["README.md"]
    assert entry["stroke"] == "#0072B2"
    assert float(entry["stroke-width"]) >= 3.0
    assert "entry" in entry["title"]


def test_doc_graph_staleness_maps_to_red_hue(doc_graph_svg):
    """days-stale -> hue: the stale doc (committed years ago) is dark red while
    the fresh entry/guide docs are pale - staleness = low green/blue channels."""
    circles = _parse_circles(doc_graph_svg)
    old = _hex_to_rgb(circles["old.md"]["fill"])
    fresh = _hex_to_rgb(circles["README.md"]["fill"])
    assert old[1] < fresh[1], "stale doc green channel must be lower (redder)"
    assert old[2] < fresh[2], "stale doc blue channel must be lower (redder)"
    assert _chroma(old) > _chroma(fresh), "stale doc must be more saturated"
    # Exact golden lock (see fixture FIXTURE.md): oldest doc caps at darkest red,
    # same-day docs sit at the pale OrRd end.
    assert circles["old.md"]["fill"] == "#7f0000"
    assert circles["README.md"]["fill"] == "#fff7ec"
    assert circles["guide.md"]["fill"] == "#fff7ec"
