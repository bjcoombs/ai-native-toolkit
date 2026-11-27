# The /assess deterministic-core boundary

Read on demand by the automated reviewer when a diff touches
`skills/assess/scripts/` (paths relative to the repo root).

The `lib/` core does all data and math with **no AI**. The LLM only writes
prose, through the `assess_finalize.py` write-back. The core reaches the
network or writes outside the run only through four paths:

1. `lib/hosting/gh_cli.py` - optional live GitHub reads via the `gh` CLI. They
   resolve the remote first and degrade to `available: false`.
2. `lib/test_pressure/mutmut3.py` - the opt-in mutation pass's uv runner
   (`uv run --project` in a scratch copy). On first use it may download the
   assessed package's dependencies plus the pinned mutmut and pytest.
3. `lib/starter_delivery.py` - the one write. Only under `/assess --fix` or
   `--starter` with the opt-in `--pr`, with a clean tree, an authenticated `gh`
   or `glab` and a run from the default branch's tip, it pushes a branch and
   opens a pull (merge) request, degrading to a local branch with a named
   `reason`.
4. `backlog_reconcile_apply.py` - the scheduled backlog reconcile's tracker
   writes through `gh`, outside `lib/`. It runs only in the `apply` job of
   `.github/workflows/reusable-backlog-reconcile.yml`, never in `/assess`, and
   makes only the writes its `[backlog] mode` allows.

## Flag

- AI calls, nondeterminism, or network access outside those four paths
  creeping into `skills/assess/scripts/lib/`.
- A `gh_cli` caller that reports a clean result when a read failed.
- A change to a deterministic module **without a matching test** in
  `skills/assess/tests/`. That test is the contract that lets the output be
  trusted regardless of which LLM drives.
- A break in the `instructions_grade` schema convention: `Optional[str]`, where
  `null` means "no instruction file found anywhere", a different remediation
  from `F`.
