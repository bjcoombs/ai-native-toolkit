"""The path filter classifies with the BASE ref's copy of floor_check.py.

A pull request that edits the classification in its own copy of the script is
still classified by the base logic, so it cannot report "no floor-core change"
and skip the sign-off (FLOOR.md clause iii). A base copy that cannot answer
fails closed: every changed path counts as protected floor core.
"""
import subprocess
import sys
from pathlib import Path

from tests.test_floor_pin_exempt import (  # noqa: F401  (`repo` is a fixture)
    DEPENDABOT,
    _bump,
    _commit,
    _git,
    _run_filter,
    repo,
)

CORE_CHANGED = "floor_core_changed=true"
CORE_CLEAN = "floor_core_changed=false"


def _edit_script(root: Path, old: str, new: str) -> None:
    path = root / "scripts" / "floor_check.py"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def test_a_forged_head_copy_cannot_classify_itself_clean(repo, tmp_path):
    base = _head(repo)
    # The head copy classifies nothing as protected, whatever changed.
    _edit_script(
        repo,
        "def cmd_protected(args: argparse.Namespace) -> int:\n",
        "def cmd_protected(args: argparse.Namespace) -> int:\n    return 0\n",
    )
    _commit(repo, "forged classification")
    forged = subprocess.run(
        [sys.executable, "scripts/floor_check.py", "protected", "--base", base,
         "--role", "floor-core"],
        cwd=repo, check=True, capture_output=True, text=True,
    )
    assert forged.stdout == ""  # the head copy would report no floor-core change
    out = _run_filter(repo, base, "someone", tmp_path)
    assert CORE_CHANGED in out
    assert "run_canaries=true" in out


def test_an_unprotected_change_requests_nothing(repo, tmp_path):
    base = _head(repo)
    (repo / "notes.md").write_text("x\n", encoding="utf-8")
    _commit(repo, "unprotected")
    out = _run_filter(repo, base, "someone", tmp_path)
    assert CORE_CLEAN in out
    assert "run_canaries=false" in out


def test_a_base_without_the_protected_subcommand_fails_closed(repo, tmp_path):
    _edit_script(repo, '"protected", help=', '"retired", help=')
    base = _commit(repo, "base predates protected")
    (repo / "notes.md").write_text("x\n", encoding="utf-8")
    _commit(repo, "unprotected")
    out = _run_filter(repo, base, "someone", tmp_path)
    assert CORE_CHANGED in out
    assert "run_canaries=true" in out


def test_a_base_without_the_role_option_fails_closed(repo, tmp_path):
    _edit_script(repo, '"--role",', '"--retired-role",')
    base = _commit(repo, "base predates --role")
    (repo / "notes.md").write_text("x\n", encoding="utf-8")
    _commit(repo, "unprotected")
    assert CORE_CHANGED in _run_filter(repo, base, "someone", tmp_path)


def test_a_base_without_the_script_fails_closed(repo, tmp_path):
    (repo / "scripts" / "floor_check.py").unlink()
    base = _commit(repo, "base has no floor_check.py")
    (repo / "notes.md").write_text("x\n", encoding="utf-8")
    _commit(repo, "unprotected")
    out = _run_filter(repo, base, "someone", tmp_path)
    assert CORE_CHANGED in out
    assert "run_canaries=true" in out


def test_a_failed_classification_exempts_no_dependabot_pin_bump(repo, tmp_path):
    _edit_script(repo, '"protected", help=', '"retired", help=')
    base = _commit(repo, "base predates protected")
    _bump(repo)
    _commit(repo, "bump")
    assert CORE_CHANGED in _run_filter(repo, base, DEPENDABOT, tmp_path)
