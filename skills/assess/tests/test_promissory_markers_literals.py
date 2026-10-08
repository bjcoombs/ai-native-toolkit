"""Marker text that is data, not a comment about the code (issue #486).

A marker scanner's own fixtures and pattern table are full of marker strings;
config comments quote directives to document them. None of that is a promise.
These tests pin where the line stops: a Python hit inside a string literal is
data (``tokenize`` decides), a docstring is prose and still counts for the todo
and deprecation families, a suppression counts only in a comment and never as
a backtick-quoted example or in a config file, and every real marker still
detects and still ages. Fixture strings below carry marker text on purpose.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.keyhole_signals import integrate
import lib.promissory_markers as pm
from lib.promissory_markers import MarkerScan
from lib.python_regions import (
    CODE,
    COMMENT,
    DOCSTRING,
    STRING,
    PyRegions,
    load_regions,
    python_regions,
)
from test_promissory_markers import _commit, _init_repo, _scan


def _hits(scan: MarkerScan) -> set[tuple[str, int, str]]:
    return {(m.path, m.line, m.family) for m in scan.markers}


def _scan_files(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, int, str]]:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, files, day=1)
    return _hits(_scan(repo))


# ---------------------------------------------------------------------------
# Python: tokenize decides string vs comment vs docstring
# ---------------------------------------------------------------------------

def test_python_string_literal_markers_are_data(tmp_path: Path) -> None:
    src = (
        'A = "# TODO fix this"\n'                       # 1 plain string
        "B = f\"# FIXME {A}\"\n"                       # 2 f-string
        "C = '''\n# TODO inside a multi-line string\n'''\n"  # 3-5 interior row
        'D = "#" + "x"  # TODO real trailing comment\n'  # 6 comment after a #-string
        'E = "# TODO"  # but the comment is plain\n'     # 7 only the string has it
        "F = {'k': \"# HACK in a dict\"}\n"            # 8 nested in a container
        "# XXX a real comment\n"                       # 9
    )
    got = _scan_files(tmp_path, {"m.py": src})
    assert {line for _, line, fam in got if fam == "todo"} == {6, 9}


def test_python_docstring_todo_is_prose_and_counts(tmp_path: Path) -> None:
    src = (
        '"""Module doc.\n\nTODO: document the CLI flags.\n"""\n'  # row 3
        "def f():\n"
        '    """TODO: say what f returns."""\n'                    # row 6
        "    return 1\n"
        "class K:\n"
        '    """Class doc."""  # trailing comment, still a docstring\n'
        '    """DEPRECATED: use L."""\n'                           # row 10
    )
    got = _scan_files(tmp_path, {"m.py": src})
    assert ("m.py", 3, "todo") in got
    assert ("m.py", 6, "todo") in got
    assert ("m.py", 10, "deprecation") in got


def test_python_suppression_counts_only_in_a_comment(tmp_path: Path) -> None:
    src = (
        '"""Doc mentioning noqa / type: ignore as words."""\n'  # 1 docstring
        'PAT = "# noqa: E501"\n'                                # 2 string
        "import os  # noqa: E402\n"                             # 3 real
        "x = f()  # type: ignore[attr-defined]\n"               # 4 real
        "# carry an explicit ``# noqa: C901`` with a note\n"   # 5 quoted
        "y = 1  # noqa: E501  # see the ``# noqa`` docs\n"     # 6 real + quoted
        "z = 1  # see ``# noqa: E501``\n"                     # 7 quoted after code
    )
    got = _scan_files(tmp_path, {"m.py": src})
    # Row 7 stays: ruff reads a noqa anywhere in a trailing comment, so a
    # quoted directive after code still suppresses that code.
    assert {line for _, line, fam in got if fam == "suppression"} == {3, 4, 6, 7}


def test_python_disabled_test_in_string_is_data(tmp_path: Path) -> None:
    src = (
        "import pytest\n"
        'NAME = "pytest.mark.skip"\n'          # 2 string: data
        "@pytest.mark.skip\n"                  # 3 code: real
        "def test_x():\n    pass\n"
        "# @pytest.mark.skip  commented out\n"  # 6 comment: kept as today
    )
    got = _scan_files(tmp_path, {"m_test.py": src})
    assert {line for _, line, fam in got if fam == "disabled_test"} == {3, 6}


def test_pyi_stub_is_tokenized(tmp_path: Path) -> None:
    got = _scan_files(tmp_path, {
        "m.pyi": 'X: str = "# TODO not a marker"\ndef f() -> int: ...  # TODO real\n',
    })
    assert {line for _, line, fam in got if fam == "todo"} == {2}


def test_untokenizable_python_falls_back_to_line_filters(tmp_path: Path) -> None:
    files = {
        # Unterminated triple quote: tokenize raises TokenError.
        "open.py": 'x = """\n# TODO line-based fallback still sees this\n',
        # Inconsistent dedent: tokenize raises IndentationError.
        "dent.py": "if x:\n        a = 1\n    b = 2  # noqa: E501\n",
    }
    got = _scan_files(tmp_path, files)
    assert ("open.py", 2, "todo") in got
    assert ("dent.py", 3, "suppression") in got


