"""The scc-only hint and the deterministic tie-break by path across the stats
top lists, the scc-only hint, the dominant-file warning and largest-first.

Split from test_complexity_treemap.py (#720); the stubbed loader lives there.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_complexity_treemap import _load_treemap


@pytest.fixture(scope="module")
def treemap():
    return _load_treemap()


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


def _tied_rows(root: Path) -> list[tuple[Path, int, float, str]]:
    """Twelve files tied on every ranking key (issue #426).

    `loc` and `ccn` are passed as literals, so the only file-derived value is
    `est_token_count` (chars/4). What must hold is therefore identical byte
    *length*, which the zero-padded function names (`a01` to `a12`) preserve
    and a bare `a1`/`a10` would quietly break. The names are distinct so the
    same fixture also ties when lizard scans it for real, since lizard
    de-duplicates byte-identical sources and leaves the copies to scc.

    File names are unpadded (`f1` to `f12`), so byte order is
    `f1, f10, f11, f12, f2, ...` and `f8`/`f9` fall outside the first ten - an
    outcome no input order, enumeration order or numeric order produces by
    accident.
    """
    src = root / "src"
    src.mkdir()
    rows = []
    for i in range(1, 13):
        path = src / f"f{i}.py"
        path.write_text(f"def a{i:02d}(a):\n    return a\n")
        rows.append((path, 2, 1.0, "lizard"))
    return rows


_TIED_FIRST_TEN = [
    "src/f1.py", "src/f10.py", "src/f11.py", "src/f12.py", "src/f2.py",
    "src/f3.py", "src/f4.py", "src/f5.py", "src/f6.py", "src/f7.py",
]


def test_write_stats_tie_break_by_path_takes_first_ten_in_path_order(
        treemap, tmp_path):
    """Equal-scoring files fill a top-10 list in ascending path order.

    Twelve files tie on every ranking key, so which ten make each list is
    decided entirely by the tie-break. Byte order on the repository-relative
    path puts f8 and f9 outside the first ten; scanner emission order (what a
    single-key stable sort hands back) leaves them inside (issue #426).
    """
    rows = _tied_rows(tmp_path)
    out = tmp_path / "stats.json"
    treemap.write_stats(rows, None, None, tmp_path, out,
                        fn_ccn_by_path={p: [1.0] for p, *_ in rows})

    stats = json.loads(out.read_text())
    for key in ("top_hotspots", "top_complex", "top_large"):
        assert [r["path"] for r in stats[key]] == _TIED_FIRST_TEN, key
    # Every row in this fixture ties, which is what makes the list above
    # evidence about the tie-break alone. Precedence of the primary key is a
    # separate claim, tested below against a file whose path sorts last.
    assert len({r["ccn"] for r in stats["top_complex"]}) == 1
    assert len({r["loc"] for r in stats["top_large"]}) == 1


def test_write_stats_primary_key_outranks_tie_break_by_path(treemap, tmp_path):
    """A higher-scoring file leads every list even when its path sorts last.

    The tie-break must order only rows that are already equal. `zbig.py`
    dominates on ccn, loc and the composite, and sorts after every tied file
    by path, so a key that put path first would bury it (issue #426).
    """
    rows = _tied_rows(tmp_path)
    dominant = tmp_path / "src" / "zbig.py"
    dominant.write_text(
        "def big(a):\n"
        + "".join(f"    if a == {i}:\n        return {i}\n" for i in range(1, 41))
        + "    return 0\n"
    )
    rows.append((dominant, 82, 41.0, "lizard"))
    # The file aggregate is the sum of its per-function values, so a lizard
    # file's worst function is the largest term of it and can equal it. Giving
    # zbig a single function of 41 keeps the fixture a state the scanner can
    # produce; leaving it at 1.0 would de-rate the effective ccn to about 3.05
    # (see `_effective_ccn`) and the composite assertion would pass on the
    # token axis rather than the complexity axis this test names.
    fn_ccn = {p: [1.0] for p, *_ in rows}
    fn_ccn[dominant] = [41.0]
    out = tmp_path / "stats.json"
    treemap.write_stats(rows, None, None, tmp_path, out, fn_ccn_by_path=fn_ccn)

    stats = json.loads(out.read_text())
    assert {r["path"]: r["max_fn_ccn"] for r in stats["top_complex"]}[
        "src/zbig.py"] == 41.0
    for key in ("top_hotspots", "top_complex", "top_large"):
        paths = [r["path"] for r in stats[key]]
        assert paths[0] == "src/zbig.py", key
        # The nine places left go to the tied files in path order, so f7, f8
        # and f9 fall out.
        assert paths[1:] == _TIED_FIRST_TEN[:9], key


def test_write_stats_tie_break_by_path_is_input_order_independent(
        treemap, tmp_path):
    """Reversing the order the file list reaches the sort changes nothing.

    A directory scan cannot vary that order - the scanner enumerates its own
    way - so the seam is driven directly: two `write_stats` calls over the same
    twelve tied rows, one as built and one reversed, must write byte-identical
    top-10 lists (issue #426).
    """
    rows = _tied_rows(tmp_path)
    fn_ccn = {p: [1.0] for p, *_ in rows}
    as_built = tmp_path / "as_built.json"
    reversed_out = tmp_path / "reversed.json"
    treemap.write_stats(rows, None, None, tmp_path, as_built,
                        fn_ccn_by_path=fn_ccn)
    treemap.write_stats(list(reversed(rows)), None, None, tmp_path,
                        reversed_out, fn_ccn_by_path=fn_ccn)

    first = json.loads(as_built.read_text())
    second = json.loads(reversed_out.read_text())
    for key in ("top_hotspots", "top_complex", "top_large"):
        assert first[key] == second[key], key
        assert [r["path"] for r in first[key]] == _TIED_FIRST_TEN, key


def test_scc_only_hint_tie_break_by_path_survives_input_order(
        treemap, tmp_path, capsys):
    """A tied boundary picks the same five files whatever order they arrive in.

    Six files tie on estimated tokens for five places. `zz.py` is lizard-scored
    code, so it disqualifies the set the moment it is inside it, and its path
    sorts last. Byte order therefore leaves it out and the hint fires; scanner
    emission order could pull it in and silence the hint on one run and not the
    next. Same defect class as the top-10 lists, found while fixing them.
    """
    data = [_scc_row(tmp_path, f"d{i}.json", 200) for i in range(1, 6)]
    code = _scc_row(tmp_path, "zz.py", 200, 5.0, "lizard")
    tokens = {f[0]: 2000 for f in data + [code]}

    outputs = []
    for files in ([code] + data, data + [code]):
        treemap._hint_if_largest_files_scc_only(files, tokens, _langs(files))
        outputs.append(capsys.readouterr().err)

    assert outputs[0] == outputs[1]
    assert ".assess/config.toml" in outputs[0]
    # The five data files by path, and never the disqualifying code file.
    for i in range(1, 6):
        assert f"d{i}.json" in outputs[0]
    assert "zz.py" not in outputs[0]


def test_dominant_file_warning_tie_break_by_path_survives_input_order(
        treemap, tmp_path, capsys):
    """Two equally-large files name the same suspect whatever order they
    arrive in. Both hold 40% of the LOC, over the 30% threshold, so the tie is
    reachable; `max` returned whichever the scanner emitted first (issue #426).
    """
    a = _scc_row(tmp_path, "aaa.json", 400)
    z = _scc_row(tmp_path, "zzz.json", 400)
    small = _scc_row(tmp_path, "small.py", 200, 1.0, "lizard")

    outputs = []
    for files in ([z, a, small], [a, z, small]):
        treemap._warn_if_dominated_by_one_file(files)
        outputs.append(capsys.readouterr().err)

    assert outputs[0] == outputs[1]
    assert "aaa.json" in outputs[0]
    assert "zzz.json" not in outputs[0]


def test_largest_first_tie_break_by_path_survives_input_order(
        treemap, tmp_path):
    """The selection both stderr summaries share picks the same few files
    whatever order they arrive in.

    Twelve files tie on tokens for five places. Unpadded names make byte order
    `b1, b10, b11, b12, b2`, which neither creation order, numeric order nor
    the reversed input can produce by accident, so the assertion can only hold
    if the path tie-break ran (issue #426).
    """
    rows = [_scc_row(tmp_path, f"b{i}.json", 200) for i in range(1, 13)]
    tokens = {f[0]: 2000 for f in rows}
    expected = ["b1.json", "b10.json", "b11.json", "b12.json", "b2.json"]

    def size_of(f):
        return tokens.get(f[0], f[1])

    as_built = treemap._largest_first(rows, size_of, 5)
    reversed_in = treemap._largest_first(list(reversed(rows)), size_of, 5)

    assert [f[0].name for f in as_built] == expected
    assert as_built == reversed_in


def test_largest_first_keeps_size_order_where_sizes_differ(treemap, tmp_path):
    """The path tie-break orders only files that are already equal: a bigger
    file leads even when its name sorts last."""
    rows = [_scc_row(tmp_path, f"b{i}.json", 200) for i in range(1, 13)]
    big = _scc_row(tmp_path, "zz.json", 900)
    rows.append(big)
    tokens = {f[0]: (9000 if f is big else 2000) for f in rows}

    picked = treemap._largest_first(rows, lambda f: tokens.get(f[0], f[1]), 3)
    assert [f[0].name for f in picked] == ["zz.json", "b1.json", "b10.json"]
