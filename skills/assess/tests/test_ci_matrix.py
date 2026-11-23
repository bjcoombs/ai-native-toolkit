"""Tests for lib/ci_matrix.py: matrix commands copied out per value, or left unknown (#923)."""
from __future__ import annotations

import pytest

from lib.ci_matrix import expand_matrix


def _job(matrix: str, steps: str) -> str:
    return (f"on: pull_request\njobs:\n  t:\n    runs-on: x\n    strategy:\n      matrix:\n{matrix}"
            f"    steps:\n{steps}")


_FLOW = "        cmd: ['ruff check .', \"pytest\"]\n"


def test_a_flow_list_command_is_copied_once_per_value() -> None:
    out = expand_matrix(_job(_FLOW, "      - run: ${{ matrix.cmd }}\n        shell: bash\n"))
    lines = out.text.splitlines()
    assert "      - run: ruff check ." in lines and "      - run: pytest" in lines
    assert lines.count("        shell: bash") == 2   # each copy keeps its own keys
    assert out.unknown == frozenset()


def test_a_block_list_axis_is_read_and_its_items_blanked() -> None:
    matrix = "        cmd:\n          - ruff check .\n          - pytest -q\n"
    out = expand_matrix(_job(matrix, "      - run: |\n          ${{ matrix.cmd }}\n"))
    lines = out.text.splitlines()
    assert "          - ruff check ." not in lines   # the consumed axis value is not a second run
    assert lines.count("          ruff check .") == 1 and lines.count("          pytest -q") == 1


def test_include_and_exclude_are_combined_as_github_does() -> None:
    matrix = ("        os: [a, b]\n        cmd: [pytest, 'ruff check .']\n        exclude:\n"
              "          - os: b\n            cmd: pytest\n        include:\n"
              "          - os: a\n            extra: mypy .\n          - cmd: eslint .\n")
    out = expand_matrix(_job(matrix, "      - run: ${{ matrix.cmd }} && ${{ matrix.extra }}\n"))
    runs = [line.strip() for line in out.text.splitlines() if line.strip().startswith("- run:")]
    assert runs == ["- run: pytest && mypy .", "- run: ruff check . && mypy ."]   # extra only on os a


def test_a_command_after_a_separator_is_expanded() -> None:
    out = expand_matrix(_job(_FLOW, "      - run: cd x; ${{ matrix.cmd }}\n"))
    assert "      - run: cd x; pytest" in out.text.splitlines()


def test_an_argument_position_reference_is_left_as_it_was() -> None:
    text = _job("        m: [core, it]\n", "      - run: mvn -Dset=${{ matrix.m }} verify\n")
    assert expand_matrix(text) == (text, frozenset())


@pytest.mark.parametrize(("matrix", "run"), [
    ("        cmd: ${{ fromJSON(needs.a.outputs.c) }}\n", "${{ matrix.cmd }}"),
    ("        include: ${{ fromJSON(x) }}\n", "${{ matrix.cmd }}"),
    (_FLOW + "        include:\n          - cmd: ${{ inputs.c }}\n", "${{ matrix.cmd }}"),
    (_FLOW, "${{ matrix.other }}"),               # a key no combination sets
    (_FLOW, "${{ matrix.cmd.name }}"),            # not a literal key
    ("        cmd: [a,\n          b]\n", "${{ matrix.cmd }}"),   # a flow list over two lines
])
def test_an_unexpandable_command_is_unknown(matrix: str, run: str) -> None:
    text = _job(matrix, f"      - run: {run}\n")
    out = expand_matrix(text)
    assert out.text == text
    assert [out.text.splitlines()[i] for i in out.unknown] == [f"      - run: {run}"]


def test_a_job_without_a_matrix_is_unknown() -> None:
    text = "on: push\njobs:\n  t:\n    steps:\n      - run: |\n          ${{ matrix.cmd }}\n"
    assert sorted(expand_matrix(text).unknown) == [4, 5]


def test_unknown_indexes_follow_earlier_copies() -> None:
    text = _job(_FLOW, "      - run: ${{ matrix.cmd }}\n      - run: ${{ matrix.nope }}\n")
    out = expand_matrix(text)
    assert [out.text.splitlines()[i] for i in out.unknown] == ["      - run: ${{ matrix.nope }}"]


def test_a_with_run_input_is_expanded() -> None:
    out = expand_matrix(_job(_FLOW, "      - uses: ./act\n        with:\n          run: ${{ matrix.cmd }}\n"))
    assert out.text.count("          run: pytest") == 1
    assert out.text.count("      - uses: ./act") == 2


def test_text_without_jobs_is_unchanged() -> None:
    assert expand_matrix("stages: [test]\n") == ("stages: [test]\n", frozenset())
