# Tracking the Top 3 Actions (Steps 6a to 6e)

Steps 6a to 6e of `SKILL.md`, run when the user selects the issue-tracking or upstream-issues offer.

## Contents

- 6a: Identify the user's issue tracker
- 6b: Create items in the chosen tracker
- 6c: Idempotency
- 6d: Link the items back to the assessment
- 6e: Report back to the user

### 6a: Identify the user's issue tracker

**Start with the deterministic git-remote signal before anything else.** A GitHub / GitLab remote with issues enabled is the cheapest, most reliable tracker signal in front of you - skipping it forces the model into judgment-mode on a question that has a clear answer.

```bash
# Cheapest tracker signal: a git remote that hosts issues.
GIT_REMOTE=$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)
if [ -n "$GIT_REMOTE" ]; then
  # `gh` works against any git remote pointing at github.com (including forks);
  # hasIssuesEnabled distinguishes a code-only mirror from a real tracker.
  GH_TRACKER=$(gh repo view --json hasIssuesEnabled,nameWithOwner 2>/dev/null || true)
fi
```

Treat a non-empty `GH_TRACKER` with `hasIssuesEnabled: true` as a **detected tracker**, subject to the same write-access and no-contributions checks as Step 5. On a read-only target (READ / TRIAGE) with `NO_CONTRIBUTIONS=0`, the recommended path is the upstream issues list itself: file there, each item linking the fork-hosted report (Step 5's upstream-issues flow). That is the channel a maintainer you do not work with actually acts on. When `NO_CONTRIBUTIONS=1`, or upstream has issues disabled, use the user's fork or their personal tracker instead, never the upstream issues list. Same logic for `glab repo view --output json` on GitLab remotes.

When the deterministic signal is clear and unambiguous, use it without asking. Only fall back to judgment / multiple-signal disambiguation when the remote check is empty, ambiguous, or contradicted by something the user has told you.

Other signals - examples, not an exhaustive list, used only as fallback or to choose between equally-plausible options:

- **The user's global instructions** (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`) often state the tracker explicitly: "issues live in Linear project FOO", "we use Task Master", "Jira project ABC", etc.
- **Project-level instructions** in the target repo's `CLAUDE.md` / `AGENTS.md` / contribution docs.
- **Project files**: `.taskmaster/` directory, `.acli/` config, `.linear/` dotfiles, a Notion link in the README, etc.
- **Authenticated CLIs**: `gh auth status`, `glab auth status`, `acli` configuration, `linear` CLI tokens, anything similar.
- **Conversation history**: if the user just used a tracker, prefer that one.

The user might track work in Omnifocus, Apple Notes, a Google Doc, a Notion database, a Slack channel, or anything else. **Use judgment** on these soft signals; do not let them override a clear deterministic git-remote hit.

**Decision rules:**

1. **Deterministic GitHub/GitLab remote with issues enabled** - use that tracker without asking (subject to the write-access and no-contributions checks above; a read-only target that takes contributions files upstream). This is the common case for OSS work and most personal repos.
2. **One clear soft signal** (e.g. only `.taskmaster/` present, or global CLAUDE.md says "use Linear") and no contradicting remote - use that tracker without asking.
3. **Conflicting signals** (e.g. GitHub remote + `.taskmaster/` directory + global says "use Linear") - **ask the user**. List what you saw and let them pick:
   > I see signals for Task Master (`.taskmaster/`), GitHub Issues (remote points at `<owner/repo>` with issues enabled), and Linear (your global CLAUDE.md mentions it). Which should I create the tracking items in?
4. **No clear signal** - ask:
   > I couldn't tell which tracker you use. Where should I create the tracking items? (e.g. GitHub Issues, Task Master, Jira, Linear, somewhere else - or skip)

When asking, use **AskUserQuestion** with the detected options plus a "something else" / "skip" escape hatch.

### 6b: Create items in the chosen tracker

Once the tracker is known, use its native tooling. The skill doesn't enumerate every CLI - rely on your general knowledge of the tool. A few examples:

| Tracker | Typical command |
|---|---|
| GitHub Issues | `gh issue create --label assess-finding --title "..." --body "..."` |
| GitLab Issues | `glab issue create --label assess-finding ...` |
| Jira (via acli) | `acli workitem create ...` (see project's Atlassian instructions) |
| Task Master | `task-master add-task --prompt "..."` (under the current tag, or ask) |
| Linear (via CLI) | `linear issue create ...` |
| Anything else | follow the user's documented convention |

**Build the items from `actions.json`, never from the report.** One command renders every item, one per `actions` entry in `rank` order, each followed by one item per entry in its `sub_items` (the report's lettered sub-actions, such as `2a`), as a JSON array of `{rank, title, body}`; a sub-item's object also carries its `label` (`2a`), and an item whose action names a `condition` carries its `marker`:

```bash
<!-- chat-replace:uv-actions-render -->
RENDER="${CLAUDE_SKILL_DIR}/../assess/scripts/assess_actions_render.py"   # set again here: Step 6 runs when the PR offer was declined
uv run "$RENDER" "$REPO_ROOT" items --report-link "<full-report link>"   # omit --report-link when there is none
```

- **Title**: the entry's `action`, as `title` gives it; a sub-item's title is its label and action (`2a. <action>`).
- **Body**: `body` as given: the finding and its files first (the `condition` when the entry has no `finding`), then the First step, the **Done when** criterion, the Scope fence, a Context block (Layer, Effort, the full-report link) and last a one-line footer linking back to the plugin. Write each body to a file and pass it with `--body-file` (or the tracker's equivalent); do not reword it.
- **Marker**: an item with a `condition` ends with its condition marker, `<!-- assess:condition=<row id>@<level>[ sub=<letters>] model=<version> -->`, already in `body`. Keep it on every create and edit: later runs match the item by it (6c). An item split from one action (One concern per item, below) adds ` part=<n>` before `model=`, numbering the parts 1, 2, ... in the order filed, so the parts keep separate keys. The format is documented in `skills/assess/references/actions-schema.md`.
- **Tag / Label / Category** that supports idempotency (see 6c)

**One concern per item.** Each item carries one change a maintainer can accept or decline on its own. `actions.json` holds one entry per rank, and each lettered sub-action is already its own item. If an entry's `action` still bundles independent changes (an instruction-file stub *and* a lockfile *and* a CI step), file each change as its own item with its own one-line title. Start each split item from the entry's body and narrow its First step, Done when and Scope fence to that one change, so each item closes on its own change alone; keep the finding line and the Context block as rendered. The parts a maintainer would accept separately must be tracked separately, or the declined part takes the accepted ones down with it. List every reference for that rank, its sub-items' included, in its Issue cell (6d).

**Done when** is the entry's `done_when`, written in the repository's own terms when the report was assembled (`assess-findings`): a command that passes, a file state or a CI result first, the `/assess` re-run criterion after it, marked optional.

**Full-report link.** Use the assessment PR when one was opened, `$REPORT_URL` from Step 5's upstream-issues flow when the report is hosted in the user's fork, and omit the line when neither exists.

A ccn figure in an entry's text follows the report's caveat (issue #590): an scc-only figure (`source` `scc`, `max_fn_ccn` null) is written `ccn ~N (approximate)`, a path in `stats_summary.complexity_unreliable` gets `ccn ~N (unreliable)` instead, and neither is offered as the reason the item is prioritised. The entry carries that wording from `finalize-input.json`; the renderer copies it as written.

The body the renderer writes:

```markdown
**Finding:** `<finding>` in `<file>`, `<file>`   (without a finding: **Condition:** `<condition>`, else **Files:** alone)

**Sub-action <label> of action <rank>:** <parent action>   (a sub-item only; <label> is the item's `label`, already rank plus letter, such as `2a`)

### First step
<first_step>

### Done when
- <done_when>

### Scope fence
<scope_fence>

### Context
- Layer: <layer> (`<condition>`)
- Effort: <effort>
- Full report: <the --report-link value; the line is left out without one>

---
Generated by /assess - https://github.com/bjcoombs/ai-native-toolkit

<!-- assess:condition=<row id>@<level> model=<version> -->   (only when the entry has a condition; a sub-item adds sub=<letters>)
```

**Filing upstream from a read-only account** (Step 5's upstream-issues flow): pass `--repo "$GH_SLUG"` on every create and search, since the fork step may have rearranged the remotes. Drop `--label`: an account without triage access cannot apply labels, and a label the repository lacks fails the create. Deduplicate by title instead (6c).

### 6c: Idempotency

Re-running `/assess` on the same repo must not create duplicate tracking items. An item that carries a condition marker is matched on its condition id, not its title, so a retitled item is still found; an item filed before the marker existed is matched by label plus title and gains the marker. The dedup mechanism depends on the tracker:

- **GitHub / GitLab Issues**: list the existing items, open and closed, and let the renderer plan each one:

  ```bash
  EXISTING=$(mktemp)   # outside the repository, so nothing is committed by mistake
  gh issue list --label assess-finding --state all --limit 500 --json number,title,body,state,labels > "$EXISTING"
  uv run "$RENDER" "$REPO_ROOT" plan --existing "$EXISTING" --report-link "<full-report link>"
  ```

  For a split item, look in the same list for an item whose marker carries the same `condition`, `sub` and `part` and whose title matches; only the unsplit items go through `plan`. Each item comes back with a `filing` step: `create` (file it), `reuse` (an item with the same marker exists: `filing.number`) or `reuse_and_mark` (an unmarked item with the label and the same title exists: write `filing.marked_body` back with `gh issue edit <number> --body-file`, which adds the marker and changes nothing else). Where no label can be applied (filing upstream from a read-only account), list the user's own issues instead, `gh issue list --repo "$GH_SLUG" --author @me --state all --limit 500 --json number,title,body,state,labels > "$EXISTING"`, and pass `--label ""`. On GitLab, `glab issue list --label assess-finding --all --output json` gives `iid` and `description`; map them to `number` and `body` before planning.
- **Other tag/label-based** (Linear, Jira labels): apply a stable label like `assess-finding`, search for the marker's `condition=` text first where the tracker searches bodies, then by `label + title`. Open OR closed match → reuse, and add the marker to an unmarked match.
- **Search-based** (Jira via JQL, Notion, Linear queries): search the tracker for items with the action title. Match → reuse.
- **Hierarchical** (Task Master): list the current tag's tasks; compare titles. Match → reuse.
- **Free-form** (Apple Notes, Google Docs, plain markdown files): no reliable structured dedup. In this case, list existing items the user can see, **show them, and ask before re-creating**: "I see 3 existing items that look like these. Re-create, skip, or update? "

A sub-item's marker keys on its parent's condition and its letter (`sub=a`), never its rank. The letter, like a split item's `part`, is the model's ordering in that run, so a marker with `sub` or `part` matches only when the title matches too; a sub-item whose letter moved is found by title among the items marked for the same condition, and `plan` rewrites its marker. Without a marker, a sub-item's title starts with its rank and label (`2a. `), which move when its parent changes rank between runs: match a sub-item on the title text after that prefix, so `1a. Pin ruff` reuses the item filed as `2a. Pin ruff`. The match crosses parents on purpose: the same change under another action is the same work. When a sub-item is promoted to its own ranked action, match it on the action text without the label, so `Pin ruff` reuses the item filed as `2a. Pin ruff`.

In all cases, before creating: search first. If a match is found (open or closed in trackers that have state), reuse it. If a match was previously closed (the gap was once resolved but has re-emerged), flag this to the user in the chat output - don't silently re-open or duplicate.

**Task rotation across runs:** when an action drops out of the Top 3 between runs (e.g., a hotspot graduated, or a higher-priority issue emerged), don't auto-close the existing tracker task. Leave it pending. Mention the demotion in the new report's "Additional Opportunities" section so the user can decide if it's still worth doing. The user owns the close decision.

### 6d: Link the items back to the assessment

How the link is recorded depends on the tracker, but the goal is the same: someone reading the assessment PR / report can click through to the items, and someone reading an item can click back to the assessment.

- **GitHub PR + GitHub Issues** (the original flow): re-render the table with one `--issue` per filed rank, `uv run "$RENDER" "$REPO_ROOT" table --issue 1='#42' --issue 2='#43, #44'` (a rank split into several items lists every reference), and replace the table in the PR body with the output. Update `.assess/assess-report.md` locally so the on-disk report stays in sync (commit the change if you're working in a worktree before pushing).
- **GitHub PR + Task Master tasks**: include the task IDs in the `Issue` column through `--issue` (e.g. `--issue 1='TM #1.2'`). Reference the assessment PR URL in each task's body.
- **GitHub PR + Jira**: include the Jira keys in the `Issue` column through `--issue` (e.g. `--issue 1=PROJ-1234`). Set the assessment PR URL as a Jira link.
- **No PR was opened**: update only `.assess/assess-report.md` locally with the item references. With the upstream-issues flow, upstream references read `<owner>/<repo>#N`; push the updated report to the fork branch so `$REPORT_URL` shows them.
- **Other trackers**: do whatever makes the cross-link work; if the tracker doesn't support links back, include the assessment date + PR URL (if any) in each item's body so a human can trace it.

### 6e: Report back to the user

End with a short, tracker-specific summary. Examples:

> Created 3 GitHub issues: #42 (Action 1), #43 (Action 2), #44 (Action 3). Linked from the assessment PR. All labelled `assess-finding` so re-running `/assess` later won't duplicate them.

> Filed 4 issues on `<owner>/<repo>`: #979 (Action 1), #980 (Action 2), #981 and #982 (Action 3, split into two independent changes). Each links the full report in your fork. Unlabelled (no triage access), so a re-run matches them by title.

> Created 3 Task Master tasks under tag `assess-2026-05-22`: #1.1, #1.2, #1.3. Run `task-master next` to start.

> Action 1 already tracked in PROJ-1024 (in progress) - linked. Created PROJ-1198 (Action 2) and PROJ-1199 (Action 3) in Jira.
