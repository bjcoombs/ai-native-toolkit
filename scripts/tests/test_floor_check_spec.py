"""floor_check.py characterised from FLOOR.md's Tokens and Markers sections.

An independent review (#719) mapped every rule in FLOOR.md to the tests
that check it. Clauses i to iv, the token count and clause iii's path list are
already pinned from the shipped text in ``test_floor_text_oracle.py``. The pin
exemption is pinned in ``test_floor_pin_exempt.py``, and base-copy
classification in ``test_floor_base_classify.py``. This module covers the
Tokens and Markers rules that had no test deriving its oracle from FLOOR.md.

Each oracle comes from FLOOR.md read with this module's own regexes: the token
block, the marker the Markers section displays, the sentences quoted in each
docstring. The script is driven only through its CLI against a scratch
repository. Nothing here imports ``MARKER``, ``MINIMUM_TOKEN_COUNT``,
``_parse_token_block`` or any other constant of the script, so weakening the
script turns a test red instead of moving its expectation along with it.

A test marked ``xfail(strict=True)`` records a place where the current script
departs from FLOOR.md's text. Those are floor-core findings: the fix belongs to
a floor change with the maintainer's sign-off, never to this file.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FLOOR_TEXT = (REPO_ROOT / "FLOOR.md").read_text(encoding="utf-8")
SCRIPT = REPO_ROOT / "scripts" / "floor_check.py"

COMPONENT = "skills/demo/SKILL.md"


def _section(heading: str) -> str:
    """The body of FLOOR.md's ``## <heading>`` section, up to the next heading."""
    match = re.search(
        rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", FLOOR_TEXT, re.MULTILINE | re.DOTALL
    )
    assert match, f"FLOOR.md has no '## {heading}' section"
    return match.group(1)


def _declared_tokens() -> list[str]:
    """The Tokens section's block, one token per line, in declaration order."""
    block = re.search(r"```floor-tokens\n(.*?)\n```", _section("Tokens"), re.DOTALL)
    assert block, "the Tokens section no longer carries its floor-tokens block"
    return [line.strip() for line in block.group(1).splitlines() if line.strip()]


def _displayed_marker() -> str:
    """The marker the Markers section shows "on a line of its own"."""
    match = re.search(
        r"holds the marker on a line of its own:\n\n {4}(\S.*)\n", _section("Markers")
    )
    assert match, "the Markers section no longer displays the marker"
    return match.group(1).strip()


TOKENS = _declared_tokens()
MARKER_TEXT = TOKENS[0]
INVOCATIONS = TOKENS[1:]


# ── Scratch repository and CLI ───────────────────────────────────────────────

def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    return proc.stdout


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD").strip()


