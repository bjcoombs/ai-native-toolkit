"""Deterministic contract + reference checks for the plugin's skills and commands.

No AI, no network. Encodes the invariants documented in CLAUDE.md as executable
assertions so a broken reference or dropped frontmatter fails the PR.
"""
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"
COMMANDS = REPO / "commands"
AGENTS = REPO / "agents"
PLUGIN = REPO / ".claude-plugin"

# Skills referenced by name that live outside this plugin (superpowers, etc.).
EXTERNAL_SKILLS = {
    "brainstorming", "writing-plans", "executing-plans",
    "subagent-driven-development", "using-superpowers",
}
# subagent_type values that are built into Claude Code, not agents/*.md.
BUILTIN_AGENTS = {"general-purpose", "Explore", "Plan", "statusline-setup"}

PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME)\b")
# No trailing \b: bash reads $1x and $10 as $1 followed by text.
BARE_POSITIONAL_RE = re.compile(r"\$[1-9]")
# Any indent: substitution ignores markdown structure, so a fence nested in a
# list item is corrupted the same way.
FENCE_OPEN_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
USE_SKILL_RE = re.compile(r"[Uu]se the ([a-z0-9][a-z0-9-]*) skill")
SUBAGENT_RE = re.compile(r'subagent_type:\s*"([^"]+)"')

# Tool-call envelope tags that leak from an agent's own output into authored
# markdown (e.g. a doc-writing agent echoing </invoke> or </content> into the
# file it writes). These render as visible junk and ride into the standalone
# skill ZIPs / releases. Regression guard for the v1.24.0 leaked-tag escape,
# where 5 Map-of-Content docs shipped </content> and </invoke> residue.
ENVELOPE_TAG_RE = re.compile(
    r"</?(?:antml:)?(?:invoke|parameter|function_calls)\b|</?content>",
)

# Unresolved git conflict markers committed into a shipped file. Markdown is the
# silent vector - no parser rejects a literal `<<<<<<<` line, so an unresolved
# merge rides into the plugin (and the standalone ZIPs) unnoticed. Default git
# markers are 7+ identical chars at line start; the angle/pipe forms are
# unambiguous (a `=======` separator collides with markdown setext headings, and
# is always bracketed by the angle markers anyway, so we don't need it).
# Regression guard for #211/#216, where commands/tm.md shipped on main for ~3
# weeks with three unresolved conflict regions (535 stale lines). Reference a
# marker illustratively as inline code (`` `<<<<<<<` ``) so it never starts a line.
CONFLICT_MARKER_RE = re.compile(r"(?:<{7,}|>{7,}|\|{7,})(?: |$)")


