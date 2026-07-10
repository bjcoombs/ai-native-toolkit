"""Unit tests for floor_check.py (base-vs-head removal detection + clause integrity)."""
import subprocess
from pathlib import Path

import pytest
from floor_check import (
    FLOOR_TOKENS,
    INVOCATIONS,
    MARKER,
    REQUIRED_CLAUSES,
    main,
    missing_clauses,
    removed_tokens,
    standalone_anchor_count,
)


# ── removed_tokens: base-vs-head removal detection ───────────────────────────

def test_no_base_file_never_flags():
    # File did not exist on base -> nothing can be removed.
    assert removed_tokens(None, "anything") == []


def test_base_without_tokens_never_flags():
    # Bootstrap case: marked file exists but carries no floor token yet.
    assert removed_tokens("plain text no markers", "still plain") == []


def test_token_kept_is_not_removed():
    text = f"prose {MARKER} more prose"
    assert removed_tokens(text, text) == []


def test_marker_removed_is_flagged():
    base = f"intro\n{MARKER}\nbody"
    head = "intro\nbody"
    assert removed_tokens(base, head) == [MARKER]


def test_invocation_removed_is_flagged():
    base = "call start_gate.py then complete_gate.py"
    head = "call complete_gate.py"
    assert removed_tokens(base, head) == ["start_gate.py"]


def test_file_deleted_removes_all_carried_tokens():
    base = f"{MARKER} run start_gate.py and spawn_verifier.py"
    # head None => file deleted on head => every carried token removed.
    removed = removed_tokens(base, None)
    assert set(removed) == {MARKER, "start_gate.py", "spawn_verifier.py"}


def test_only_carried_tokens_are_evaluated():
    # Base carries only the marker; head drops it. Uncarried invocations don't count.
    base = f"only {MARKER} here"
    head = "gutted"
    assert removed_tokens(base, head) == [MARKER]


def test_all_invocations_are_watched():
    for inv in INVOCATIONS:
        base = f"prefix {inv} suffix"
        assert removed_tokens(base, "gutted") == [inv]


# ── Anchor-vs-prose: the false-negative Attack A exposed (PR #276) ────────────

_PROSE_MENTION = f"- the `{MARKER}` markers in all four marked files"


def test_anchor_removed_but_prose_mention_retained_is_flagged():
    # Exact reproduction of the Attack A false-negative: the load-bearing
    # standalone anchor line is deleted, but an incidental backtick-wrapped
    # prose mention of the same string survives. Count drops 2 -> 1, so the
    # marker MUST be flagged even though the substring is still "present".
    base = f"intro\n{MARKER}\nbody\n{_PROSE_MENTION}\n"
    head = f"intro\nbody\n{_PROSE_MENTION}\n"
    assert removed_tokens(base, head) == [MARKER]


def test_anchor_deleted_with_count_masked_is_flagged():
    # Belt-and-braces: adversary deletes the anchor line but adds a fresh prose
    # mention so the raw occurrence count is held constant (2 -> 2). The
    # standalone-anchor-loss signal (1 -> 0) still flags it.
    base = f"{MARKER}\n{_PROSE_MENTION}\n"
    head = f"{_PROSE_MENTION}\n- and another `{MARKER}` reference\n"
    assert base.count(MARKER) == head.count(MARKER)  # count is masked
    assert removed_tokens(base, head) == [MARKER]


def test_prose_only_mention_kept_is_not_flagged():
    # A file that carries only a backtick prose mention (no standalone anchor)
    # and keeps it unchanged is legitimate -> no flag.
    text = f"see {_PROSE_MENTION} for details"
    assert removed_tokens(text, text) == []


def test_marker_gained_is_not_flagged():
    # Bootstrap: base has no marker, head adds the standalone anchor.
    base = "plain skill body, no floor obligation yet"
    head = f"plain skill body\n{MARKER}\nnow armed"
    assert removed_tokens(base, head) == []


def test_extra_prose_mention_added_with_anchor_kept_is_not_flagged():
    # Count increases (documentation added) but the anchor survives -> no flag.
    base = f"{MARKER}\nbody"
    head = f"{MARKER}\nbody\n{_PROSE_MENTION}"
    assert removed_tokens(base, head) == []


def test_standalone_anchor_count_excludes_prose_and_none():
    assert standalone_anchor_count(None) == 0
    assert standalone_anchor_count(f"{MARKER}") == 1
    assert standalone_anchor_count(f"  {MARKER}  ") == 1  # indented anchor counts
    assert standalone_anchor_count(_PROSE_MENTION) == 0  # backtick-wrapped prose
    assert standalone_anchor_count(f"{MARKER}\n{MARKER}\n{_PROSE_MENTION}") == 2


