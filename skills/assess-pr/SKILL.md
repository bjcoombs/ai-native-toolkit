---
name: assess-pr
description: The /assess end-of-run offers - open a PR with the report, track the Top 3 Actions in the user's issue tracker, freeze the assessment into a CI gate, and file tool feedback. TRIGGER when the /assess orchestrator reaches the end-of-run offers; not a standalone user command.
---

# Assess PR and Issues

The end-of-run half of `/assess`. The report (`.assess/assess-report.md`), the complexity heatmap, and the doc graph are written. This step runs the three optional offers - in order - then closes the loop with a feedback prompt:

1. **Open a PR** with the report (Step 5).
2. **Track the Top 3 Actions** in the user's issue tracker (Step 6).
3. **Freeze the assessment into a CI gate** (Step 6.5).
4. **Tool feedback** (Step 7) - surface anomalies and offer to file feedback.

Each offer is independent: a user can accept any subset. The step references back into the written `assess-report.md` artifact (notably the Top 3 Actions table's `Issue` column), not into the scoring prose, so it stays decoupled from how the report was produced.

---

## Step 5: Ask Whether to Open a PR

After writing the files, first **check whether a direct PR is even possible** before offering one. A user on `READ` or `TRIAGE` access can't push a branch to the target repo, so an unconditional "open a PR?" offer is infeasible and wastes a turn.

```bash
# Detect push capability. `gh` returns viewer fields for the current user.
# Push capability is derived from `viewerPermission` (one of ADMIN |
# MAINTAIN | WRITE | TRIAGE | READ | NONE) - GitHub's GraphQL Repository
# type has no `viewerCanPush` field, so don't request it (the CLI errors
# and the whole call returns empty, silently degrading every write-
# accessible repo to the "leave local" branch).
PUSH_INFO=$(gh repo view --json viewerPermission,viewerCanAdminister,nameWithOwner 2>/dev/null || true)
PERM=$(echo "$PUSH_INFO" | jq -r '.viewerPermission // empty')
case "$PERM" in
  ADMIN|MAINTAIN|WRITE) CAN_PUSH=1 ;;
  *)                    CAN_PUSH=0 ;;
esac
# If the command failed (no remote, no gh, not a GitHub repo, unauthenticated),
# $PUSH_INFO is empty and $PERM stays empty - fall back to the local-branch
# flow with the reason. Never silently assume push works.
```

Interpret the result:

- `CAN_PUSH=1` (viewerPermission is `WRITE` / `MAINTAIN` / `ADMIN`, or the remote is a push-eligible fork): offer the direct PR flow below.
- `CAN_PUSH=0` and viewerPermission is `READ` / `TRIAGE`: name the constraint, then offer the fork-based PR flow ("fork `<owner>/<repo>` and open the PR from your fork?") as an alternative to "leave local". Do not offer the direct flow.
- `gh` unavailable / not a GitHub remote / not authenticated (`$PUSH_INFO` empty): skip both PR offers entirely and surface only the "leave local" outcome, naming the reason ("no GitHub remote detected" / "`gh` not authenticated").

Then surface the question - verbatim, picking the shape that matches the access tier.

Push-capable target (`CAN_PUSH=1`):

> Wrote `.assess/assess-report.md`, `.assess/complexity-heatmap.svg`, and `.assess/doc-graph.svg` in `<repo-name>`. Want me to open a PR in this repo with these files, or leave them local for you to review first?

Read-only target (viewerPermission `READ` / `TRIAGE`):

> Wrote `.assess/assess-report.md`, `.assess/complexity-heatmap.svg`, and `.assess/doc-graph.svg` in `<repo-name>`. You have `READ` access to `<owner/repo>`, so a direct PR isn't possible. I can fork `<owner/repo>` to your account and open the PR from there, or leave the files local for you to review.

If the user says **yes / PR** (direct flow, `CAN_PUSH=1`):
1. Create a branch in the target repo: `assess/snapshot-<YYYY-MM-DD>` (use the existing worktree workflow if `<repo>-main` + `worktree/` layout is present; otherwise branch in place).
2. Stage and commit the report, the complexity heatmap, and the doc graph. Commit message: `docs: Add AI-readiness assessment + complexity and doc-navigability snapshots`.
3. Push the branch and open a PR. Title: `docs: Codebase assessment - <YYYY-MM-DD>`.

If the user says **yes / PR** (fork flow, `CAN_PUSH=0` on the upstream):
1. `gh repo fork <owner>/<repo> --clone=false --remote=true` (creates the fork under the user's account and adds it as a remote named `origin` or similar; the upstream becomes `upstream` if the original was already `origin`).
2. Create the branch as above, push to the **fork** (`git push -u <fork-remote> <branch>`), and open the PR via `gh pr create --repo <owner>/<repo>` (head defaults to the fork).
3. Commit message, PR title, and body are unchanged from the direct flow.
4. **PR body must include the plugin reference at the bottom** so reviewers can install the tool that generated the report. Use this body template:

   ```markdown
   ## Summary

   Snapshot of this codebase's AI-agent readiness, complexity hotspots, and doc navigability as of <YYYY-MM-DD>.

   - **AI Readiness:** <X / 8> — <maturity-label>
   - **Hotspot leader:** `<top hotspot path>` (<loc> LOC, ccn <N>, <M> commits in window)
   - **Top lying map:** `<top stale-hub doc>` (<N>d stale, subject churn <M>)

   ## Top 3 Actions

   <paste the Top 3 Actions table from .assess/assess-report.md verbatim>

   Full report: [`.assess/assess-report.md`](./.assess/assess-report.md) (the heatmap and doc graph render inline).

   ---

   <!-- chat-replace:pr-footer -->
   _Generated by [`/ai-native-toolkit:assess`](https://github.com/bjcoombs/ai-native-toolkit) — a Claude Code plugin for codebase readiness assessment with complexity hotspot heatmaps. Install in any Claude Code session: `/plugin marketplace add https://github.com/bjcoombs/ai-native-toolkit` then `/plugin install ai-native-toolkit@ai-native-toolkit`._
   ```

If the user says **no / leave it**: stop. Files stay in `.assess/` for them to review - the plugin footer in the MD already advertises the tool when anyone opens the file.

**Gitignore hint:** suggest the user add `.assess/complexity-stats.prior.json` to their `.gitignore`. It's a transient rotation file that the next run overwrites; keeping it tracked creates noisy diffs. The current stats (`complexity-stats.json`) should still be committed - it's the baseline for the next run's diff.

## Step 6: Offer to Track the Top 3 Actions in the User's Issue Tracker

After Step 5 (whether a PR was opened or not), surface a separate question:

> Want me to create tracking items for the Top 3 Actions in your issue tracker? Each becomes a closeable, assignable work item rather than a bullet buried in a PR description.

If the user says **no**: stop. The Top 3 Actions table in the PR/report still lists everything inline.

If the user says **yes**, proceed agnostically - **don't assume GitHub** (or any specific tracker). Use your judgment based on what's actually in front of you.

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

Treat a non-empty `GH_TRACKER` with `hasIssuesEnabled: true` as a **detected tracker** (subject to the same write-access check as Step 5 - read-only repos still get tracking items via the user's fork or their personal tracker, not the upstream issues list). Same logic for `glab repo view --output json` on GitLab remotes.

When the deterministic signal is clear and unambiguous, use it without asking. Only fall back to judgment / multiple-signal disambiguation when the remote check is empty, ambiguous, or contradicted by something the user has told you.

Other signals - examples, not an exhaustive list, used only as fallback or to choose between equally-plausible options:

- **The user's global instructions** (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`) often state the tracker explicitly: "issues live in Linear project FOO", "we use Task Master", "Jira project ABC", etc.
- **Project-level instructions** in the target repo's `CLAUDE.md` / `AGENTS.md` / contribution docs.
- **Project files**: `.taskmaster/` directory, `.acli/` config, `.linear/` dotfiles, a Notion link in the README, etc.
- **Authenticated CLIs**: `gh auth status`, `glab auth status`, `acli` configuration, `linear` CLI tokens, anything similar.
- **Conversation history**: if the user just used a tracker, prefer that one.

The user might track work in Omnifocus, Apple Notes, a Google Doc, a Notion database, a Slack channel, or anything else. **Use judgment** on these soft signals; do not let them override a clear deterministic git-remote hit.

**Decision rules:**

1. **Deterministic GitHub/GitLab remote with issues enabled** - use that tracker without asking (subject to write-access check). This is the common case for OSS work and most personal repos.
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

For each Top 3 Action, the item should contain:

- **Title**: the action title verbatim from the Top 3 table
- **Body**: the action detail (Command / First Step, Hotspot files, the "Why these three" reasoning), a small metadata block (Layer, Effort, link to the assessment PR if one was opened), and a one-line footer linking back to the plugin
- **Tag / Label / Category** that supports idempotency (see 6c)

Example minimal body shape (adapt to the tracker's conventions):

```markdown
<action one-line>

<Command / First Step>

<Hotspot files this addresses>

<Why-this-action reasoning>

### From /assess
- Layer: <N>: <name>
- Effort: <small | medium | large>
- Assessment PR: <PR link or omit if no PR>

---
Generated by /assess - https://github.com/bjcoombs/ai-native-toolkit
```

### 6c: Idempotency

Re-running `/assess` on the same repo must not create duplicate tracking items. The dedup mechanism depends on the tracker:

- **Tag/label-based** (GitHub, GitLab, Linear, Jira labels): apply a stable label like `assess-finding` and search by `label + title` before creating. Open OR closed match → reuse.
- **Search-based** (Jira via JQL, Notion, Linear queries): search the tracker for items with the action title. Match → reuse.
- **Hierarchical** (Task Master): list the current tag's tasks; compare titles. Match → reuse.
- **Free-form** (Apple Notes, Google Docs, plain markdown files): no reliable structured dedup. In this case, list existing items the user can see, **show them, and ask before re-creating**: "I see 3 existing items that look like these. Re-create, skip, or update? "

In all cases, before creating: search first. If a match is found (open or closed in trackers that have state), reuse it. If a match was previously closed (the gap was once resolved but has re-emerged), flag this to the user in the chat output - don't silently re-open or duplicate.

**Task rotation across runs:** when an action drops out of the Top 3 between runs (e.g., a hotspot graduated, or a higher-priority issue emerged), don't auto-close the existing tracker task. Leave it pending. Mention the demotion in the new report's "Additional Opportunities" section so the user can decide if it's still worth doing. The user owns the close decision.

### 6d: Link the items back to the assessment

How the link is recorded depends on the tracker, but the goal is the same: someone reading the assessment PR / report can click through to the items, and someone reading an item can click back to the assessment.

- **GitHub PR + GitHub Issues** (the original flow): edit the PR body so the `Issue` column in the Top 3 Actions table replaces `—` with `#N` references. Update `.assess/assess-report.md` locally so the on-disk report stays in sync (commit the change if you're working in a worktree before pushing).
- **GitHub PR + Task Master tasks**: include the task IDs in the `Issue` column (e.g. `TM #1.2`). Reference the assessment PR URL in each task's body.
- **GitHub PR + Jira**: include the Jira keys (e.g. `PROJ-1234`) in the `Issue` column. Set the assessment PR URL as a Jira link.
- **No PR was opened**: update only `.assess/assess-report.md` locally with the item references.
- **Other trackers**: do whatever makes the cross-link work; if the tracker doesn't support links back, include the assessment date + PR URL (if any) in each item's body so a human can trace it.

### 6e: Report back to the user

End with a short, tracker-specific summary. Examples:

> Created 3 GitHub issues: #42 (Action 1), #43 (Action 2), #44 (Action 3). Linked from the assessment PR. All labelled `assess-finding` so re-running `/assess` later won't duplicate them.

> Created 3 Task Master tasks under tag `assess-2026-05-22`: #1.1, #1.2, #1.3. Run `task-master next` to start.

> Action 1 already tracked in PROJ-1024 (in progress) - linked. Created PROJ-1198 (Action 2) and PROJ-1199 (Action 3) in Jira.

## Step 6.5: Offer to Freeze the Assessment into a CI Check

After the PR and issue offers, surface a third - the one that converts `/assess` from a norm into a contract:

> **Freeze this into a repeatable check?** I can emit a GitHub Action that:
> - Runs the deterministic toolchain on every PR (the exact tools this run found),
> - Renders the metrics report via `assess_report.py`,
> - Gates on AI-readiness floors (a flagged finding present, a containment drop, p95 complexity past a threshold) and, if you opt in, on cross-run regressions against the prior committed snapshot.
>
> This turns `/assess` from a thing-you-run into a thing-that-runs. No AI in the loop - it is the frozen, contract version of the assessment you just ran by hand.

This offer only makes sense when the repo can actually run the workflow. Apply the **same write-access check as Step 5** (`CAN_PUSH`): on a read-only target, the file can still be written locally for the user to commit via their fork, but don't promise to open the gating PR. If `gh` / the remote is unavailable, write the file locally and say so.

If the user says **no**: stop. The deterministic report and findings already shipped in `.assess/`.

If the user says **yes**, emit the workflow. The generator bakes in the plugin version this run used (read from `run-context.json`) and the discovered toolchain, so the frozen core matches the snapshot you just produced:

````bash
<!-- chat-skip:start -->
# Re-resolve the skill dir in case this runs in a fresh shell (the env var
# $CLAUDE_PLUGIN_ROOT survives; Step 2's shell var won't have).
SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/assess}"
SKILL_DIR="${SKILL_DIR:-$(dirname "$(realpath ~/.claude/skills/assess/SKILL.md)")}"
<!-- chat-skip:end -->
<!-- chat-replace:uv-emit-workflow -->
uv run "$SKILL_DIR/scripts/assess_emit_workflow.py" "$REPO_ROOT"
````

This writes `.github/workflows/assess-gate.yml` (relative to the repo root). The script auto-detects the default branch and the discovered tools; override with `--branch <name>` or `--tools lizard,scc,...` when the run found a different toolchain (e.g. a per-language dead-code tool).

**Heads-up on the toolkit version pin.** The emitted workflow clones `ai-native-toolkit` at the tag `v<version>` matching this run. That tag must exist upstream before the first gated PR runs - it is published by the marathon's release step, so a workflow emitted in the same minute the version was bumped has nothing to fetch until the release lands. Until then the fetch step skips the assessment with a notice instead of failing the PR - infra failures (a missing tag, a rate limit, a network blip) never red the check. The workflow's supply chain is pinned end to end: actions ride commit SHAs and tools exact releases, so the snapshot stays reproducible run over run. A repo running a fork of the toolkit must edit the clone URL in the generated workflow.

**Tell the user about the gate config.** The emitted workflow runs `assess_gate.py`, which reads the optional `[gate]` section of `.assess/config.toml` (relative to the repo root) and is **warn-only by default** - it reports every finding but fails nothing until the repo opts in. The gate has two kinds of check: **floors** (absolute, on the current snapshot) and a **regression** check (cross-run, against the prior committed snapshot). Point them at the knobs:

```toml
[gate]
# FLOOR: findings whose presence (non-empty paths) fails the PR. Empty = warn-only.
fail_on = ["lying_map", "orphaned_understanding"]
# Findings reported but non-blocking. Omit to warn on every concern; [] silences all.
warn_on = ["hidden_coupling", "candidate_dead_weight"]
ccn_p95_max = 120        # FLOOR: fail when the p95 file complexity rises past this
containment_min = 0.3    # FLOOR: fail when the safe-zone share drops below this (0-1)
fail_on_regression = true  # REGRESSION: fail when hotspots worsened vs the prior snapshot
enabled = true           # set false to mute the gate without deleting the workflow
```

`fail_on` finding names are the cross-layer findings (`hidden_coupling`, `lying_map`, `unexplained_complexity`, `untrusted_hotspot`, `self_referential_tests`, `orphaned_understanding`, `candidate_dead_weight`). The floors evaluate the current snapshot, so they read as an AI-readiness *floor* (a clean repo never trips them; a long-standing concern trips them every run until fixed). `fail_on_regression` is the true cross-run check - it fires only when the diff against the prior committed snapshot reports a worsening, and is skipped entirely on a first run or an unreliable diff, so a freshly-cloned repo never fails its first gated PR. The asymmetric-caution default - never block a pipeline by surprise - means a repo that adopts the workflow without writing config gets reports, not red builds. The same asymmetry covers infrastructure: a failed toolkit fetch, tool install, or uv setup degrades to a skip notice rather than a red check, even on an opted-in repo - only the assessment itself can fail a PR.


## Step 7: Tool Feedback (Optional)

Close the loop: surface detected anomalies and offer the user a chance to file feedback against the toolkit.

```bash
jq '.anomalies' "$REPO_ROOT/.assess/run-context.json"
```

If the array is non-empty, list each anomaly to the user:

> Detected anomalies in this run:
> - `<code>`: <description>
>
> These may indicate a bug or miscalibration in `/assess`. Want to file feedback so the toolkit can improve?

Always also offer the open-ended option, even when no anomalies were detected:

> Anything else in this report look wrong or surprising? Filing feedback helps `/assess` improve for everyone.

If the user wants to file feedback, build a sanitized issue body from `run-context.json`:

- **Include**: plugin version, run date, files_scored, instructions_grade (top-level) + per-file subscores (numbers only - file basenames like `CLAUDE.md` are public), stats percentiles (p50/p95/max for LOC and CCN), diff summary counts, anomaly codes.
- **Never include**: file paths, code snippets, repo name, commit messages, hotspot path lists.

Prepend the body with: `_This feedback was generated by /assess. The data below is sanitized - no file paths or code content._`

Show the body to the user, then run (after explicit confirmation, per the never-auto-create-issues rule):

```bash
gh issue create \
  --repo bjcoombs/ai-native-toolkit \
  --label assess-feedback \
  --title "[assess-feedback] <user's summary>" \
  --body "$BODY"
```

The user adds their observation in their own words; the pre-fill is just the deterministic context. Positive framing applies here too: "the grader missed positive directives in section X" beats "the grader is broken."
