---
name: tm-marathon-config-example
disable-model-invocation: true
description: "Example Marathon Configuration for CLAUDE.md - copy the section below into your project's CLAUDE.md"
---

# Marathon Configuration Example

Copy the `## Marathon Configuration` section below into your project's CLAUDE.md file.
The `/tm` and `/issues` commands read this section to configure marathon mode for your specific codebase.

**If this section is missing**, `/tm` uses these defaults:
- Base branch: `main`
- Required approvals: 1
- No bot reviewer rules
- No retro log

---

## Marathon Configuration

Project-specific settings for `/tm` marathon mode.

### Branch and Merge

- **Base branch**: `main`
- **PR target branch**: `main`
- **Required approvals**: 1 (minimum for auto-merge)
- **Markdown-only PR approvals**: 1

### Bot Reviewers

<!-- Remove any bots you don't use. Add entries for any custom bots. -->

Three optional per-bot fields drive `pr-review-merge` Ready Criterion 6 (bot re-review of the head SHA):
- `Re-reviews on push: yes|no` - opt-in, `no` by default. `yes` means the PR is not ready until this bot has completed its pass on the current head SHA. Bots without `yes` are never waited on. Turn it on only for a bot that reliably re-reviews every push and whose findings you want before merge (for example an AI reviewer on AI-authored changes); leave it `no` for a bot that is rate-limited or often skips re-reviews, or every PR stalls for the full max wait.
- `Max wait for re-review: <duration>` - upper bound (e.g. `10m`) measured from when the head commit was pushed (its earliest check-suite creation). When `Re-reviews on push: yes` and this field is absent, the bound is 15m. When it expires the criterion passes with a warning naming the bot in the merge record.
- `Re-review check name: <check-run name>` - set when the bot's pass shows up as a check run (for example a GitHub Actions reviewer job) rather than a submitted review. The criterion then reads only the check run of that name on the head SHA: in progress means keep waiting until the max wait expires; completed with conclusion `success` satisfies the criterion; completed with conclusion `skipped` means the bot does not apply to this PR, which satisfies the criterion with no warning; completed with any other conclusion (failure, cancelled, neutral, timed_out) is a settled verdict that the bot did not complete a green pass, so the criterion passes at once with a warning in the merge record. Nothing holds forever on an advisory bot.

**CodeRabbit** (`coderabbitai[bot]`):
- Re-reviews on push: no
- Max wait for re-review: 10m (inert while `Re-reviews on push` is `no`; the bound that applies if flipped to `yes`)
- Fix code and push. CodeRabbit re-reviews automatically and resolves its own threads.
- **NEVER reply in CodeRabbit threads** - CodeRabbit ignores replies from other bots.
- If `request_changes_workflow` is enabled: CodeRabbit submits CHANGES_REQUESTED reviews that GitHub does not auto-dismiss on re-review. Every PR needs stale bot CR dismissal before merging.

**claude[bot]** (`claude[bot]`):
- Re-reviews on push: no
- Max wait for re-review: 20m (inert while `Re-reviews on push` is `no`; the bound that applies if flipped to `yes`)
- Re-review check name: claude-review (example for an Actions-hosted reviewer; set to the reviewer job's check-run name, used once `Re-reviews on push` is `yes`)
- Resolve threads via GraphQL after addressing the feedback.

**Human reviewers**:
- Fix code, reply inline, @mention reviewer. Do NOT resolve human threads - let the reviewer confirm.

### CI Patterns

<!-- Document your CI quirks so teammates don't waste time investigating known issues. -->

- **Known flaky tests**: (list any tests that fail intermittently on CI but are not real failures)
- **Non-blocking checks**: (list checks that are informational only, not merge gates - e.g., codecov/patch, Trivy scans)
- **Pre-existing failures**: (list any tests that are currently broken on the base branch)
- **Slow checks**: (list checks that routinely take 10+ min so teammates know to expect delays)

### GitHub Issues (for `/issues`)

<!-- Defaults shown. Adjust label names to match your repo's conventions. -->

- **Agent-ready label**: `agent-ready` (opt-in label that makes an issue marathon-eligible)
- **Needs-triage label**: `needs-triage` (applied with a clarifying-question comment)
- **In-progress label**: `in-progress` (applied when a teammate starts an issue)
- **Issue exclude labels**: (none — e.g. `discussion`, `wontfix`, `question` to skip during triage)

### Retrospective

<!-- Optional. If you want marathon retros to accumulate across sessions, specify a path. -->

- **Retro log**: `~/.claude/projects/<project-slug>/memory/marathon-retros.md`
- Append each marathon's retrospective to this log after completion
