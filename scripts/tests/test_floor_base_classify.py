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
from floor_check import main

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


def test_a_merge_group_answers_false_twice_for_a_floor_core_change(repo, tmp_path):
    # #1088: inside the merge queue the sign-off is not requested again. It is
    # a pull-request gate, and the queue admits only a pull request whose
    # `floor enforcement` passed on its head (FLOOR.md, "The merge queue").
    base = _head(repo)
    with (repo / "scripts" / "floor_check.py").open("a", encoding="utf-8") as fh:
        fh.write("# a floor-core edit\n")
    _commit(repo, "floor core edit")
    queued = _run_filter(repo, base, "someone", tmp_path, event="merge_group")
    assert queued == "run_canaries=false\nfloor_core_changed=false\n"


def test_only_the_merge_group_event_skips_the_classification(repo, tmp_path):
    # The same floor-core change on any other event is classified as before.
    base = _head(repo)
    with (repo / "scripts" / "floor_check.py").open("a", encoding="utf-8") as fh:
        fh.write("# a floor-core edit\n")
    _commit(repo, "floor core edit")
    for event in ("pull_request", "push", "", "merge_group_x"):
        assert CORE_CHANGED in _run_filter(repo, base, "someone", tmp_path, event=event)


def test_a_base_without_the_path_filter_subcommand_fails_closed(repo, tmp_path):
    _edit_script(repo, '"path-filter",\n', '"retired",\n')
    base = _commit(repo, "base predates path-filter")
    (repo / "notes.md").write_text("x\n", encoding="utf-8")
    _commit(repo, "unprotected")
    out = _run_filter(repo, base, "someone", tmp_path)
    assert CORE_CHANGED in out
    assert "run_canaries=true" in out


def test_a_base_whose_path_filter_crashes_fails_closed(repo, tmp_path):
    _edit_script(
        repo,
        "    roles = _diff_roles(args.base)\n    protected =",
        "    raise RuntimeError('crash')\n    protected =",
    )
    base = _commit(repo, "base path-filter crashes")
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
    _edit_script(repo, '"path-filter",\n', '"retired",\n')
    base = _commit(repo, "base predates path-filter")
    _bump(repo)
    _commit(repo, "bump")
    assert CORE_CHANGED in _run_filter(repo, base, DEPENDABOT, tmp_path)


