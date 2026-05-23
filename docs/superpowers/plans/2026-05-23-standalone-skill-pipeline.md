# Standalone Skill Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce installable standalone skill ZIPs for `assess` and `huddle`, available in Claude Desktop and claude.ai via `Settings → Customize → Skills → Upload Skill` — the same surface coverage as the forgd-marketplace, without the "chat-skill" misnomer (the ZIPs install in Chat *and* Cowork, not Chat only).

**Architecture:** A marker-based source transform treats the plugin SKILL.md as the authoritative source and produces standalone ZIPs as derived artifacts. `<!-- chat-skip:start/end -->` markers strip plugin-only content (SKILL_DIR path resolution, agent-orchestration infrastructure, namespaced slash commands); `<!-- chat-replace:key -->` markers swap lines with surface-neutral replacements defined in `standalone_skill_config.py`. A Python transformer handles the mechanics; a bash orchestrator stages, transforms, validates, and zips. GitHub Actions rebuilds on every `plugin.json` version bump and publishes to a rolling `standalone-skills-latest` release. Tests follow the project convention: `uv run --with pytest pytest` from within a `pyproject.toml`-bearing directory.

**Tech Stack:** Python 3.11+ (stdlib only), bash, GitHub Actions, `zipfile`, `re`, `shutil`, `uv` + pytest

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Create | `scripts/transform_skill.py` | Core transformer: strip markers, apply replacements, override frontmatter, build ZIP |
| Create | `scripts/standalone_skill_config.py` | Per-skill config: standalone names, descriptions, replacement text keyed to markers |
| Create | `scripts/pyproject.toml` | pytest config for the pipeline scripts; mirrors `skills/assess/pyproject.toml` |
| Create | `scripts/tests/__init__.py` | Make tests a package (consistent with `skills/assess/tests/`) |
| Create | `scripts/tests/test_transform.py` | Unit tests for transformer primitives |
| Create | `scripts/tests/test_integration.py` | Full build + ZIP content validation (forbidden strings, expected files) |
| Create | `scripts/build-standalone-skills.sh` | Orchestrator: invoke transformer for each skill → `dist/standalone-skills/` |
| Create | `.github/workflows/build-standalone-skills.yml` | CI: trigger on `plugin.json` version bump, publish to `standalone-skills-latest` |
| Create | `dist/.gitkeep` | Track empty dist dir; generated ZIPs are gitignored |
| Modify | `.gitignore` | Allowlist `scripts/` and `dist/`; ignore generated ZIPs |
| Modify | `skills/assess/SKILL.md` | Add `chat-skip`/`chat-replace` markers on plugin-only content |
| Modify | `skills/huddle/SKILL.md` | Add `chat-skip`/`chat-replace` markers on agent-orchestration content |
| Modify | `.github/workflows/tests.yml` | Add second job to run `scripts/tests/` suite via `uv run --with pytest pytest` |
| Modify | `README.md` | Add "Also available in Claude Chat and Cowork" install section; update repo structure diagram |
| Modify | `CLAUDE.md` | Document the pipeline, marker rules, and how to run locally |
| Modify | `.claude-plugin/plugin.json` | Bump version 1.5.0 → 1.5.1 (PATCH: tooling + docs, no skill content changes) |

---

## Task 1: Update .gitignore and create dist scaffold

**Files:**
- Modify: `.gitignore`
- Create: `dist/.gitkeep`

The repo uses a deny-all / allowlist `.gitignore`. `scripts/` and `dist/` must be explicitly allowed; generated ZIPs inside `dist/standalone-skills/` must be ignored.

- [ ] **Step 1: Add allowlist entries and ignore generated ZIPs**

In `.gitignore`, after `!/docs/` add:

```
!/scripts/
!/dist/
dist/standalone-skills/
```

- [ ] **Step 2: Create dist marker file**

```bash
touch /Users/jerome/tools/skills/ai-native-toolkit/dist/.gitkeep
```

- [ ] **Step 3: Commit**

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit
git add .gitignore dist/.gitkeep
git commit -m "chore: allowlist scripts/ and dist/ in .gitignore"
```

---

## Task 2: Create pyproject.toml for the pipeline scripts

**Files:**
- Create: `scripts/pyproject.toml`
- Create: `scripts/tests/__init__.py`

The project runs all tests with `uv run --with pytest pytest` from within a `pyproject.toml`-bearing directory — same pattern as `skills/assess/`. The pipeline scripts need their own equivalent.

- [ ] **Step 1: Write pyproject.toml**

`scripts/pyproject.toml`:

```toml
[project]
name = "standalone-skill-pipeline"
version = "0.1.0"
description = "Build pipeline for standalone skill ZIPs"
requires-python = ">=3.11"

