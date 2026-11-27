---
name: assess-pr
description: The /assess end-of-run offers - open a PR with the report, track the Top 3 Actions in the user's issue tracker, freeze the assessment into a CI gate, and file tool feedback. TRIGGER when the /assess orchestrator reaches the end-of-run offers; not a standalone user command.
---

# Assess PR and Issues

The end-of-run half of `/assess`. The report (`.assess/assess-report.md`), the complexity heatmap, and the doc graph are written. This step runs the optional end-of-run offers - in order - then closes the loop:

**Windows:** run the bash blocks below in Git Bash, which ships with Git for Windows. Installs, PATH notes and the make-free commands are in the [Windows setup guide](../../docs/windows.md).

<!-- chat-skip:start -->
**Script paths in the references.** This skill's directory is `${CLAUDE_SKILL_DIR}`. Where a command in `references/` names `${CLAUDE_SKILL_DIR}`, put this directory in its place, so `${CLAUDE_SKILL_DIR}/../assess/scripts/` reaches the assess scripts.
<!-- chat-skip:end -->

1. **Open a PR** with the report (Step 5).
2. **Track the Top 3 Actions** in the user's issue tracker (Step 6).
3. **Freeze the assessment into a CI gate** (Step 6.5).
4. **Tool feedback** (Step 7) - surface anomalies and offer to file feedback.
5. **Uninstall `/assess`** (Step 8) - the escape hatch: remove everything this run wrote.

Each offer is independent (uninstall excepted - it's mutually exclusive with the write-back offers): a user can accept any subset. The step references back into the written `assess-report.md` artifact (notably the Top 3 Actions table's `Issue` column), not into the scoring prose, so it stays decoupled from how the report was produced.

## Phase 2: ask once, batched (and the non-interactive contract)

These are the **write-back phase** of the consent lifecycle (Phase 2; Phase 1 was the tool installs, Phase 3 the mutation pass - see the assess SKILL.md). Do not serialise them into back-to-back modals. Present them as **one batched, multi-select AskUserQuestion**: "Now that the report is written, which of these should I do?" with the options (open a PR, track the Top 3 Actions, freeze a CI gate, file feedback, and - the mutually-exclusive escape hatch - uninstall `/assess` from this repo), pre-filtered by feasibility:

