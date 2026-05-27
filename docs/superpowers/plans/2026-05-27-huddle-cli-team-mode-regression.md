# Huddle CLI Team Mode Regression — PRD / Issue

> **For agentic workers:** Implement on a worktree branched from `main` (e.g. `fix-huddle-cli-team-mode`). Suggested home for the committed copy of this doc: `docs/superpowers/plans/2026-05-27-huddle-cli-team-mode-regression.md`. Steps use checkbox (`- [ ]`) syntax for tracking. This is a **PATCH** bug fix (no user-facing feature change) — bump `.claude-plugin/plugin.json` `.version` in the same PR.

**Type:** Bug (regression)
**Severity:** High — the headline `/huddle` capability (team mode) is silently unreachable in the CLI, which is its primary surface.
**Introduced by:** `docs/superpowers/plans/2026-05-23-standalone-skill-pipeline.md` (the marker-based dual-distribution refactor).
**Affected skill:** `skills/huddle/SKILL.md`.

---

## Problem Statement

In the Claude Code CLI, with `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` set and confirmed live, invoking `/huddle` on a size-2+ topic does **not** enter team mode (`TeamCreate` / `SendMessage` persistent agents). It silently degrades to phased sub-agent mode. Team mode is the skill's headline capability on its primary surface, so the regression is high-impact but invisible — no error is raised; the skill simply announces phased mode and proceeds.

The flag is not the problem. The skill's own `SKILL.md` instructs the model that it is in a standalone build where team mode is unreachable, and the model correctly obeys its skill file.

## Technical Context

The standalone-skill pipeline treats the plugin `SKILL.md` as the authoritative source and derives the standalone ZIP from it via two transform operations (`scripts/transform_skill.py`):

1. `strip_chat_skip()` — deletes everything between `<!-- chat-skip:start -->` / `<!-- chat-skip:end -->`.
2. `apply_chat_replace()` — for each `<!-- chat-replace:KEY -->`, deletes the marker **and the line immediately after it**, substituting `replacements[KEY]` from `scripts/standalone_skill_config.py`.

Critical property: **the transform only runs for the ZIP. The CLI plugin ships the raw, untransformed `SKILL.md`.** Therefore any standalone-specific wording written as plain body prose — i.e. text that is neither inside a `chat-skip` block nor positioned as a `chat-replace` default line — survives verbatim into the CLI build.

The pipeline has no "standalone-only / strip-from-CLI" primitive. Standalone-divergent text must live in `standalone_skill_config.py` (`replacements`), never as visible body prose in `SKILL.md`.

## Root Cause

Three pieces of standalone-only wording were authored as content the **untransformed CLI copy** reads as live instruction. They override the (correctly `chat-skip`-fenced) team-mode logic above them. Line numbers are indicative as of 2026-05-27 and may drift — match on the quoted text.

| # | Location | Text the CLI reads verbatim | Why it breaks CLI |
|---|----------|------------------------------|-------------------|
| 1 | ~line 30, unguarded paragraph after a `chat-skip:end` | "Two execution modes are reachable in **this standalone build**… Team mode (persistent agents with cross-talk) is only available in the Claude Code CLI and is **not reachable from here**." | Not inside any guard → present in the CLI copy. Directly tells the model team mode is off. |
| 2 | ~line 39, unguarded table row after a `chat-skip:end` | "\| **Team mode** \| Size 2+, **Claude Code only** \| … **not available in standalone context** \| — \|" | Unguarded duplicate of the correctly-fenced team-mode row (~line 37, inside `chat-skip`) → contradictory mode table. |
| 3 | ~lines 53–54, the `chat-replace:capability-detection` **default line** | "**Tell the user which mode you're in** before you start. One line: \"Running in **phased sub-agent mode** (3 lenses, 5 phases — Agent Teams flag **not detected**).\"" | The line-after-marker is the CLI default (the standalone wording is injected from config for the ZIP). It was authored with standalone wording, so the CLI hardcodes "announce phased mode." |

Leak #3 is the decisive one: `standalone_skill_config.py` already holds the correct standalone replacement for `capability-detection`; the SKILL.md default line should have been the **CLI** instruction (detect the Agent Teams capability and announce team mode when present) but is instead a near-copy of the standalone text.

The correctly-fenced team-mode logic (the three-mode rule, the `Size 2+ AND flag enabled → Team Mode` branch row, and Steps 2–4/6) is all still present inside `chat-skip` blocks — it is simply overridden in the model's reading by these louder, declarative standalone sentences.

### Why it used to work

The previous local-only `huddle` skill was CLI-only, with no standalone variant text. Merging both distributions into one marker-driven source moved standalone prose into the shared body. The transform strips/swaps it correctly for the ZIP, but the CLI reads the raw file with the standalone sentences intact.

## Process Gap

