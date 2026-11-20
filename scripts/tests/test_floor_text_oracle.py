"""FLOOR.md pinned from its own text, not from floor_check.py's constants.

Each test here takes its oracle from the shipped ``FLOOR.md`` (or from a number
the file states in words) and drives ``floor_check.py`` through its CLI. None
imports ``REQUIRED_CLAUSES``, ``MINIMUM_TOKEN_COUNT`` or ``FLOOR_CORE_PATHS``,
so weakening one of those constants turns a test here red instead of moving
the expectation along with it (#410).
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest
from floor_check import main

REPO_ROOT = Path(__file__).resolve().parents[2]
FLOOR_TEXT = (REPO_ROOT / "FLOOR.md").read_text(encoding="utf-8")
SCRIPT = REPO_ROOT / "scripts" / "floor_check.py"

ANCHOR_RE = re.compile(r"<!-- floor-clause:([a-z]+) -->")
CLAUSE_IDS = ANCHOR_RE.findall(FLOOR_TEXT)


def _clause_body(text: str, clause_id: str) -> str:
    """The text after ``clause_id``'s anchor, up to the next anchor or heading."""
    start = text.index(f"<!-- floor-clause:{clause_id} -->")
    start = text.index("\n", start) + 1
    end = len(text)
    for stop in (re.compile(r"<!-- floor-clause:"), re.compile(r"^## ", re.MULTILINE)):
        match = stop.search(text, start)
        if match:
            end = min(end, match.start())
    return text[start:end]


def _run_clauses(tmp_path: Path, text: str) -> tuple[int, str]:
    floor = tmp_path / "FLOOR.md"
    floor.write_text(text, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "clauses", "--floor", str(floor)],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _named_clauses(output: str) -> list[str]:
    match = re.search(r"clauses not intact -> (.+)$", output, re.MULTILINE)
    return [part.strip() for part in match.group(1).split(",")] if match else []


def test_the_shipped_floor_declares_four_clauses():
    assert CLAUSE_IDS == ["i", "ii", "iii", "iv"]


def test_the_shipped_floor_passes_the_clause_check(tmp_path):
    rc, out = _run_clauses(tmp_path, FLOOR_TEXT)
    assert rc == 0, out


# ── 1. Clause iii's listed paths are the ones that request sign-off ──────────

def _clause_iii_paths() -> list[str]:
    """The paths clause iii's sentence names, read from the shipped file.

    Only the span "A change to ... takes effect only" lists paths; the clause's
    other code spans (the environment name, the exemption's author) are not.
    """
    body = _clause_body(FLOOR_TEXT, "iii")
    span = re.search(r"A change to (.+?) takes effect only", body, re.DOTALL)
    assert span, "clause iii no longer carries its 'A change to ...' sentence"
    paths = re.findall(r"`([^`]+)`", span.group(1))
    if re.search(r"\bthis file\b", span.group(1)):
        paths.insert(0, "FLOOR.md")
    return paths


def _sample(path: str) -> str:
    return f"{path}sample.py" if path.endswith("/") else path


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _floor_core(root: Path, paths: list[str]) -> set[str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "protected", "--base", "HEAD",
         "--changed", "--role", "floor-core"],
        input="\n".join(paths) + "\n",
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert proc.returncode == 0, proc.stderr
    return {line for line in proc.stdout.splitlines() if line.strip()}


@pytest.fixture()
def scratch_repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "FLOOR.md").write_text(FLOOR_TEXT, encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "floor")
    return tmp_path


def test_clause_iii_names_the_floor_core_files():
    assert _clause_iii_paths() == [
        "FLOOR.md",
        "scripts/floor_check.py",
        "scripts/floor_anchor.py",
        ".github/workflows/floor.yml",
    ]


def test_every_path_clause_iii_names_requests_sign_off(scratch_repo):
    listed = [_sample(path) for path in _clause_iii_paths()]
    assert listed, "clause iii lists no paths"
    assert _floor_core(scratch_repo, listed) == set(listed)


