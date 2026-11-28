"""The dominant-file warning names a large file as dominant, and calls it a
likely build artifact only on content evidence (#1491).

The tie-break by path for the same warning stays in
test_complexity_treemap_ordering.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from test_complexity_treemap import stubbed_treemap

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "browser_extension"


@pytest.fixture(scope="module")
def treemap():
    with stubbed_treemap() as mod:
        yield mod


def _row(path: Path, ccn: float = 1.0):
    loc = len(path.read_text(encoding="utf-8").splitlines())
    return (path, loc, ccn, "lizard")


def test_hand_written_browser_script_is_dominant_not_an_artifact(
        treemap, capsys):
    """A 388-line hand-written content script holds most of a small extension.
    The warning reports the concentration and never calls it a build artifact
    or sends the reader to .gitignore."""
    files = [_row(_FIXTURE / "content.js"), _row(_FIXTURE / "popup.js"),
             _row(_FIXTURE / "manifest.json", 0.0)]
    assert files[0][1] / sum(f[1] for f in files) > treemap.DOMINANCE_WARN_THRESHOLD

    treemap._warn_if_dominated_by_one_file(files)
    err = capsys.readouterr().err

    assert "content.js" in err
    assert "dominant" in err
    assert "build artifact" not in err
    assert ".gitignore" not in err


def test_dominant_bundle_with_sourcemap_is_called_a_likely_artifact(
        treemap, tmp_path, capsys):
    bundle = tmp_path / "app.js"
    bundle.write_text("var a = 1;\n" * 400 + "//# sourceMappingURL=app.js.map\n",
                      encoding="utf-8")
    small = tmp_path / "main.py"
    small.write_text("x = 1\n" * 50, encoding="utf-8")

    treemap._warn_if_dominated_by_one_file([_row(bundle), _row(small)])
    err = capsys.readouterr().err

    assert "app.js" in err
    assert "likely a build artifact" in err
    assert "sourcemap" in err
    assert ".gitignore" in err


def test_no_warning_below_threshold(treemap, tmp_path, capsys):
    rows = []
    for i in range(4):
        p = tmp_path / f"m{i}.js"
        p.write_text("var a = 1;\n" * 100, encoding="utf-8")
        rows.append(_row(p))
    treemap._warn_if_dominated_by_one_file(rows)
    assert capsys.readouterr().err == ""
