## Platform pass: `--platform`

The offline scan never reaches the network. `--platform` adds a second, separate
pass after it: for each repository whose row is `ok`, one more
`assess_signals.py --platform` child reads the platform for the scans in
`lib/signals.py` `PLATFORM_SCANS` (`review_reality`, `delivery`, `review_evidence`,
which the pass rebuilds from the measured `review_reality`, and `review_automation` and
`ci_checks`, which read GitLab's merged CI configuration), narrowed by
`--scans` when given. Each needs `gh` (github.com remotes) or `glab` (GitLab
remotes) logged in.

- **One shared sample.** Both blocks read the same sample: up to 30 changes merged
  inside `--window` (counted back from the scan date), the most recently merged
  first. `review_reality` used to read the 30 most recently *opened* merged
  changes with no window, so its shares move between a pre-#715 run and a later
  one, and a narrow window can sample fewer than 30.
- **`delivery`.** `merged_count`, `time_to_merge_hours_p50` / `_p90` (opened to
  merged), `first_human_comment_hours_p50` / `_p90` and `human_commented_share`
  (the first comment or review by a person other than the author), `ai_review_share`
  (a review or comment by a known AI review app, a pinned agent app, or an
  `[[ai_usage.agent_identities]]` rule), and `approved_share` / `self_merged_share`
  copied from `review_reality`, and `agent_authored_share` (changes authored by a
  pinned agent app or an `[[ai_usage.agent_identities]]` rule, by username; a
  GitLab access-token user counts only under `token_users_as_agents`), and
  `sample_page_size` (changes per request the sample was read at: 20, or 10 after
  one retry on an HTTP 502 or 504 from a large repository). Durations
  are hours, percentiles nearest-rank. On GitLab the comment and AI-review
  figures come from each merge request's notes (#716): the first human comment is
  the first non-system note by a person other than the author, and AI review is a
  note with an AI-review marker or by an agent or a pinned AI-review app. A figure
  is null when the notes leave it undecided after 3 pages (300 notes) on any
  sampled merge request, or when the notes read is refused. Counts-only output
  (the default) keeps every `delivery` figure, the same as `--full`.

- **GitLab merged CI configuration (#796).** A GitLab repository whose `.gitlab-ci.yml`
  includes a file the offline scan could not read (a template project not cloned under
  `ROOT`, a `template:` or `remote:` file) gets its merged configuration from GitLab's
  CI lint API: one `glab api --hostname <host> projects/<path>/ci/lint?content_ref=<HEAD>`
  GET per such repository. A repository whose every include resolved offline is not
  asked: the template read from `ROOT` wins. `ci_checks.test_or_lint_configured` then reads
  true when a merged job runs a test or lint check (`basis: merged_config`) and false
  when none does, where offline it was null. It stays null, with `basis:
  merged_config_partial`, when the merged configuration may lack jobs: an include under
  `rules:` (GitLab may merge without it) or a `trigger:` child pipeline (not expanded); `ci_checks.merged_config` says whether the
  read ran (`read`, `not_needed`, `invalid`, `unavailable` with `merged_config_reason`,
  or `not_read` offline). `review_automation` credits a review component the merge
  pulled in. A refused or invalid read leaves the offline reading unchanged. The merged
  YAML is never written to disk, cached or published: only a count and a status token.
- **Joined, never failing a row.** The pass's blocks replace the offline
  `network` placeholders in the row's `scans`, and add `delivery`, which a row
  read without `--platform` does not carry at all (absent, never zeros or a
  placeholder); `--scans delivery` without `--platform` exits 2. A refused read (HTTP 403 from `gh` or `glab`) gives
  `review_reality` and `delivery` `available: false` with code `no_access`. It never changes a row's
  `status` or `reason`: a slow or refusing host cannot turn an offline row into
  `scan_timeout`. A read that runs past `--platform-timeout`, exits non-zero or
  writes no valid document gives each platform block `available: false` with
  `platform_timeout`, `platform_exit_<code>`, `platform_error` or
  `platform_invalid_output`. Only a child that exits non-zero
  (`platform_exit_<code>`) leaves its stderr tail in
  `DIR/logs/<repo>.platform.stderr`; a timeout, an error starting the child or
  invalid output writes no log. A refused read (`no_access`, `not_found`,
  `rate_limited`) is an unavailable block with that reason, never zeros. A row
  whose offline scan failed gets no platform read.
- **Network hygiene.** `gh` and `glab` run with stdin closed and prompts disabled
  (`GH_PROMPT_DISABLED`, `NO_PROMPT`, `GIT_TERMINAL_PROMPT=0`). A rate limit (HTTP 429, or GitHub's 403
  "API rate limit exceeded" / "secondary rate limit") is retried up to 3 times, waiting the `Retry-After` seconds when the CLI prints
  it, else 2, 4 and 8 seconds (at most 60 per wait); still limited, the block is
  `rate_limited`. Plain `gh api` and `glab api` calls do not print response
  headers, so in practice the 2, 4 and 8 second fallback applies. `--platform-jobs` caps the concurrent reads. One response
  cache per run, in a temporary directory removed when the pass ends, answers a
  repeated request (an auth probe, a bot-account lookup) once; it never lands in
  `DIR`, the history or a target. The response cache never holds comment or
  review text on either provider: GitHub pull request reviews and comments, the
  GitLab merged list and GitLab merge-request notes are read in memory only and
  never written to disk. Nor does it hold CI configuration text: GitLab's merged CI
  configuration is read the same way.
- **Stamp.** `run.json` records `platform: true` only when the pass ran a
  platform read (`--platform` with `--scans` selecting no platform scan reads
  nothing and stamps `false`); runs with and without the pass
  are not comparable (`platform pass changed ...`), and each gets its own
  history entry and trend line.
- **With `--delete-after`.** A clone is deleted after its platform read, not
  after its offline scan, so the pass can still read its remote. The platform
  pass starts only once the whole offline scan is done, so no clone is deleted
  before then: peak disk use is the whole estate, not flat. On an estate too
  large to hold at once, run without `--platform`, or in batches with
  `--from-list`.
- **Counts-only.** Every platform field is a count, share, flag or duration.