def test_bom_file_columns_line_up(tmp_path: Path) -> None:
    got = _scan_files(tmp_path, {
        "bom.py": '\ufeffx = 1  # noqa: E501\ny = "# noqa: E501"\n',
    })
    assert {line for _, line, fam in got if fam == "suppression"} == {1}


# ---------------------------------------------------------------------------
# Other languages and config files
# ---------------------------------------------------------------------------

def test_other_languages_keep_line_based_filters(tmp_path: Path) -> None:
    """Non-Python code is not tokenized: a quoted marker after a comment
    leader still counts, as before. Only the backtick rule is new there."""
    got = _scan_files(tmp_path, {
        "a.js": (
            "// TODO real comment\n"                           # 1
            'const s = "// TODO looks like a comment";\n'      # 2 kept as today
            "x(); // eslint-disable-line no-console\n"         # 3 real
            "// write ``// eslint-disable-line`` to silence\n"  # 4 quoted
            "y(); // see `// eslint-disable-line`\n"           # 5 after code
        ),
        "b.go": "return nil //nolint:nilerr\n// TODO wire it\n",
    })
    assert {(p, ln) for p, ln, fam in got if fam == "todo"} == {
        ("a.js", 1), ("a.js", 2), ("b.go", 2),
    }
    assert {(p, ln) for p, ln, fam in got if fam == "suppression"} == {
        ("a.js", 3), ("a.js", 5), ("b.go", 1),
    }


def test_config_files_carry_no_suppressions_but_keep_todos(tmp_path: Path) -> None:
    got = _scan_files(tmp_path, {
        "pyproject.toml": (
            "# two offenders carry # noqa: C901 with a note\n"
            "# TODO drop this override once the split lands\n"
            "[tool.x]\n"
        ),
        "ci.yml": "steps:  # nosec B603\n",
        "setup.cfg": "# type: ignore everywhere\n",
    })
    assert {fam for _, _, fam in got} == {"todo"}
    assert ("pyproject.toml", 2, "todo") in got


# ---------------------------------------------------------------------------
# Aging and the finding: real markers still age, data never does
# ---------------------------------------------------------------------------

def test_real_comment_markers_age_and_string_markers_never_appear(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    body = (
        'FIXTURE = "# TODO fixture"\n'
        "# TODO real promise\n"
        "x = 1  # noqa: E501\n"
        "{n}\n"
    )
    _commit(repo, {"a.py": "a", "b.py": "b", "c.py": "c"}, day=1)
    for i in range(7):
        _commit(repo, {"app.py": body.replace("{n}", f"n = {i}")}, day=2 + i)
    _commit(repo, {"a.py": "a2", "b.py": "b2", "c.py": "c2"}, day=20)
    scan = _scan(repo)
    stale = {(m.line, m.family) for m in scan.stale}
    assert stale == {(2, "todo"), (3, "suppression")}
    summary = scan.summary()
    assert summary["total_stale"] == 2
    result = integrate(
        repo_root=repo, complexity_stats={}, doc_staleness={},
        dead_code={}, observability={}, structure={},
        promissory_markers=summary,
    )
    findings = {f["name"]: f for f in result["derived_findings"]}
    assert findings["unactioned_intent"]["paths"] == ["app.py"]


def test_fixture_only_file_yields_no_unactioned_intent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, {"a.py": "a", "b.py": "b", "c.py": "c"}, day=1)
    for i in range(7):
        _commit(repo, {"tests/test_x.py": f'CASES = ["# TODO", "# noqa"]\nn = {i}\n'},
                day=2 + i)
    _commit(repo, {"a.py": "a2", "b.py": "b2", "c.py": "c2"}, day=20)
    summary = _scan(repo).summary()
    assert summary["total_stale"] == 0
    assert summary["stale_by_file"] == {}


