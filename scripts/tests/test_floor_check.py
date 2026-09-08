"""Unit tests for floor_check.py (base-vs-head removal detection + clause integrity)."""
import subprocess
import sys
from pathlib import Path

import pytest
from floor_check import (
    FLOOR_FILE,
    MARKER,
    MINIMUM_TOKEN_COUNT,
    REQUIRED_CLAUSES,
    ROLE_FLOOR_CORE,
    ROLE_GATE_CODE,
    ROLE_MARKED_COMPONENT,
    TOKEN_BLOCK_FENCE,
    FloorTokenError,
    _component_dir,
    _discover_marked_files,
    _is_valid_component_path,
    _parse_token_block,
    main,
    missing_clauses,
    removed_tokens,
    standalone_anchor_count,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# The tokens the floor declares. Read from FLOOR.md the same way the script
# reads them, so these tests carry no token list of their own either.
DECLARED_TOKENS = tuple(
    _parse_token_block((REPO_ROOT / FLOOR_FILE).read_text(encoding="utf-8"))
)
DECLARED_INVOCATIONS = tuple(t for t in DECLARED_TOKENS if t != MARKER)


# ── removed_tokens: base-vs-head removal detection ───────────────────────────

def test_no_base_file_never_flags():
    # File did not exist on base -> nothing can be removed.
    assert removed_tokens(None, "anything", DECLARED_TOKENS) == []


def test_base_without_tokens_never_flags():
    # Bootstrap case: marked file exists but carries no floor token yet.
    assert removed_tokens("plain text no markers", "still plain", DECLARED_TOKENS) == []


def test_token_kept_is_not_removed():
    text = f"prose {MARKER} more prose"
    assert removed_tokens(text, text, DECLARED_TOKENS) == []


def test_marker_removed_is_flagged():
    base = f"intro\n{MARKER}\nbody"
    head = "intro\nbody"
    assert removed_tokens(base, head, DECLARED_TOKENS) == [MARKER]


def test_invocation_removed_is_flagged():
    base = "call start_gate.py then complete_gate.py"
    head = "call complete_gate.py"
    assert removed_tokens(base, head, DECLARED_TOKENS) == ["start_gate.py"]


def test_file_deleted_removes_all_carried_tokens():
    base = f"{MARKER} run start_gate.py and spawn_verifier.py"
    # head None => file deleted on head => every carried token removed.
    removed = removed_tokens(base, None, DECLARED_TOKENS)
    assert set(removed) == {MARKER, "start_gate.py", "spawn_verifier.py"}


def test_only_carried_tokens_are_evaluated():
    # Base carries only the marker; head drops it. Uncarried invocations don't count.
    base = f"only {MARKER} here"
    head = "gutted"
    assert removed_tokens(base, head, DECLARED_TOKENS) == [MARKER]


def test_all_invocations_are_watched():
    for inv in DECLARED_INVOCATIONS:
        base = f"prefix {inv} suffix"
        assert removed_tokens(base, "gutted", DECLARED_TOKENS) == [inv]


# ── Anchor-vs-prose: the false-negative Attack A exposed (PR #276) ────────────

_PROSE_MENTION = f"- the `{MARKER}` markers in all four marked files"


def test_anchor_removed_but_prose_mention_retained_is_flagged():
    # Exact reproduction of the Attack A false-negative: the load-bearing
    # standalone anchor line is deleted, but an incidental backtick-wrapped
    # prose mention of the same string survives. Count drops 2 -> 1, so the
    # marker MUST be flagged even though the substring is still "present".
    base = f"intro\n{MARKER}\nbody\n{_PROSE_MENTION}\n"
    head = f"intro\nbody\n{_PROSE_MENTION}\n"
    assert removed_tokens(base, head, DECLARED_TOKENS) == [MARKER]


def test_anchor_deleted_with_count_masked_is_flagged():
    # Belt-and-braces: adversary deletes the anchor line but adds a fresh prose
    # mention so the raw occurrence count is held constant (2 -> 2). The
    # standalone-anchor-loss signal (1 -> 0) still flags it.
    base = f"{MARKER}\n{_PROSE_MENTION}\n"
    head = f"{_PROSE_MENTION}\n- and another `{MARKER}` reference\n"
    assert base.count(MARKER) == head.count(MARKER)  # count is masked
    assert removed_tokens(base, head, DECLARED_TOKENS) == [MARKER]


def test_prose_only_mention_kept_is_not_flagged():
    # A file that carries only a backtick prose mention (no standalone anchor)
    # and keeps it unchanged is legitimate -> no flag.
    text = f"see {_PROSE_MENTION} for details"
    assert removed_tokens(text, text, DECLARED_TOKENS) == []


def test_marker_gained_is_not_flagged():
    # Bootstrap: base has no marker, head adds the standalone anchor.
    base = "plain skill body, no floor obligation yet"
    head = f"plain skill body\n{MARKER}\nnow armed"
    assert removed_tokens(base, head, DECLARED_TOKENS) == []


def test_extra_prose_mention_added_with_anchor_kept_is_not_flagged():
    # Count increases (documentation added) but the anchor survives -> no flag.
    base = f"{MARKER}\nbody"
    head = f"{MARKER}\nbody\n{_PROSE_MENTION}"
    assert removed_tokens(base, head, DECLARED_TOKENS) == []


def test_standalone_anchor_count_excludes_prose_and_none():
    assert standalone_anchor_count(None) == 0
    assert standalone_anchor_count(f"{MARKER}") == 1
    assert standalone_anchor_count(f"  {MARKER}  ") == 1  # indented anchor counts
    assert standalone_anchor_count(_PROSE_MENTION) == 0  # backtick-wrapped prose
    assert standalone_anchor_count(f"{MARKER}\n{MARKER}\n{_PROSE_MENTION}") == 2


def test_real_marathon_anchor_removal_is_flagged():
    # Regression guard against a real marked file, resolved the way the floor
    # resolves it: discovery at HEAD, not a path this test hard-codes. The
    # marathon component carries the standalone anchor AND a prose mention
    # (Retro Boundary section). Simulate Attack A -- drop only the standalone
    # anchor line -- and require a flag.
    discovered = _discover_marked_files("HEAD", MARKER, cwd=REPO_ROOT)
    assert discovered, "discovery must return the marked set at HEAD"
    marathon = [path for path in discovered if "marathon" in path]
    assert len(marathon) == 1, f"discovery must resolve one marathon component: {discovered}"
    base = (REPO_ROOT / marathon[0]).read_text(encoding="utf-8")
    assert standalone_anchor_count(base) >= 1, "real file must carry the anchor"
    assert base.count(MARKER) >= 2, "real file also has a prose mention"
    head = "\n".join(
        line for line in base.splitlines() if line.strip() != MARKER
    )
    assert removed_tokens(base, head, DECLARED_TOKENS) == [MARKER]


# ── _parse_token_block: FLOOR.md declares the token set ───────────────────────

def _token_block(tokens) -> str:
    return TOKEN_BLOCK_FENCE + "\n" + "\n".join(tokens) + "\n```\n"


def test_parse_token_block_reads_the_declared_tokens():
    text = f"# FLOOR\n\nprose\n\n{_token_block(DECLARED_TOKENS)}\nmore prose\n"
    assert _parse_token_block(text) == list(DECLARED_TOKENS)


def test_parse_token_block_rejects_an_absent_block():
    with pytest.raises(FloorTokenError):
        _parse_token_block("# FLOOR\n\nno token block here at all\n")
    with pytest.raises(FloorTokenError):
        _parse_token_block(None)


def test_parse_token_block_rejects_fewer_than_four_tokens():
    shrunk = list(DECLARED_TOKENS)[: MINIMUM_TOKEN_COUNT - 1]
    with pytest.raises(FloorTokenError):
        _parse_token_block(_token_block(shrunk))


def test_floor_tokens_shape():
    # The real FLOOR.md block is the source of the token set: the marker plus
    # the three gate invocations.
    tokens = _parse_token_block((REPO_ROOT / FLOOR_FILE).read_text(encoding="utf-8"))
    assert len(tokens) >= MINIMUM_TOKEN_COUNT
    assert MARKER in tokens
    for inv in ("start_gate.py", "spawn_verifier.py", "complete_gate.py"):
        assert inv in tokens


# ── missing_clauses: FLOOR.md integrity ──────────────────────────────────────

def _intact_floor() -> str:
    parts = []
    for anchor, *phrases in REQUIRED_CLAUSES.values():
        joined = " and ".join(phrases)
        parts.append(f"{anchor}\nsome text with the {joined} phrase in it.\n")
    return "\n".join(parts)


def _floor_document(tokens=DECLARED_TOKENS) -> str:
    return _intact_floor() + "\n" + _token_block(tokens)


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
    floor = REPO_ROOT / FLOOR_FILE
    assert floor.exists(), "FLOOR.md must exist at repo root"
    assert missing_clauses(floor.read_text(encoding="utf-8")) == []


# ── CLI integration against a real temp git repo ─────────────────────────────

def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")


def _write_floor(root: Path, tokens=DECLARED_TOKENS) -> Path:
    floor = root / FLOOR_FILE
    floor.write_text(_floor_document(tokens), encoding="utf-8")
    return floor


@pytest.fixture()
def temp_repo(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _write_floor(tmp_path)
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
    _init_repo(tmp_path)
    _write_floor(tmp_path)
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
    rc = main(["clauses", "--floor", str(REPO_ROOT / FLOOR_FILE)])
    assert rc == 0


def test_cli_clauses_fail_when_absent(tmp_path):
    rc = main(["clauses", "--floor", str(tmp_path / "nope.md")])
    assert rc == 1


def test_cli_clauses_fail_when_token_block_absent(tmp_path, capsys):
    floor = tmp_path / FLOOR_FILE
    floor.write_text(_intact_floor(), encoding="utf-8")  # clauses intact, no block
    rc = main(["clauses", "--floor", str(floor)])
    assert rc == 1
    assert TOKEN_BLOCK_FENCE in capsys.readouterr().out


def test_cli_clauses_fail_when_token_block_is_short(tmp_path, capsys):
    floor = tmp_path / FLOOR_FILE
    floor.write_text(
        _floor_document(list(DECLARED_TOKENS)[: MINIMUM_TOKEN_COUNT - 1]),
        encoding="utf-8",
    )
    rc = main(["clauses", "--floor", str(floor)])
    assert rc == 1
    out = capsys.readouterr().out
    assert str(MINIMUM_TOKEN_COUNT - 1) in out and str(MINIMUM_TOKEN_COUNT) in out


# ── Discovery: the marked set is data, not a constant ────────────────────────

def _marked_body(name: str) -> str:
    return (
        f"# {name}\n\n{MARKER}\n\n"
        "Runs start_gate.py, then spawn_verifier.py, then complete_gate.py.\n"
    )


def _prose_body(name: str) -> str:
    return (
        f"# {name}\n\n"
        f"Documents the `{MARKER}` marker without carrying the obligation.\n"
    )


@pytest.fixture()
def discovery_repo(tmp_path, monkeypatch):
    """Scratch repo: four marked files, two prose-only carriers, plus FLOOR.md.

    FLOOR.md itself holds a bare marker line inside its token block, so it is a
    seventh candidate the exclusion has to drop.
    """
    _init_repo(tmp_path)
    _write_floor(tmp_path)
    for name in ("alpha", "beta", "gamma", "delta"):
        (tmp_path / f"{name}.md").write_text(_marked_body(name), encoding="utf-8")
    for name in ("epsilon", "zeta"):
        (tmp_path / f"{name}.md").write_text(_prose_body(name), encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "four marked, two prose carriers")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_discovery_returns_only_standalone_anchor_carriers(discovery_repo):
    discovered = _discover_marked_files("HEAD", MARKER, cwd=discovery_repo)
    assert sorted(discovered) == ["alpha.md", "beta.md", "delta.md", "gamma.md"]
    assert FLOOR_FILE not in discovered  # the file that defines the marker


def test_cli_markers_without_files_uses_discovery(discovery_repo, capsys):
    # Weaken one discovered file; the prose-only carriers stay untouched.
    target = discovery_repo / "beta.md"
    target.write_text(
        "\n".join(
            line
            for line in target.read_text(encoding="utf-8").splitlines()
            if line.strip() != MARKER
        ),
        encoding="utf-8",
    )
    rc = main(["markers", "--base", "HEAD"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAIL beta.md" in out
    assert "ok   alpha.md" in out
    assert "epsilon.md" not in out and "zeta.md" not in out


# ── The token set follows FLOOR.md at the base ref (contract FC19) ────────────

def test_token_added_to_the_block_at_base_is_enforced(tmp_path, monkeypatch, capsys):
    # A token the script never heard of is enforced because the base ref's
    # FLOOR.md declares it.
    _init_repo(tmp_path)
    _write_floor(tmp_path, (*DECLARED_TOKENS, "probe_gate.py"))
    marked = tmp_path / "marked.md"
    marked.write_text(
        f"intro\n{MARKER}\nrun start_gate.py\nAlso invokes probe_gate.py.\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base declaring probe_gate.py")
    monkeypatch.chdir(tmp_path)
    marked.write_text(f"intro\n{MARKER}\nrun start_gate.py\n", encoding="utf-8")
    rc = main(["markers", "--base", "HEAD"])
    assert rc == 1
    assert "probe_gate.py" in capsys.readouterr().out


def test_token_added_only_in_the_working_tree_is_not_enforced(
    tmp_path, monkeypatch, capsys
):
    # The token set comes from git show <base>:FLOOR.md, never the working tree,
    # so a token added on the head side is not yet enforced by that run.
    _init_repo(tmp_path)
    _write_floor(tmp_path)
    marked = tmp_path / "marked.md"
    marked.write_text(
        f"intro\n{MARKER}\nrun start_gate.py\nAlso invokes zeta_gate.py.\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base without zeta_gate.py declared")
    monkeypatch.chdir(tmp_path)
    _write_floor(tmp_path, (*DECLARED_TOKENS, "zeta_gate.py"))  # working tree only
    marked.write_text(f"intro\n{MARKER}\nrun start_gate.py\n", encoding="utf-8")
    rc = main(["markers", "--base", "HEAD"])
    assert rc == 0
    assert "zeta_gate.py" not in capsys.readouterr().out


def test_markers_falls_back_to_the_working_tree_when_the_base_predates_the_block(
    tmp_path, monkeypatch, capsys
):
    # Bootstrap: the base ref has no token block at all (every base before the
    # block landed). The run enforces the working-tree declaration and says so.
    _init_repo(tmp_path)
    (tmp_path / FLOOR_FILE).write_text(_intact_floor(), encoding="utf-8")
    marked = tmp_path / "marked.md"
    marked.write_text(f"intro\n{MARKER}\nrun start_gate.py\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base with no token block")
    monkeypatch.chdir(tmp_path)
    _write_floor(tmp_path)  # the block arrives on the head side
    marked.write_text("intro\nrun something\n", encoding="utf-8")
    rc = main(["markers", "--base", "HEAD"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "bootstrap" in out
    assert MARKER in out and "start_gate.py" in out


def test_markers_fails_when_neither_side_declares_a_token_block(
    tmp_path, monkeypatch, capsys
):
    _init_repo(tmp_path)
    (tmp_path / FLOOR_FILE).write_text(_intact_floor(), encoding="utf-8")
    (tmp_path / "marked.md").write_text(f"{MARKER}\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "no token block anywhere")
    monkeypatch.chdir(tmp_path)
    rc = main(["markers", "--base", "HEAD"])
    assert rc == 1
    assert TOKEN_BLOCK_FENCE in capsys.readouterr().out


# ── Component shapes: what a marked file may be, and what it spans ───────────

def test_valid_component_paths_are_the_three_shapes():
    assert _is_valid_component_path("skills/marathon/SKILL.md")
    assert _is_valid_component_path("plugins/delivery/skills/marathon/SKILL.md")
    assert _is_valid_component_path("commands/tm.md")
    # An archive path, a nested reference file and a bare skill directory are
    # not places a component lives.
    assert not _is_valid_component_path("docs/archive/SKILL.md")
    assert not _is_valid_component_path("skills/marathon/forge/SKILL.md")
    assert not _is_valid_component_path("commands/nested/tm.md")


def test_component_dir_spans_a_directory_or_a_single_file():
    assert _component_dir("skills/marathon/SKILL.md") == "skills/marathon"
    assert (
        _component_dir("plugins/delivery/skills/marathon/SKILL.md")
        == "plugins/delivery/skills/marathon"
    )
    assert _component_dir("commands/tm.md") == "commands/tm.md"
    # A prose carrier has no component directory, so quoting the marker in
    # documentation protects nothing.
    assert _component_dir("docs/floor-anchor-proof.md") is None


# ── Rename mapping: a move is followed, not read as a deletion ───────────────

def _long_marked_body(name: str) -> str:
    """A marked component big enough that dropping one line stays above -M50%."""
    filler = "\n".join(f"Step {i}: describe the {name} workflow in detail." for i in range(20))
    return (
        f"# {name}\n\n{MARKER}\n\n"
        "Runs start_gate.py, then spawn_verifier.py, then complete_gate.py.\n\n"
        f"{filler}\n"
    )


@pytest.fixture()
def component_repo(tmp_path, monkeypatch):
    """Scratch repo carrying one directory-shaped and one file-shaped component."""
    _init_repo(tmp_path)
    _write_floor(tmp_path)
    (tmp_path / "skills" / "probe").mkdir(parents=True)
    (tmp_path / "skills" / "probe" / "SKILL.md").write_text(
        _long_marked_body("probe"), encoding="utf-8"
    )
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "probe.md").write_text(
        _long_marked_body("probe command"), encoding="utf-8"
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "two marked components")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_bare_move_of_both_component_shapes_passes(component_repo, capsys):
    # R100 for both: a byte-identical relocation of a directory-shaped and a
    # file-shaped component costs no floor edit.
    (component_repo / "plugins" / "pp" / "skills" / "probe").mkdir(parents=True)
    (component_repo / "plugins" / "pp" / "skills" / "moved").mkdir(parents=True)
    _git(component_repo, "mv", "skills/probe/SKILL.md",
         "plugins/pp/skills/probe/SKILL.md")
    _git(component_repo, "mv", "commands/probe.md",
         "plugins/pp/skills/moved/SKILL.md")
    _git(component_repo, "commit", "-q", "-m", "move both components")
    rc = main(["markers", "--base", "HEAD~1"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "ok   plugins/pp/skills/probe/SKILL.md" in out
    assert "ok   plugins/pp/skills/moved/SKILL.md" in out
    assert "FAIL" not in out


def test_move_that_strips_the_anchor_fails_naming_only_the_marker(
    component_repo, capsys
):
    # R099: the pair stays mapped, so only the weakened token is reported. A
    # build that read the move as a deletion would report all four tokens here,
    # which is the discriminator between following a move and losing one.
    (component_repo / "plugins" / "pp" / "skills" / "probe").mkdir(parents=True)
    _git(component_repo, "mv", "skills/probe/SKILL.md",
         "plugins/pp/skills/probe/SKILL.md")
    moved = component_repo / "plugins" / "pp" / "skills" / "probe" / "SKILL.md"
    moved.write_text(
        "\n".join(
            line
            for line in moved.read_text(encoding="utf-8").splitlines()
            if line.strip() != MARKER
        ),
        encoding="utf-8",
    )
    _git(component_repo, "add", "-A")
    _git(component_repo, "commit", "-q", "-m", "move and strip the anchor")
    rc = main(["markers", "--base", "HEAD~1"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL plugins/pp/skills/probe/SKILL.md" in out
    assert MARKER in out
    for invocation in DECLARED_INVOCATIONS:
        assert invocation not in out, f"{invocation} reported: the move was read as a deletion"


def test_move_past_the_similarity_threshold_fails_as_deleted(component_repo, capsys):
    # A rewrite big enough to leave rename detection is a D plus an A, and the
    # marked file is gone with nothing mapping it: a deletion.
    (component_repo / "plugins" / "pp" / "skills" / "probe").mkdir(parents=True)
    _git(component_repo, "mv", "skills/probe/SKILL.md",
         "plugins/pp/skills/probe/SKILL.md")
    moved = component_repo / "plugins" / "pp" / "skills" / "probe" / "SKILL.md"
    moved.write_text("# unrelated content entirely\n" * 30, encoding="utf-8")
    _git(component_repo, "add", "-A")
    _git(component_repo, "commit", "-q", "-m", "move and rewrite")
    rc = main(["markers", "--base", "HEAD~1"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL skills/probe/SKILL.md" in out
    assert "deleted" in out


def test_move_to_an_archive_destination_fails_naming_both_paths(
    component_repo, capsys
):
    # A rename git is happy to map, into a path no component can live at. The
    # single FAIL line names the origin and the destination it rejected.
    (component_repo / "docs" / "archive").mkdir(parents=True)
    _git(component_repo, "mv", "skills/probe/SKILL.md", "docs/archive/SKILL.md")
    _git(component_repo, "commit", "-q", "-m", "archive the component")
    rc = main(["markers", "--base", "HEAD~1"])
    out = capsys.readouterr().out
    assert rc == 1
    archive_lines = [
        line for line in out.splitlines()
        if line.startswith("FAIL skills/probe/SKILL.md:")
    ]
    assert len(archive_lines) == 1, out
    assert "deleted" in archive_lines[0]
    assert "docs/archive/SKILL.md" in archive_lines[0]


def test_deleted_marked_file_fails_as_deleted(component_repo, capsys):
    _git(component_repo, "rm", "-q", "skills/probe/SKILL.md")
    _git(component_repo, "commit", "-q", "-m", "delete the component")
    rc = main(["markers", "--base", "HEAD~1"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL skills/probe/SKILL.md" in out
    assert "deleted" in out


# ── protected: directory roles replace the path regex ────────────────────────

FIXTURES = Path(__file__).parent / "fixtures"

# The four files the floor marks on the tree the fixtures were taken from, and
# the fifth that only quotes the marker in prose.
_FIXTURE_MARKED = (
    "skills/marathon/SKILL.md",
    "skills/pr-review-merge/SKILL.md",
    "commands/tm.md",
    "commands/issues.md",
)
_FIXTURE_PROSE_CARRIER = "docs/floor-anchor-proof.md"

# The three files the roles protect that the retired path regex did not: the
# floor's own machinery, which the sign-off layer needs.
_FLOOR_CORE_BEYOND_THE_REGEX = {
    FLOOR_FILE,
    "scripts/floor_anchor.py",
    "scripts/floor_check.py",
}


def _fixture_lines(name: str) -> list[str]:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.strip()]


def _protected(cwd: Path, paths, *extra_args) -> set[str]:
    """Run the subcommand as CI runs it: paths on stdin, protected set on stdout."""
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "floor_check.py"),
            "protected",
            "--base",
            "HEAD",
            "--changed",
            *extra_args,
        ],
        input="\n".join(paths) + "\n",
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    assert result.returncode == 0, result.stderr
    return {line for line in result.stdout.splitlines() if line.strip()}


def _materialise_tree(root: Path, paths) -> None:
    for path in paths:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")


@pytest.fixture()
def roles_repo(tmp_path):
    """The committed tree fixture, materialised and marked, in a scratch repo.

    Fixture-based rather than pinned to a live SHA: this suite's checkout sets
    no fetch-depth, so a historical SHA resolves to nothing here.
    """
    _init_repo(tmp_path)
    _materialise_tree(tmp_path, _fixture_lines("floor_roles_tree.txt"))
    for marked in _FIXTURE_MARKED:
        (tmp_path / marked).write_text(_marked_body(marked), encoding="utf-8")
    (tmp_path / _FIXTURE_PROSE_CARRIER).write_text(
        _prose_body(_FIXTURE_PROSE_CARRIER), encoding="utf-8"
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "fixture tree with four marked components")
    return tmp_path


def test_protected_roles_match_regex_plus_floor_core(roles_repo):
    tree = _fixture_lines("floor_roles_tree.txt")
    regex_set = set(_fixture_lines("floor_roles_regex.txt"))
    assert len(tree) == 346, "the tree fixture is the whole tree it was taken from"
    assert len(regex_set) == 38, "the regex fixture is what the retired filter caught"

    protected = _protected(roles_repo, tree)

    # Nothing the regex protected is dropped, and exactly three files are added.
    assert regex_set - protected == set(), "the roles must lose nothing the regex caught"
    assert protected - regex_set == _FLOOR_CORE_BEYOND_THE_REGEX
    assert protected == regex_set | _FLOOR_CORE_BEYOND_THE_REGEX


def test_protected_keeps_the_gate_and_canary_files_a_basename_match_would_drop(
    roles_repo,
):
    # The rejected narrowing was matching gate scripts by basename, which drops
    # every file in those directories that is not one of the three gate names.
    protected = _protected(roles_repo, _fixture_lines("floor_roles_tree.txt"))
    assert "scripts/contract/verifier.py" in protected
    assert "scripts/contract/tiers.py" in protected
    assert "scripts/canaries/drive_interactive.mjs" in protected


def test_protected_excludes_a_prose_only_carrier(roles_repo):
    # It holds no standalone anchor, so it is no marked component -- and a docs
    # path has no component directory to span either.
    protected = _protected(roles_repo, _fixture_lines("floor_roles_tree.txt"))
    assert _FIXTURE_PROSE_CARRIER not in protected


def test_protected_covers_a_plugin_shaped_marked_component(tmp_path):
    # A shape the floor had never seen becomes a marked component the moment it
    # carries an anchor, and its whole directory travels with it.
    _init_repo(tmp_path)
    _write_floor(tmp_path)
    component = tmp_path / "plugins" / "pp" / "skills" / "pq"
    component.mkdir(parents=True)
    (component / "SKILL.md").write_text(_marked_body("pq"), encoding="utf-8")
    (tmp_path / "plugins" / "pp" / "skills" / "pr").mkdir(parents=True)
    (tmp_path / "plugins" / "pp" / "skills" / "pr" / "SKILL.md").write_text(
        _prose_body("pr"), encoding="utf-8"
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "plugin-shaped marked component")
    protected = _protected(
        tmp_path,
        [
            "plugins/pp/skills/pq/refs/x.md",
            "plugins/pp/skills/pq/SKILL.md",
            "plugins/pp/skills/pr/SKILL.md",
        ],
    )
    assert protected == {
        "plugins/pp/skills/pq/refs/x.md",
        "plugins/pp/skills/pq/SKILL.md",
    }


def test_protected_sees_a_component_marked_only_on_the_head_side(tmp_path):
    # The obligation binds from the commit that declares it: at --base HEAD the
    # component is unmarked, and the anchor exists only in the working tree.
    _init_repo(tmp_path)
    _write_floor(tmp_path)
    (tmp_path / "skills" / "probe").mkdir(parents=True)
    (tmp_path / "skills" / "probe" / "SKILL.md").write_text(
        _prose_body("probe"), encoding="utf-8"
    )
    (tmp_path / "skills" / "other").mkdir(parents=True)
    (tmp_path / "skills" / "other" / "SKILL.md").write_text("", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "unmarked base")
    paths = ["skills/probe/notes.md", "skills/other/SKILL.md"]
    assert _protected(tmp_path, paths) == set()  # unmarked at base and at head

    (tmp_path / "skills" / "probe" / "SKILL.md").write_text(
        _marked_body("probe"), encoding="utf-8"
    )
    assert _protected(tmp_path, paths) == {"skills/probe/notes.md"}


def test_protected_without_changed_classifies_the_diff_against_the_base(
    tmp_path, monkeypatch, capsys
):
    # No --changed: the input is git diff --name-only <base>, so the workflow can
    # hand the subcommand a base ref and nothing else.
    _init_repo(tmp_path)
    _write_floor(tmp_path)
    (tmp_path / "skills" / "probe").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.md").write_text("unprotected\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base without the component")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "skills" / "probe" / "SKILL.md").write_text(
        _marked_body("probe"), encoding="utf-8"
    )
    (tmp_path / "docs" / "notes.md").write_text("edited, still unprotected\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "mark the component and edit the docs")
    rc = main(["protected", "--base", "HEAD~1"])
    assert rc == 0
    printed = {line for line in capsys.readouterr().out.splitlines() if line.strip()}
    assert printed == {"skills/probe/SKILL.md"}


def test_protected_role_filter_returns_only_the_floor_core(roles_repo):
    # The flag the sign-off job keys off: floor core alone, not every protected
    # path, so touching a marked component does not demand a deployment review.
    tree = _fixture_lines("floor_roles_tree.txt")
    assert _protected(roles_repo, tree, "--role", ROLE_FLOOR_CORE) == {
        FLOOR_FILE,
        "scripts/floor_anchor.py",
        "scripts/floor_check.py",
        ".github/workflows/floor.yml",
    }
    assert _protected(roles_repo, tree, "--role", ROLE_GATE_CODE) == {
        path for path in tree if path.startswith("scripts/contract/")
    }
    assert _protected(roles_repo, tree, "--role", ROLE_MARKED_COMPONENT) == {
        path
        for path in tree
        if path.startswith(("skills/marathon/", "skills/pr-review-merge/"))
        or path in ("commands/tm.md", "commands/issues.md")
    }


def test_protected_exits_zero_on_an_empty_classification(tmp_path):
    # Classification is not a verdict: an unprotected diff is a successful run
    # with no output, which is what lets the workflow read emptiness as "skip".
    _init_repo(tmp_path)
    _write_floor(tmp_path)
    (tmp_path / "README.md").write_text("nothing protected here\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "no marked components")
    assert _protected(tmp_path, ["README.md", "docs/notes.md"]) == set()