[dependency-groups]
dev = [
    "pytest>=8.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-v --tb=short"
```

`pythonpath = ["."]` puts `scripts/` on `sys.path` so `import transform_skill` and `import standalone_skill_config` resolve from within the `tests/` directory.

- [ ] **Step 2: Create tests package**

```bash
mkdir -p /Users/jerome/tools/skills/ai-native-toolkit/scripts/tests
touch /Users/jerome/tools/skills/ai-native-toolkit/scripts/tests/__init__.py
```

- [ ] **Step 3: Verify uv can find pytest from the scripts directory**

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit/scripts
uv run --with pytest pytest --collect-only 2>&1 | head -5
```

Expected: `no tests ran` (empty suite, no errors).

- [ ] **Step 4: Commit**

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit
git add scripts/pyproject.toml scripts/tests/__init__.py
git commit -m "chore: add pyproject.toml for standalone-skill pipeline tests"
```

---

## Task 3: Write failing unit tests

**Files:**
- Create: `scripts/tests/test_transform.py`

Write tests before the implementation. All fail until Task 4 creates the module.

- [ ] **Step 1: Write the unit tests**

`scripts/tests/test_transform.py`:

```python
"""Unit tests for transform_skill.py primitives."""
import textwrap
import pytest
from transform_skill import (
    strip_chat_skip,
    apply_chat_replace,
    override_frontmatter,
    check_orphan_markers,
)


# ── strip_chat_skip ──────────────────────────────────────────────────────────

def test_strip_removes_block():
    text = "before\n<!-- chat-skip:start -->\nhidden\n<!-- chat-skip:end -->\nafter\n"
    assert strip_chat_skip(text) == "before\nafter\n"


def test_strip_no_markers_unchanged():
    text = "no markers here\n"
    assert strip_chat_skip(text) == text


def test_strip_multiple_blocks():
    text = (
        "a\n<!-- chat-skip:start -->\nb\n<!-- chat-skip:end -->\n"
        "c\n<!-- chat-skip:start -->\nd\n<!-- chat-skip:end -->\ne\n"
    )
    assert strip_chat_skip(text) == "a\nc\ne\n"


def test_strip_trailing_newline_consumed():
    text = "keep\n<!-- chat-skip:start -->\nremove\n<!-- chat-skip:end -->\nkeep2\n"
    result = strip_chat_skip(text)
    assert result == "keep\nkeep2\n"


# ── apply_chat_replace ───────────────────────────────────────────────────────

def test_replace_substitutes_next_line():
    text = "before\n<!-- chat-replace:my-key -->\noriginal\nafter\n"
    result = apply_chat_replace(text, {"my-key": "replacement"})
    assert result == "before\nreplacement\nafter\n"


def test_replace_unknown_key_leaves_line():
    text = "<!-- chat-replace:unknown -->\noriginal\n"
    result = apply_chat_replace(text, {"other": "x"})
    assert "original" in result


def test_replace_multiple_keys():
    text = "<!-- chat-replace:k1 -->\nold1\n<!-- chat-replace:k2 -->\nold2\n"
    result = apply_chat_replace(text, {"k1": "new1", "k2": "new2"})
    assert result == "new1\nnew2\n"


def test_replace_preserves_surrounding_content():
    text = "line1\n<!-- chat-replace:k -->\nreplaced\nline3\n"
    result = apply_chat_replace(text, {"k": "NEW"})
    assert result == "line1\nNEW\nline3\n"


# ── override_frontmatter ─────────────────────────────────────────────────────

def test_frontmatter_replaces_name():
    text = "---\nname: old-name\ndescription: \"old\"\n---\nbody\n"
    result = override_frontmatter(text, "new-name", "new desc")
    assert "name: new-name" in result
    assert "old-name" not in result


def test_frontmatter_replaces_description():
    text = "---\nname: s\ndescription: \"old description\"\n---\nbody\n"
    result = override_frontmatter(text, "s", "new description")
    assert "new description" in result
    assert "old description" not in result


def test_frontmatter_preserves_body():
    text = "---\nname: s\ndescription: \"d\"\n---\nbody content here\n"
    result = override_frontmatter(text, "s", "d2")
    assert "body content here" in result


# ── check_orphan_markers ─────────────────────────────────────────────────────

def test_orphan_unclosed_start():
    text = "text\n<!-- chat-skip:start -->\nunclosed\n"
    issues = check_orphan_markers(text)
    assert any("chat-skip:start" in i for i in issues)


def test_orphan_unmatched_end():
    text = "text\n<!-- chat-skip:end -->\n"
    issues = check_orphan_markers(text)
    assert any("chat-skip:end" in i for i in issues)


def test_orphan_unconsumed_replace():
    text = "<!-- chat-replace:leftover -->\n"
    issues = check_orphan_markers(text)
    assert any("chat-replace" in i for i in issues)


def test_no_orphans_clean_text():
    assert check_orphan_markers("clean text\n") == []
```

- [ ] **Step 2: Verify tests fail (module not yet created)**

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit/scripts
uv run --with pytest pytest tests/test_transform.py -v 2>&1 | head -15
```

Expected: `ModuleNotFoundError: No module named 'transform_skill'`

---

## Task 4: Implement transform_skill.py

**Files:**
- Create: `scripts/transform_skill.py`

- [ ] **Step 1: Write the module**

`scripts/transform_skill.py`:

```python
"""Transform a Claude Code plugin skill into a standalone ZIP for Chat / Cowork."""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

_SKIP_PATTERN = re.compile(
    r"<!-- chat-skip:start -->.*?<!-- chat-skip:end -->\n?",
    re.DOTALL,
)
_REPLACE_PATTERN = re.compile(r"<!-- chat-replace:(\S+?) -->")


def strip_chat_skip(text: str) -> str:
    """Remove blocks delimited by chat-skip:start / chat-skip:end."""
    return _SKIP_PATTERN.sub("", text)


def apply_chat_replace(text: str, replacements: dict[str, str]) -> str:
    """Replace each '<!-- chat-replace:key -->\\nNEXT_LINE' with replacements[key]."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _REPLACE_PATTERN.match(lines[i].rstrip())
        if m:
            key = m.group(1)
            if key in replacements:
                out.append(replacements[key])
                i += 2  # consume marker + following line
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def override_frontmatter(text: str, name: str, description: str) -> str:
    """Rewrite name: and description: fields in YAML frontmatter."""
    text = re.sub(r"^name:.*$", f"name: {name}", text, flags=re.MULTILINE)
    text = re.sub(
        r'^description:.*?(?=^\S|\Z)',
        f'description: "{description}"\n',
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    return text


def check_orphan_markers(text: str) -> list[str]:
    """Return marker strings that were not consumed by the transforms."""
    issues: list[str] = []
    if "<!-- chat-skip:start -->" in text:
        issues.append("orphan <!-- chat-skip:start -->")
    if "<!-- chat-skip:end -->" in text:
        issues.append("orphan <!-- chat-skip:end -->")
    for m in _REPLACE_PATTERN.finditer(text):
        issues.append(f"orphan {m.group(0)}")
    return issues


def _transform_md(text: str, replacements: dict[str, str]) -> str:
    text = strip_chat_skip(text)
    text = apply_chat_replace(text, replacements)
    return text


def build_standalone_skill_zip(
    skill_source_dir: Path,
    out_zip: Path,
    standalone_name: str,
    standalone_description: str,
    replacements: dict[str, str],
    exclude_dirs: frozenset[str] = frozenset({"tests", "__pycache__", ".pytest_cache"}),
) -> list[str]:
    """
    Transform *skill_source_dir* and write a standalone ZIP to *out_zip*.

    Returns validation issues; empty list means success. ZIP is not written if
    there are issues.
    """
    staging = out_zip.parent / f"_staging_{standalone_name}"
    if staging.exists():
        shutil.rmtree(staging)

    target_root = staging / standalone_name
    target_root.mkdir(parents=True)

    for src in skill_source_dir.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(skill_source_dir)
        if any(part in exclude_dirs for part in rel.parts):
            continue
        dest = target_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".md":
            dest.write_text(_transform_md(src.read_text("utf-8"), replacements), "utf-8")
        else:
            shutil.copy2(src, dest)

    skill_md = target_root / "SKILL.md"
    if skill_md.exists():
        skill_md.write_text(
            override_frontmatter(
                skill_md.read_text("utf-8"), standalone_name, standalone_description
            ),
            "utf-8",
        )

    issues: list[str] = []
    for md in sorted(target_root.rglob("*.md")):
        for problem in check_orphan_markers(md.read_text("utf-8")):
            issues.append(f"{md.relative_to(staging)}: {problem}")

    if not issues:
        out_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(target_root.rglob("*")):
                if f.is_file():
                    zi = zipfile.ZipInfo(str(f.relative_to(staging)))
                    zi.date_time = (2026, 1, 1, 0, 0, 0)  # normalise mtime for determinism
                    zf.writestr(zi, f.read_bytes())

    shutil.rmtree(staging)
    return issues
```

- [ ] **Step 2: Run unit tests**

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit/scripts
uv run --with pytest pytest tests/test_transform.py -v
```

Expected: all tests PASS (16 tests, 0 failures).

- [ ] **Step 3: Commit**

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit
git add scripts/transform_skill.py scripts/tests/test_transform.py
git commit -m "feat: add standalone skill transformer + unit tests"
```

---

## Task 5: Annotate assess/SKILL.md with markers

**Files:**
- Modify: `skills/assess/SKILL.md`

Four marker sites. All changes are additive (insert comment lines only).

- [ ] **Step 1: Skip the `$ARGUMENTS` section header**

Find the line:
```
**$ARGUMENTS**
```
Wrap it:
```
<!-- chat-skip:start -->
**$ARGUMENTS**
<!-- chat-skip:end -->
```
Rationale: `$ARGUMENTS` is substituted at plugin invocation time in Claude Code. In standalone context there is no slash-command invocation — users describe their intent in natural language and the skill triggers via description matching.

- [ ] **Step 2: Skip the SKILL_DIR resolution line**

Find the single line inside the bash block:
```bash
SKILL_DIR="$(dirname "$(realpath ~/.claude/skills/assess/SKILL.md)")"
```
Wrap just that line:
```bash
<!-- chat-skip:start -->
SKILL_DIR="$(dirname "$(realpath ~/.claude/skills/assess/SKILL.md)")"
<!-- chat-skip:end -->
```

- [ ] **Step 3: Replace the two `uv run "$SKILL_DIR/..."` invocations**

These two lines reference `$SKILL_DIR` and need surface-neutral equivalents. Add a `chat-replace` marker before each:

Before the treemap run line:
```bash
<!-- chat-replace:uv-treemap -->
uv run "$SKILL_DIR/scripts/complexity-treemap.py" "$REPO_ROOT" \
```

Before the core run line:
```bash
<!-- chat-replace:uv-core -->
uv run "$SKILL_DIR/scripts/assess_core.py" "$REPO_ROOT"
```

`standalone_skill_config.py` (Task 7) maps:
- `uv-treemap` → `uv run scripts/complexity-treemap.py "$REPO_ROOT" -o "$REPO_ROOT/.assess/complexity-heatmap.svg" --stats "$REPO_ROOT/.assess/complexity-stats.json"`
- `uv-core` → `uv run scripts/assess_core.py "$REPO_ROOT"`

These paths work when the skill is installed as a standalone ZIP at its standard location (`~/.claude/skills/assess/`).

- [ ] **Step 4: Replace the namespaced plugin references in report and PR footers**

In the report template, find the footer line:
```
_Report generated by [`/ai-native-toolkit:assess`](https://github.com/bjcoombs/ai-native-toolkit). Install in any Claude Code session: `/plugin marketplace add https://github.com/bjcoombs/ai-native-toolkit` then `/plugin install ai-native-toolkit@ai-native-toolkit`._
```
Add before it:
```
<!-- chat-replace:report-footer -->
```

In the PR body template, find the footer line beginning:
```
_Generated by [`/ai-native-toolkit:assess`](https://github.com/bjcoombs/ai-native-toolkit) — a Claude Code plugin...
```
Add before it:
```
<!-- chat-replace:pr-footer -->
```

Config maps both to plain attribution without plugin install instructions.

- [ ] **Step 5: Verify no unclosed skip markers**

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit
python3 - <<'EOF'
from pathlib import Path
import re, sys
text = Path("skills/assess/SKILL.md").read_text()
# strip all skip blocks
stripped = re.sub(r"<!-- chat-skip:start -->.*?<!-- chat-skip:end -->\n?", "", text, flags=re.DOTALL)
if "<!-- chat-skip:start -->" in stripped or "<!-- chat-skip:end -->" in stripped:
    print("FAIL: unclosed chat-skip marker")
    sys.exit(1)
print("OK: no unclosed chat-skip markers")
EOF
```

Expected: `OK: no unclosed chat-skip markers`

- [ ] **Step 6: Commit**

```bash
git add skills/assess/SKILL.md
git commit -m "chore(assess): add standalone-skill markers"
```

---

## Task 6: Annotate huddle/SKILL.md with markers

**Files:**
- Modify: `skills/huddle/SKILL.md`

Huddle's core methodology (six hats, team sizing, facilitation principles, solo flat-parallel mode, phased sub-agent mode, verdict delivery) works in standalone context — Claude reasons through each hat phase directly. Only the Claude Code–specific agent-orchestration infrastructure is removed: `TeamCreate`, `SendMessage`, `TeamDelete`, the capability flag bash export, and the sections that depend on them.

- [ ] **Step 1: Skip the bash export line**

Find:
```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```
Wrap:
```bash
<!-- chat-skip:start -->
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
<!-- chat-skip:end -->
```

- [ ] **Step 2: Replace the Team mode row in the capability table**

Find:
```
| **Team mode** | Size 2+, flag enabled | Persistent `TeamCreate` agents cross-talk via `SendMessage` across phases | 5–15× |
```
Add before it:
```
<!-- chat-replace:team-mode-row -->
```

Config maps `team-mode-row` to:
```
| **Team mode** | Size 2+, Claude Code only | Persistent agents with `SendMessage` cross-talk — not available in standalone context | — |
```

- [ ] **Step 3: Skip the Agent Teams flag explanation block**

Find the block starting with:
```
**Agent Teams flag** enables `TeamCreate`, `SendMessage`, and related tools. Enable in your environment:
```
...through to the paragraph ending `"Worth it for decisions where being wrong costs 100× more than the analysis: ..."`.

Wrap the whole block:
```
<!-- chat-skip:start -->
**Agent Teams flag** enables `TeamCreate`, `SendMessage`, and related tools...
...worth it for decisions where being wrong costs 100× more than the analysis...
<!-- chat-skip:end -->
```

- [ ] **Step 4: Skip Steps 2, 3, 4, and 6 (Claude Code–only team orchestration)**

These steps require `TeamCreate`, `Agent(subagent_type="general-purpose", team_name=...)`, `SendMessage`, and `TeamDelete`. Wrap each heading + body:

```
<!-- chat-skip:start -->
### Step 2: Create the Team
...TeamCreate(...)...
<!-- chat-skip:end -->
```

```
<!-- chat-skip:start -->
### Step 3: Spawn Team Members
...Agent(subagent_type: "general-purpose", team_name: ...)...
<!-- chat-skip:end -->
```

```
<!-- chat-skip:start -->
### Step 4: Facilitate Hat Phases
...SendMessage(to: "*", ...)...
<!-- chat-skip:end -->
```

```
<!-- chat-skip:start -->
### Step 6: Shutdown the Team
...TeamDelete()...
<!-- chat-skip:end -->
```

Steps 1 (Analyze the Topic), 5 (Deliver the Verdict), Solo flat-parallel, and Phased Sub-Agent Mode are **kept** — all work in standalone context.

- [ ] **Step 5: Verify no unclosed skip markers and no TeamCreate in stripped output**

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit
python3 - <<'EOF'
from pathlib import Path
import re, sys
text = Path("skills/huddle/SKILL.md").read_text()
stripped = re.sub(r"<!-- chat-skip:start -->.*?<!-- chat-skip:end -->\n?", "", text, flags=re.DOTALL)
errors = []
if "<!-- chat-skip:start -->" in stripped or "<!-- chat-skip:end -->" in stripped:
    errors.append("unclosed chat-skip marker")
if "TeamCreate" in stripped:
    errors.append("TeamCreate leaked into standalone output")
if "TeamDelete" in stripped:
    errors.append("TeamDelete leaked into standalone output")
if errors:
    print("FAIL:", errors); sys.exit(1)
print("OK: huddle markers valid")
EOF
```

Expected: `OK: huddle markers valid`

- [ ] **Step 6: Commit**

```bash
git add skills/huddle/SKILL.md
git commit -m "chore(huddle): add standalone-skill markers"
```

---

## Task 7: Create standalone_skill_config.py

**Files:**
- Create: `scripts/standalone_skill_config.py`

- [ ] **Step 1: Write the config**

`scripts/standalone_skill_config.py`:

```python
"""
Per-skill configuration for the standalone skill ZIP build pipeline.

Each entry in SKILLS maps a skill name to:
  standalone_name        — name field written into the ZIP's SKILL.md frontmatter
  standalone_description — description field (trigger-phrased for description-based
                           auto-matching; no plugin namespacing)
  source_dir             — path relative to repo root
  replacements           — dict mapping chat-replace:KEY → replacement text
  exclude_dirs           — subdirectories omitted from the ZIP
"""

SKILLS: dict[str, dict] = {
    "assess": {
        "standalone_name": "assess",
        "standalone_description": (
            "Assess a codebase's AI-agent readiness (0-7 layered contract model) and generate "
            "a complexity hotspot SVG treemap. TRIGGER when asked for an AI-readiness review, "
            "codebase assessment, complexity heatmap, migration risk triage, or 'how ready is "
            "this code for agents?'. Full script automation (SVG, deterministic core) requires "
            "terminal access; the layered assessment works in any context."
        ),
        "source_dir": "skills/assess",
        "exclude_dirs": {"tests", "__pycache__", ".pytest_cache"},
        "replacements": {
            "uv-treemap": (
                'uv run scripts/complexity-treemap.py "$REPO_ROOT" '
                '-o "$REPO_ROOT/.assess/complexity-heatmap.svg" '
                '--stats "$REPO_ROOT/.assess/complexity-stats.json"'
            ),
            "uv-core": 'uv run scripts/assess_core.py "$REPO_ROOT"',
            "report-footer": (
                "_Report generated by "
                "[assess](https://github.com/bjcoombs/ai-native-toolkit)._"
            ),
            "pr-footer": (
                "_Generated by "
                "[assess](https://github.com/bjcoombs/ai-native-toolkit)._"
            ),
        },
    },
    "huddle": {
        "standalone_name": "huddle",
        "standalone_description": (
            "Structured multi-perspective analysis using Six Thinking Hats. TRIGGER when asked "
            "to run a huddle, wanting multi-perspective or red-team/blue-team analysis, needing "
            "to weigh a hard decision from several angles, or wanting panel/board deliberation. "
            "Scales from solo gut-check (1) to board-level deliberation (8+) using Fibonacci "
            "team sizing. Team mode with persistent agents requires Claude Code."
        ),
        "source_dir": "skills/huddle",
        "exclude_dirs": set(),
        "replacements": {
            "team-mode-row": (
                "| **Team mode** | Size 2+, Claude Code only | "
                "Persistent agents with cross-talk — not available in standalone context | — |"
            ),
        },
    },
}
```

- [ ] **Step 2: Add config smoke tests to test_transform.py**

Append to `scripts/tests/test_transform.py`:

```python
# ── standalone_skill_config smoke tests ─────────────────────────────────────

def test_config_has_required_keys():
    from standalone_skill_config import SKILLS
    required = {
        "standalone_name", "standalone_description",
        "source_dir", "replacements", "exclude_dirs",
    }
    for name, cfg in SKILLS.items():
        missing = required - set(cfg)
        assert not missing, f"SKILLS['{name}'] missing keys: {missing}"


def test_assess_config_covers_all_markers():
    from standalone_skill_config import SKILLS
    from pathlib import Path
    import re
    text = Path("../../skills/assess/SKILL.md").read_text()
    in_file = set(re.findall(r"<!-- chat-replace:(\S+?) -->", text))
    in_config = set(SKILLS["assess"]["replacements"])
    assert not (in_file - in_config), f"assess markers with no config entry: {in_file - in_config}"


def test_huddle_config_covers_all_markers():
    from standalone_skill_config import SKILLS
    from pathlib import Path
    import re
    text = Path("../../skills/huddle/SKILL.md").read_text()
    in_file = set(re.findall(r"<!-- chat-replace:(\S+?) -->", text))
    in_config = set(SKILLS["huddle"]["replacements"])
    assert not (in_file - in_config), f"huddle markers with no config entry: {in_file - in_config}"
```

- [ ] **Step 3: Run all unit tests**

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit/scripts
uv run --with pytest pytest tests/test_transform.py -v
```

Expected: all tests PASS (19+ tests).

- [ ] **Step 4: Commit**

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit
git add scripts/standalone_skill_config.py scripts/tests/test_transform.py
git commit -m "feat: add standalone skill config (assess + huddle)"
```

---

## Task 8: Write integration tests

**Files:**
- Create: `scripts/tests/test_integration.py`

These tests run the full `build_standalone_skill_zip()` pipeline against the real skill directories and assert on ZIP contents — forbidden strings, expected files, excluded directories.

- [ ] **Step 1: Write the integration tests**

`scripts/tests/test_integration.py`:

```python
"""
Integration tests: run the full standalone skill build and validate ZIP contents.

These tests catch the class of bug where plugin-internal content leaks into a
bundled reference file that wasn't processed by the transformer.
"""
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent  # scripts/tests/../../


def _build(skill_name: str, tmp_path: Path) -> zipfile.ZipFile:
    from standalone_skill_config import SKILLS
    from transform_skill import build_standalone_skill_zip

    cfg = SKILLS[skill_name]
    out_zip = tmp_path / f"{cfg['standalone_name']}.zip"
    issues = build_standalone_skill_zip(
        skill_source_dir=REPO_ROOT / cfg["source_dir"],
        out_zip=out_zip,
        standalone_name=cfg["standalone_name"],
        standalone_description=cfg["standalone_description"],
        replacements=cfg["replacements"],
        exclude_dirs=frozenset(cfg["exclude_dirs"]),
    )
    assert not issues, f"Build produced issues:\n" + "\n".join(issues)
    assert out_zip.exists(), "ZIP not created despite no issues reported"
    return zipfile.ZipFile(out_zip)


def _md_contents(zf: zipfile.ZipFile) -> dict[str, str]:
    return {
        name: zf.read(name).decode("utf-8")
        for name in zf.namelist()
        if name.endswith(".md")
    }


# ── assess ───────────────────────────────────────────────────────────────────

class TestAssessBuild:
    @pytest.fixture(scope="class")
    def assess_zip(self, tmp_path_factory):
        return _build("assess", tmp_path_factory.mktemp("assess"))

    def test_skill_md_present(self, assess_zip):
        assert "assess/SKILL.md" in assess_zip.namelist()

    def test_scripts_present(self, assess_zip):
        names = assess_zip.namelist()
        assert any(n.startswith("assess/scripts/") for n in names)

    def test_tests_excluded(self, assess_zip):
        names = assess_zip.namelist()
        assert not any("tests/" in n for n in names), "tests/ directory leaked into ZIP"

    def test_no_skill_dir_reference(self, assess_zip):
        for name, content in _md_contents(assess_zip).items():
            assert "SKILL_DIR" not in content, f"{name}: SKILL_DIR leaked"

    def test_no_dollar_arguments(self, assess_zip):
        for name, content in _md_contents(assess_zip).items():
            assert "$ARGUMENTS" not in content, f"{name}: $ARGUMENTS leaked"

    def test_no_namespaced_slash_command(self, assess_zip):
        for name, content in _md_contents(assess_zip).items():
            assert "ai-native-toolkit:assess" not in content, (
                f"{name}: namespaced slash command leaked"
            )

    def test_no_plugin_install_instructions(self, assess_zip):
        for name, content in _md_contents(assess_zip).items():
            assert "/plugin marketplace add" not in content, (
                f"{name}: plugin install instructions leaked"
            )

    def test_frontmatter_name_correct(self, assess_zip):
        skill_md = assess_zip.read("assess/SKILL.md").decode("utf-8")
        assert "name: assess" in skill_md

    def test_frontmatter_no_original_description(self, assess_zip):
        # Original description references CLAUDE Code-specific TRIGGER pattern text.
        # The standalone description is different.
        skill_md = assess_zip.read("assess/SKILL.md").decode("utf-8")
        assert "ai-native-toolkit:assess" not in skill_md

    def test_zip_is_deterministic(self, tmp_path):
        # Build twice; ZIPs should be byte-identical.
        zf1 = _build("assess", tmp_path / "run1")
        zf2 = _build("assess", tmp_path / "run2")
        path1 = Path(zf1.filename)
        path2 = Path(zf2.filename)
        assert path1.read_bytes() == path2.read_bytes(), "ZIP is not deterministic"


# ── huddle ───────────────────────────────────────────────────────────────────

class TestHuddleBuild:
    @pytest.fixture(scope="class")
    def huddle_zip(self, tmp_path_factory):
        return _build("huddle", tmp_path_factory.mktemp("huddle"))

    def test_skill_md_present(self, huddle_zip):
        assert "huddle/SKILL.md" in huddle_zip.namelist()

    def test_no_team_create(self, huddle_zip):
        for name, content in _md_contents(huddle_zip).items():
            assert "TeamCreate" not in content, f"{name}: TeamCreate leaked"

    def test_no_send_message(self, huddle_zip):
        for name, content in _md_contents(huddle_zip).items():
            assert "SendMessage" not in content, f"{name}: SendMessage leaked"

    def test_no_team_delete(self, huddle_zip):
        for name, content in _md_contents(huddle_zip).items():
            assert "TeamDelete" not in content, f"{name}: TeamDelete leaked"

    def test_no_agent_teams_env_var(self, huddle_zip):
        for name, content in _md_contents(huddle_zip).items():
            assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in content, (
                f"{name}: capability flag env var leaked"
            )

    def test_solo_mode_preserved(self, huddle_zip):
        # Solo flat-parallel mode must survive — it's the default Chat execution path.
        skill_md = huddle_zip.read("huddle/SKILL.md").decode("utf-8")
        assert "Solo flat-parallel" in skill_md

    def test_frontmatter_name_correct(self, huddle_zip):
        skill_md = huddle_zip.read("huddle/SKILL.md").decode("utf-8")
        assert "name: huddle" in skill_md
```

- [ ] **Step 2: Run the integration tests** (they will fail until Tasks 5 and 6 have been done — run after both marker annotation tasks are complete)

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit/scripts
uv run --with pytest pytest tests/test_integration.py -v
```

Expected: all tests PASS. If any forbidden-string test fails, the relevant SKILL.md marker annotation (Task 5 or 6) needs a fix — locate the leaking string, wrap it in a `chat-skip` block or add a `chat-replace` marker, and re-run.

- [ ] **Step 3: Run the full suite**

```bash
uv run --with pytest pytest -v
```

Expected: both `test_transform.py` and `test_integration.py` PASS.

- [ ] **Step 4: Commit**

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit
git add scripts/tests/test_integration.py
git commit -m "test: add standalone skill ZIP integration tests"
```

---

## Task 9: Create build-standalone-skills.sh

**Files:**
- Create: `scripts/build-standalone-skills.sh`

- [ ] **Step 1: Write the script**

`scripts/build-standalone-skills.sh`:

```bash
#!/usr/bin/env bash
# Build standalone skill ZIPs from the plugin SKILL.md source files.
#
# Usage:
#   scripts/build-standalone-skills.sh              # build all skills
#   scripts/build-standalone-skills.sh assess       # build one skill by name
#   scripts/build-standalone-skills.sh --dest ~/Desktop  # override output dir
#
# Output: dist/standalone-skills/<name>.zip  (default)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST="$REPO_ROOT/dist/standalone-skills"

SKILLS_TO_BUILD=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dest)   DEST="$2"; shift 2 ;;
    --dest=*) DEST="${1#*=}"; shift ;;
    -*)        echo "Unknown flag: $1" >&2; exit 1 ;;
    *)         SKILLS_TO_BUILD+=("$1"); shift ;;
  esac
