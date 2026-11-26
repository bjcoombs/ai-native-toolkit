# `lib/docs/doc_staleness.py`

The full reference for `skills/assess/scripts/lib/docs/doc_staleness.py`, whose module docstring holds the summary.

## Ownership, churn and the block

Associating a doc with the code it describes uses the one ownership rule in
``lib.docs.doc_ownership`` (nearest-ancestor base doc, then a parallel ``docs/`` tree,
the doc's explicit code links, then repo-wide churn), the same rule the
complexity join applies. Each row reports the method (``subject_method``), the
rule's roots (``subject_roots``), for a parallel ``docs/`` match the deciding key and
tie-break (``subject_match``, #817), the subject size (``subject_code_count``) and
up to ``SUBJECT_PATHS_CAP`` of its paths (``subject_paths``), so a finding's
subject can be checked.

Churn comes from ``lib.history.git_churn`` -- the same machinery the complexity treemap
uses, so churn is computed one way across the whole skill.

Both `stale_hubs` and the `lying_map` join read the capped `ratio`. The association logic
reuses `doc_graph`'s code-link edges. For *generated* docs it reads `doc_provenance`
(per-doc and via the `[[generated]]` config map) and replaces the churn ratio with a
source-vs-doc verdict. `last_commit_days` and the instruction grader's `freshness_days`
(via `content_clock`) both read `git_churn.content_commit_clock`, and the skipped bulk
commits are reported as `bulk_commits_skipped` (newest first, capped) with
`bulk_commits_skipped_total`. A doc dated by the creation fallback carries
`last_change_basis: "creation"` and confidence `low`, which keeps it out of the stale-hub
finding; `creation_date_fallback_count` counts them. On a shallow clone the block
carries `history_truncated: true` and `history_boundary_date`, every row is confidence
`low`, and a doc whose newest visible change is the boundary carries
`last_commit_days_bound: "lower"` (copied onto its `stale_hubs` entry): its age is a
floor. Its ratio still rests only on subject commits after the boundary, so the
boundary listing every code file does not make it stale. `summary` (#699) carries the integer
counts a maturity model reads: `measured_docs` (rows with a code subject of their own:
every method but `repo-baseline`, and not a base doc with nothing beside it to measure,
`doc_staleness_scale.nothing_beside`), `stale_docs` (those `doc_complexity_join.is_stale`
marks: ratio above `STALENESS_RATIO_THRESHOLD`, a newer declared source, or a renamed
direct child the doc still names) and `stale_pct` (their share, a whole percent rounded up).
`stale_docs` and `stale_pct` are null on a truncated or degenerate history, or with no
measured doc, so the doc-freshness row reads unknown. Below 20 measured docs only
`stale_pct` is null, with `stale_pct_reason` `too_few_measured_docs`; `stale_docs` stays a count.
A page of only generator directives (`lib.docs.doc_directives`, a mkdocstrings `::: pkg.mod`
stub with no declared provenance) renders from code at build time: its row carries
`directive_only: true` and ratio 0, it leaves `measured_docs`, and
`summary.generator_directive_docs` counts it (#816).
Paths are repo-relative POSIX (`as_posix`) on every platform. The `modularity` block's `base_doc_dir_ratio`
is code directories holding a base doc over all code directories; a base doc in a
directory with no code (`docs/`, `agents/`) counts in neither, so the ratio stays
within 0-1 (tests: `tests/test_doc_modularity.py`).

## Scaling to the base doc's directory (#822)

A nearest-ancestor base doc is measured against the code files directly in its own directory, not its whole subtree; a rename of a direct child the doc still names makes it stale; below 20 measured docs `summary.stale_pct` is null with `stale_pct_reason` `too_few_measured_docs`. The block records the rule as `normalisation`. The rule and its fields: `lib/docs/doc_staleness_scale.py`.

Known limit: an old path that exists again at HEAD does not count as renamed, and that check
reads the filesystem. On a case-insensitive filesystem (macOS, Windows) a case-only rename
(`Helper.py` to `helper.py`) that the doc still names as `Helper.py` therefore does not count,
while on Linux it does, so `renamed_children_named` and the stale verdict can differ by platform
in that one case.
