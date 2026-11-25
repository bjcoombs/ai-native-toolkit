"""Reading a committed CI script's body (#1033): credit only a check that fails the script.

``lib.script_body.body_runs`` is checked two ways. The table pins each rule. The
differential test runs every fixture the reader credits through a real shell, with a
``pytest`` on PATH that always fails, under the step shells CI uses (``bash -e``, ``bash
-eo pipefail``, ``sh -e``): a credited body must make the step fail in each. The
masked fixtures (``pytest; echo done`` without errexit, ``pytest || true``) must pass,
which shows the reader is right not to credit them.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from assess_test_support import skip_without_posix_shell
from lib.agents.agent_environment import scan_agent_environment
from lib.ci.check_command import is_check_command, is_lint_run
from lib.ci.ci_checks import scan_ci_checks
from lib.ci.ci_scripts import ScriptTree, script_runs
from lib.script_body import body_runs, start_state

SH = "#!/bin/sh\n"
BASH_E = "#!/usr/bin/env bash\nset -euo pipefail\n"

# (invocation, body, credited): the invocation runs ./x.sh, the body is x.sh.
CASES: list[tuple[str, str, bool]] = [
    ("./x.sh", SH + "pytest\n", True),                       # last statement
    ("./x.sh", SH + "set -e\npytest\necho done\n", True),    # errexit set in the body
    ("./x.sh", BASH_E + "pytest\nrm -rf out\n", True),
    ("./x.sh", "#!/bin/bash -e\npytest\necho done\n", True),  # errexit from the shebang
    ("./x.sh", SH + "cd sub && pytest\n", True),
    ("./x.sh", SH + "pytest || exit 1\necho done\n", True),  # re-raised
    ("./x.sh", SH + "set -e\npytest -q tests/ > out.log 2>&1\n", True),
    ("bash -e x.sh", SH + "pytest\necho done\n", True),      # errexit from the shell's flags
    ("sh -e x.sh", SH + "pytest\necho done\n", True),
    (". ./x.sh", SH + "pytest\n", True),
    (". ./x.sh", SH + "pytest || exit 1\necho done\n", True),
    # a sourced body's own set -e is not trusted: bash ignores it in an exempt position
    (". ./x.sh", SH + "set -e\npytest\necho done\n", False),
    ("./x.sh", SH + "[ -n \"$STRICT\" ] && set -e\npytest\necho done\n", False),
    ("./x.sh", SH + "set -e\necho \"Usage:\npytest\n\"\n", False),       # pytest is quoted text
    ("./x.sh", "#!/usr/bin/make -f\npytest\n", False),                    # not a shell
    ("./x.sh", SH + "pytest\necho done\n", False),           # errexit off, not last
    ("./x.sh", SH + "pytest || true\n", False),
    ("./x.sh", SH + "set -e\npytest || true\n", False),
    ("./x.sh", SH + "set -e\npytest | tee log\n", False),     # pipefail off
    ("./x.sh", SH + "set -e\nset +e\npytest\necho done\n", False),
    ("./x.sh", SH + "pytest\nexit 0\n", False),
    ("./x.sh", SH + "exit 0\npytest\n", False),
    ("./x.sh", SH + "set -e\n! pytest\n", False),
    ("bash x.sh", SH + "pytest\necho done\n", False),
    # Not modelled: never credited, whatever it does.
    ("./x.sh", SH + "if true; then\n  pytest\nfi\n", False),
    ("./x.sh", SH + "set -e\nrun() { pytest; }\nrun\n", False),
    ("./x.sh", SH + "set -e\npytest &\nwait\n", False),
    ("./x.sh", SH + "set -e\ncat <<EOF\nx\nEOF\npytest\n", False),
    ("./x.sh", SH + "set -e\neval pytest\n", False),
    ("./x.sh", SH + "set -e\nfor d in a b; do pytest $d; done\n", False),
    # the last review round (#1054): each was credited, yet the step can pass
    ("./x.sh", SH + "set -e\necho hi; trap 'exit 0' ERR\npytest\n", False),
    ("./x.sh", SH + "set -e\necho; pytest() { true; }\npytest\n", False),
    ("./x.sh", SH + "set -e\necho; exec true\npytest\n", False),
    ("./x.sh", SH + "set -e\n[ -z \"$RUN\" ] && exit 0\npytest\n", False),
    ("./x.sh", SH + "set -e\n. ./lib.sh\npytest\necho done\n", False),
    ("./x.sh", SH + "set -e\n. venv/bin/activate\npytest\n", True),   # an env file is fine
]
# Bodies that must not fail the step when pytest fails: the reader is right to skip them.
MASKED = [SH + "pytest\necho done\n", SH + "pytest || true\n", SH + "set -e\npytest | tee log\n",
          SH + "pytest\nexit 0\n", SH + "set -e\n! pytest\n",
          SH + "[ -n \"$STRICT\" ] && set -e\npytest\necho done\n", SH + "set -e\necho \"Usage:\npytest\n\"\n",
          # ERR traps are a bash feature (dash, Ubuntu's /bin/sh, has none), so this one names bash
          "#!/bin/bash\nset -e\necho hi; trap 'exit 0' ERR\npytest\n", SH + "set -e\necho; pytest() { true; }\npytest\n",
          SH + "set -e\necho; exec true\npytest\n", SH + "set -e\n[ -z \"$RUN\" ] && exit 0\npytest\n",
          SH + "set -e\n. ./lib.sh\npytest\necho done\n"]


def _read(invocation: str, body: str) -> bool:
    words = invocation.split()
    form = "sourced" if words[0] in (".", "source") else "shell" if words[0] in ("bash", "sh") else "executed"
    flags = [w for w in words[1:] if w.startswith("-")]
    state = start_state(form, body, flags)
    return state is not None and body_runs(body, errexit=state[0], pipefail=state[1],
                                           test=is_check_command, sourced=form == "sourced")


@pytest.mark.parametrize(("invocation", "body", "credited"), CASES)
def test_the_reader_credits_only_an_enforced_check(invocation: str, body: str, credited: bool) -> None:
    assert _read(invocation, body) is credited


@pytest.mark.parametrize(("line", "expected"), [
    ("#!/bin/bash -e", (True, False)), ("#!/usr/bin/env -S bash -eo pipefail", (True, True)),
    ("#!/bin/sh", (False, False)), ("#!/usr/bin/python3 -e", None), ("pytest", (False, False)),
])
def test_an_executed_script_starts_from_its_shebang(line: str, expected: tuple[bool, bool] | None) -> None:
    assert start_state("executed", line + "\n", []) == expected


def test_a_sourced_script_starts_with_errexit_off() -> None:
    assert start_state("sourced", "#!/bin/bash -e\n", ["-e"]) == (False, False)
    assert start_state("shell", "", ["-eo", "pipefail"]) == (True, True)


def test_a_lint_body_is_read_for_layer_three() -> None:
    assert body_runs(SH + "set -e\nruff check .\nmypy .\n", errexit=False, pipefail=False, test=is_lint_run)


def _shells() -> list[list[str]]:
    bash = shutil.which("bash")
    sh = shutil.which("dash") or shutil.which("sh")
    return [s for s in ([bash, "-e", "-c"] if bash else [], [bash, "-eo", "pipefail", "-c"] if bash else [],
                        [sh, "-e", "-c"] if sh else []) if s]


def _run(shell: list[str], tmp_path: Path, invocation: str, body: str) -> int:
    script = tmp_path / "x.sh"
    script.write_text(body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "sub").mkdir(exist_ok=True)
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    (fake / "pytest").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (fake / "pytest").chmod(0o755)
    (tmp_path / "lib.sh").write_text("set +e\n", encoding="utf-8")
    (tmp_path / "venv/bin").mkdir(parents=True, exist_ok=True)
    (tmp_path / "venv/bin/activate").write_text("X=1\n", encoding="utf-8")
    env = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ.get('PATH', '')}"}
    done = subprocess.run([*shell, invocation], cwd=tmp_path, env=env, capture_output=True,
                          encoding="utf-8", errors="replace", check=False)
    return done.returncode


@pytest.mark.parametrize(("invocation", "body"), [(i, b) for i, b, credited in CASES if credited])
def test_a_credited_body_fails_the_step_in_a_real_shell(tmp_path: Path, invocation: str, body: str) -> None:
    skip_without_posix_shell()
    shells = _shells()
    if not shells:
        pytest.skip("no POSIX shell on PATH")
    for shell in shells:
        assert _run(shell, tmp_path, invocation, body) != 0, (shell, invocation, body)


@pytest.mark.parametrize("body", MASKED)
def test_a_masked_body_passes_in_a_real_shell(tmp_path: Path, body: str) -> None:
    """Each body runs as written, and a ``#!/bin/sh`` one also under dash where it is installed
    (macOS ``/bin/sh`` is bash; Ubuntu's is dash)."""
    skip_without_posix_shell()
    dash = shutil.which("dash")
    bodies = [body, *([body.replace(SH, f"#!{dash}\n", 1)] if dash and body.startswith(SH) else [])]
    for shell in _shells():
        for text in bodies:
            assert _run(shell, tmp_path, "./x.sh", text) == 0, (shell, text)


def test_a_sourced_set_e_is_ignored_in_an_exempt_position(tmp_path: Path) -> None:
    """Why a sourced body's own ``set -e`` is not trusted: from ``. ./x.sh && echo ok``, bash
    runs past the failing check and the step passes."""
    skip_without_posix_shell()
    body = SH + "set -e\npytest\necho done\n"
    for shell in [s for s in _shells() if "bash" in s[0]]:
        assert _run(shell, tmp_path, ". ./x.sh && echo ok", body) == 0, shell


def _repo(root: Path, run: str, files: dict[str, str], executable: bool = True) -> Path:
    wf = root / ".github/workflows/ci.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text(f"on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: {run}\n",
                  encoding="utf-8")
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    scripts = [rel for rel in files if rel.endswith(".sh")] if executable else []
    for args in (["init", "-q"], ["add", "-A"], *([["update-index", "--chmod=+x", *scripts]] if scripts else [])):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                       encoding="utf-8", errors="replace")
    return root