def _split_frontmatter(path: Path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    return text[3:end], text[end + 4:]


def _fm_scalar(fm: str, key: str):
    m = re.search(rf"(?m)^\s*{re.escape(key)}\s*:\s*(.*)$", fm)
    return m.group(1).strip() if m else None


def _ships(path: Path) -> bool:
    """Is this an authored file that ships, rather than build output or an input?

    Test fixtures are inputs to a test, not components: a deliberately flawed
    sample skill must not be held to the contract the real skills are held to.
    """
    parts = path.relative_to(REPO).parts
    if {".git", "dist", "node_modules"} & set(parts):
        return False
    return "fixtures" not in parts


def skill_md_files():
    """Every shipped SKILL.md, found by walking the repo rather than one level.

    Discovery is by file, not by directory position, so a skill keeps its
    coverage wherever it lives - ``skills/<x>/`` today, a nested plugin layout
    tomorrow - and a component that moves out of the one directory this used to
    read cannot silently drop out of the suite.
    """
    return sorted(p for p in REPO.rglob("SKILL.md") if _ships(p))


def skill_dirs():
    return [p.parent for p in skill_md_files()]


def command_files():
    return sorted(COMMANDS.glob("*.md")) if COMMANDS.is_dir() else []


def shipped_md():
    # references/*.md is left out on purpose: a skill opens those by path, so
    # Claude Code never argument-substitutes them and a bare $1 there is safe.
    return skill_md_files() + command_files()


def all_authored_markdown():
    """Every authored markdown file that ships with the plugin.

    Broader than ``shipped_md()`` (SKILL.md + commands) because leaked envelope
    tags can land in docs/ and module README.md files too - that's exactly where
    the v1.24.0 escape happened. Excludes test fixtures (intentional inputs) and
    build/VCS dirs.
    """
    if not REPO.is_dir():
        return []
    return [p for p in sorted(REPO.rglob("*.md")) if _ships(p)]


def known_skill_names():
    return {d.name for d in skill_dirs()} | EXTERNAL_SKILLS


def known_agent_names():
    names = {p.stem for p in AGENTS.glob("*.md")} if AGENTS.is_dir() else set()
    return names | BUILTIN_AGENTS


@pytest.mark.parametrize("d", skill_dirs(), ids=lambda d: d.name)
def test_skill_frontmatter(d):
    fm, _ = _split_frontmatter(d / "SKILL.md")
    assert fm is not None, f"{d.name}/SKILL.md missing YAML frontmatter"
    assert _fm_scalar(fm, "name") == d.name, f"{d.name}: name: must match directory"
    assert _fm_scalar(fm, "description"), f"{d.name}: non-empty description required"


@pytest.mark.parametrize("d", skill_dirs(), ids=lambda d: d.name)
def test_skill_has_trigger_clause(d):
    fm, _ = _split_frontmatter(d / "SKILL.md")
    assert fm and "TRIGGER" in fm, f"{d.name}: description must include a TRIGGER clause"


def _fence_spans(lines):
    """Yield (opener index, closer index) per fenced block at any indent: a
    closer uses the opener's character, is at least as long, and has nothing
    after it but whitespace, so a ``` inside a ```` fence stays part of the
    body. An opener with no closer yields nothing, so the text after it stays
    in scope for the checks that skip fenced content."""
    i = 0
    while i < len(lines):
        m = FENCE_OPEN_RE.match(lines[i])
        if not m or (m.group(1)[0] == "`" and "`" in lines[i][m.end():]):
            i += 1
            continue
        marker, opener = m.group(1), i
        i += 1
        while i < len(lines):
            c = FENCE_OPEN_RE.match(lines[i])
            if (c and c.group(1)[0] == marker[0] and len(c.group(1)) >= len(marker)
                    and not lines[i][c.end():].strip()):
                break
            i += 1
        if i == len(lines):
            i = opener + 1
            continue
        yield opener, i
        i += 1


def strip_fences(text):
    """The text with every fenced block, fence lines included, removed."""
    lines = text.splitlines()
    fenced = set()
    for opener, closer in _fence_spans(lines):
        fenced.update(range(opener, closer + 1))
    return "\n".join(l for i, l in enumerate(lines) if i not in fenced)


@pytest.mark.parametrize("p", shipped_md(), ids=lambda p: str(p.relative_to(REPO)))
def test_no_placeholder_tokens(p):
    body = INLINE_CODE_RE.sub("", strip_fences(p.read_text(encoding="utf-8")))
    assert not PLACEHOLDER_RE.search(body), f"{p.relative_to(REPO)}: placeholder token outside code fence"


def test_fence_spans_parser():
    text = "a\n````bash\n```\nx $1\n```\n````\n~~~\ny\n~~~\nb\n"
    assert list(_fence_spans(text.splitlines())) == [(1, 5), (6, 8)]
    assert strip_fences(text) == "a\nb"


def test_unclosed_fence_leaves_the_tail_in_scope():
    text = "a\n```bash\nTODO\n~~~\ny\n~~~\n"
    assert list(_fence_spans(text.splitlines())) == [(3, 5)]
    assert strip_fences(text) == "a\n```bash\nTODO"


@pytest.mark.parametrize("p", shipped_md(), ids=lambda p: str(p.relative_to(REPO)))
def test_no_bare_positional_in_skill_md(p):
    # Claude Code substitutes the invocation's arguments into bare $1..$9
    # anywhere in a SKILL.md before the model reads it, fenced or not, so a
    # shell function or awk field reference runs with argument words in place
    # of its parameters. Brace form (${1}) and awk's $(1) are left alone.
    # Commands are exempt: there $1..$9 is the documented per-argument
    # placeholder, used on purpose.
    if p.name != "SKILL.md":
        pytest.skip("commands use $1..$9 as intended argument placeholders")
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        m = BARE_POSITIONAL_RE.search(line)
        assert not m, (
            f"{p.relative_to(REPO)}:{n}: bare positional {m.group(0)}; write "
            f"${{{m.group(0)[1]}}} in shell or $({m.group(0)[1]}) in awk, or restructure "
            f"the snippet so it takes no positional parameters"
        )


SKILL_DIR_TOKEN_RE = re.compile(r"\$\{CLAUDE_SKILL_DIR\}(/[^\s\"'`)]*)")
LEGACY_SKILL_DIR_RE = re.compile(r"CLAUDE_PLUGIN_ROOT:\+|realpath ~/\.claude/skills")


def skill_dir_token_targets(skill_md: Path):
    """(line, target) for each ${CLAUDE_SKILL_DIR}/<path> in a SKILL.md.

    Claude Code replaces the token with the directory holding that SKILL.md, so
    the target is resolved against the file's own directory, `..` hops included.
    """
    for n, line in enumerate(skill_md.read_text(encoding="utf-8").splitlines(), 1):
        for m in SKILL_DIR_TOKEN_RE.finditer(line):
            yield n, (skill_md.parent / m.group(1).lstrip("/")).resolve()


def test_skill_dir_token_targets_resolve_relative_to_the_skill(tmp_path):
    (tmp_path / "a" / "scripts").mkdir(parents=True)
    (tmp_path / "a" / "scripts" / "x.py").write_text("")
    (tmp_path / "b").mkdir()
    md = tmp_path / "b" / "SKILL.md"
    md.write_text('uv run "${CLAUDE_SKILL_DIR}/../a/scripts/x.py"\n'
                  'uv run "${CLAUDE_SKILL_DIR}/scripts/x.py" "$REPO_ROOT"\n')
    got = [(n, t.exists()) for n, t in skill_dir_token_targets(md)]
    # The sibling hop resolves; the same script named under b's own dir does not.
    assert got == [(1, True), (2, False)]


@pytest.mark.parametrize("p", skill_md_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_skill_dir_token_paths_exist(p):
    # ${CLAUDE_SKILL_DIR} is the only way a plugin skill reaches its bundled
    # scripts (#329): the env var CLAUDE_PLUGIN_ROOT is unset in Bash tool
    # calls. A path that names a missing file fails only in a live run.
    for n, target in skill_dir_token_targets(p):
        assert target.exists(), (
            f"{p.relative_to(REPO)}:{n}: ${{CLAUDE_SKILL_DIR}} path -> "
            f"{target.relative_to(REPO) if target.is_relative_to(REPO) else target} "
            f"does not exist (the token is this skill's own directory)"
        )


@pytest.mark.parametrize("p", skill_md_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_no_legacy_skill_dir_bootstrap(p):
    # The shell-expansion bootstrap resolved to nothing on a plugin install
    # (#329); ${CLAUDE_SKILL_DIR} replaced it and it must not come back.
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        m = LEGACY_SKILL_DIR_RE.search(line)
        assert not m, (
            f"{p.relative_to(REPO)}:{n}: {m.group(0)!r} is the retired skill-dir "
            f"bootstrap; write \"${{CLAUDE_SKILL_DIR}}/scripts/<script>\" instead"
        )


@pytest.mark.parametrize("p", shipped_md(), ids=lambda p: str(p.relative_to(REPO)))
def test_internal_links_resolve(p):
    for target in LINK_RE.findall(p.read_text(encoding="utf-8")):
        if (target.startswith(("http://", "https://", "#", "mailto:"))
                or "$" in target
                or "<" in target
                or target.endswith(".svg")
                or "/.assess/" in target or target.startswith(".assess/")):
            continue
        rel = target.split("#", 1)[0]
        if not rel:
            continue
        assert (p.parent / rel).resolve().exists(), f"{p.relative_to(REPO)}: dead link -> {target}"


@pytest.mark.parametrize("p", shipped_md(), ids=lambda p: str(p.relative_to(REPO)))
def test_use_the_skill_references_resolve(p):
    known = known_skill_names()
    for name in USE_SKILL_RE.findall(p.read_text(encoding="utf-8")):
        assert name in known, f"{p.relative_to(REPO)}: 'Use the {name} skill' references unknown skill"


@pytest.mark.parametrize("p", shipped_md(), ids=lambda p: str(p.relative_to(REPO)))
def test_subagent_types_resolve(p):
    known = known_agent_names()
    for name in SUBAGENT_RE.findall(p.read_text(encoding="utf-8")):
        if "<" in name:  # template placeholder like task-<task-id>
            continue
        assert name in known, (
            f"{p.relative_to(REPO)}: subagent_type \"{name}\" has no agents/{name}.md"
        )


def test_subagent_types_has_cases():
    """Parametrizing over an empty list is a silently green test.

    The subagent check ran over ``commands/*.md`` alone before; a discovery
    change that returns nothing would make it pass by collecting no cases at
    all. Pin the floor so the regression is red instead of invisible.
    """
    found = shipped_md()
    assert len(found) >= 20, f"expected >= 20 shipped markdown components, found {len(found)}"


@pytest.mark.parametrize("p", all_authored_markdown(), ids=lambda p: str(p.relative_to(REPO)))
def test_no_leaked_tool_envelope_tags(p):
    found = sorted(set(ENVELOPE_TAG_RE.findall(p.read_text(encoding="utf-8"))))
    assert not found, (
        f"{p.relative_to(REPO)}: leaked tool-call envelope tag(s) {found} - "
        "agent tool-envelope residue must never ship in authored markdown"
    )


@pytest.mark.parametrize("p", all_authored_markdown(), ids=lambda p: str(p.relative_to(REPO)))
def test_no_conflict_markers(p):
    hits = [
        i + 1
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines())
        if CONFLICT_MARKER_RE.match(line)
    ]
    assert not hits, (
        f"{p.relative_to(REPO)}: unresolved git conflict marker(s) at line(s) {hits} - "
        "a merge was committed without resolving it; reference markers as inline code instead"
    )


def test_plugin_json_valid():
    data = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    assert data.get("version"), "plugin.json missing version"


def test_marketplace_entries_exist():
    mk = PLUGIN / "marketplace.json"
    if not mk.is_file():
        pytest.skip("no marketplace.json")
    data = json.loads(mk.read_text(encoding="utf-8"))
    plugins = data.get("plugins", data) if isinstance(data, dict) else data
    for entry in plugins if isinstance(plugins, list) else []:
        src = entry.get("source") or entry.get("path") if isinstance(entry, dict) else None
        if src and not str(src).startswith(("http", "git")):
            assert (REPO / src).exists(), f"marketplace.json entry missing on disk: {src}"


def test_team_skills_excluded_from_standalone():
    cfg = REPO / "scripts" / "standalone_skill_config.py"
    builder = REPO / "scripts" / "build-standalone-skills.sh"
    for f in (cfg, builder):
        if f.is_file():
            text = f.read_text(encoding="utf-8")
            for s in ("marathon", "pr-review-merge"):
                assert s not in text, f"{f.name}: team-only skill '{s}' must not be in the standalone build"
