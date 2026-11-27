# Backlog refinement

How another repository adopts the refiner: three GitHub workflows that check issues against a definition of ready, label the ready ones, close exact duplicates and keep a project board in step, or one GitLab CI template that does the same on GitLab ([GitLab](#gitlab)). Back to the [Map of Content](./index.md). For a one-off pass from your terminal, run [`/refine`](../skills/refine/SKILL.md) instead; it reads the same verdict and asks before each write.

**Live mode is not ready yet.** Four review points from the first release can cause a wrong `agent-ready` label or a wrong duplicate close: [#1341](https://github.com/bjcoombs/ai-native-toolkit/issues/1341). Run in dry-run until that issue is closed. Dry-run is the default and writes nothing.

**Version.** The workflows first ship in the toolkit release after 4.0.0. The examples pin `v4.1.0`, the newest release at the time of writing; each release moves the pin to itself, so copy the examples from the release you adopt. On 4.0.0 or earlier the `uses:` lines have no workflow to call.

## What runs

| Workflow | Trigger | Model | What it does |
|---|---|---|---|
| `reusable-refine-triage.yml` | an issue is opened, or a comment is added | yes, read-only | `refine.py` decides what a script can. An issue it leaves unsettled goes to a Sonnet-class model, which returns a plan; a separate job with no model applies the plan through guarded write scripts. A `/reopen` comment reopens a duplicate the refiner closed |
| `reusable-refine-dedupe.yml` | an issue is opened | yes, read-only | An exact duplicate is closed with no model, under a weekly cap; otherwise the model names at most three candidates in one comment and closes nothing |
| `reusable-refine-sweep.yml` | a schedule, twice a day | no | Ready label on settled ready issues, exact-duplicate closes, the ready-buffer report with the unsettled issues listed oldest first for a maintainer, and the optional project sync |

Paths are relative to the toolkit's `.github/workflows/`. The definition of ready is your repository's `docs/contributing/definition-of-ready.md` when it has one, and the toolkit's copy otherwise.

Duplicate closes from the dedupe and sweep workflows take turns in one concurrency group per repository, so two runs never read the weekly close count at once. GitHub keeps one pending run per group, so when several exact duplicates are opened together some dedupe runs show as `cancelled` while pending. That is expected: each pair stays closable, and the next sweep closes it.

The model never holds a token that can write: it runs in a job with `issues: read`, and the job that writes runs no model. The scripts it runs come from the toolkit commit your `uses:` line pins, never from an input.

## Set it up

1. **Credential.** Triage and dedupe take the first of three credentials that is set; with none, an issue that needs the model fails the plan job with an error naming all three. The sweep needs no credential.
   - **Claude Code OAuth token (simplest).** Run `claude setup-token` and store the token as the repository secret `CLAUDE_CODE_OAUTH_TOKEN`, the same secret the `claude-code-action` review workflow uses. It bills the subscription's usage limits, it has no spend cap of its own, and every refiner run competes with the subscriber's own sessions in Claude Code and claude.ai for those limits. It suits a solo maintainer; the workflows use it only when neither option below is set.
   - **API key with a spend limit (recommended for teams).** Create an API key in the Claude Console, in a workspace of its own with a monthly spend limit, and store it as the repository secret `REFINE_ANTHROPIC_API_KEY`. The workspace limit is what caps the cost: every opened issue and every comment can start a model run. When set, it wins over the other two.
   - **Workload Identity Federation.** Set the variables `REFINE_ANTHROPIC_FEDERATION_RULE_ID`, `REFINE_ANTHROPIC_ORGANIZATION_ID`, `REFINE_ANTHROPIC_SERVICE_ACCOUNT_ID` and `REFINE_ANTHROPIC_WORKSPACE_ID`, and pass them as the `anthropic_*` inputs; the callers below already grant `id-token: write`. It is used when no API key is set.

   Whichever credential is chosen reaches only the model step of the read-only plan job, never a job that can write.
2. **Questions issue.** Open an issue for the refiner's questions and ready-buffer report, and set its number as the variable `REFINE_QUESTIONS_ISSUE`. The refiner never triages, labels or closes that issue. Without one, the questions and the report appear in each run's job summary instead.
3. **Callers.** Add the three workflows below.
4. **Kill switch.** Set the variable `REFINE_ENABLED` to `true`. Any other value, or none, skips every job. Set it back to `false` to stop the refiner at once.
5. **Dry-run first.** Leave `REFINE_MODE` unset. Every job then runs its reads and its guardrails and writes nothing: each run's summary lists what live mode would have done, and the verdict, plan and actions log are kept as artifacts. Read a week of them.
6. **Live.** Once #1341 is closed and the dry-run summaries match what you would have done, set `REFINE_MODE` to `live`.

### `.github/workflows/refine-triage.yml`

```yaml
name: Refine triage
on:
  issues:
    types: [opened]
  issue_comment:
    types: [created]
permissions:
  contents: read
  issues: write
  pull-requests: read
  id-token: write
jobs:
  triage:
    if: >-
      vars.REFINE_ENABLED == 'true' &&
      (github.event_name == 'issues' ||
       (!github.event.issue.pull_request && github.event.comment.user.type != 'Bot'))
    uses: bjcoombs/ai-native-toolkit/.github/workflows/reusable-refine-triage.yml@v4.1.0
    with:
      enabled: ${{ vars.REFINE_ENABLED == 'true' }}
      mode: ${{ vars.REFINE_MODE || 'dry-run' }}
      issue_number: ${{ github.event.issue.number }}
      questions_issue: ${{ vars.REFINE_QUESTIONS_ISSUE }}
    secrets:
      anthropic_api_key: ${{ secrets.REFINE_ANTHROPIC_API_KEY }}
      claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

### `.github/workflows/refine-dedupe.yml`

```yaml
name: Refine dedupe
on:
  issues:
    types: [opened]
permissions:
  contents: read
  issues: write
  pull-requests: read
  id-token: write
jobs:
  dedupe:
    if: vars.REFINE_ENABLED == 'true'
    uses: bjcoombs/ai-native-toolkit/.github/workflows/reusable-refine-dedupe.yml@v4.1.0
    with:
      enabled: ${{ vars.REFINE_ENABLED == 'true' }}
      mode: ${{ vars.REFINE_MODE || 'dry-run' }}
      issue_number: ${{ github.event.issue.number }}
    secrets:
      anthropic_api_key: ${{ secrets.REFINE_ANTHROPIC_API_KEY }}
      claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

### `.github/workflows/refine-sweep.yml`

```yaml
name: Refine sweep
on:
  schedule:
    - cron: '17 6,18 * * *'
  workflow_dispatch:
permissions:
  contents: read
  issues: write
  pull-requests: read
jobs:
  sweep:
    if: vars.REFINE_ENABLED == 'true'
    uses: bjcoombs/ai-native-toolkit/.github/workflows/reusable-refine-sweep.yml@v4.1.0
    with:
      enabled: ${{ vars.REFINE_ENABLED == 'true' }}
      mode: ${{ vars.REFINE_MODE || 'dry-run' }}
      questions_issue: ${{ vars.REFINE_QUESTIONS_ISSUE }}
      project_number: ${{ vars.REFINE_PROJECT_NUMBER }}
    secrets:
      project_token: ${{ secrets.REFINE_PROJECT_TOKEN }}
```

`pull-requests: read` lets `refine.py` find the issues an open pull request already closes or overlaps; nothing writes to a pull request.

## Settings

Pass any of these under `with:`. Labels must exist in the repository.

| Input | Workflows | Default | What it sets |
|---|---|---|---|
| `mode` | all | `dry-run` | `live` writes; anything else is a dry-run |
| `ready_label`, `triage_label` | triage, sweep | `agent-ready`, `needs-triage` | The two readiness labels; match `labels` in your definition of ready |
| `allowed_labels` | triage, sweep | `agent-ready,needs-triage,bug,enhancement,documentation,question` | The only labels the refiner may add or remove |
| `max_points` | triage | 0 | Overrides `max_points` in your definition of ready, the largest size that can be `agent-ready`; 0 uses the definition's value |
| `close_cap` | dedupe, sweep | 10 | The most duplicate closes the refiner may make in any 7 days, and so in one sweep |
| `builder_slots` | sweep | 5 | The ready-buffer target is twice this |
| `model` | triage, dedupe | `sonnet` | The model the read-only plan job runs |
| `project_number`, `project_owner` | sweep | empty, the repository owner | The project to keep in sync (below) |
| `in_progress_label` | sweep | `in-progress` | The label that means In progress on the project |

## Project sync

The sweep can keep a GitHub project's `Status`, `Size` and `Verdict` fields in step with the verdict. It is skipped when `project_number` is empty, and skipped with a warning when the `project_token` secret is missing.

- **Token.** `GITHUB_TOKEN` cannot write a user's project. For a project owned by a user, store a classic personal access token with the `project` scope as `REFINE_PROJECT_TOKEN`; for an organization's project, a fine-grained token with the organization's Projects read and write permission also works. It is passed only to the step that writes the project; that job's own token can only read (`contents`, `issues` and `pull-requests`).
- **Fields.** The project needs a single-select `Status` with the options `Needs refinement`, `Waiting on you`, `Ready`, `Blocked`, `In progress` and `In review`; a number field `Size`; and a single-select `Verdict` with `ready`, `needs_judgement` and `not_ready`. A missing field or option stops the sync before it writes.
- **Status** is the first row that matches: In review (an open pull request closes the issue), In progress (the in-progress label), Blocked (ready, with an open blocker), Ready (the ready label, with a verdict that is not `not_ready`), Waiting on you (the refiner asked about it on the questions issue and nobody has replied naming it), Needs refinement.
- **Size** comes from the `<!-- refine:size N -->` marker or a `Size: N points` line, and is cleared when the issue has neither. **Verdict** is `refine.py`'s verdict.
- An open issue missing from the project is added. A project with more than 2000 items stops the sync before it writes. Only a field whose value differs is written, at most 500 writes a run, and nothing is written in dry-run.
- The sync is the only writer of these three fields; a value set by hand is put back on the next sweep.

## GitLab

GitLab has no CI trigger for a new issue, so one template, `templates/refine.yml` (relative to the toolkit root), runs everything from a pipeline schedule: the sweep over every open issue, plus triage and dedupe for each issue opened since the last successful scheduled pipeline. It reads and writes through `glab`, with the same `refine.py` verdict, the same prompts, and the same write scripts and caps as the GitHub workflows. Add it to `.gitlab-ci.yml`:

```yaml
include:
  - remote: https://raw.githubusercontent.com/bjcoombs/ai-native-toolkit/v4.1.0/templates/refine.yml
```

| Job | Model | Token | What it does |
|---|---|---|---|
| `refine-verdict` | no | `REFINE_READ_TOKEN` | `refine.py --platform gitlab` writes the verdict and lists the new issues |
| `refine-plan` | yes, read-only | `REFINE_READ_TOKEN` | For each new issue the script left unsettled, `/triage-issue` and `/dedupe` return plans. Its tools are the read-only `gh.sh` and Read of the verdict. It runs only when the issue's author is a Reporter or above |
| `refine-apply` | no | `REFINE_WRITE_TOKEN` | Checks each plan against its schema, applies it or the settled verdict through the write scripts, then runs the sweep on a scheduled or manual pipeline |

Set it up:

1. **Tokens.** Create two project access tokens: one with the `read_api` scope and the Reporter role, stored as the masked CI/CD variable `REFINE_READ_TOKEN`; one with the `api` scope and the Reporter role, stored as `REFINE_WRITE_TOKEN` with its environment scope set to `refine-apply`. Only the `refine-apply` job names that environment, so the model's job never receives the write token; the two read jobs fail if they see it. Protect the environment to choose who may run it.
2. **Credential and spend limit.** Store a Claude Console API key from a workspace with a monthly spend limit as the masked variable `REFINE_ANTHROPIC_API_KEY`. Workload Identity Federation is GitHub-only for now. Without the key, the model job is skipped and the deterministic steps still run.
3. **Schedule.** Add a pipeline schedule on the default branch, for example `17 6,18 * * *`. The jobs run only on the default branch, in scheduled, manual (`web`) and trigger pipelines, so they run in every scheduled pipeline there, not only the refiner's own. If the project has other schedules, set `REFINE_ENABLED` as a variable on the refiner's schedule (and as `variables[REFINE_ENABLED]=true` in the webhook URL below) rather than project-wide. Each run triages the issues opened since the last `refine-apply` job that succeeded on the default branch outside a webhook pipeline, so a failed run leaves its issues for the next one.
4. **Kill switch, dry-run, live.** As on GitHub: nothing runs until the variable `REFINE_ENABLED` is `true` (unset it, or set it to `false`, wherever you set it), and writes are logged, not sent, until `REFINE_MODE` is `live`. In dry-run `REFINE_WRITE_TOKEN` may be left unset; `refine-apply` then reads with the read token. Each run keeps the verdict, the plans and `.refine/actions.jsonl` as job artifacts.
5. **Pipeline variables.** Set the project's minimum role to use pipeline variables to Maintainer, so a manual pipeline cannot set `REFINE_MODE` or the settings below.

Settings are CI/CD variables with the GitHub inputs' defaults: `REFINE_QUESTIONS_ISSUE`, `REFINE_READY_LABEL`, `REFINE_TRIAGE_LABEL`, `REFINE_ALLOWED_LABELS`, `REFINE_MAX_POINTS`, `REFINE_CLOSE_CAP`, `REFINE_BUILDER_SLOTS` and `REFINE_MODEL`, plus `REFINE_MAX_NEW_ISSUES` (20), the most new issues one run triages, and `REFINE_BOT_LOGIN`, which defaults to the write token's user. The template's inputs choose the code and tools, never a variable: `toolkit_ref` (a release tag or a full commit sha; keep a tag equal to the tag in the include line), `toolkit_url`, `image`, `plan_image`, `claude_code_version`, `glab_version`, `glab_sha256` and `stage`. The project sync is GitHub-only.

**Faster path: a webhook.** To triage a new issue within a minute rather than at the next schedule, create a pipeline trigger token (Settings > CI/CD > Pipeline trigger tokens) and a project webhook on Issues events pointing at `https://<gitlab host>/api/v4/projects/<project id>/ref/<default branch>/trigger/pipeline?token=<trigger token>`. GitLab hands the webhook body to the jobs as `TRIGGER_PAYLOAD`: a newly opened issue is triaged and deduplicated at once, and any other issue event ends the pipeline in `refine-verdict` with nothing to do. The sweep stays on the schedule.

**Readiness on GitLab.**

- **Issue templates.** Markdown templates under `.gitlab/issue_templates/` stand in for GitHub's issue forms: give them the six section headings of your definition of ready. As on GitHub, `refine.py` checks the issue body, not the template.
- **Scoped labels.** `refine::ready` and `refine::triage` work as the ready and triage labels; set `REFINE_READY_LABEL`, `REFINE_TRIAGE_LABEL` and `REFINE_ALLOWED_LABELS` to match, and the definition of ready's `labels` too.
- **Blocked-by edges.** Linked issues of type `blocks` stand in for GitHub's blocked-by edges; the refiner reads and writes them in the same project. They need GitLab Premium; on Free every issue reads no edge, and adding one fails.
- **Trusted comments.** A size marker or clarified-scope comment counts when its author is a project member at Reporter or above, or the refiner's bot.
- **Not read on GitLab:** child tasks, so a large issue must be split rather than marked decomposed, and close reasons.
- **`/reopen`.** GitLab runs `/reopen` itself as a quick action, so there is no reopen job. The refiner never closes an issue again once it has been reopened, and it neutralises any line starting with `/` in the comments it writes, so issue or model text cannot run a quick action.

This template follows GitLab's documentation and has not been run on a real GitLab instance.