@pytest.mark.parametrize(("run", "files", "expected"), [
    # a read body upgrades a null or false name reading ...
    ("./ci.sh", {"ci.sh": SH + "set -e\npytest\n"}, (True, "enforced_check")),
    (". ./ci.sh", {"ci.sh": SH + "pytest\n"}, (True, "enforced_check")),
    ("./scripts/deploy-ci.sh", {"scripts/deploy-ci.sh": BASH_E + "pytest\n./deploy\n"},
     (True, "enforced_check")),
    ("bash scripts/foo.sh", {"scripts/foo.sh": SH + "pytest\n"}, (True, "enforced_check")),
    # ... and an unread or unshown one leaves the name reading as it was
    ("./ci.sh", {"ci.sh": SH + "pytest\necho done\n"}, (None, "check_target")),
    ("./ci.sh", {"ci.sh": SH + "echo deploy\n"}, (None, "check_target")),
    ("./test.sh", {"test.sh": SH + "echo hi\n"}, (True, "enforced_check")),
    ("./scripts/deploy-ci.sh", {"scripts/deploy-ci.sh": SH + "echo deploy\n"}, (False, "no_check_command")),
    # only the exact tracked path is read: a suffix match is presence, not a body
    ("cd svc && ./ci.sh", {"svc/ci.sh": SH + "pytest\n"}, (None, "check_target")),
    # ... and not when another tracked path ends in it (the line may run svc/ci.sh)
    ("cd svc && ./ci.sh", {"ci.sh": SH + "pytest\n", "svc/ci.sh": SH + "echo\n"}, (None, "check_target")),
    # masked on the CI line: the body does not lift it
    ("./ci.sh || true", {"ci.sh": SH + "set -e\npytest\n"}, (False, "not_enforced")),
])
def test_ci_reads_a_committed_body(tmp_path: Path, run: str, files: dict[str, str],
                                   expected: tuple[bool | None, str]) -> None:
    block = scan_ci_checks(_repo(tmp_path, run, files))
    assert (block["enforced_check_runs"], block["enforced_basis"]) == expected


