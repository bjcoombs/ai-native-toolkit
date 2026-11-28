"""How the apidiff reader decides a shell branch re-raises (#1617): ``lib.ci.apidiff_reraise``."""
from __future__ import annotations

import pytest

from lib.ci.apidiff_reraise import SUBSHELL, backgrounded, branch_reraises, exits, line_ends, reraises
from lib.ci.ci_job_commands import OPERATOR


@pytest.mark.parametrize(("cmd", "want"), [
    (["exit", "1"], True), (["then", "exit", "1"], True), (["{", "exit", "1"], True), (["false"], True),
    (["return", "$rc"], True), (["exit", "0"], False), (["exit", "$?"], False),
    (["echo", "false"], False), (["echo", "exit", "1"], False),
])
def test_a_re_raise_is_the_command_itself(cmd: list[str], want: bool) -> None:
    assert reraises(cmd) is want


def test_exits_needs_a_non_zero_literal() -> None:
    assert exits(["exit", "1"]) and exits(["exit", '"2"'])
    assert not exits(["exit", "0"]) and not exits(["exit", "$rc"]) and not exits(["false"])


@pytest.mark.parametrize(("tail", "want"), [
    ("&& { echo broke; exit 1; } || true", True),    # the exit ends the shell first
    ("&& { echo broke; false; } || true", False),    # ``|| true`` rescues false
    ("&& echo broke && { false && exit 1; } || true", False),
    ("&& ( echo broke; exit 1 ) || true", False),    # a subshell's exit ends only the subshell
    ("|| exit 1", True),
])
def test_branch_reraises_reads_the_rescue(tail: str, want: bool) -> None:
    assert branch_reraises(OPERATOR.split(f"[ -s out.txt ] {tail}")[1:]) is want


@pytest.mark.parametrize(("line", "want"), [
    ("exit 1", True), ("then exit 1", True), ("echo broke; exit 1", True), ("echo broke && exit 1", True),
    ("[ -s other.txt ] && exit 1", False), ("[ -s other.txt ] || exit 1", False),
    ("echo broke; ( exit 1 )", False), ("echo exit 1", False), ("exit 0", False),
])
def test_line_ends_credits_only_an_unconditional_exit(line: str, want: bool) -> None:
    assert line_ends(line) is want


def test_a_quoted_parenthesis_is_not_a_subshell() -> None:
    assert SUBSHELL.match(" ( exit 1 )") and SUBSHELL.match("then ( exit 1 )") and SUBSHELL.match(" { ( exit 1 )")
    assert not SUBSHELL.match(' echo "API break (see above)"') and not SUBSHELL.match(" [ $(wc -l < f) -gt 0 ]")


@pytest.mark.parametrize(("line", "want"), [
    ("[ -s o.txt ] && exit 1 &", True), ("exit 1 &", True), ("fi &", True),
    ("apidiff a b > o.txt 2>&1", False), ("read x <&3 && exit 1", False), ("apidiff a b &> o.txt", False), ("apidiff a b |& tee o.txt", False),
    ("[ -s o.txt ] && exit 1", False), ('echo "R&D" && exit 1', False), ("echo 'a & b'", False),
])
def test_backgrounded_reads_a_bare_ampersand_outside_quotes(line: str, want: bool) -> None:
    assert backgrounded(line) is want
