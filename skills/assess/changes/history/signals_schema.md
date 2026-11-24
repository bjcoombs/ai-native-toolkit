# `SIGNALS_SCHEMA_VERSION` history

The version history of `SIGNALS_SCHEMA_VERSION` in `skills/assess/scripts/lib/signals.py`, newest entry first.
The release PR's `make fold` writes each new entry at the top from the notes in
[`skills/assess/changes/`](../README.md).

<!-- make fold writes each new entry below this line, newest first. -->

4 (4.0.0 release): one change, without a bump.
- a new network block, `github_secret_scanning` (platform pass only), three booleans.

4 (4.0.0 release): one change, without a bump.
- a new headless block, `ci_model_jobs` (issue #1242): paths dropped in counts-only output or
  hashed under `--hash-paths`, job names dropped, signs, triggers, gates and states kept as
  tokens.

4 (4.0.0 release): 12 changes since 3.
- observability.rung can read differently on unchanged code (issue #823): standard-library
  loggers in production source and the Rust `tracing` / `log` crates now count as
  instrumented, manifest names match whole, and test and fixture paths no longer credit
  instrumentation or a runbook. scans.observability.instrumented gains `kinds`.
- the stamp gains ``scope`` (#866): the folder ``--scope`` measured, repository-relative, null
  for the whole checkout; counts-only output writes ``set`` in its place, or its salted hash
  under ``--hash-paths``, so null always means the whole checkout. ``--units`` adds the ``units`` and ``unit_readings`` blocks (each unit's
  own readings); a unit's folder is dropped in counts-only output, hashed under
  ``--hash-paths`` (privacy class ``hashed_path``). A run without the new flags reads as before.
- the headless `instruction_placement` block gains `est_tokens` on each file record and
  `thresholds.loaded_in_full_min_tokens` (issue #1045), and `loaded_in_full` takes a file
  over 5000 estimated tokens as well as one over 200 lines, most tokens first. Additive.
- a new headless block, `skill_disclosure` (issue #1045), with paths dropped in counts-only
  output or hashed under `--hash-paths`, and rule names kept as tokens.
- new headless blocks `contributor_files` (issue #1045), run after the headless
  `archetype`, and `org_security_policy`, a `--platform` network read (`network` without it);
  paths dropped in counts-only output or hashed under `--hash-paths`.
- a new headless block, `skill_script_tests` (issue #1045), with paths dropped in
  counts-only output or hashed under `--hash-paths`.
- a new headless block, `skill_personal_data` (issue #1045): paths dropped in counts-only
  output or hashed under `--hash-paths`, kinds kept as tokens, no matched text.
- Under `--units`, each `units.units[]` entry gains `kind` (`package`, `skill`, `agents`,
  `commands`; issue #1098), kept as a token in counts-only output. Additive.
- Under `--units`, each `unit_readings.units[]` entry gains `kind` (issue #1098), the unit's
  kind token, kept in counts-only output. Additive.
- scans.ci_checks.includes[].status gains `toolkit_gate` (issue #1152): the toolkit's own
  regression gate template, known to run no project tests; it counts in `resolved_includes`.
- The stamp gains `managed_settings` (issue #1153): whether `--managed-settings` read the
  machine's Claude Code managed settings. The `agent_ops` block gains `managed`,
  `settings[].default_mode`, `summary.permissions_source` and
  `summary.permissions_encoded_reason`, kept as tokens and counts in counts-only output;
  `summary.permissions_encoded` may be null under the option.
- a new headless block, `secrets_guard` (issue #1173): file paths dropped in counts-only
  output or hashed under `--hash-paths`, tool and source tokens kept.

3 (3.0.0 release): one change since 2.
- layer_grades.instruction_file_present reads true, and instructions_grade a letter,
  for a repository whose only instruction files are nested (instruction_presence
  nested_only, issue #821). instruction_file_present, documented as never null before,
  now reads null (with instruction_presence) when there is no root file and the
  nested_instructions scan did not run.