`scripts/tests/test_integration.py` validates forbidden strings in the **ZIP** output. Nothing validates the **CLI source** (`skills/<name>/SKILL.md` as shipped, untransformed) for leaked standalone wording. This entire bug class — standalone prose authored as body text — is currently untested. A guard that greps the raw source for standalone-only phrases would have caught this and will prevent recurrence.

## Solution Requirements

1. The CLI `skills/huddle/SKILL.md` (untransformed) must contain only CLI-correct capability text: it must describe detecting the Agent Teams capability and, when present at team size ≥ 2, entering team mode.
2. The standalone ZIP must remain unchanged in behaviour (still announces phased sub-agent mode for size 2+) — the existing `replacements["capability-detection"]` already provides this; do not regress it.
3. All standalone-divergent wording must live in `scripts/standalone_skill_config.py`, not in `SKILL.md` body prose.
4. A test must assert the raw CLI source is free of standalone-only assertions, so this bug class cannot silently return.

## Proposed Fix

Convert each of the three leaks so the CLI copy is correct and the standalone wording is supplied by config:

- [ ] **Leak #3 (capability-detection default line):** rewrite the line after `<!-- chat-replace:capability-detection -->` to CLI-correct instruction, e.g.:
  > "Tell the user which mode you're in before starting. Check whether the Agent Teams capability is available (`TeamCreate` / `SendMessage`). If it is and team size ≥ 2, announce team mode (one line, e.g. \"Running in team mode — 3 professional lenses, 5 phases\"). Otherwise announce phased sub-agent mode."

  Leave `replacements["capability-detection"]` in `standalone_skill_config.py` untouched (it correctly injects the standalone wording for the ZIP).

- [ ] **Leak #1 (unguarded "standalone build" paragraph):** remove the standalone assertion from the body. Either (a) rely on the existing `chat-skip` three-mode block for CLI and convert this paragraph into a `chat-replace` whose default line is a neutral CLI summary with the standalone sentence moved to `replacements`; or (b) wrap it appropriately so it cannot appear in the CLI copy. The CLI must not read "this standalone build" or "not reachable from here."

- [ ] **Leak #2 (unguarded "Claude Code only" table row):** drop the duplicate row (the `chat-skip`-fenced team-mode row already covers CLI) or convert it to `chat-replace` with a CLI-correct default and the standalone row text moved to `replacements`. The CLI mode table must list team mode as reachable when the flag is enabled.

- [ ] **Sweep:** re-read the full `skills/huddle/SKILL.md` for any other body prose asserting standalone/phased-only behaviour that is not `chat-skip`-fenced or a `chat-replace` default. Apply the same treatment.

- [ ] **Guard test:** add a test (alongside `scripts/tests/test_integration.py`) asserting the **raw, untransformed** `skills/huddle/SKILL.md` does not contain standalone-only phrases — candidate forbidden list: `"standalone build"`, `"not reachable from here"`, `"flag not detected"`, `"Claude Code only"`, `"not available in standalone"`. Assert positively too: the raw source must contain the team-mode branch (e.g. the `TeamCreate` / `Size 2+ … Team Mode` path). Consider generalising to all skills, not just huddle.

- [ ] **Rebuild + validate:** `cd scripts && uv run --with pytest pytest -v`, then `bash scripts/build-standalone-skills.sh huddle` and confirm the ZIP still strips correctly (no orphan markers, standalone capability text intact).

- [ ] **Version bump:** PATCH bump in `.claude-plugin/plugin.json` in the same PR (per repo CLAUDE.md versioning rules), so `/plugin update` surfaces the fix.

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Modify | `skills/huddle/SKILL.md` | Fix the three leaks; ensure CLI copy is team-mode-correct |
| Modify | `scripts/standalone_skill_config.py` | Hold any standalone wording relocated out of the body (if leaks #1/#2 converted to `chat-replace`) |
| Create | `scripts/tests/test_cli_source.py` (or extend `test_integration.py`) | Guard: raw CLI source free of standalone-only phrases; team-mode path present |
| Modify | `.claude-plugin/plugin.json` | PATCH version bump |

## Success Criteria

- In the CLI with `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, `/huddle <size-2+ topic>` announces and enters team mode (`TeamCreate` is called).
- Without the flag, `/huddle` still degrades cleanly to phased sub-agent mode in the CLI.
- The standalone ZIP build is unchanged: size-2+ still announces phased sub-agent mode; no orphan markers; capability text intact.
- New guard test fails against the current (buggy) source and passes after the fix.
- `cd scripts && uv run --with pytest pytest -v` green; PATCH version bumped.

## Risk Assessment

Low. Markdown-only change to one skill file plus a test and a version bump; no executable runtime affected. Main risk is asymmetric drift between CLI and ZIP wording — the new guard test plus the existing ZIP forbidden-string test together pin both surfaces. Verify by reading the rebuilt ZIP's `SKILL.md` and by a live `/huddle` in a flag-enabled CLI session.