def test_real_marathon_anchor_removal_is_flagged():
    # Regression guard against the real file: the marathon skill carries the
    # standalone anchor AND a prose mention (Retro Boundary section). Simulate
    # Attack A -- drop only the standalone anchor line -- and require a flag.
    repo_root = Path(__file__).resolve().parents[2]
    marathon = repo_root / "skills" / "marathon" / "SKILL.md"
    base = marathon.read_text(encoding="utf-8")
    assert standalone_anchor_count(base) >= 1, "real file must carry the anchor"
    assert base.count(MARKER) >= 2, "real file also has a prose mention"
    head = "\n".join(
        line for line in base.splitlines() if line.strip() != MARKER
    )
    assert removed_tokens(base, head) == [MARKER]


# ── missing_clauses: FLOOR.md integrity ──────────────────────────────────────

def _intact_floor() -> str:
    parts = []
    for anchor, phrase in REQUIRED_CLAUSES.values():
        parts.append(f"{anchor}\nsome text with the {phrase} phrase in it.\n")
    return "\n".join(parts)


def test_intact_floor_has_no_missing_clauses():
    assert missing_clauses(_intact_floor()) == []


def test_absent_floor_reports_all_clauses():
    assert missing_clauses(None) == list(REQUIRED_CLAUSES)


def test_removed_anchor_flags_clause():
    text = _intact_floor().replace("<!-- floor-clause:iii -->", "")
    assert missing_clauses(text) == ["iii"]


def test_gutted_phrase_with_anchor_intact_still_flags():
    # Anchor kept, key phrase removed -> clause is not intact.
    text = _intact_floor().replace("unamendable", "changed")
    assert missing_clauses(text) == ["ii"]


def test_real_floor_file_passes():
    repo_root = Path(__file__).resolve().parents[2]
    floor = repo_root / "FLOOR.md"
    assert floor.exists(), "FLOOR.md must exist at repo root"
    assert missing_clauses(floor.read_text(encoding="utf-8")) == []


# ── CLI integration against a real temp git repo ─────────────────────────────

def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def temp_repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    marked = tmp_path / "marked.md"
    marked.write_text(f"intro\n{MARKER}\nrun start_gate.py\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base with marker")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_cli_markers_pass_when_kept(temp_repo, capsys):
    rc = main(["markers", "--base", "HEAD", "--files", "marked.md"])
    assert rc == 0


def test_cli_markers_fail_when_removed(temp_repo, capsys):
    (temp_repo / "marked.md").write_text("intro\nrun something\n", encoding="utf-8")
    rc = main(["markers", "--base", "HEAD", "--files", "marked.md"])
    assert rc == 1
    out = capsys.readouterr().out
    assert MARKER in out
    assert "start_gate.py" in out


def test_cli_markers_fail_when_file_deleted(temp_repo):
    (temp_repo / "marked.md").unlink()
    rc = main(["markers", "--base", "HEAD", "--files", "marked.md"])
    assert rc == 1


def test_cli_markers_fail_when_anchor_removed_but_prose_kept(tmp_path, monkeypatch):
    # End-to-end reproduction of Attack A (PR #276): base file has the standalone
    # anchor AND a backtick prose mention; head deletes only the anchor line.
    # The check must FAIL even though the marker substring is still present.
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    marked = tmp_path / "marked.md"
    marked.write_text(
        f"intro\n{MARKER}\nbody\n- the `{MARKER}` markers in files\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base with anchor + prose mention")
    monkeypatch.chdir(tmp_path)
    # Attack: remove only the standalone anchor line; keep the prose mention.
    marked.write_text(
        f"intro\nbody\n- the `{MARKER}` markers in files\n",
        encoding="utf-8",
    )
    rc = main(["markers", "--base", "HEAD", "--files", "marked.md"])
    assert rc == 1


def test_cli_clauses_pass_on_real_floor(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    rc = main(["clauses", "--floor", str(repo_root / "FLOOR.md")])
    assert rc == 0


def test_cli_clauses_fail_when_absent(tmp_path):
    rc = main(["clauses", "--floor", str(tmp_path / "nope.md")])
    assert rc == 1


def test_floor_tokens_shape():
    assert MARKER in FLOOR_TOKENS
    for inv in INVOCATIONS:
        assert inv in FLOOR_TOKENS