done

mkdir -p "$DEST"

cd "$SCRIPT_DIR"
python3 - "$DEST" "${SKILLS_TO_BUILD[@]}" <<'PYEOF'
import sys
from pathlib import Path

dest = Path(sys.argv[1])
requested = set(sys.argv[2:]) if len(sys.argv) > 2 else None

from standalone_skill_config import SKILLS
from transform_skill import build_standalone_skill_zip

repo_root = Path(__file__).parent.parent

results: dict[str, str] = {}
for name, cfg in SKILLS.items():
    if requested and name not in requested:
        continue
    out_zip = dest / f"{cfg['standalone_name']}.zip"
    print(f"Building {name} -> {out_zip.relative_to(repo_root)} ...", flush=True)
    issues = build_standalone_skill_zip(
        skill_source_dir=repo_root / cfg["source_dir"],
        out_zip=out_zip,
        standalone_name=cfg["standalone_name"],
        standalone_description=cfg["standalone_description"],
        replacements=cfg["replacements"],
        exclude_dirs=frozenset(cfg["exclude_dirs"]),
    )
    if issues:
        print(f"  FAIL — {len(issues)} issue(s):")
        for issue in issues:
            print(f"    {issue}")
        results[name] = "FAIL"
    else:
        size_kb = out_zip.stat().st_size // 1024
        print(f"  OK  ({size_kb} KB)")
        results[name] = "OK"

