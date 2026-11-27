# Tracking the Top 3 Actions (Steps 6a to 6g)

Steps 6a to 6g of the assess-pr skill, run when the user selects the issue-tracking or upstream-issues offer.

## Contents

- 6a: Identify the user's issue tracker
- 6b: Create items in the chosen tracker
- 6c: Idempotency
- 6d: Link the items back to the assessment
- 6e: Reconcile filed items (propose only)
- 6f: Report back to the user
- 6g: The scheduled reconcile (GitHub, opt-in)

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
- **Status comment** (GitHub Issues): after creating an item that has a `condition`, post its status comment, `COMMENT=$(mktemp); uv run "$RENDER" "$REPO_ROOT" status-comment --today "$(date +%Y-%m-%d)" > "$COMMENT"` then `gh issue comment <number> --body-file "$COMMENT"`. It is the one comment the scheduled reconcile (6g) edits in place; post it once per item, never again on a reused one that already has it.
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

  For an action you split (One concern per item), ignore its item's `filing` step: look in the same list for an item whose marker carries the same `condition`, `sub` and `part` and whose title matches. When the action was filed whole before (an item marked for the same `condition` and `sub` with no `part`), reuse that item as part 1 and add ` part=1` before `model=` in its marker, so the split does not leave a duplicate. The reverse needs no handling: an action a previous run split that this run files whole gets one of its earlier parts from `plan` as `reuse_and_mark`, with `part` dropped from the marker, and 6e proposes closing the other parts as superseded. Two actions that name the same condition each take their own item: `plan` matches each existing item once per run, preferring the one whose title agrees. Each item comes back with a `filing` step: `create` (file it), `reuse` (an item with the same marker exists: `filing.number`) or `reuse_and_mark` (an unmarked item with the label and the same title exists: write `filing.marked_body` back with `gh issue edit <number> --body-file`, which adds the marker and changes nothing else). Where no label can be applied (filing upstream from a read-only account), list the user's own issues instead, `gh issue list --repo "$GH_SLUG" --author @me --state all --limit 500 --json number,title,body,state,labels > "$EXISTING"`, and pass `--label ""`. On GitLab, `glab issue list --label assess-finding --all --output json` gives `iid` and `description`; map them to `number` and `body` before planning.
  **Open-item limit (#812).** Each planned item also carries a `wip` step. The backlog keeps at most `[backlog] wip` items open at once (`.assess/config.toml`, default 3): open items with a condition marker or the `assess-finding` label count, and so does an open item a step reuses. `wip.decision` reads `open` (the item is already open: reuse it, it takes no slot), `file` (create it; for a closed `filing.number`, propose reopening that item as the closed-match rule below says: flag it to the user and reopen only on their yes) or `next_up` (the limit is reached: file nothing and propose no reopen). The items that need a slot are taken in the next-unlock order, `wip.tier`: `next_unlock` (its condition blocks the next Step) first, then `recovery` (it matches a closed item whose condition reads unmet again), then `gain`; the Top 3 order stands within a tier. Split parts (above) get no `wip` step: count each part you would create or reopen against the limit yourself, filing parts only while `[backlog] wip` minus the open count leaves a slot, and pass `--wip` reduced by the parts filed to `plan` so the unsplit items share what is left; list the parts held back under Next up. Print the items held back with `uv run "$RENDER" "$REPO_ROOT" next-up --existing "$EXISTING"` (the same `--label` and `--wip` as `plan`) and paste its output under the Top 3 table in the report and the PR body (6d); it prints nothing when every item fits.
- **Other tag/label-based** (Linear, Jira labels): apply a stable label like `assess-finding`, search for the marker's `condition=` text first where the tracker searches bodies, then by `label + title`. Open OR closed match → reuse, and add the marker to an unmarked match.
- **Search-based** (Jira via JQL, Notion, Linear queries): search the tracker for items with the action title. Match → reuse.
- **Hierarchical** (Task Master): list the current tag's tasks; compare titles. Match → reuse.
- **Free-form** (Apple Notes, Google Docs, plain markdown files): no reliable structured dedup. In this case, list existing items the user can see, **show them, and ask before re-creating**: "I see 3 existing items that look like these. Re-create, skip, or update? "

A sub-item's marker keys on its parent's condition and its letter (`sub=a`), never its rank. The letter, like a split item's `part`, is the model's ordering in that run, so a marker with `sub` or `part` matches only when the title matches too; a sub-item whose letter moved is found by title among the items marked for the same condition, and `plan` rewrites its marker. Without a marker, a sub-item's title starts with its rank and label (`2a. `), which move when its parent changes rank between runs: match a sub-item on the title text after that prefix, so `1a. Pin ruff` reuses the item filed as `2a. Pin ruff`. The match crosses parents on purpose: the same change under another action is the same work. When a sub-item is promoted to its own ranked action, match it on the action text without the label, so `Pin ruff` reuses the item filed as `2a. Pin ruff`.

In all cases, before creating: search first. If a match is found (open or closed in trackers that have state), reuse it. If a match was previously closed (the gap was once resolved but has re-emerged), flag this to the user in the chat output - don't silently re-open or duplicate. A closed match whose `wip.decision` is `file` is the one to propose reopening; one held as `next_up` is listed under Next up and not proposed.

**Task rotation across runs:** when an action drops out of the Top 3 between runs (e.g., a hotspot graduated, or a higher-priority issue emerged), don't auto-close the existing tracker task. Leave it pending. Mention the demotion in the new report's "Additional Opportunities" section so the user can decide if it's still worth doing. The user owns the close decision: 6e proposes a close only when the item's condition now holds, and writes it only once the user confirms.

### 6d: Link the items back to the assessment

How the link is recorded depends on the tracker, but the goal is the same: someone reading the assessment PR / report can click through to the items, and someone reading an item can click back to the assessment.

- **GitHub PR + GitHub Issues** (the original flow): re-render the table with one `--issue` per filed rank, `uv run "$RENDER" "$REPO_ROOT" table --issue 1='#42' --issue 2='#43, #44'` (a rank split into several items lists every reference; a rank held back by the open-item limit reads `--issue 3='next up'`), and replace the table in the PR body with the output, the `next-up` block (6c) under it. Update `.assess/assess-report.md` locally so the on-disk report stays in sync (commit the change if you're working in a worktree before pushing).
- **GitHub PR + Task Master tasks**: include the task IDs in the `Issue` column through `--issue` (e.g. `--issue 1='TM #1.2'`). Reference the assessment PR URL in each task's body.
- **GitHub PR + Jira**: include the Jira keys in the `Issue` column through `--issue` (e.g. `--issue 1=PROJ-1234`). Set the assessment PR URL as a Jira link.
- **No PR was opened**: update only `.assess/assess-report.md` locally with the item references. With the upstream-issues flow, upstream references read `<owner>/<repo>#N`; push the updated report to the fork branch so `$REPORT_URL` shows them.
- **Other trackers**: do whatever makes the cross-link work; if the tracker doesn't support links back, include the assessment date + PR URL (if any) in each item's body so a human can trace it.

### 6e: Reconcile filed items (propose only)

On an interactive run with a GitHub or GitLab tracker, after 6b to 6d, read the tracker again and reconcile every filed item against this run. Nothing is written until the user confirms, and only the confirmed proposals are written. Skip this step on a headless run (the non-interactive contract above); the command also proposes nothing when `run-context.json .interactive` is `false`.

```bash
gh issue list --label assess-finding --state all --limit 500 --json number,title,body,state,labels > "$EXISTING"
uv run "$RENDER" "$REPO_ROOT" reconcile --existing "$EXISTING" --filed "<numbers 6b filed or reused, comma-separated>"
```

When 6b filed the items without a label (filing upstream from a read-only account), list the existing items with the 6c command instead, `gh issue list --repo "$GH_SLUG" --author @me --state all --limit 500 --json number,title,body,state,labels > "$EXISTING"`, and pass `--label ""` to `reconcile`. Use the same `--label ""` and GitLab mapping as 6c. The output holds `proposals`, `rules_changed`, `notes` and `summary`. Each item gets at most one proposal:

- **Close**: an open item's condition now holds. The proposal cites the evidence line and the commit this run measured.
- **Reopen**: a closed item's condition lapsed. The proposal names the cause. When an action was split and also filed whole, only the whole item is reopened, never its closed parts.
- **Demote to informational**: the item can no longer move, because its finding keys on commit origin (`self_referential_tests`) or its condition reads not applicable. An item whose condition this run's model does not list (a `--profile` or `--pack` difference) is left as it is, with a note. The write adds the `assess-informational` label and keeps the item's state; a later run passes the item over. With `--label ""` the label cannot be written, so no demote is proposed and a note counts the skipped ones.
- **Close as superseded**: a part of an action that is no longer split, beside the item that now carries the action.
- **Rules changed**: a move that only the new maturity model caused (the model changed, the repository did not) reads `rules changed: your repo did not` under "No change proposed". It is never a proposal and never progress or regression.
- **Declined before**: a proposal the user declined on an earlier run is not proposed again while its condition reads the same state under the same maturity model. It is listed under `declined` and at the foot of `summary`, naming who declined it, when and why. Once the state or the model changes, the proposal comes back once. To be asked again sooner, delete its entry from `.assess/backlog-declines.json`.

Show `summary` verbatim as one batched message, then ask with multi-select AskUserQuestion questions of three proposals each, one option per proposal (named by its item number and kind) and "none" as the fourth, all in one call (up to four questions, so twelve proposals; past twelve, a second call for the rest). Print the writes for the confirmed numbers only (`none` when the user picked "none" everywhere), record the rest as declined, and run each write. Every comment holds backticks, so it goes through a file, never a quoted argument:

```bash
WRITES=$(mktemp); COMMENT=$(mktemp)   # outside the repository
WHO=$(git -C "$REPO_ROOT" config user.name 2>/dev/null || echo "${USER:-unknown}")
uv run "$RENDER" "$REPO_ROOT" reconcile --existing "$EXISTING" --filed "<same numbers>" \
  --confirm "<confirmed numbers, all or none>" --record-declines --declined-by "$WHO" \
  --declined-at "$(date +%Y-%m-%d)" > "$WRITES"
while read -r w; do
  n=$(jq -r '.number' <<<"$w"); jq -r '.comment' <<<"$w" > "$COMMENT"
  case "$(jq -r '.op' <<<"$w")" in
    close)  gh issue close "$n" </dev/null ;;
    reopen) gh issue reopen "$n" </dev/null ;;
    label)  l=$(jq -r '.add_label' <<<"$w"); gh label create "$l" --force </dev/null
            gh issue edit "$n" --add-label "$l" </dev/null ;;
  esac
  gh issue comment "$n" --body-file "$COMMENT" </dev/null
done < <(jq -c '.[]' "$WRITES")
```

On GitLab use `glab issue close`, `glab issue reopen` and `glab issue update --label`. Each proposal the user answered and left unconfirmed is recorded in `.assess/backlog-declines.json` with who declined it, when, the model and the evidence line, and the next run holds it back until the condition's state or the model changes. Add one `--decline-reason "<number>=<text>"` for each declined item the user gave a reason for, and none for the others. When the user dismissed the questions without answering, skip the `--confirm` call: nothing is written to the tracker or newly recorded, and the next run proposes the same changes again. Other trackers: show the same summary and make the confirmed changes with their own tooling.

### 6f: Report back to the user

End with a short, tracker-specific summary. Examples:

> Created 3 GitHub issues: #42 (Action 1), #43 (Action 2), #44 (Action 3). Linked from the assessment PR. All labelled `assess-finding` so re-running `/assess` later won't duplicate them.

> Filed 4 issues on `<owner>/<repo>`: #979 (Action 1), #980 (Action 2), #981 and #982 (Action 3, split into two independent changes). Each links the full report in your fork. Unlabelled (no triage access), so a re-run matches them by title.

> Created 3 Task Master tasks under tag `assess-2026-05-22`: #1.1, #1.2, #1.3. Run `task-master next` to start.

> Action 1 already tracked in PROJ-1024 (in progress) - linked. Created PROJ-1198 (Action 2) and PROJ-1199 (Action 3) in Jira.

### 6g: The scheduled reconcile (GitHub, opt-in)

The reconcile in 6e runs only when someone runs `/assess`. A repository can run it daily with no model through `.github/workflows/reusable-backlog-reconcile.yml` in this toolkit; `.github/workflows/backlog-reconcile.yml` is the reference caller. Copy that caller, point its `uses:` at `bjcoombs/ai-native-toolkit/.github/workflows/reusable-backlog-reconcile.yml@<release tag>`, grant `contents: read` and `issues: write` and nothing else, and keep its workflow-level `concurrency` group (one run at a time per repository, `cancel-in-progress: false`), so two runs never file the same gate. Set the repository variable `BACKLOG_RECONCILE_ENABLED` to `false` to stop it.

It reads the filed items the way 6e does and writes only what `[backlog] mode` in the committed `.assess/config.toml` allows:

| `mode` | What a scheduled run writes |
|---|---|
| `propose` (default) | The `**Status:**` line of each marked item's existing status comment (6b), and nothing else: the status, the evidence and the last run. A close, reopen, demote or supersede it would propose is written into that line, for the next interactive `/assess` to confirm. |
| `file` | As `propose`, plus a status comment on a marked item that has none, plus one new item for each gate of the next Step that no item is marked for, while `[backlog] wip` leaves a slot. A new item carries the workflow's `label` input (default `assess-finding`), so the next run's listing finds it. |
| `file-and-close` | As `file`, plus closing an open item whose condition now holds, with the evidence in its status line. |

`file-and-close` closes items with no one confirming each close, so it is off unless the config names it:

```toml
[backlog]
mode = "file-and-close"   # or "file"; leave it out for propose
```

A value other than these three fails the scheduled run, so a typo never runs in another mode. In every mode a reopen, demote or supersede stays a proposal, a proposal declined before (6e) is never written, an item without a condition marker is never touched, and no write edits an item's title, body, labels or assignees. A status comment counts only when someone with write access or the workflow's own bot wrote it. One run closes at most 10 items and files at most 10; the rest wait for the next run. A failed write ends the run with gh's error and the list of writes already made; re-running only the failed `apply` job writes the same plan again, so after a failure part way through new items, re-run all jobs. The rules: `skills/assess/scripts/lib/report/backlog_schedule.py`.
