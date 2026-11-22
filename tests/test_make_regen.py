"""`make regen` rewrites every generated file and golden, and nothing else (#984).

The generated files are the ones `.gitattributes` marks `linguist-generated`.
The test copies the tracked tree into a scratch repository, overwrites each
generated file with a merge-conflict body, runs `make regen` there and expects
a clean `git status`. That covers both promises at once: every marked file is
restored byte for byte (a conflict needs no hand edit), and a clean tree stays
clean (a generator that drifted from its committed output leaves a diff).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
# Built at test time from a real fleet run; its test skips without ripgrep, so
# without rg the file is left as committed and only checked for a clean diff.
FLEET_GOLDEN = "skills/assess/tests/fixtures/fleet_golden/repos.jsonl"
CONFLICT = "<<<<<<< ours\nleft\n=======\nright\n>>>>>>> theirs\n"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not all(shutil.which(tool) for tool in ("make", "uv", "git")),
    reason="make regen needs make, uv and git on a POSIX shell",
)


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd, env=_env(), check=True, capture_output=True,
        encoding="utf-8", errors="surrogateescape",
    ).stdout


def generated_files() -> list[str]:
    return _git(REPO, "ls-files", ":(attr:linguist-generated)").splitlines()


def test_gitattributes_marks_the_known_generated_files() -> None:
    marked = set(generated_files())
    assert {
        "docs/what-we-measure.md",
        "skills/assess/references/measures.json",
        "skills/assess/references/counts-only.md",
        "skills/assess/references/signals.schema.json",
        "skills/fleet/references/repos.schema.json",
        "skills/fleet/references/level.schema.json",
        FLEET_GOLDEN,
    } <= marked


def test_make_regen_restores_every_generated_file_and_leaves_no_diff(tmp_path: Path) -> None:
    copy = tmp_path / "repo"
    for rel in _git(REPO, "ls-files", "-z").split("\0"):
        source = REPO / rel
        if rel and source.is_file():
            (copy / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copy / rel)
    _git(copy, "init", "-q")
    _git(copy, "add", "-A", "-f", ".")
    _git(copy, "commit", "-q", "--no-verify", "-m", "tree")

    targets = [rel for rel in generated_files()
               if rel != FLEET_GOLDEN or shutil.which("rg")]
    for rel in targets:
        (copy / rel).write_text(CONFLICT, encoding="utf-8")

    run = subprocess.run(["make", "regen"], cwd=copy, env=_env(), capture_output=True,
                         encoding="utf-8", errors="replace", timeout=1200)
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-4000:]
    status = _git(copy, "status", "--porcelain", "--untracked-files=all")
    assert status == "", f"make regen left a diff:\n{status}"