failed = [k for k, v in results.items() if v != "OK"]
if failed:
    print(f"\nFailed: {', '.join(failed)}", file=sys.stderr)
    sys.exit(1)
print(f"\n{len(results)} skill(s) built -> {dest}")
PYEOF
```

- [ ] **Step 2: Make executable**

```bash
chmod +x /Users/jerome/tools/skills/ai-native-toolkit/scripts/build-standalone-skills.sh
```

- [ ] **Step 3: Run the build end-to-end**

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit
bash scripts/build-standalone-skills.sh
```

Expected:
```
Building assess -> dist/standalone-skills/assess.zip ...
  OK  (N KB)
Building huddle -> dist/standalone-skills/huddle.zip ...
  OK  (N KB)

2 skill(s) built -> .../dist/standalone-skills
```

- [ ] **Step 4: Spot-check the ZIPs**

```bash
# Verify assess ZIP contents
unzip -l dist/standalone-skills/assess.zip

# Verify huddle ZIP contents  
unzip -l dist/standalone-skills/huddle.zip

# Forbidden string checks
unzip -p dist/standalone-skills/assess.zip assess/SKILL.md | grep -c "SKILL_DIR" || true
# Expected: 0

unzip -p dist/standalone-skills/huddle.zip huddle/SKILL.md | grep -c "TeamCreate" || true
# Expected: 0
```

