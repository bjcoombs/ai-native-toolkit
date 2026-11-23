"""A target named ``ci`` is not read as a test run on its name alone (#951).

``make ci``, ``npm run ci`` and ``./ci.sh`` can run a build, a deploy or a test
suite, and the recipe is not read, so Layer 5 level 1 (``enforced_check_runs``)
reads null with basis ``check_target``, like ``make foo``. ``bun ci`` and
``npm ci`` are installs and stay false; a name that also carries a check word
(``make ci-test``, ``npm run test:ci``) still counts. Instruction files keep ``ci``
(``lib.named_check``): a CLAUDE.md that names ``make ci`` is naming how to verify the
work, which is what Layer 0 measures, so Layer 0 does not move.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.agent_consistency import check_commands
from lib.agent_environment import scan_agent_environment
from lib.check_command import check_verdict, is_check_name
from lib.ci_checks import scan_ci_checks
from lib.ci_scripts import Present, runs_missing
from lib.coverage_gate import detect_coverage_gate
from lib.named_check import is_named_check, is_named_check_name, unread_ci_target

_CI_TARGETS = ["make ci", "just ci", "task ci", "npm run ci", "pnpm ci", "yarn ci", "bun run ci",
               "./ci.sh", "./scripts/ci.sh", "bash ci.sh"]


def _scan(tmp_path: Path, *runs: str) -> tuple[bool | None, str, bool | None, str]:
    steps = "".join(f"      - run: {r}\n" for r in runs)
    path = tmp_path / ".github/workflows/ci.yml"
    path.parent.mkdir(parents=True)
    path.write_text(f"on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n{steps}",
                    encoding="utf-8")
    block = scan_ci_checks(tmp_path)
    return (block["enforced_check_runs"], block["enforced_basis"], block["lint_runs"],
            block["lint_basis"])


@pytest.mark.parametrize("text", _CI_TARGETS)
def test_a_ci_target_is_not_read_as_a_check(text: str) -> None:
    assert check_verdict(text) is None


@pytest.mark.parametrize(("text", "expected"), [
    ("bun ci", False), ("npm ci", False), ("./ci/deploy.sh", False),
    ("make ci-test", True), ("npm run test:ci", True), ("npm run ci:test", True),
])
def test_installs_stay_false_and_check_named_ci_targets_stay_true(text: str, expected: bool) -> None:
    assert check_verdict(text) is expected


_RUNNER_CI = ["python ci.py", "python3 scripts/ci.py", "source ci.sh", "cargo ci",
              "./gradlew ci", "gradle ci", "mvn ci", "./mvnw -B ci", "go ci"]


@pytest.mark.parametrize("run", ["make ci", "npm run ci", "./ci.sh", *_RUNNER_CI])
def test_a_lone_ci_target_in_ci_reads_not_measured(tmp_path: Path, run: str) -> None:
    """The issue's fixtures: Layer 5 level 1 and Layer 3 level 1 read null, never a false 0.
    A ``./ci.sh`` the tree does not hold keeps #937's more specific null, ``script_not_found``."""
    basis = "script_not_found" if run == "./ci.sh" else "check_target"
    assert _scan(tmp_path, run) == (None, basis, None, "check_target")


@pytest.mark.parametrize("text", _RUNNER_CI)
def test_a_ci_name_behind_a_runner_is_an_unread_target(text: str) -> None:
    """Read false by the CI vocabulary and true by the instruction one: the reading rests on
    the name alone, so ``ci_checks`` treats it like ``./ci.sh`` (null), never as no check."""
    assert unread_ci_target(text)


@pytest.mark.parametrize("text", ["bun ci", "npm ci", "python build.py", "cargo build", "make ci",
                                  "pytest", "./ci/deploy.sh", "mvn install"])
def test_other_commands_are_not_unread_ci_targets(text: str) -> None:
    assert not unread_ci_target(text)


