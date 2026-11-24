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
| `eval_tests` | a command running `pytest -m eval` / `-m evals`, or a path under `evals/` |

A command line that installs (`pip install ragas`) is never a sign. A secret named inside a command is
not a sign either: a deploy that stores the key in a secrets manager calls no model. A workflow-level
`env:` is not read: it would mark every job of the workflow. A step or job whose `if:` is never
true (`if: false`) is not read.

## Gates

| Gate | GitHub Actions | GitLab CI |
|---|---|---|
| `paths` | `paths:` or `paths-ignore:` on every ordinary event (`push`, `pull_request`, `pull_request_target`, `merge_group`) that fires the workflow | |
| `pr_types` | pull request `types:` without `synchronize` | |
| `label` | `types: [labeled]`, or a job or step `if:` on `github.event.pull_request.labels` or `github.event.label` | |
| `event_if` | a job `if:` requiring `github.event_name ==` a non-ordinary event | |
| `tags_only` | a push filtered to tags with no branch filter | |
| `event_trigger` | no ordinary event fires the workflow (schedule, `workflow_dispatch`, comments) | the job runs in neither a branch nor a merge request pipeline (`lib.ci_triggers`) |
| `changes` | | the job runs in an ordinary pipeline only through a `rules:` item holding `changes:` |

A job `if:` that requires an ordinary event (`github.event_name == 'pull_request'`) narrows the
job's triggers to that event; when none of the ungated events is left, the job is gated (`event_if`).

Not gates: a `branches:` filter alone (the default branch is still pushed), an actor `if:`, and
`concurrency` with `cancel-in-progress: true`, recorded as `cancel_superseded`: it cancels a
superseded run, but the last push of every update still runs the job.

GitLab `changes` is read by rewriting the file (`without_changes`): each `rules:` item holding
`changes:` becomes one that cannot run the job (or `when: never` when it was one), line numbers
kept, and `lib.ci_triggers.gitlab_line_triggers` reads the job again. A job that ran before and
no longer runs in an ordinary pipeline is gated by `changes`; one that still does is ungated.
`extends:` and `workflow: rules:` are read as `ci_triggers` reads them.

## Unknown, never a guess

A job is `unknown` (with `reason`) and never ungated or gated when:

- `on_unread`: the workflow's `on` cannot be listed;
- `filters_unread`: an event's filters are a flow mapping or a `types:` that is not a list;
- `called_workflow`: only `workflow_call` / `workflow_run` fire it and no workflow of the
  repository calls it (a called workflow takes its local callers' route, the worst one first);
- `condition_unread`: a label or event `if:` joined with `||`, or a job or step `if:` on a value
  this reader does not follow (`steps.`, `needs.`, `vars.`, `env.`, `inputs.`), such as a
  changed-files job's output, which may be a paths gate;
- `rules_unread`: GitLab rules this reader cannot settle.

`unread` counts jobs that call a reusable workflow from another repository and GitLab includes
that were not read. Either can hide an ungated job, so with none ungated the signal is null
(`ci_jobs_unread`) rather than 0.

## Known limits

- Only workflow files directly under `.github/workflows/` and `.gitlab-ci.yml` (with its
  includes, reported at the include line of the repository's own file) are read.
- GitLab `only: changes:` is not read as a gate.
- A job whose model call sits in a script file the job runs is not found, nor one whose key is
  written into a file by a command (`echo ANTHROPIC_API_KEY=... >> .env`).