def _floor_check(root: Path, *args: str, stdin: str | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=root,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    return proc.returncode, proc.stdout + proc.stderr


def _weakened(output: str) -> list[str]:
    """The tokens a ``markers`` run names as weakened, in the order it names them."""
    return re.findall(r"floor token weakened -> '([^']+)'", output)


def _marked_body() -> str:
    lines = ["# demo", "", MARKER_TEXT, ""]
    lines += [f"Step {n}: run {name}." for n, name in enumerate(INVOCATIONS, start=1)]
    return "\n".join(lines) + "\n"


def _write(root: Path, path: str, text: str) -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


@pytest.fixture()
def floor_repo(tmp_path):
    """A scratch repo holding the shipped FLOOR.md and one marked component."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    _write(tmp_path, "FLOOR.md", FLOOR_TEXT)
    _write(tmp_path, COMPONENT, _marked_body())
    _commit(tmp_path, "base: shipped floor and one marked component")
    return tmp_path


# ── Tokens ───────────────────────────────────────────────────────────────────

def test_tokens_the_first_token_is_the_marker_the_markers_section_shows():
    """Tokens: "The first token is the marker"; Markers shows it on its own line."""
    assert MARKER_TEXT == _displayed_marker()
    assert all(token != MARKER_TEXT for token in INVOCATIONS)


def test_tokens_each_declared_line_is_one_token():
    """Tokens: the floor declares its tokens "one per line"."""
    # The marker is an HTML comment, so it holds spaces; every gate invocation
    # is a single script name, and two names on one line would be one token.
    assert MARKER_TEXT.startswith("<!--") and MARKER_TEXT.endswith("-->")
    for gate in INVOCATIONS:
        assert len(gate.split()) == 1 and gate.endswith(".py"), gate


def test_tokens_the_script_holds_no_token_list_of_its_own():
    """Tokens: "the enforcement script holds no token list of its own".

    The marker is the discovery needle the Markers section describes ("searching
    the tree for that marker"), so the script may name it. No gate invocation
    may appear in the script's source.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    assert [name for name in INVOCATIONS if name in source] == []


def test_tokens_removing_a_token_is_a_floor_change_that_goes_red(floor_repo):
    """Tokens: "removing one is a floor change that goes red rather than a quiet
    narrowing of the check".

    The base declares a fifth token that a marked file carries. A head that
    drops it from the block and from the file fails the markers run, because
    the base's declaration is enforced. The FLOOR.md edit also requests
    sign-off (clause iii: "a change ... to the floor tokens it declares").
    """
    extra = "probe_gate.py"
    widened = FLOOR_TEXT.replace(f"{INVOCATIONS[-1]}\n```", f"{INVOCATIONS[-1]}\n{extra}\n```", 1)
    assert widened != FLOOR_TEXT
    _write(floor_repo, "FLOOR.md", widened)
    _write(floor_repo, COMPONENT, _marked_body() + f"Then run {extra}.\n")
    _commit(floor_repo, "base declares and carries a fifth token")

    _write(floor_repo, "FLOOR.md", FLOOR_TEXT)
    _write(floor_repo, COMPONENT, _marked_body())

    rc, out = _floor_check(floor_repo, "markers", "--base", "HEAD")
    assert rc == 1, out
    assert _weakened(out) == [extra]

    rc, out = _floor_check(floor_repo, "protected", "--base", "HEAD", "--role", "floor-core")
    assert rc == 0, out
    assert "FLOOR.md" in out.splitlines()


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "floor-core finding (#719): removed_tokens compares raw substring "
        "counts, so a gate name inside a longer name (restart_gate.py for "
        "start_gate.py) still counts as naming the gate"
    ),
)
@pytest.mark.parametrize("gate", INVOCATIONS)
def test_tokens_a_gate_renamed_inside_a_longer_name_is_no_longer_named(floor_repo, gate):
    """Tokens: "the rest are the gate invocations a marked file must keep naming".

    ``re<gate>`` names a different script. A marked file that swaps every
    mention of the gate for it no longer names the gate.
    """
    target = floor_repo / COMPONENT
    target.write_text(
        target.read_text(encoding="utf-8").replace(gate, f"re{gate}"), encoding="utf-8"
    )
    rc, out = _floor_check(floor_repo, "markers", "--base", "HEAD")
    assert rc == 1, out
    assert _weakened(out) == [gate]


# ── Markers ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("token", TOKENS)
def test_markers_removing_any_declared_token_from_a_marked_file_fails(floor_repo, token):
    """Markers: floor.yml "fails any pull request that removes one of them from a
    file that previously carried it" - each of the Tokens block's lines.

    Discovery runs without ``--files``, so the file is found from the marker
    alone. Only the removed token is named.
    """
    target = floor_repo / COMPONENT
    kept = [
        line
        for line in target.read_text(encoding="utf-8").splitlines()
        if token not in line
    ]
    target.write_text("\n".join(kept) + "\n", encoding="utf-8")
    rc, out = _floor_check(floor_repo, "markers", "--base", "HEAD")
    assert rc == 1, out
    assert _weakened(out) == [token]


def test_markers_gutting_the_instructions_with_the_marker_kept_goes_red(floor_repo):
    """Markers: "a retro that guts the instructions while leaving the marker
    comment intact still goes red"."""
    _write(floor_repo, COMPONENT, f"# demo\n\n{MARKER_TEXT}\n\nNothing to run here.\n")
    rc, out = _floor_check(floor_repo, "markers", "--base", "HEAD")
    assert rc == 1, out
    assert sorted(_weakened(out)) == sorted(INVOCATIONS)