@pytest.mark.parametrize("run", ["cargo ci || true", "./ci.sh || true"])
def test_a_masked_ci_name_still_reads_false(tmp_path: Path, run: str) -> None:
    """Masked, a ci name reads false ``not_enforced`` (``named_check.named_count``), as on main."""
    assert _scan(tmp_path, run)[:2] == (False, "not_enforced")


@pytest.mark.parametrize("run", ["make ci", "npm run ci", "./ci.sh", "task ci", "cargo ci"])
def test_a_ci_target_keeps_the_coverage_gate_not_measured(tmp_path: Path, run: str) -> None:
    """The Step 4 coverage gate: a recipe that is not read may run the gate, so it reads
    null ``indirect_run``, as on main, never a false ``not_run_in_ci``."""
    path = tmp_path / ".github/workflows/ci.yml"
    path.parent.mkdir(parents=True)
    path.write_text(f"on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: {run}\n",
                    encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'x'\n\n[tool.coverage.report]\nfail_under = 80\n", encoding="utf-8")
    block = detect_coverage_gate(tmp_path)
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (None, "indirect_run")


def test_a_committed_ci_sh_is_read_by_its_name_no_more(tmp_path: Path) -> None:
    """``lib.ci_scripts`` checks the file is there and never reads it: ``echo deploy`` is no check."""
    (tmp_path / "ci.sh").write_text("echo deploy\n", encoding="utf-8")
    assert _scan(tmp_path, "./ci.sh") == (None, "check_target", None, "check_target")


def test_runs_missing_holds_only_where_the_script_is_absent() -> None:
    """``ci_scripts.runs_missing``: true with no predicate, false once ``present`` holds the file."""
    def test(line: str, *, script_item: bool = True, present: Present | None = None) -> bool:
        return present is None or present("ci.sh")
    missing = runs_missing(test)
    assert missing("./ci.sh", present=lambda _: False)
    assert not missing("./ci.sh", present=lambda _: True) and not missing("./ci.sh")


def test_a_ci_target_beside_pytest_reads_true(tmp_path: Path) -> None:
    assert _scan(tmp_path, "make ci", "pytest")[:2] == (True, "enforced_check")


def test_bun_ci_alone_runs_no_check(tmp_path: Path) -> None:
    assert _scan(tmp_path, "bun ci")[:2] == (False, "no_check_command")


# --- instruction files keep `ci` (Layer 0 is unchanged) ----------------------------


@pytest.mark.parametrize(("text", "expected"), [
    *((t, True) for t in _CI_TARGETS), ("make test", True), ("bun ci", False), ("npm ci", False),
    ("./ci/deploy.sh", False), ("make build", False),
])
def test_an_instruction_file_still_names_a_ci_target_as_its_check(text: str, expected: bool) -> None:
    assert is_named_check(text) is expected


def test_the_instruction_vocabulary_is_scoped_to_one_call() -> None:
    assert is_named_check("make ci") and is_named_check_name("ci")
    assert check_verdict("make ci") is None and not is_check_name("ci")


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                       encoding="utf-8", errors="replace")
    return root


def test_a_claude_md_naming_only_make_ci_keeps_layer_zero(tmp_path: Path) -> None:
    """Layer 0 level 2 reads ``agent_environment.check.named_total``: a named ``make ci``
    still counts, and the Makefile's ``ci`` target is still an entry point."""
    root = _repo(tmp_path, {"Makefile": "ci:\n\techo hi\n",
                            "CLAUDE.md": "# Repo\n\n```bash\nmake ci\n```\n"})
    check = scan_agent_environment(root, {"CLAUDE.md": {"grade": "B"}}, None, probe_ci=False)["check"]
    assert check["named_total"] == 1
    assert [n["command"] for n in check["named"]] == ["make ci"]
    assert {"command": "make ci", "source": "Makefile"} in check["entry_points"]


def test_agent_consistency_still_compares_a_named_make_ci() -> None:
    assert check_commands("Run `make ci` before pushing.\n\n```bash\nmake ci\n```\n") == {"make ci"}
