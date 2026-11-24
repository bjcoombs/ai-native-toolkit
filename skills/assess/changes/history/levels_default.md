# Default maturity model `version` history

The version history of `version` in `skills/assess/levels/default.toml`, newest entry first.
The release PR's `make fold` writes each new entry at the top from the notes in
[`skills/assess/changes/`](../README.md).

<!-- make fold writes each new entry below this line, newest first. -->

default-2026.11.24 (4.0.0 release): two changes, without a bump.
- the Layer 0, Layer 4 and Layer 8 comments name the #922 rules; the criteria text is
  unchanged. layer-0-instructions.2 reads unmet on a named Make target that runs
  nothing, layer-4-architecture.1 on an architecture doc naming one module path, and
  layer-8-agent-ops.2 on settings hooks that run only true, :, exit 0 or nothing.
- a `model-ci-gated` row (issue #1242): level 1 reads `ci_model_jobs.ungated eq 0`, tagged
  `governance`, unknown (`no_model_jobs`) with no model-backed CI job. It gates no Step.

default-2026.11.24 (4.0.0 release): ten changes since default-2026.11.23.
- layer-4-architecture.2 reads stats_summary.loc_by_class.source.p95, the 95th-percentile
  file size of production source only, where it read stats_summary.loc.p95 over every scored
  file (#820). Test, fixture, example, vendored and generated paths (lib/path_classes.py) are
  set aside, so a repository can move either way: many small fixtures no longer hide a large
  source file, and large tests no longer fail clean source. With no source file scored the
  condition reads not measured (no_source_files), where it read no_scored_files.
- `layer-7-review.2` (wide review on merged pull requests) requires `layer-7-review.1` (review
  evidence present), so it cannot be met and counted while review evidence is unmet (#934).
- a `skill-disclosure` row (issue #1045): level 1 reads
  `skill_disclosure.files_breaking eq 0`, tagged `agent_context`, unknown (`no_skill_files`)
  with no skill or agent file. Read per skill unit; it gates no Step.
- a `contributor-files` row (issue #1045): level 1 reads `contributor_files.guide.present`
  and `contributor_files.policy.present` `eq true`,
  tagged `docs`, unknown (`knowledge_base`) for a knowledge base. It gates no Step.
- a `skill-script-tests` row (issue #1045): level 1 reads
  `skill_script_tests.skills_untested eq 0`, tagged `behaviour`, unknown
  (`no_skill_scripts`) with no skill holding a script. Read per skill unit; it gates no Step.
- a `skill-personal-data` row (issue #1045): level 1 reads
  `skill_personal_data.files_with_personal_data eq 0`, tagged `governance`, unknown
  (`no_skill_files`) with no skill or agent file. Read per skill unit; it gates no Step.
- Steps read gates only (issue #1083): the `[[steps]]` tables drop `threshold` (0, 1, 3, 5),
  so a Step needs its gates and nothing else and a condition added to the model never moves
  a Step. A repository held back only by the count now reaches the Step its gates allow.
  A `--levels` model with a `threshold` key fails to load with a message naming the removal.
- A `--levels` model whose Step after 1 adds no gate over the Step below now fails to load
  (issue #1083): with no count threshold, gates are all that tell two Steps apart, so such a
  Step would always be held with the one below. Breaking for custom models; the default
  model adds one gate per Step and is unaffected.
- Row titles name the practice only (issue #1099): "Layer 0: agent instructions" reads
  "Agent instructions", and so on for the nine former layer rows. No row id, condition id,
  gate or criterion changes, so results are the same; only the printed titles move.
- a `secrets-guard` row (issue #1173): level 1 reads `secrets_guard.configured`, level 2
  `secrets_guard.enforcement.present`, both `eq true`, tagged `governance`.

default-2026.11.23 (3.0.0 release): three changes since default-2026.11.22.
- the Layer 0 instructions comment changed with #821; the criterion did not.
- every condition gains "truth" (#843); no criterion, grade or Step changes.
- layer-5-ci.2 reads not measured (no_manifests) on a repository with no
  dependency manifest, where it read met (#927, the empty-set rule). The
  criteria text is unchanged; the Layer 5 comment names the rule.