- [ ] **Step 5: Commit**

```bash
git add scripts/build-standalone-skills.sh
git commit -m "feat: add standalone skill build script"
```

---

## Task 10: Create GitHub Actions workflow and extend tests.yml

**Files:**
- Create: `.github/workflows/build-standalone-skills.yml`
- Modify: `.github/workflows/tests.yml`

- [ ] **Step 1: Extend tests.yml with a pipeline test job**

Add a second job to `.github/workflows/tests.yml` after the existing `pytest` job:

```yaml
  pipeline-tests:
    name: scripts/ pytest
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Run pytest
        run: uv run --with pytest pytest -v
        working-directory: scripts
```

- [ ] **Step 2: Write the build and publish workflow**

`.github/workflows/build-standalone-skills.yml`:

```yaml
name: Build and publish standalone skill ZIPs

on:
  push:
    branches: [main]
    paths:
      - .claude-plugin/plugin.json
  workflow_dispatch:

permissions:
  contents: write

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # full history needed for git-show on multi-commit pushes

      - name: Check whether plugin version changed
        id: version-check
        run: |
          set -euo pipefail
          BEFORE="${{ github.event.before }}"

          old=$(git show "$BEFORE:.claude-plugin/plugin.json" 2>/dev/null \
            | python3 -c "import json,sys; print(json.load(sys.stdin).get('version',''))" \
            || echo "")
          new=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json')).get('version',''))")

          echo "old=$old  new=$new"
          if [ "$old" = "$new" ]; then
            echo "build=false" >> "$GITHUB_OUTPUT"
          else
            echo "build=true"  >> "$GITHUB_OUTPUT"
          fi

      - name: Set up Python
        if: steps.version-check.outputs.build == 'true' || github.event_name == 'workflow_dispatch'
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Build standalone skill ZIPs
        if: steps.version-check.outputs.build == 'true' || github.event_name == 'workflow_dispatch'
        run: bash scripts/build-standalone-skills.sh

      - name: Publish to standalone-skills-latest release
        if: steps.version-check.outputs.build == 'true' || github.event_name == 'workflow_dispatch'
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          set -euo pipefail
          VERSION=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])")

          gh release delete standalone-skills-latest --yes 2>/dev/null || true
          gh release create standalone-skills-latest \
            --title "Standalone skill ZIPs — latest (v${VERSION})" \
            --notes "Standalone skill ZIPs for Claude Desktop and claude.ai. Install via Settings → Customize → Skills → Upload Skill. Rebuilt automatically on every plugin version bump." \
            --latest=false \
            dist/standalone-skills/*.zip
```