@pytest.mark.parametrize(("run", "expected"), [
    ("./ci.sh", (None, "check_target")),            # exits 126: the check gates nothing
    ("bash ci.sh", (True, "enforced_check")), ("sh ci.sh", (True, "enforced_check")),
    (". ./ci.sh", (True, "enforced_check")),        # no exec bit needed to read or source it
])
def test_only_an_executable_script_is_read_when_run_directly(
        tmp_path: Path, run: str, expected: tuple[bool | None, str]) -> None:
    block = scan_ci_checks(_repo(tmp_path, run, {"ci.sh": SH + "pytest\n"}, executable=False))
    assert (block["enforced_check_runs"], block["enforced_basis"]) == expected


def test_a_credited_body_is_a_configured_check(tmp_path: Path) -> None:
    block = scan_ci_checks(_repo(tmp_path, "./ci.sh", {"ci.sh": SH + "pytest\n"}))
    assert (block["enforced_check_runs"], block["test_or_lint_configured"], block["basis"]) == (
        True, True, "check_command")


def test_layer_three_reads_a_lint_body(tmp_path: Path) -> None:
    block = scan_ci_checks(_repo(tmp_path, "./ci.sh", {"ci.sh": SH + "set -e\nruff check .\n"}))
    assert (block["lint_runs"], block["lint_basis"]) == (True, "lint_command")


def _named_total(root: Path, run: str, present: bool) -> int:
    wf = root / ".github/workflows/ci.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text(f"on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: {run}\n",
                  encoding="utf-8")
    (root / "CLAUDE.md").write_text(f"# Repo\n\n```bash\n{run}\n```\n", encoding="utf-8")
    if present:
        (root / run.split()[-1].removeprefix("./")).write_text("pytest\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                       encoding="utf-8", errors="replace")
    block = scan_agent_environment(root, {"CLAUDE.md": {"grade": "B"}}, None, probe_ci=False)
    return block["check"]["named_total"]


@pytest.mark.parametrize(("run", "present", "total"), [
    (". ./test.sh", True, 0), (". ./test.sh", False, 0), (". ./ci.sh", True, 0),
    ("source ./test.sh", True, 1), ("source ./test.sh", False, 0),
])
def test_layer_zero_reads_dot_as_on_main(tmp_path: Path, run: str, present: bool, total: int) -> None:
    """The ``.`` rewrite is for CI lines only (``command_resolver.dot_as_source``): the named
    checks of an instruction file keep main's readings."""
    assert _named_total(tmp_path, run, present) == total


def test_script_runs_needs_a_script_tree(tmp_path: Path) -> None:
    root = _repo(tmp_path, "./ci.sh", {"ci.sh": SH + "pytest\n"})
    assert script_runs("./ci.sh", ScriptTree(root, frozenset({"ci.sh"})), is_check_command)
    assert not script_runs("./ci.sh", lambda _: True, is_check_command)
    assert not script_runs("./ci.sh", ScriptTree(root, frozenset()), is_check_command)
