# Huddle Team-Mode Broadcast Incompatibility — PRD / Issue

**Type:** Bug
**Severity:** High for team-mode users — facilitation stalls mid-meeting. Zero impact on standalone ZIP and on size-1 solo mode.
**Affected file:** `skills/huddle/SKILL.md` (team-mode sections only).
**Surfaced:** during an end-to-end size-2 team-mode run on 2026-05-27, immediately after the PR #30 CLI-team-mode fix. Worked around live by sending per-recipient; the skill text was not changed.
**Status:** Implemented on branch `fix/huddle-broadcast-per-recipient` (branched from `main` at the PR #30 tip). Plugin bumped 1.7.1 → 1.7.2.

---

## Problem Statement

The current Agent Teams runtime rejects fan-out broadcasts. A `SendMessage` with `to: "*"` returns:

```
broadcast (to: "*") is no longer supported — send a message per recipient
```

The huddle skill still instructs both the chair (Blue Hat) and the team members to broadcast with `to: "*"`. In team mode this means:

- **Chair → team** phase announcements (Step 4a) and phase transitions (Step 4c) fail to send.
- **Member → team** sharing of findings (Step 3 member-prompt template) fails to send.

The net effect: in a real team-mode huddle, phases never get announced and members can't share findings, so the meeting cannot progress without manual per-recipient intervention. Static inspection does not reveal this — only an end-to-end team-mode run does.

## Technical Context

The skill was authored against an earlier Agent Teams API that supported `to: "*"` fan-out. The API changed to require one `SendMessage` per named recipient. The skill's team-mode instructions were not updated.

All four occurrences are inside `chat-skip` blocks, so they ship **only** in the CLI plugin (team mode) and are stripped from the standalone ZIP. Confirmed sites (line numbers indicative as of installed 1.7.1 — match on context):

| Line | Section | Current text |
|------|---------|--------------|
| ~184 | Step 3 — member prompt, IMMEDIATE FIRST TASK | "share your key findings with the team via `SendMessage(to="*")`" |
| ~195 | Step 3 — member prompt, How Subsequent Phases Work | "**Share findings** via `SendMessage(to="*")`" |
| ~232 | Step 4a — chair phase announcement | `SendMessage(to: "*", …)` |
| ~259 | Step 4c — chair phase transition | `SendMessage(to: "*", …)` |

Because all sites are `chat-skip`-wrapped, **no `standalone_skill_config.py` change is required** and the standalone ZIP build is unaffected.

## Root Cause

Reliance on a removed broadcast primitive (`to: "*"`). Sends must now be addressed to a specific teammate name. Replacing a broadcast requires the sender to know the recipient names — straightforward for the chair (it spawned everyone), but members need the roster supplied to them.

## Design Decision (the substantive part)

Two distinct senders need fixing differently:

**Chair → members (Steps 4a, 4c):** the chair knows every member's name. Replace each broadcast with a loop that sends the identical message to each member individually.

**Members → peers (Step 3):** a member must share findings with the team without broadcast. Options:

- **(a) Chair supplies the roster.** The chair includes the full list of member names in each spawn prompt (Step 3) and in phase announcements; members send findings to each named peer individually. *Recommended.*
- **(b) Members self-discover.** Members `Read('~/.claude/teams/{team}/config.json')` to enumerate `members[].name`, then send per-peer. Keep as a fallback note for members that weren't given a roster.
- **(c) Members send only to the chair, who relays.** Rejected — violates the skill's "Be a Chair, Not a Switchboard" principle.

**Recommendation: (a), with (b) as a documented fallback.** Fibonacci sizing keeps N small (≤5 typical), so per-peer fan-out is a handful of messages per share, not a scaling problem. Preserve peer-to-peer discussion — members still talk directly to each other, just addressed per-recipient instead of via broadcast.

## Solution Requirements

1. No `SendMessage(to: "*")` (or `to="*"`) anywhere in the skill.
2. Chair sends phase announcements and transitions to each member by name (Steps 4a, 4c) — show the per-recipient loop explicitly in the example.
3. Members share findings to each named peer individually; the chair must supply the roster in spawn prompts (Step 3) and in phase announcements. Document the team-config self-discovery fallback.
4. Preserve the "don't relay / chair is not a switchboard" facilitation principle — members continue to talk peer-to-peer.
5. Update the "Control Pacing" message-budget guidance so the math accounts for per-peer fan-out (a single "share" is now N−1 sends).
6. Standalone ZIP behaviour and build output must remain unchanged (sites are already `chat-skip`-only; verify after edits).

## Proposed Fix

- [x] **Step 4a / 4c (chair):** replace the `to: "*"` broadcast examples with a per-recipient send — e.g. "send this announcement to each member individually (one `SendMessage` per name): `SendMessage(to: "<member-name>", …)`."
- [x] **Step 3 (member template):** change the two `SendMessage(to="*")` instructions to "send to each of your named peers individually." Add the roster into the spawn-prompt template (a `## Your Teammates` line listing names) and note the `~/.claude/teams/{team}/config.json` fallback.
- [x] **Facilitation Principles / Control Pacing:** add a sentence that a "share" now fans out to N−1 per-recipient sends, and keep the existing 2–3 substantive messages per member per phase ceiling.
- [x] **Lessons Learned:** add a one-liner — "Broadcast (one message to all teammates at once) is unsupported; address each teammate by name."
- [x] **Static guard:** extend `tests/test_cli_source.py` to assert `skills/huddle/SKILL.md` contains no `to: "*"` / `to="*"`. Cheap, and prevents regression (live team mode can't be unit-tested, so this static check is the guardrail).
- [x] **Version bump:** PATCH bump in `.claude-plugin/plugin.json` (1.7.1 → 1.7.2).
- [x] **Validate:** `cd scripts && uv run --with pytest pytest -v` (new assert green); rebuild the ZIP and confirm no diff in standalone output.

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Modify | `skills/huddle/SKILL.md` | Steps 3, 4a, 4c per-recipient; roster in spawn prompt; pacing + lessons notes |
| Modify | `scripts/tests/test_cli_source.py` | Add assert: no `to: "*"` in the CLI source |
| Modify | `.claude-plugin/plugin.json` | PATCH version bump |

## Success Criteria

- A live team-mode huddle (size ≥ 2) completes all phases with no broadcast-rejection errors; chair announcements and member shares are all delivered per-recipient.
- Peer-to-peer discussion is preserved (no chair relaying).
- New static assert fails against the current source and passes after the fix.
- Standalone ZIP build output is byte-unchanged; full suite green.
- PATCH version bumped.

## Risk Assessment

Low. Markdown-only change confined to the team-mode (`chat-skip`) path; solo and standalone surfaces untouched. Main residual concern is per-peer fan-out verbosity at larger team sizes — mitigated by Fibonacci sizing and the tightened message budget. Roster drift (a member added mid-run) is rare and chair-controlled; the config self-discovery fallback covers it.

## Companion Item

A sibling cosmetic fix from the same review — the `continue to Step 2` dangling line leaking into the standalone ZIP — is captured separately in `2026-05-27-huddle-cli-team-mode-regression-prd.md` follow-ups. This broadcast fix is the higher-impact of the two.

---

## Implementation Notes (added at implementation time, 2026-05-27)

Two places where the PRD as written was internally inconsistent, and how each was resolved:

1. **`to: "*"` literal in the Lessons one-liner vs. Requirement 1.** Requirement 1 ("no `to: "*"` anywhere") and the proposed Lessons one-liner (which quoted the literal `to: "*"`) conflict. Resolved in favour of the absolute rule: the literal is banned even in "don't do this" prose, so the static guard is a dead-simple substring check with no usage-vs-mention exceptions to get wrong. Explanatory mentions were reworded ("all-recipients broadcast" / "one message to all teammates at once").

2. **"Byte-unchanged" standalone ZIP vs. the version bump.** The standalone build injects the plugin version into the skill `description` (the update-awareness feature from PR #29), so a PATCH bump necessarily changes one line of standalone output (`Standalone build v1.7.1` → `v1.7.2`). Verified by building huddle from `main` and from this branch and diffing: the **only** difference is that version string. The substantive skill body is byte-identical, confirming the broadcast fixes are entirely within `chat-skip` blocks.

Additionally, the new Control Pacing and Lessons Learned sentences reference `SendMessage` (a Claude Code–only tool) and were therefore wrapped in `chat-skip` markers per the repo's marker convention — keeping them out of the standalone build, which has no team mode.

**Branch note:** the original PRD suggested landing on the existing `huddle-cleanup-followups` branch. That branch was already merged (PR #4) and stale (predated the PR #30 team-mode fix this bug builds on), so a fresh `fix/huddle-broadcast-per-recipient` branch was cut from current `main` instead — consistent with the PRD's primary instruction ("worktree branched from `main`").