- [ ] **Step 3: Commit**

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit
git add .github/workflows/build-standalone-skills.yml .github/workflows/tests.yml
git commit -m "ci: add standalone skill build/publish workflow and pipeline test job"
```

---

## Task 11: Update README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a standalone install section**

In `README.md`, after the existing `## Install` section add:

```markdown
## Also available in Claude Desktop chat and Cowork

`/assess` and `/huddle` are available as standalone skill ZIPs — no Claude Code session or plugin install required. Works in Claude Desktop chat, claude.ai web, and Cowork.

**Install:**
1. Download `assess.zip` and `huddle.zip` from the [standalone-skills-latest release](https://github.com/bjcoombs/ai-native-toolkit/releases/tag/standalone-skills-latest)
2. In Claude Desktop or claude.ai: **Settings → Customize → Skills → Upload Skill**
3. Upload each ZIP and toggle the skill on

In this context `/huddle` runs in solo or phased sub-agent mode — Claude reasons through each hat phase directly. Team mode (persistent agents with cross-talk) requires Claude Code. `/assess` runs the full layer assessment; the SVG treemap and deterministic wiki require terminal access to the bundled scripts.
```

- [ ] **Step 2: Update the repository structure diagram**

In the `## Repository structure` section, update the tree to include the new directories:

```text
ai-native-toolkit/
├── README.md
├── .claude-plugin/
│   ├── plugin.json
│   └── marketplace.json
├── skills/
│   ├── assess/
│   │   ├── SKILL.md
│   │   └── scripts/
│   └── huddle/
│       └── SKILL.md
├── commands/
├── agents/
├── scripts/                           # Standalone skill ZIP build pipeline
│   ├── transform_skill.py             # Marker-based SKILL.md transformer
│   ├── standalone_skill_config.py     # Per-skill config (names, descriptions, replacements)
│   ├── build-standalone-skills.sh     # Build orchestrator
│   ├── pyproject.toml
│   └── tests/
│       ├── test_transform.py          # Transformer unit tests
│       └── test_integration.py        # Full-build ZIP content validation
├── dist/                              # Generated ZIPs (gitignored; published via CI)
└── docs/
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add standalone skill install section and updated repo structure"
```

---

## Task 12: Update CLAUDE.md, bump version, and final test run

**Files:**
- Modify: `CLAUDE.md`
- Modify: `.claude-plugin/plugin.json`

- [ ] **Step 1: Add pipeline docs to CLAUDE.md**

After the `## CI` section, add:

```markdown
## Standalone skill pipeline

`assess` and `huddle` are also distributed as standalone ZIPs for Claude Desktop chat and Cowork via `Settings → Customize → Skills → Upload Skill`.

**Build locally:**
```bash
bash scripts/build-standalone-skills.sh            # all skills → dist/standalone-skills/
bash scripts/build-standalone-skills.sh assess     # one skill
bash scripts/build-standalone-skills.sh --dest ~/Desktop  # custom output dir
```

**How it works:** HTML comment markers in source SKILL.md files flag plugin-only content. `scripts/transform_skill.py` strips `<!-- chat-skip:start/end -->` blocks and applies `<!-- chat-replace:key -->` substitutions defined in `scripts/standalone_skill_config.py`. The transformer is tested via `uv run --with pytest pytest` from `scripts/`.

**CI:** `.github/workflows/build-standalone-skills.yml` triggers on `plugin.json` version bumps and publishes to the `standalone-skills-latest` rolling release. `.github/workflows/tests.yml` now also runs the `scripts/tests/` suite.

**Marker rules:**
- `<!-- chat-skip:start/end -->` — wraps content to remove entirely (plugin path resolution, `$ARGUMENTS`, agent-orchestration infrastructure, namespaced slash commands)
- `<!-- chat-replace:key -->` + next line — replaces one line with surface-neutral text defined in `standalone_skill_config.py`
- Apply markers to ALL `.md` files in the skill directory, not just `SKILL.md`. Reference files that contain plugin-specific content will leak into the ZIP if unmarked.
- Markers must be balanced. `uv run --with pytest pytest` from `scripts/` catches orphans and forbidden-string leaks.

**When to add markers:** any new content that references `SKILL_DIR`, `$ARGUMENTS`, `$CLAUDE_PLUGIN_ROOT`, a namespaced slash command (`/ai-native-toolkit:*`), or a Claude Code–only tool (`Agent`, `TeamCreate`, `SendMessage`, `TeamDelete`).
```

