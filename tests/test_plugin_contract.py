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
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
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


def skill_dirs():
    if not SKILLS.is_dir():
        return []
    return sorted(d for d in SKILLS.iterdir() if (d / "SKILL.md").is_file())


def command_files():
    return sorted(COMMANDS.glob("*.md")) if COMMANDS.is_dir() else []


def shipped_md():
    return [d / "SKILL.md" for d in skill_dirs()] + command_files()


def all_authored_markdown():
    """Every authored markdown file that ships with the plugin.

    Broader than ``shipped_md()`` (SKILL.md + commands) because leaked envelope
    tags can land in docs/ and module README.md files too - that's exactly where
    the v1.24.0 escape happened. Excludes test fixtures (intentional inputs) and
    build/VCS dirs.
    """
    if not REPO.is_dir():
        return []
    out = []
    for p in sorted(REPO.rglob("*.md")):
        parts = p.relative_to(REPO).parts
        if {".git", "dist", "node_modules"} & set(parts):
            continue
        if "fixtures" in parts:
            continue
        out.append(p)
    return out


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


@pytest.mark.parametrize("p", shipped_md(), ids=lambda p: str(p.relative_to(REPO)))
def test_no_placeholder_tokens(p):
    body = INLINE_CODE_RE.sub("", FENCE_RE.sub("", p.read_text(encoding="utf-8")))
    assert not PLACEHOLDER_RE.search(body), f"{p.relative_to(REPO)}: placeholder token outside code fence"


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


@pytest.mark.parametrize("p", command_files(), ids=lambda p: p.name)
def test_subagent_types_resolve(p):
    known = known_agent_names()
    for name in SUBAGENT_RE.findall(p.read_text(encoding="utf-8")):
        if "<" in name:  # template placeholder like task-<task-id>
            continue
        assert name in known, f"{p.name}: subagent_type \"{name}\" has no agents/{name}.md"


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
