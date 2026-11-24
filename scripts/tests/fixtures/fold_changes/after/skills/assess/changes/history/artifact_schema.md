# `ARTIFACT_SCHEMA_VERSION` history

Fixture history of `ARTIFACT_SCHEMA_VERSION`, newest entry first.

<!-- make fold writes each new entry below this line, newest first. -->

1.43.0 (3.0.0 release): three changes since 1.42.0.
- run-context.json layer_grades gains instruction_presence (issue #821): root,
  nested_only or none. instruction_file_present is true for nested_only, and
  instructions_grade there is the best nested grade, where both read as no
  instruction file before; both are null when there is no root file and the
  nested_instructions scan did not run. gap_actions gains a layer_grades entry for
  nested_only and now sits after layer_grades in run-context.json.
- ci_checks basis, enforced_basis and lint_basis gain external_ci and
  external_ci_marker (issue #928): under --platform, more than half of the sampled
  merged pull requests carry a check of a known external CI system; offline, a root
  Prow OWNERS_ALIASES with no CI file. Either reads null in place of a false case.
  unread_ci_files counts more paths (LUCI .ci.yaml and infra/config/, every Azure
  Pipelines layout, Prow, Zuul, Drone, Woodpecker and others; lib/unread_ci.py).
  Additive: no existing key moves.
- run-context.json delivery gains sample_page_size (issue #995): the changes per
  heavy read the merged sample finished at, or null when none was made. Additive:
  no existing key moves.

- run-context.json coverage_gate gains enforced_meets_minimum and enforced_basis
  (issue #921): the threshold reading with an enforced CI run of the gate
  required. A gate's unit gains missed_ratio, and a JaCoCo MISSEDCOUNT /
  MISSEDRATIO maximum is reported as a gate. Additive: no existing key moves.