- Drop the **PR** option when the push-capability / remote check below (Step 5) shows no direct or fork PR is possible. On a read-only target (READ / TRIAGE) that takes contributions and has issues enabled, lead with the **upstream-issues** variant instead (it replaces the generic issue-tracking option below, so the batch lists it once and records it under `issue_tracking`): file the Top 3 Actions as issues upstream, each linking the full report hosted in the user's fork (Step 5's upstream-issues flow, then Step 6). Offer the fork snapshot PR against upstream only as a separate, explicit secondary option, noting that maintainers often decline committed generated output. When the target states it takes no contributions (Step 5's no-contributions scan), offer the **no-contributions** variant instead on a read-only target (READ / TRIAGE) or in a clone of the user's own fork: a PR inside the user's fork, never one against upstream. A user who can push to the non-fork repo itself keeps the direct PR option, with the statement quoted.
- Drop the **CI gate** option when the workflow could never run (no GitHub remote).
- Keep **issue tracking** and **feedback** always (feedback needs no repo write). On a read-only target that takes contributions, issue tracking appears as the upstream-issues option above.
- **Uninstall** (Step 8) always appears: it removes what this run wrote. It doesn't compose with the write-back offers (no point opening a PR *and* deleting the report), so treat selecting it as "skip the others and clean up".

Then execute the accepted offers **in order** (PR → issues → gate → feedback), because the later ones cross-link to the PR the first may have opened. Uninstall, if chosen, runs instead of the others. The per-step detail below is the execution recipe for each accepted offer; the *asking* happens once, here.

**Non-interactive contract (headless / CI).** When `run-context.json .interactive` is `false`, **make no AskUserQuestion call at all** - every write-back offer is already recorded as `{type, status: "skipped", reason: "non-interactive"}` in `run-context.json .offers` (`pr`, `issue_tracking`, `ci_gate`, `feedback`, `uninstall`). Write nothing back, open no PR, create no issues, emit no gate, file no feedback, uninstall nothing; the run ends with the local `.assess/` artifacts only. A headless `/assess` completes with zero prompts.

```bash
jq '{interactive, offers}' "$REPO_ROOT/.assess/run-context.json"
```

---

## Step 5: Ask Whether to Open a PR

After writing the files, first **check whether a direct PR is even possible** before offering one. A user on `READ` or `TRIAGE` access can't push a branch to the target repo, so an unconditional "open a PR?" offer is infeasible and wastes a turn.

Run the two checks in `references/offer-pr-checks.md` (relative to this skill dir) before framing the batched question: the push-capability and redirect check (`CAN_PUSH`, `IS_FORK`, `IS_OWN_FORK`, `HAS_ISSUES`, `GH_SLUG`, `REDIRECT_NOTICE`), then the no-contributions scan (`NO_CONTRIBUTIONS`, `NO_CONTRIBUTIONS_SOURCE`, `NO_CONTRIBUTIONS_STATEMENT`), which runs before any upstream offer is framed.

Interpret the result:

- `CAN_PUSH=1` (viewerPermission is `WRITE` / `MAINTAIN` / `ADMIN`, or the remote is a push-eligible fork): offer the direct PR flow below. Two cases when `NO_CONTRIBUTIONS=1`. With `IS_FORK=true` the user is working in a clone of a fork (their own reports `ADMIN`; a collaborator on someone else's fork sees `WRITE`); they are the outsider the statement addresses, and a bare `gh pr create` there targets the parent, so offer the no-contributions flow instead (below; its fork step reuses the current repo only when `IS_OWN_FORK=true`). With `IS_FORK=false` the user maintains the repo itself: the statement addresses outsiders, not them, and GitHub will not fork a repo into the account that owns it, so keep the direct flow. In both cases quote `$NO_CONTRIBUTIONS_STATEMENT` and name `$NO_CONTRIBUTIONS_SOURCE` beside the offer so the user decides knowingly.
- `CAN_PUSH=0`, viewerPermission is `READ` / `TRIAGE`, and `NO_CONTRIBUTIONS=0`: name the constraint, then offer, in this order (with `HAS_ISSUES=false` drop option (1): the offers are the fork-based PR flow and "leave local", with issue tracking going to the fork or a personal tracker as in Step 6): (1) the upstream-issues flow, the default ("file the Top 3 Actions as issues on `<owner>/<repo>`, each linking the full report in your fork?"), which proposes no `.assess/` file for upstream; (2) the fork-based PR flow as an explicit opt-in ("also open a snapshot PR against `<owner>/<repo>` from your fork? Maintainers often decline committed generated output: the `.assess/` files are a dated snapshot that goes stale with the next commit."); (3) "leave local". Do not offer the direct flow. The findings are what a maintainer you do not work with acts on; issues carry them, a snapshot PR mostly does not.
- `CAN_PUSH=0`, viewerPermission is `READ` / `TRIAGE`, and `NO_CONTRIBUTIONS=1`: the target says it takes no contributions, so never offer a PR against `<owner>/<repo>`. Quote `$NO_CONTRIBUTIONS_STATEMENT` and name `$NO_CONTRIBUTIONS_SOURCE`, then offer the no-contributions flow instead: "open the PR inside your fork, against the fork's default branch, and share the link?" Pair it with the Actions hint (see the flow below).
- `gh` unavailable / not a GitHub remote / not authenticated (`$PUSH_INFO` empty), whatever `NO_CONTRIBUTIONS` says: skip every PR offer (direct, fork, and no-contributions) and surface only the "leave local" outcome, naming the reason ("no GitHub remote detected" / "`gh` not authenticated").

If `$REDIRECT_NOTICE` is non-empty, output it verbatim on its own line before the batched Phase 2 question.

The push-capability result decides **how the PR option appears in the batched Phase 2 question** (it is not a separate prompt): use "open a PR in this repo" on a push-capable target, "file the Top 3 Actions as upstream issues linking the report in your fork" first (when `HAS_ISSUES=true`) and "fork and open a snapshot PR from your fork" second (two separate options) on a read-only target, "open a PR inside your fork and share the link" when `NO_CONTRIBUTIONS=1` on a read-only target or with `IS_FORK=true`, and drop the PR option entirely when no GitHub remote is detected. Frame the batched question with the written artifacts, e.g. _"Wrote `.assess/assess-report.md`, `.assess/complexity-heatmap.svg`, and `.assess/doc-graph.svg` in `<repo-name>`. Which of these should I do?"_ followed by the feasible offers.

**When the user selects a PR or upstream-issues offer,** follow its flow in `references/offer-pr-flows.md` (relative to this skill dir): the direct, fork, upstream-issues and no-contributions flows, and the PR body that `assess_actions_render.py` fills.

If the user **did not select the PR offer**: skip it. Files stay in `.assess/` for them to review - the plugin footer in the MD already advertises the tool when anyone opens the file.

**Gitignore hint:** suggest the user add `.assess/complexity-stats.prior.json` to their `.gitignore`. It's a transient rotation file that the next run overwrites; keeping it tracked creates noisy diffs. The current stats (`complexity-stats.json`) should still be committed - it's the baseline for the next run's diff.

## Step 6: Offer to Track the Top 3 Actions in the User's Issue Tracker

This is the second item in the batched Phase 2 question (its wording there: _"track the Top 3 Actions in your issue tracker - each becomes a closeable, assignable work item rather than a bullet buried in a PR description"_). Execute this step only if the user **selected the issue-tracking offer**, or the upstream-issues offer (which is the issue-tracking offer on a read-only target, run after Step 5 hosts the report in the fork).

If the user **did not select** it: skip. The Top 3 Actions table in the PR and the report still lists everything inline.

When selected, proceed agnostically - **don't assume GitHub** (or any specific tracker). Use your judgment based on what's actually in front of you.

Then follow `references/offer-issue-tracking.md` (relative to this skill dir): 6a identify the tracker, 6b create the items from `actions.json`, 6c idempotency, 6d link the items back to the assessment, 6e reconcile filed items (propose only), 6f report back to the user.

## Step 6.5: Offer to Freeze the Assessment into a CI Check

This is the third item in the batched Phase 2 question - the one that converts `/assess` from a norm into a contract. Its wording in the batch:

> **Freeze this into a repeatable check?** I can emit a GitHub Action that:
> - Runs the deterministic toolchain on every PR (the exact tools this run found),
> - Renders the metrics report via `assess_report.py`,
> - Gates on AI-readiness floors (a flagged finding present, a containment drop, p95 complexity past a threshold) and, if you opt in, on cross-run regressions against the prior committed snapshot.
>
> This turns `/assess` from a thing-you-run into a thing-that-runs. No AI in the loop - it is the frozen, contract version of the assessment you just ran by hand.

**State the cost in the offer.** Read `gate_cost_estimate` from `.assess/run-context.json`. When its `available` is true, add one line to the offer: "Estimated cost: about N runs a month, roughly M minutes of GitHub Actions time", with N its `runs_per_month` and M its `minutes_per_month` (when `capped` is true the count hit the listing limit, so say "at least" in place of "about" and "roughly"), followed by its `assumption` sentence verbatim, so the figure reads as an estimate. Then branch on `private`, which has three states: `true` - a private repository spends these minutes from the account's Actions allowance; `false` - a public repository runs on standard runners at no charge; `null` - visibility could not be read, so say the minutes are billed only if the repository is private, and never call them free. When `available` is false, leave the line out rather than guess a figure.

This offer only makes sense when the repo can actually run the workflow. Apply the **same write-access check as Step 5** (`CAN_PUSH`): on a read-only target, the file can still be written locally for the user to commit via their fork, but don't promise to open the gating PR. If `gh` / the remote is unavailable, drop this option from the batched question (the workflow could never run).

If the user **did not select** the CI-gate offer: skip. The deterministic report and findings already shipped in `.assess/`.

If the user **selected** it, follow `references/offer-ci-gate.md` (relative to this skill dir): emit the workflow, relay the toolkit version pin, tell the user about the gate config, and commit `.assess/findings.json` with the workflow.


## Step 7: Tool Feedback (Optional)

Feedback is the fourth item in the batched Phase 2 question (its wording there: _"file feedback against the toolkit - flag anything in this report that looks wrong or surprising"_). It is always offered, since it needs no repo write. Execute this step only if the user **selected the feedback offer**.

When surfacing the batched question, first read detected anomalies so the feedback option can name them:

```bash
jq '.anomalies' "$REPO_ROOT/.assess/run-context.json"
```

When it is an object with `available: false` instead, anomaly detection itself failed: mention its `reason` alongside the feedback option, since a failed check is worth reporting. If the array is non-empty, mention them alongside the feedback option, e.g. _"I detected anomalies this run (`<code>`: <description>) that may indicate a bug - want to file feedback?"_. When the user **selected** the feedback offer, build a sanitized issue body from `run-context.json`:

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

## Step 8: Offer to Uninstall `/assess`

The fifth option in the batched Phase 2 question - the escape hatch. `/assess` should be as easy to remove as to run: a tool that can't be cleanly undone is one users hesitate to try. Its wording in the batch:

> **Remove `/assess` from this repo?** I can undo everything this run wrote - delete `.assess/`, strip the README badge, remove the CI gate workflow and any decline markers, and clear archetype override markers - leaving the repo exactly as it was. (This removes the *artifacts*, not the plugin.)

Execute this step only if the user **selected the uninstall offer**. It is mutually exclusive with the write-back offers (uninstalling and opening a PR are contradictory) - if the user somehow selected both, confirm which they meant before acting.

When selected, follow `references/offer-uninstall.md` (relative to this skill dir): it reads `uninstall_instructions_path` from `run-context.json` and performs the assess skill's removal steps, each destructive one only after the user has confirmed.