# ---------------------------------------------------------------------------
# python_regions unit cases
# ---------------------------------------------------------------------------

def test_regions_classify_each_token_kind() -> None:
    src = (
        '"""doc"""\n'                 # 1 module docstring
        'x = "s"  # c\n'              # 2 string, code, comment
        "def f():\n"
        "    '''fdoc'''\n"            # 4 function docstring
        '    return ("a"\n'           # 5 implicit concatenation: string
        '            "b")\n'
        'y = f"{x!r} {f\'{x}\'}"\n'   # 7 nested f-string: one string span
        '"bare"; z = 1\n'             # 8 not alone on its line: string
    )
    r = python_regions(src)
    assert r is not None
    assert r.region(1, 0) == DOCSTRING
    assert r.region(2, 0) == CODE
    assert r.region(2, 4) == STRING
    assert r.region(2, 9) == COMMENT
    assert r.region(4, 4) == DOCSTRING
    assert r.region(5, 12) == STRING
    assert r.region(6, 13) == STRING
    assert r.region(7, 4) == STRING
    assert r.region(7, 16) == STRING
    assert r.region(8, 0) == STRING
    assert r.region(8, 8) == CODE


def test_regions_merge_implicitly_concatenated_literals() -> None:
    """Python reads adjacent literals as one string, so a statement made of
    them is one docstring, and a marker in its second part still counts."""
    r = python_regions('"""a""" """TODO: b"""\nx = ("c"\n     f"d{x}")\n')
    assert r is not None
    assert r.region(1, 0) == DOCSTRING
    assert r.region(1, 7) == DOCSTRING  # the gap between the two parts
    assert r.region(1, 12) == DOCSTRING
    assert r.region(2, 5) == STRING
    assert r.region(3, 5) == STRING
    assert len(r.spans) == 2


def test_concatenated_docstring_todo_counts(tmp_path: Path) -> None:
    got = _scan_files(tmp_path, {
        "m.py": 'def f():\n    """Doc.""" """TODO: say what f returns."""\n',
    })
    assert ("m.py", 2, "todo") in got


def test_regions_multiline_string_interior_and_end() -> None:
    r = python_regions('s = """\nTODO in here\n"""  # after\n')
    assert r is not None
    assert r.region(2, 0) == STRING
    assert r.region(3, 2) == STRING
    assert r.region(3, 3) == CODE
    assert r.region(3, 5) == COMMENT


def test_regions_none_on_tokenize_failure() -> None:
    assert python_regions('x = """\nnever closed\n') is None
    assert python_regions("if x:\n        a\n    b\n") is None


def test_load_regions_memoises_and_tolerates_missing_files(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("# c\n", encoding="utf-8")
    cache: dict[str, PyRegions | None] = {}
    first = load_regions(tmp_path, "m.py", cache)
    assert first is not None and first.region(1, 0) == COMMENT
    assert load_regions(tmp_path, "m.py", cache) is first
    assert load_regions(tmp_path, "gone.py", cache) is None
    assert "gone.py" in cache


# ---------------------------------------------------------------------------
# Reproducibility: rg's output order must not reach run-context.json
# ---------------------------------------------------------------------------

def test_summary_is_byte_stable_whatever_order_rg_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rg searches files on parallel threads, so two runs on one tree can list
    hits in different orders. Reversing its output must not change a byte of
    the summary: not the stale_by_file key order, not top_offenders ties."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    files = {f"f{i}.py": "# FIXME a\nx = 1  # noqa: E501\n" for i in range(6)}
    files.update({"a.py": "a", "b.py": "b", "c.py": "c"})
    _commit(repo, files, day=1)
    for i in range(3):
        _commit(repo, {f"f{j}.py": f"# FIXME a\nx = 1  # noqa: E501\nn = {i}\n"
                       for j in range(6)}, day=2 + i)

    def dump() -> str:
        return json.dumps(_scan(repo, stale_touches=1).summary())

    forward = dump()
    real_run = pm._run

    def reversed_rg(cmd: list[str], cwd: Path) -> str:
        out = real_run(cmd, cwd)
        return "\n".join(reversed(out.splitlines())) if cmd[0] == "rg" else out

    monkeypatch.setattr(pm, "_run", reversed_rg)
    assert dump() == forward
    assert list(json.loads(forward)["stale_by_file"]) == sorted(
        json.loads(forward)["stale_by_file"]
    )