def test_nothing_beyond_clause_iii_requests_sign_off(scratch_repo):
    # The gate code and the canaries are canary-gated, not sign-off-gated,
    # under the narrowed clause; a path clause iii does not name stays quiet.
    unlisted = [
        "scripts/contract/start_gate.py",
        "scripts/canaries/run_canaries.py",
        "tests/canaries/known-good/contract.md",
        ".github/workflows/ci.yml",
        "scripts/floor_check_helpers.py",
    ]
    assert _floor_core(scratch_repo, unlisted) == set()


# ── 2. Gutting any clause body, or dropping its anchor, goes red ─────────────

@pytest.mark.parametrize("clause_id", CLAUSE_IDS)
def test_gutted_clause_body_is_named(tmp_path, clause_id):
    body = _clause_body(FLOOR_TEXT, clause_id)
    gutted = FLOOR_TEXT.replace(body, "Intentionally left blank.\n\n", 1)
    rc, out = _run_clauses(tmp_path, gutted)
    assert rc == 1, out
    assert _named_clauses(out) == [clause_id]


@pytest.mark.parametrize("clause_id", CLAUSE_IDS)
def test_dropped_clause_anchor_is_named(tmp_path, clause_id):
    text = FLOOR_TEXT.replace(f"<!-- floor-clause:{clause_id} -->\n", "", 1)
    rc, out = _run_clauses(tmp_path, text)
    assert rc == 1, out
    assert clause_id in _named_clauses(out)


@pytest.mark.parametrize(
    ("clause_id", "phrase"),
    [
        ("i", "run-complete"),
        ("ii", "unamendable"),
        ("iii", "out-of-band"),
        ("iii", "floor-signoff"),
    ],
)
def test_clause_loses_a_load_bearing_word(tmp_path, clause_id, phrase):
    # The words each clause's meaning turns on, read off its heading and the
    # environment clause iii names. Rewording only that word inside the clause
    # goes red even when the word survives elsewhere in the file.
    body = _clause_body(FLOOR_TEXT, clause_id)
    assert phrase in body
    text = FLOOR_TEXT.replace(body, body.replace(phrase, "reworded"), 1)
    rc, out = _run_clauses(tmp_path, text)
    assert rc == 1, out
    assert _named_clauses(out) == [clause_id]


# ── 3. Clause iv cannot be negated while keeping its keyword ─────────────────

def test_negated_clause_iv_goes_red(tmp_path):
    # Keeps the word "Immutability" but says it does not protect clause iii.
    # A keyword check alone cannot defend clause iv against every rewrite;
    # this pins the one the #410 review named.
    negated = (
        "**iv. Immutability does not cover clause iii.** The immutability rule\n"
        "in clause iii may be weakened or removed by an automated step.\n\n"
    )
    text = FLOOR_TEXT.replace(_clause_body(FLOOR_TEXT, "iv"), negated, 1)
    assert "Immutability" in text
    rc, out = _run_clauses(tmp_path, text)
    assert rc == 1, out
    assert _named_clauses(out) == ["iv"]


# ── 4. The token floor is four, stated as a number ───────────────────────────

def _token_lines() -> list[str]:
    block = re.search(r"```floor-tokens\n(.*?)```", FLOOR_TEXT, re.DOTALL)
    assert block
    return [line for line in block.group(1).splitlines() if line.strip()]


def test_the_shipped_floor_declares_four_tokens():
    assert len(_token_lines()) == 4


@pytest.mark.parametrize("dropped", range(4))
def test_three_tokens_are_refused(tmp_path, dropped):
    line = _token_lines()[dropped]
    text = FLOOR_TEXT.replace(f"{line}\n", "", 1)
    rc, out = _run_clauses(tmp_path, text)
    assert rc == 1, out
    assert "floor-tokens" in out


def test_four_tokens_are_accepted(tmp_path, capsys):
    floor = tmp_path / "FLOOR.md"
    floor.write_text(FLOOR_TEXT, encoding="utf-8")
    assert main(["clauses", "--floor", str(floor)]) == 0
    assert "4 floor token(s)" in capsys.readouterr().out