- [ ] **Step 2: Bump plugin.json**

Change `"version": "1.5.0"` to `"version": "1.5.1"`.

- [ ] **Step 3: Final full test run**

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit/scripts
uv run --with pytest pytest -v
```

Expected: all tests in `test_transform.py` and `test_integration.py` PASS.

Also run the assess deterministic core suite to confirm nothing regressed:

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit/skills/assess
uv run --with pytest pytest -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit
git add CLAUDE.md .claude-plugin/plugin.json
git commit -m "chore: document standalone skill pipeline, bump to v1.5.1"
```

---

## Task 13: Manual skill eval checklist

This task is not automated — run it by hand before publishing the release. Verifies that the standalone skill ZIPs trigger correctly and produce correct output.

**Setup:** Install both ZIPs in Claude Desktop or claude.ai:
`Settings → Customize → Skills → Upload Skill` → upload `assess.zip`, toggle on → upload `huddle.zip`, toggle on.

- [ ] **assess — trigger tests (each in a fresh chat)**

| Prompt | Expected |
|--------|----------|
| "How AI-ready is this codebase?" | assess skill activates |
| "Run assess on this repo" | assess skill activates |
| "Give me a complexity heatmap" | assess skill activates |
| "Score this code for AI agent contributors" | assess skill activates |
| "What's the migration risk in this codebase?" | assess skill activates |
| "Help me write a function" | assess skill does **not** activate |

- [ ] **assess — output correctness (one real run)**

Paste or describe a small codebase and ask for an assessment. Verify:
- [ ] Response follows the report format (Hotspot snapshot, AI Readiness table, Top 3 Actions)
- [ ] No reference to `SKILL_DIR` in Claude's response
- [ ] No `/ai-native-toolkit:assess` in Claude's response
- [ ] No plugin install instructions in the report footer
- [ ] Score is 0–7; maturity label matches score range

- [ ] **huddle — trigger tests (each in a fresh chat)**

| Prompt | Expected |
|--------|----------|
| "Should we rewrite this in Go?" | huddle skill activates |
| "Run a huddle on this architecture decision" | huddle skill activates |
| "I need a multi-perspective analysis of X" | huddle skill activates |
| "Red-team this idea" | huddle activates (or 6hats) |
| "Help me debug this error" | huddle does **not** activate |

- [ ] **huddle — output correctness (one real run)**

Run `/huddle Should we migrate our monolith to microservices?`. Verify:
- [ ] Claude announces team size and hat sequence
- [ ] Runs in solo or phased sub-agent mode (no attempt to call TeamCreate/SendMessage)
- [ ] All relevant hat phases complete
- [ ] Delivers Chairperson's Summary with Recommendation
- [ ] No reference to Agent Teams capability flag

- [ ] **Once eval passes:** publish the release

```bash
cd /Users/jerome/tools/skills/ai-native-toolkit
bash scripts/build-standalone-skills.sh
gh release delete standalone-skills-latest --yes 2>/dev/null || true
VERSION=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])")
gh release create standalone-skills-latest \
  --title "Standalone skill ZIPs — v${VERSION}" \
  --notes "Install via Settings → Customize → Skills → Upload Skill." \
  --latest=false \
  dist/standalone-skills/*.zip
```

---

## Self-Review

**Spec coverage:**
- ✅ Rename: "chat-skill" → "standalone-skill" throughout all file names, variable names, workflow names, release tag
- ✅ Testing follows project convention: `uv run --with pytest pytest` from a `pyproject.toml`-bearing directory
- ✅ Tests in `scripts/tests/` (not `scripts/` root), matching the `skills/assess/tests/` pattern
- ✅ `tests.yml` extended with a second job for `scripts/tests/` — pipeline tests run in CI on every PR and push
- ✅ Integration tests: full build run against real skill directories with forbidden-string sweeps on all `.md` entries, expected-files checks, tests-excluded check, determinism check
- ✅ Manual eval checklist: trigger phrases, false-positive checks, output correctness checks for both skills
- ✅ README updated: standalone install section, updated repo structure diagram
- ✅ CLAUDE.md updated: pipeline docs, marker rules, when to add markers
- ✅ Version bumped 1.5.0 → 1.5.1

**Placeholder scan:** No TBDs. All code blocks are complete and runnable.

**Type consistency:** `build_standalone_skill_zip()` signature in `transform_skill.py` (Task 4) matches every call site in `test_integration.py` (Task 8) and `build-standalone-skills.sh` (Task 9). Config keys `standalone_name`/`standalone_description` match the function parameter names and the config access in the build script.
