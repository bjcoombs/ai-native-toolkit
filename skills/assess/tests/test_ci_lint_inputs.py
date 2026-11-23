"""Tests for lib/ci_lint_inputs.py: lint action steps that only install the linter (#893).

Each case is pinned against the action's own documented behaviour: astral-sh/ruff-action and
chartboost/ruff-action run ``ruff <args> <src>`` (``args`` defaults to ``check``; the astral
README documents ``args: --version`` as install only), and golangci/golangci-lint-action reads
``install-only`` with ``core.getBooleanInput`` from v9.0.0 on (an undeclared input before).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.ci_checks import scan_ci_checks
from lib.ci_lint_inputs import LintInputs, read_lint_inputs


def _step(uses: str, with_block: str = "", extra: str = "") -> str:
    return (f"on: [pull_request]\njobs:\n  lint:\n    runs-on: ubuntu-latest\n    steps:\n"
            f"      - uses: actions/checkout@v4\n      - uses: {uses}\n{with_block}{extra}")


def _with(**inputs: str) -> str:
    return "        with:\n" + "".join(f"          {k}: {v}\n" for k, v in inputs.items())


_USES_LINE = 6   # the lint step's uses: line in _step


def _reading(text: str) -> str:
    got = read_lint_inputs(text)
    if _USES_LINE in got.install_only:
        return "install_only"
    return "unsure" if _USES_LINE in got.unsure else "lint"


@pytest.mark.parametrize(("uses", "with_block", "expected"), [
    # ruff-action: args read as `ruff <args>`.
    ("astral-sh/ruff-action@v3", _with(args='"--version"'), "install_only"),
    ("astral-sh/ruff-action@v3", _with(args="--version"), "install_only"),
    ("astral-sh/ruff-action@v3", _with(args="-V"), "install_only"),
    ("astral-sh/ruff-action@v3", _with(args="'--help'"), "install_only"),
    ("astral-sh/ruff-action@v3", _with(args="version"), "install_only"),
    ("astral-sh/ruff-action@v3", _with(args="help"), "install_only"),
    ("astral-sh/ruff-action@v3", _with(args="format --version"), "install_only"),
    ("chartboost/ruff-action@v1", _with(args="--version"), "install_only"),
    ("'astral-sh/ruff-action@v3'", _with(args="--version"), "install_only"),
    ("astral-sh/ruff-action@v3", "        with:\n          args: |\n            --version\n",
     "install_only"),
    ("astral-sh/ruff-action@v3", "        with: {args: --version}\n", "install_only"),
    ("astral-sh/ruff-action@v3", "", "lint"),
    ("astral-sh/ruff-action@v3", _with(src="./src"), "lint"),
    ("astral-sh/ruff-action@v3", _with(args="check --select I"), "lint"),
    ("astral-sh/ruff-action@v3", _with(args='"format --check --diff"'), "lint"),
    ("chartboost/ruff-action@v1", _with(args="check"), "lint"),
    ("astral-sh/ruff-action@v3", "        with: {src: ./src}\n", "lint"),
    ("astral-sh/ruff-action@v3", _with(args="${{ matrix.args }}"), "unsure"),
    ("astral-sh/ruff-action@v3", _with(args='"check ${{ env.EXTRA }}"'), "unsure"),
    ("astral-sh/ruff-action@v3", _with(args='""'), "unsure"),
    # An unreadable with: keeps today's answer (the issue's rule).
    ("astral-sh/ruff-action@v3", _with(args="*ruff_args"), "lint"),
    ("astral-sh/ruff-action@v3", "        with:\n          <<: *defaults\n", "lint"),
    ("astral-sh/ruff-action@v3", "        with:\n          <<: *defaults\n          args: --version\n",
     "install_only"),
    ("astral-sh/ruff-action@v3", "        with:\n          \"args\": --version\n", "lint"),
    ("astral-sh/ruff-action@v3", "        with: {args: \"--version\"}\n", "install_only"),
    ("astral-sh/ruff-action@v3", "        with: {src: \"./src\"}\n", "lint"),
    ("astral-sh/ruff-action@v3", "        with: {args: \"a, b\"}\n", "lint"),
    ("astral-sh/ruff-action@v3", "        with: {args: [--version]}\n", "lint"),
    # golangci-lint-action: install-only from v9.0.0.
    ("golangci/golangci-lint-action@v9", _with(**{"install-only": "true"}), "install_only"),
    ("golangci/golangci-lint-action@v9.2.1", _with(**{"install-only": "'True'"}), "install_only"),
    ("golangci/golangci-lint-action@v10", _with(**{"install-only": '"TRUE"'}), "install_only"),
    ("golangci/golangci-lint-action@v9", _with(**{"install-only": "false"}), "lint"),
    ("golangci/golangci-lint-action@v9", "", "lint"),
    ("golangci/golangci-lint-action@v9", _with(**{"install-mode": "none"}), "lint"),
    ("golangci/golangci-lint-action@v9", _with(**{"install-mode": "goinstall"}), "lint"),
    ("golangci/golangci-lint-action@v9", _with(**{"only-new-issues": "true"}), "lint"),
    ("golangci/golangci-lint-action@v8", _with(**{"install-only": "true"}), "lint"),
    ("golangci/golangci-lint-action@v6.5.2", _with(**{"install-only": "true"}), "lint"),
    ("golangci/golangci-lint-action@v9", _with(**{"install-only": "${{ inputs.only }}"}), "unsure"),
    ("golangci/golangci-lint-action@v9", _with(**{"install-only": "yes"}), "unsure"),
    ("golangci/golangci-lint-action@3a919529898de77ec3da873e3063ca4b10e7f5cc",
     _with(**{"install-only": "true"}), "unsure"),
    ("golangci/golangci-lint-action@main", _with(**{"install-only": "true"}), "unsure"),
    ("golangci/golangci-lint-action@main", _with(**{"install-only": "false"}), "lint"),
    ("golangci/golangci-lint-action@3a919529898de77ec3da873e3063ca4b10e7f5cc",
     _with(**{"install-only": "'false'"}), "lint"),
    ("golangci/golangci-lint-action@v8", _with(**{"install-only": "${{ inputs.only }}"}), "lint"),
    ("golangci/golangci-lint-action@v9", "        with: {version: \"v2.1\"}\n", "lint"),
    ("golangci/golangci-lint-action@v9", "        with: {install-only: true}\n", "install_only"),
    # Other lint actions are not read here: their #782 credit stands.
    ("reviewdog/action-eslint@v1", _with(args="--version"), "lint"),
    ("super-linter/super-linter@v7", _with(**{"install-only": "true"}), "lint"),
])
def test_lint_action_inputs(uses: str, with_block: str, expected: str) -> None:
    assert _reading(_step(uses, with_block)) == expected


def test_a_named_step_is_read() -> None:
    """The common shape: ``- name: ...`` first, then ``uses:``."""
    text = ("on: [pull_request]\njobs:\n  lint:\n    steps:\n      - name: Install ruff\n"
            "        uses: astral-sh/ruff-action@v3\n        with:\n          args: --version\n")
    assert read_lint_inputs(text).install_only == {5}


def test_a_non_lint_action_is_not_read() -> None:
    text = _step("actions/setup-python@v5", _with(args="--version"))
    assert read_lint_inputs(text) == LintInputs(frozenset(), frozenset())


def test_an_input_of_the_next_step_is_not_read() -> None:
    text = _step("astral-sh/ruff-action@v3", "      - uses: x/y@v1\n" + _with(args="--version"))
    assert _reading(text) == "lint"


def _scan(tmp_path: Path, text: str) -> tuple[bool | None, str]:
    wf = tmp_path / ".github" / "workflows" / "lint.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text(text, encoding="utf-8")
    block = scan_ci_checks(tmp_path)
    return block["lint_runs"], block["lint_basis"]


@pytest.mark.parametrize(("uses", "with_block", "expected"), [
    ("astral-sh/ruff-action@v3", _with(args='"--version"'), (False, "no_lint_command")),
    ("golangci/golangci-lint-action@v9", _with(**{"install-only": "true"}), (False, "no_lint_command")),
    ("astral-sh/ruff-action@v3", "", (True, "lint_command")),
    ("golangci/golangci-lint-action@v9", _with(**{"install-mode": "none"}), (True, "lint_command")),
    ("golangci/golangci-lint-action@v9", _with(**{"only-new-issues": "true"}), (True, "lint_command")),
    ("golangci/golangci-lint-action@v8", _with(**{"install-only": "true"}), (True, "lint_command")),
    ("astral-sh/ruff-action@v3", _with(args="${{ matrix.args }}"), (None, "check_target")),
    ("golangci/golangci-lint-action@v9", _with(**{"install-only": "${{ inputs.only }}"}),
     (None, "check_target")),
])
def test_scan_reads_the_inputs(tmp_path: Path, uses: str, with_block: str,
                               expected: tuple[bool | None, str]) -> None:
    assert _scan(tmp_path, _step(uses, with_block)) == expected


def test_an_install_only_step_beside_a_lint_run_keeps_the_run(tmp_path: Path) -> None:
    text = _step("astral-sh/ruff-action@v3", _with(args="--version"), "      - run: ruff check .\n")
    assert _scan(tmp_path, text) == (True, "lint_command")


def test_an_unsure_step_beside_a_lint_run_keeps_the_run(tmp_path: Path) -> None:
    text = _step("astral-sh/ruff-action@v3", _with(args="${{ matrix.args }}"), "      - run: ruff check .\n")
    assert _scan(tmp_path, text) == (True, "lint_command")


def test_a_masked_unsure_step_gives_no_credit(tmp_path: Path) -> None:
    """False either way; it is no lint line, so the basis is no_lint_command, not not_enforced."""
    text = _step("astral-sh/ruff-action@v3", _with(args="${{ matrix.args }}"),
                 "        continue-on-error: true\n")
    assert _scan(tmp_path, text) == (False, "no_lint_command")


def test_a_masked_lint_step_still_reads_not_enforced(tmp_path: Path) -> None:
    text = _step("astral-sh/ruff-action@v3", _with(args="check"), "        continue-on-error: true\n")
    assert _scan(tmp_path, text) == (False, "not_enforced")


def test_layer_5_and_the_configured_reading_do_not_move(tmp_path: Path) -> None:
    """The lint action never counted at Layer 5 (#782); #893 leaves those signals alone."""
    for name, with_block in (("a", _with(args="--version")), ("b", _with(args="${{ matrix.a }}"))):
        root = tmp_path / name
        wf = root / ".github" / "workflows" / "lint.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text(_step("astral-sh/ruff-action@v3", with_block), encoding="utf-8")
        block = scan_ci_checks(root)
        assert (block["test_or_lint_configured"], block["basis"]) == (False, "no_check_command")
        assert (block["enforced_check_runs"], block["enforced_basis"]) == (False, "no_check_command")
