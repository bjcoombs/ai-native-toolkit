"""The path filter classifies with the BASE ref's copy of floor_check.py.

A pull request that edits the classification in its own copy of the script is
still classified by the base logic, so it cannot report "no floor-core change"
and skip the sign-off (FLOOR.md clause iii). A base copy that cannot answer
fails closed: every changed path counts as protected floor core.
"""
import subprocess
import sys
from pathlib import Path

import pytest

import floor_anchor
from tests.test_floor_anchor import FLOOR_YML_WIRED, _floor_yml
from tests import test_floor_pin_exempt as pin
from tests.test_floor_pin_exempt import DEPENDABOT, _bump, _commit, _git, _run_filter

# The pin-exemption suite's `repo` fixture: a base holding the floor core.
repo = pin.repo

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


# The self-anchor pins that floor.yml keeps asking the base copy (#645): each
# rewrite of the repository's real classification step below must fail it.
FLOOR_YML = Path(floor_anchor.REPO_ROOT) / floor_anchor.FLOOR_PATH
BASE_CALL = 'python "${BASE_SCRIPTS}"/floor_check.py protected'


def _anchor(tmp_path: Path, text: str) -> None:
    target = tmp_path / floor_anchor.FLOOR_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    floor_anchor.check_workflow_wiring(tmp_path)


def test_the_anchor_accepts_the_real_base_copy_classification(tmp_path):
    _anchor(tmp_path, FLOOR_YML.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "old, new",
    [
        # the issue's case: the step calls the head copy directly
        (BASE_CALL, "python scripts/floor_check.py protected"),
        # the extract no longer fails closed
        ('2> /dev/null || CLASSIFY_FAILED=true', "2> /dev/null"),
        # the "base" is the head
        ('echo "merge-base: ${BASE_SHA}"', 'BASE_SHA="$(git rev-parse HEAD)"'),
        # the extract reads the head commit
        ('git show "${BASE_SHA}:scripts', 'git show "HEAD:scripts'),
        # a head-copy call appended after the base-copy one
        (
            '          if [ "${CLASSIFY_FAILED}" = "true" ]; then\n',
            '          FLOOR_CORE="$(python scripts/floor_check.py protected --role floor-core)"\n'
            '          if [ "${CLASSIFY_FAILED}" = "true" ]; then\n',
        ),
        # the base-copy extract survives only as a comment
        ('          git show "${BASE_SHA}:scripts', '          # git show "${BASE_SHA}:scripts'),
        # a failed classification no longer replaces the answer
        ('            FLOOR_CORE="${PROTECTED}"\n', ""),
    ],
)
def test_the_anchor_fails_when_the_step_stops_running_the_base_copy(
    tmp_path, old, new
):
    text = FLOOR_YML.read_text(encoding="utf-8")
    assert old in text
    with pytest.raises(floor_anchor.AnchorError) as exc:
        _anchor(tmp_path, text.replace(old, new))
    assert "BASE ref's copy" in str(exc.value)


@pytest.mark.parametrize(
    "old, new",
    [
        # the producing step no longer runs the classifier
        (
            '          if ! FLOOR_CORE="$(python "${BASE}"/floor_check.py protected \\\n'
            '              --base "${BASE_SHA}" --changed --role floor-core < "${CHANGED}")"; then\n',
            "",
        ),
        # the classifier is asked for a different role
        ("--role floor-core", "--role canary"),
        # the output forwards a step that does not exist
        ("steps.filter.outputs.floor_core_changed", "steps.other.outputs.floor_core_changed"),
    ],
)
def test_signoff_wiring_fails_closed_when_the_producer_skips_the_classifier(
    tmp_path, old, new
):
    # Disarm path 19: the output is forwarded from a step, but that step does
    # not run `floor_check.py protected --role floor-core`, so the answer is
    # the step's own, not the script's classification.
    assert old in FLOOR_YML_WIRED
    body = FLOOR_YML_WIRED.replace(old, new, 1)
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    assert "floor_check.py protected" in str(exc.value)