def test_path_filter_failure_leaves_stdout_empty(repo, tmp_path):
    # The workflow fails closed on a non-zero exit, so a crash must never leave
    # a partial answer on stdout for the step to forward.
    result = subprocess.run(
        [sys.executable, "scripts/floor_check.py", "path-filter", "--base", "no-such-ref"],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert result.stdout == ""


def test_path_filter_prints_both_outputs_last(repo):
    base = _head(repo)
    with (repo / "scripts" / "floor_check.py").open("a", encoding="utf-8") as fh:
        fh.write("# a floor-core edit\n")
    _commit(repo, "floor core edit")
    result = subprocess.run(
        [sys.executable, "scripts/floor_check.py", "path-filter", "--base", base],
        cwd=repo, check=True, capture_output=True, text=True,
    )
    assert result.stdout == "run_canaries=true\nfloor_core_changed=true\n"
    assert "floor-core: scripts/floor_check.py" in result.stderr


@pytest.mark.parametrize(
    "author, extra, expected",
    [
        ("someone", "", "run_canaries=true\nfloor_core_changed=true\n"),
        (DEPENDABOT, "", "run_canaries=true\nfloor_core_changed=false\n"),
        (DEPENDABOT, "      - run: echo extra\n", "run_canaries=true\nfloor_core_changed=true\n"),
    ],
)
def test_path_filter_stdout_is_exactly_the_two_outputs(repo, capsys, author, extra, expected):
    base = _head(repo)
    _bump(repo, extra=extra)
    _commit(repo, "bump")
    assert main(["path-filter", "--base", base, "--author", author, "--actor", author]) == 0
    assert capsys.readouterr().out == expected


def test_path_filter_on_an_unprotected_change_says_false_twice(repo, capsys):
    base = _head(repo)
    (repo / "notes.md").write_text("x\n", encoding="utf-8")
    _commit(repo, "unprotected")
    assert main(["path-filter", "--base", base]) == 0
    assert capsys.readouterr().out == "run_canaries=false\nfloor_core_changed=false\n"


# The self-anchor pins the filter step byte for byte (#645, #679): each rewrite
# of the repository's real step below must fail it, and the bypass shapes from
# #679 are the first four.
FLOOR_YML = Path(floor_anchor.REPO_ROOT) / floor_anchor.FLOOR_PATH
CALL = '! ANSWER="$(python "${BASE_COPY}" path-filter'
STEP_START = "      - name: Detect changes to protected paths\n"
WRITE = '          echo "${ANSWER}" >> "$GITHUB_OUTPUT"\n'


def _anchor(tmp_path: Path, text: str) -> None:
    target = tmp_path / floor_anchor.FLOOR_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    floor_anchor.check_workflow_wiring(tmp_path)


def test_the_anchor_accepts_the_real_base_copy_classification(tmp_path):
    _anchor(tmp_path, FLOOR_YML.read_text(encoding="utf-8"))


def test_the_anchor_accepts_a_clean_change_beside_the_step(tmp_path):
    # A Dependabot bump of the filter job's checkout pin, a reworded comment
    # above the step and a blank line after it leave the step itself intact.
    text = FLOOR_YML.read_text(encoding="utf-8")
    job = text.index("  canary-changes:\n")
    pin = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1"
    at = text.index(pin, job)
    text = text[:at] + "actions/checkout@" + "a" * 40 + "  # v7.0.2" + text[at + len(pin):]
    old = "      # The one path decision this workflow makes"
    assert old in text
    text = text.replace(old, "      # The single path decision this workflow makes")
    text = text.replace(WRITE, WRITE + "\n")
    _anchor(tmp_path, text)


@pytest.mark.parametrize(
    "old, new",
    [
        # #679: the path variable reassigned between the extract and the call
        (
            "          if ! git show",
            '          BASE_COPY=scripts/floor_check.py\n          if ! git show',
        ),
        # #679: a glob copy over the extracted base copy
        (
            "              || " + CALL,
            '              || cp scripts/floor_ch*.py "${BASE_COPY}"\n'
            "              || " + CALL,
        ),
        # #679: code built at run time with eval
        (
            "          fi\n          echo \"${ANSWER}\"\n",
            "          fi\n          eval \"$(printf 'ANSWER=floor_core_changed=fals%s' e)\"\n"
            "          echo \"${ANSWER}\"\n",
        ),
        # #679: the answer overwritten after the fail-closed branch
        (WRITE, WRITE + '          echo "floor_core_changed=false" >> "$GITHUB_OUTPUT"\n'),
        # ...behind a run-block line shaped like a new step
        (
            WRITE,
            WRITE + "          - x: y || true\n"
            '          echo "floor_core_changed=false" >> "$GITHUB_OUTPUT"\n',
        ),
        # the step calls the head copy directly
        ('python "${BASE_COPY}" path-filter', "python scripts/floor_check.py path-filter"),
        # the "base" is the head
        ('git merge-base "origin/${BASE_REF}" HEAD', "git rev-parse HEAD"),
        # the base ref arrives from the head ref
        ("BASE_REF: ${{ github.base_ref }}", "BASE_REF: ${{ github.head_ref }}"),
        # the extract reads the head commit
        ('git show "${BASE_SHA}:scripts', 'git show "HEAD:scripts'),
        # an interpreter flag on the call
        ('python "${BASE_COPY}"', 'python -I "${BASE_COPY}"'),
        # the fail-closed answer no longer requests sign-off
        ("floor_core_changed=true')", "floor_core_changed=false')"),
        # an author forged in env, waiving sign-off for a pin-only diff
        ("PR_AUTHOR: ${{ github.event.pull_request.user.login }}", "PR_AUTHOR: dependabot[bot]"),
        # the step made non-fatal, or run by another shell
        ("        id: filter\n", "        id: filter\n        continue-on-error: true\n"),
        ("        id: filter\n", "        id: filter\n        shell: sh\n"),
        # #1088: the merge-group branch widened to other events
        ('[ "${EVENT_NAME}" != "merge_group" ]', '[ "${EVENT_NAME}" = "push" ]'),
        # ...or keyed on a value a pull request controls
        ("EVENT_NAME: ${{ github.event_name }}", "EVENT_NAME: ${{ github.head_ref }}"),
        # ...or answering true-for-false inside the queue on a pull request
        ("        run: |\n          [", "        run: |\n          EVENT_NAME=merge_group\n          ["),
        # a comment in the run block, which bash reads after a continuation
        (
            "          BASE_COPY=",
            "          # the base copy\n          BASE_COPY=",
        ),
    ],
)
def test_the_anchor_fails_when_the_step_stops_running_the_base_copy(
    tmp_path, old, new
):
    text = FLOOR_YML.read_text(encoding="utf-8")
    step = text.index(STEP_START)  # rewrite the filter step, not a namesake
    assert old in text[step:]
    with pytest.raises(floor_anchor.AnchorError) as exc:
        _anchor(tmp_path, text[:step] + text[step:].replace(old, new, 1))
    assert "BASE ref's copy" in str(exc.value)


@pytest.mark.parametrize(
    "old, new, reason",
    [
        # the output forwards a step that does not exist
        (
            "steps.filter.outputs.floor_core_changed",
            "steps.other.outputs.floor_core_changed",
            "no such step",
        ),
        # a second step claims the producer's id
        (
            "    steps:\n      - name: Detect",
            "    steps:\n      - name: Decoy\n        id: filter\n        run: true\n"
            "      - name: Detect",
            "2 steps carry that id",
        ),
        # ...even when the decoy quotes the id
        (
            "    steps:\n      - name: Detect",
            "    steps:\n      - name: Decoy\n        id: 'filter'  # x\n        run: true\n"
            "      - name: Detect",
            "2 steps carry that id",
        ),
    ],
)
def test_signoff_wiring_fails_closed_when_the_producer_is_not_the_filter_step(
    tmp_path, old, new, reason
):
    assert old in FLOOR_YML_WIRED
    body = FLOOR_YML_WIRED.replace(old, new, 1)
    with pytest.raises(floor_anchor.AnchorError) as exc:
        floor_anchor.check_workflow_wiring(_floor_yml(tmp_path, body))
    assert reason in str(exc.value)
