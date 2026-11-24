# ci_model_jobs: model-backed CI jobs and their gates (#1242)

Reference for `lib/ci_model_jobs.py`. The module docstring has the summary and the block shape.

## Why

A CI job that calls a model API costs money on every run. On every default-branch push and every
pull request update, the cost follows merge volume, not changes to the prompts the job tests.
The tendency it compensates for is accretion of spend: each new agent-written pull request adds
runs nobody priced.

## Signs (a job is model-backed when any line of it shows one)

| Sign | Read from |
|---|---|
| `eval_harness` | a `run:` or `script:` command: `promptfoo eval` / `redteam`, `deepeval test`, `oaieval`, `inspect eval`, `lm_eval` / `lm-eval`, `ragas` |
| `agent_cli` | a command running `claude -p` or `claude --print` (also through `npx @anthropic-ai/claude-code`) |
| `agent_action` | a step `uses:` one of `AGENT_ACTIONS`: `anthropics/claude-code-action`, `anthropics/claude-code-base-action`, `openai/codex-action`, `google-github-actions/run-gemini-cli` |
| `provider_secret` | an `env:` or `with:` line of the job names one of `PROVIDER_SECRETS` (as the key or in `secrets.<NAME>`) |
| `eval_tests` | a command running `pytest -m eval` / `-m evals`, or a runner (`pytest`, `python`, `node`, `npx` ...) given a path under `evals/` |

A command line that installs (`pip install ragas`) is never a sign. Nor is an `evals/` path a runner is told to leave out
(`--ignore`, `--ignore-glob`, `--deselect`, `--exclude`): keeping evals out of a job is the remedy. A secret named inside a command is
not a sign either. A workflow-level `env:` secret reaches every job, so it is a sign of each. A step
that `uses:` a repo-local action (`./.github/actions/x`) carries the signs of that action's
`action.yml`, one level, read from tracked files as `lib.ci_composite` reads one. A step or job
whose `if:` is never true (`if: false`) is not read.

**A secret alone is not enough to read ungated.** A deploy or secret-store step names the key
without calling a model (`gcloud secrets versions add ... <<< "$OPENAI_API_KEY"`, a vault-sync
action given the secret's name). A job whose only sign is `provider_secret`, and which would read
ungated, reads unknown (`secret_only`). A secret beside any other sign is a model job.

## Gates

| Gate | GitHub Actions | GitLab CI |
|---|---|---|
| `paths` | `paths:` or `paths-ignore:` on every ordinary event (`push`, `pull_request`, `pull_request_target`, `merge_group`) that fires the workflow; a `paths:` list holding a catch-all (`**`, `**/*`, `*`) and a `paths-ignore:` that is empty or only catch-alls gate nothing | |
| `pr_types` | pull request `types:` without `synchronize` | |
| `label` | `types: [labeled]`, or a job or step `if:` on `github.event.pull_request.labels` or `github.event.label` | |
| `event_if` | a job `if:` requiring `github.event_name ==` a non-ordinary event | |
| `tags_only` | a push filtered to tags with no branch filter | |
| `event_trigger` | no ordinary event fires the workflow, and every event is one GitHub documents (`GATING_EVENTS`: schedule, `workflow_dispatch`, comments ...) | the job runs in neither a branch nor a merge request pipeline (`lib.ci_triggers`) |
| `changes` | | the job runs in an ordinary pipeline only through a `rules:` item holding `changes:` |

A job `if:` that requires an ordinary event (`github.event_name == 'pull_request'`) narrows the
job's triggers to that event; when none of the ungated events is left, the job is gated (`event_if`).

A `paths-ignore:` counts as a gate whatever it names, so `paths-ignore: ['docs/**']` reads gated
although it skips only docs-only changes. The reader does not compare the patterns with the
files the job tests.

Not gates: a `branches:` filter alone (the default branch is still pushed), an actor `if:`, and
`concurrency` with `cancel-in-progress: true`, recorded as `cancel_superseded`: it cancels a
superseded run, but the last push of every update still runs the job.

GitLab `changes` is read by rewriting the file (`without_changes`): each `rules:` item holding
`changes:` becomes one that never matches (the pipeline of a change outside its paths), line
numbers kept, and `lib.ci_triggers.gitlab_line_triggers` reads the job again. A job that ran before and
no longer runs in an ordinary pipeline is gated by `changes`; one that still does is ungated.
So a docs-skip rule (`changes: [docs/**]` with `when: never`, then `when: always`) is not a
gate: a source change reaches the later rule. This differs from GitHub `paths-ignore`, which counts.
`extends:` and `workflow: rules:` are read as `ci_triggers` reads them.

## Unknown, never a guess

A job is `unknown` (with `reason`) and never ungated or gated when:

- `on_unread`: the workflow's `on` cannot be listed, or names an event this reader does not
  know (an expression such as `${{ fromJSON(x) }}`, a typo);
- `filters_unread`: an event's filters are a flow mapping or a `types:` that is not a list;
- `called_workflow`: only `workflow_call` / `workflow_run` fire it and no workflow of the
  repository calls it. A called workflow's route is the worst of its local callers' routes and
  its own other events (ungated, then unknown, then gated); its jobs' own `if:` and step
  conditions then apply. A `workflow_run` trigger adds an unknown route. A job-level
  `uses: ./.github/workflows/x.yml` is never read as a composite: the callee is scanned itself;
- `secret_only`: see above;
- `condition_unread`: a label or event `if:` joined with `||`, holding a negation (`!`, `!=`) or
  compared with `false`, or a job or step `if:` on a value
  this reader does not follow (`steps.`, `needs.`, `vars.`, `env.`, `inputs.`), such as a
  changed-files job's output, which may be a paths gate;
- `rules_unread`: GitLab rules this reader cannot settle.

`unread` counts jobs that call a reusable workflow from another repository, GitLab includes
that were not read, and tracked CI files that could not be read (empty, over the size limit). With no
model-backed job found but `unread` above 0, `model_jobs` is null and the reason is `ci_jobs_unread`, not
`no_model_jobs`. The step gate reads only the lines that show a model call: a provider key in a
job `env:`, or in a step that only sets it, neither gates nor ungates a job. Either can hide an ungated job, so with none ungated the signal is null
(`ci_jobs_unread`) rather than 0.

## Known limits

- GitLab `extends:` is read for rules, not for commands: a model command inherited from a
  template is not found.
- `paths: ['**', '!docs/**']` reads as no gate, though it acts like `paths-ignore: ['docs/**']`.
- An install word inside a command's arguments (`claude -p 'add tests'`) hides the command.
- Two sign steps with different recognised gates (one `label`, one `event_if`) read ungated.

- Only workflow files directly under `.github/workflows/` and `.gitlab-ci.yml` (with its
  includes, reported at the include line of the repository's own file) are read.
- GitLab `only: changes:` is not read as a gate.
- A job whose model call sits in a script file the job runs is not found, nor one whose key is
  written into a file by a command (`echo ANTHROPIC_API_KEY=... >> .env`).
