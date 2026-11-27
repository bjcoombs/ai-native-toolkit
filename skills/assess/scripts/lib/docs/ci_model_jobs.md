# ci_model_jobs: model-backed CI jobs and their gates (#1242)

Reference for `lib/ci/ci_model_jobs.py`. The module docstring has the summary and the block shape.

## Why

A CI job that calls a model API costs money on every run. On every default-branch push and every
pull request update, the cost follows merge volume, not changes to the prompts the job tests.
The tendency it compensates for is accretion of spend: each new agent-written pull request adds
runs nobody priced.

## Signs (a job is model-backed when any line of it shows one)

| Sign | Read from |
|---|---|
| `eval_harness` | a `run:` or `script:` command: `promptfoo eval` / `redteam`, `deepeval test`, `oaieval`, `inspect eval`, `lm_eval` / `lm-eval`, `ragas` |
| `agent_cli` | a command running `claude -p` or `claude --print`, or `claude plugin eval` (also through `npx @anthropic-ai/claude-code`). `plugin eval` is read with options before or after `plugin` and `eval`, as a quoted command (`run: "claude plugin eval ."`, `sh -c '...'`) and across the lines of a block `run:` or `script:` (a `\` continuation, a folded `>`, a GitLab `- >` item), and inside a `$(...)` or backtick span in double quotes (`OUT="$(claude plugin eval .)"`, read by `lib.ci.ci_shell_text`); a quoted argument (`git commit -m "..."`), an `echo` or `printf` and a `#` comment naming it are not read; an apostrophe with no later `'` anywhere in the text (`don't` in a heredoc body) ends at its line, so the commands after it are still read; a later `'`, even inside double quotes, still pairs with it |
| `agent_action` | a step `uses:` one of `AGENT_ACTIONS`: `anthropics/claude-code-action`, `anthropics/claude-code-base-action`, `openai/codex-action`, `google-github-actions/run-gemini-cli` |
| `provider_secret` | an `env:`, `with:`, `secrets:` or GitLab `variables:` entry of the job names one of `PROVIDER_SECRETS` (as the key or in `secrets.<NAME>`); a step `name:` naming one is not a sign |
| `eval_tests` | a command running `pytest -m eval` / `-m evals`, or a runner (`pytest`, `python`, `node`, `npx` ...) given a path under `evals/` |

A command line that installs (`pip install ragas`, `python -m pip install`, `poetry add`) is never a sign. The
install or add word counts only as the subcommand of a package manager (`pip`, `uv`, `npm`, `yarn`,
`poetry`, `apt-get` ...), with up to three options or `global` / `tool` / `pip` between them, also
inside `sh -c "..."`. The managers include versioned pips (`pip3.11`), `pipenv`, `rye`, `pixi` and
`hatch`, with `python` also allowed between (`uv python install`). A prompt word (`claude -p 'add regression tests'`) does not hide the command,
and a `claude -p` line is an `agent_cli` sign even when it also installs (`npm install -g ... && claude -p ...`). Nor is an `evals/` path a runner is told to leave out
(`--ignore`, `--ignore-glob`, `--deselect`, `--exclude`): keeping evals out of a job is the remedy. A secret named inside a command is
not a sign either. A workflow-level `env:` secret reaches every job, so it is a sign of each. A step
that `uses:` a repo-local action (`./.github/actions/x`) carries the signs of that action's
`action.yml`, one level, read from tracked files as `lib.ci.ci_composite` reads one. A step or job
whose `if:` is never true (`if: false`) is not read.

**A secret alone is not enough to read ungated.** A deploy or secret-store step names the key
without calling a model (`gcloud secrets versions add ... <<< "$OPENAI_API_KEY"`, a vault-sync
action given the secret's name). A job whose only sign is `provider_secret`, and which would read
ungated, reads unknown (`secret_only`). A secret beside any other sign is a model job.

## Gates

| Gate | GitHub Actions | GitLab CI |
|---|---|---|
| `paths` | `paths:` or `paths-ignore:` on every ordinary event (`push`, `pull_request`, `pull_request_target`, `merge_group`) that fires the workflow. A `paths:` list is read in order, as GitHub reads it (the last match wins): it gates nothing when a catch-all (`**`, `**/*`) is not followed by a `!` exclusion, so `['**', '!docs/**']` gates and `['!docs/**', '**']` does not. A `paths-ignore:` that is empty or only catch-alls gates nothing. GitHub's `*` does not match `/`, so `paths: ['*']` (root files only) gates | |
| `pr_types` | pull request `types:` without `synchronize` | |
| `label` | `types: [labeled]`, or a job or step `if:` on `github.event.pull_request.labels` or `github.event.label` | |
| `event_if` | a job `if:` requiring `github.event_name ==` a non-ordinary event. Sign steps under different recognised gates (one `label`, one `event_if`) combine them | |
| `tags_only` | a push filtered to tags with no branch filter | |
| `event_trigger` | no ordinary event fires the workflow, and every event is one GitHub documents (`GATING_EVENTS`: schedule, `workflow_dispatch`, comments ...) | the job runs in neither a branch nor a merge request pipeline (`lib.ci.ci_triggers`) |
| `changes` | | the job runs in an ordinary pipeline only through a `rules:` item holding `changes:` |

A job `if:` that requires an ordinary event (`github.event_name == 'pull_request'`) narrows the
job's triggers to that event; when none of the ungated events is left, the job is gated (`event_if`).
Only a positive requirement narrows: `(github.event_name == 'push') == false` or `!=` does not.

A `paths-ignore:` that names anything but catch-alls counts as a gate, so `paths-ignore: ['docs/**']` reads gated
although it skips only docs-only changes. The reader does not compare the patterns with the
files the job tests.

Not gates: a `branches:` filter alone (the default branch is still pushed), an actor `if:`, and
`concurrency` with `cancel-in-progress: true`, recorded as `cancel_superseded`: it cancels a
superseded run, but the last push of every update still runs the job.

GitLab `changes` is read by rewriting the file (`without_changes`): each `rules:` item holding
`changes:` becomes one that never matches (the pipeline of a change outside its paths), line
numbers kept, and `lib.ci.ci_triggers.gitlab_line_triggers` reads the job again. A job that ran before and
no longer runs in an ordinary pipeline is gated by `changes`; one that still does is ungated.
So a docs-skip rule (`changes: [docs/**]` with `when: never`, then `when: always`) is not a
gate: a source change reaches the later rule. This differs from GitHub `paths-ignore`, which counts.
GitLab jobs are read as GitLab merges them (#1180): `lib.ci.ci_merged_view` over
`ci_includes.merged_config` blanks each job whose keys come from more than one place (the root
file and an include, `extends:`, a `default:` script key) and renders it once with its
effective keys. So a root `- when: always` over an included schedule-only job reads ungated, and
a job whose model `script:` comes from a hidden `extends:` template is found. A merged job is
reported at its definition in the repository's own file, else at the include line that led to
it. `MergedJob.open` (an include not read may also define the job) is not applied: that include
already counts in `unread`. `workflow: rules:` are read as `ci_triggers` reads them.

## Unknown, never a guess

A job is `unknown` (with `reason`) and never ungated or gated when:

- `on_unread`: the workflow's `on` cannot be listed, or names an event this reader does not
  know (an expression such as `${{ fromJSON(x) }}`, a typo);
- `filters_unread`: an event's filters are a flow mapping or a `types:` that is not a list;
- `called_workflow`: a `workflow_call` that no workflow of the repository calls (callers in
  other repositories run it on events this reader cannot see), even beside a `workflow_dispatch`,
  or a `workflow_run`. A called workflow's jobs take each route that reaches it: each local
  caller's route and its own other events. The job's own `if:` and step conditions apply to each
  route, then the worst is taken (ungated, then unknown, then gated), so a callee job requiring
  `pull_request` under a bare `push` caller and a bare `pull_request` caller reads ungated.
  Caller routes are resolved to a fixed point, so A on `pull_request` calling B calling C carries
  A's route to C. A caller job whose `if:` is never true registers no route. A job-level
  `uses: ./.github/workflows/x.yml` is never a model job itself, even with a `secrets:` entry
  naming a provider key: the callee is scanned on its own;
- `secret_only`: see above;
- `condition_unread`: a label or event `if:` joined with `||`, holding a negation (`!`, `!=`), or
  whose label or event test is itself compared with `false`, in either order (`contains(...labels...) == false`;
  a guard beside it, `github.event.pull_request.draft == false`, is not), or a job or step `if:` on a value
  this reader does not follow (`steps.`, `needs.`, `vars.`, `env.`, `inputs.`), such as a
  changed-files job's output, which may be a paths gate;
- `rules_unread`: GitLab rules this reader cannot settle;
- `merge_unread`: a GitLab job whose merge `ci_merged_view` cannot settle (an include under
  `rules:`, an anchor merge, an `extends:` parent not read; `MergedJob.unresolved`).

`unread` counts jobs that call a reusable workflow from another repository under a route that
is not gated (a schedule- or dispatch-only caller does not count), GitLab includes
that were not read, and tracked CI files that could not be read (empty, over the size limit). With no
model-backed job found but `unread` above 0, `model_jobs` is null and the reason is `ci_jobs_unread`, not
`no_model_jobs`. The step gate reads only the lines that show a model call: a provider key in a
job `env:`, or in a step that only sets it, neither gates nor ungates a job. Either can hide an ungated job, so with none ungated the signal is null
(`ci_jobs_unread`) rather than 0.

## Known limits

- A line that installs and then runs an eval harness (`pip install ragas && ragas ...`) is read as
  an install line, so the harness is not found.
- A quoted event key under `on:` (`"push":`) is not read; a quoted `"on":` is.
- `agent_cli` is `claude -p` / `--print` and `claude plugin eval` only: another Claude Code
  subcommand that calls a model is not a sign. `claude -p` is still read line by line, without
  the echo and comment blanking `plugin eval` gets.
- Only workflow files directly under `.github/workflows/` and `.gitlab-ci.yml` (with its
  includes, reported at the include line of the repository's own file) are read.
- GitLab `only: changes:` is not read as a gate.
- A job whose model call sits in a script file the job runs is not found, nor one whose key is
  written into a file by a command (`echo ANTHROPIC_API_KEY=... >> .env`).

## Agent eval suites (`agent_evals`, #850)

The block also carries `agent_evals`, built by `lib.ci.agent_evals` from the same CI files plus
the repo-local `action.yml` files: each eval suite folder that calls a model, whether a CI file
runs it and the committed baseline it holds. Its module docstring has the rules and the shape.
Known limits:

- A path in a quoted argument (`pytest "evals/"`, `-m "eval and not slow"`) is not read, as
  `command_text` blanks every quoted argument.
- A suite run only through a whole-tree command (`pytest` with no path, `pytest tests/` for
  `tests/agent`) is not named, nor one run from a folder the job `cd`s into (`cd tests && pytest evals`).
- One task-runner level is followed, at the repository root only: a `Makefile` or `justfile`
  recipe or a `package.json` script. A recipe that calls another target is not followed.
- A baseline outside the suite folder (`baselines/evals.json` beside `evals/`) is not read.
