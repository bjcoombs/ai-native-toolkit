"""A check inside a ``( )`` subshell that ends in a no-op earns no credit (#1008).

``(pytest || true)`` always exits 0, so it is masked as ``pytest || true`` is at top level.
``(pytest)``, ``(pytest && echo ok)`` and ``x=$(pytest)`` exit with the check's status and
stay credited (#936). Each expected reading was checked against real bash 5 and sh, with a
stub ``pytest`` that always exits 1, under GitHub's default shell (``bash -e {0}``),
``shell: bash`` (``bash --noprofile --norc -eo pipefail {0}``) and ``shell: sh`` (``sh -e
{0}``): a masked form exits 0 under all three, a credited form exits 1.
"""
from __future__ import annotations

import pytest

from lib.ci.ci_checks import enforced_check_commands
from lib.ci.ci_masking import unmasked_lines
from lib.ci.ci_statements import _same_line_keep, split_statements

SHELLS = {"default": "", "bash": "        shell: bash\n", "sh": "        shell: sh\n"}


def _step(run: str, shell: str) -> str:
    return ("on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
            f"      - run: {run}\n{SHELLS[shell]}")


MASKED = [
    "(pytest || true)",
    "(pytest || true); exit 0",
    "(cd sub && pytest || true)",
    "(cd sub && pytest || true); exit 0",
    "(pytest || :)",
    "(pytest || :); exit 0",
    "(pytest || exit 0)",
    "( pytest || true )",
    "(pytest || true) && echo after",
    "echo a && (pytest || true)",
    "(pytest || true && echo z)",
    "(echo a; pytest || true)",
]

CREDITED = [
    "(pytest && echo ok)",
    "(pytest; true)",
    # bash returns pytest's status: the group's no-op covers only ``echo x``.
    "pytest && (echo x || true)",
    "(cd sub && pytest)",
    # A ``$( )`` that ends a segment closes no group: the no-op covers the group only.
    "pytest && (V=$(cat v) && echo y || true)",
    "pytest && (cd $(dirname x) || true)",
    "pytest && (echo \")\" || true)",
]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("run", MASKED)
def test_a_subshell_ending_in_a_noop_is_masked(run: str, shell: str) -> None:
    assert enforced_check_commands(_step(run, shell)) == (0, 1)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("run", CREDITED)
def test_a_subshell_that_returns_the_check_status_stays_credited(run: str, shell: str) -> None:
    assert enforced_check_commands(_step(run, shell)) == (1, 0)


# The check-command reader does not take ``(pytest)`` or ``x=$(pytest)`` as a check on main
# (a recognition gap, not masking): the masking rules leave each line as written.
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("run", ["(pytest)", "x=$(pytest)", "(pytest) && echo ok"])
def test_masking_leaves_a_subshell_that_returns_the_check_status(run: str, shell: str) -> None:
    text = _step(run, shell)
    assert unmasked_lines(text) == text.splitlines()


def test_a_block_body_subshell_is_masked() -> None:
    text = ("on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: |\n"
            "          (cd tests && pytest || true)\n          echo done\n")
    assert enforced_check_commands(text) == (0, 1)


@pytest.mark.parametrize(("line", "kept"), [
    ("(cd sub && pytest || true)", []),
    ("pytest && (echo x || true)", [0]),
    ("(pytest || true) && ruff check .", [2]),
    ("(pytest) || true", []),
    ("pytest || true", []),
    ("mkdir -p x || true && pytest", [2]),
])
def test_a_group_noop_masks_back_to_the_group_start(line: str, kept: list[int]) -> None:
    statement = split_statements(line)[0]
    assert _same_line_keep(statement, list) == kept