def test_markers_kept_tokens_pass(floor_repo):
    """Markers: a marked file that keeps every token it carried passes."""
    rc, out = _floor_check(floor_repo, "markers", "--base", "HEAD")
    assert rc == 0, out
    assert COMPONENT in out


def test_markers_the_set_is_discovered_at_the_ref_given(floor_repo):
    """Markers: "It is discovered at a ref ... ``markers --base <ref>``".

    The obligation is read at the ref named, not at the working tree: a
    committed removal still fails against the older base, and the same tree
    passes against its own commit.
    """
    old = _git(floor_repo, "rev-parse", "HEAD").strip()
    _write(floor_repo, COMPONENT, f"# demo\n\n{MARKER_TEXT}\n")
    _commit(floor_repo, "head strips the gate invocations")

    rc, out = _floor_check(floor_repo, "markers", "--base", old)
    assert rc == 1, out
    assert sorted(_weakened(out)) == sorted(INVOCATIONS)

    rc, out = _floor_check(floor_repo, "markers", "--base", "HEAD")
    assert rc == 0, out


@pytest.mark.parametrize(
    ("line", "is_obligation"),
    [
        (MARKER_TEXT, True),
        (f"    {MARKER_TEXT}", True),
        (f"`{MARKER_TEXT}`", False),
        (f"See the `{MARKER_TEXT}` marker in prose.", False),
        (f"{MARKER_TEXT} and some trailing words", False),
    ],
    ids=["bare", "indented", "backtick-line", "backtick-prose", "trailing-text"],
)
def test_markers_only_a_line_of_its_own_carries_the_obligation(
    floor_repo, line, is_obligation
):
    """Markers: a file carries the obligation "when it holds the marker on a line
    of its own"; "a backtick-wrapped mention in prose is documentation"."""
    probe = "docs/probe.md"
    _write(floor_repo, probe, f"# probe\n\n{line}\n")
    _commit(floor_repo, "add probe")
    rc, out = _floor_check(floor_repo, "markers", "--base", "HEAD")
    assert rc == 0, out
    assert (probe in out) is is_obligation, out


def test_markers_floor_md_is_excluded_from_the_marked_set(floor_repo):
    """Markers: "this file is excluded because it is where the marker is defined".

    FLOOR.md holds the marker on lines of its own (the Tokens block and the
    Markers example), yet a run never treats it as a marked file, even when a
    head edit drops those lines.
    """
    assert any(line.strip() == MARKER_TEXT for line in FLOOR_TEXT.splitlines())
    _write(
        floor_repo,
        "FLOOR.md",
        "\n".join(
            line for line in FLOOR_TEXT.splitlines() if line.strip() != MARKER_TEXT
        ) + "\n",
    )
    rc, out = _floor_check(floor_repo, "markers", "--base", "HEAD")
    assert rc == 0, out
    assert "FLOOR.md" not in out.replace("FLOOR.md clause iii", "")


def test_markers_a_moved_component_is_followed_to_its_new_path(floor_repo):
    """Markers: "a marked file that moves is followed to its new path rather than
    read as a deletion" - here a move between component paths."""
    destination = "skills/demo-renamed/SKILL.md"
    _git(floor_repo, "mv", "skills/demo", "skills/demo-renamed")
    rc, out = _floor_check(floor_repo, "markers", "--base", "HEAD")
    assert rc == 0, out
    assert f"ok   {destination}" in out and "moved from" in out


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "floor-core finding (#719): cmd_markers follows a move only to a "
        "component path (skills/<x>/SKILL.md, plugins/<p>/skills/<x>/SKILL.md, "
        "commands/<x>.md) and reads any other move as a deletion; FLOOR.md's "
        "Markers section states no such restriction"
    ),
)
def test_markers_a_moved_marked_file_outside_a_component_path_is_followed(floor_repo):
    """Markers: "a marked file that moves is followed to its new path rather than
    read as a deletion" - the sentence names any marked file."""
    _write(floor_repo, "docs/runbook.md", _marked_body())
    _commit(floor_repo, "a marked runbook outside the component shapes")
    (floor_repo / "docs" / "ops").mkdir()
    _git(floor_repo, "mv", "docs/runbook.md", "docs/ops/runbook.md")
    rc, out = _floor_check(floor_repo, "markers", "--base", "HEAD")
    assert rc == 0, out
