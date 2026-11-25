# Pending version notes

During a version freeze, a PR that would bump a version counter adds one file here instead of editing the counter's history file. Each PR writes its own file, so two PRs that change the same counter never conflict.

- **Name:** `<issue>.<counter>.md`, for example `927.artifact_schema.md`.
- **Content:** one or more markdown bullets, `- ` then the note, continuation lines indented two spaces. Write the note as it should read in the history file; the fold copies each line verbatim.
- **Counters:**

| Counter | Constant | Assigned in | History |
|---------|----------|-------------|---------|
| `artifact_schema` | `ARTIFACT_SCHEMA_VERSION` | `skills/assess/scripts/lib/artifact_schema.py` | [`history/artifact_schema.md`](history/artifact_schema.md) |
| `assess_method` | `ASSESS_METHOD_VERSION` | `skills/assess/scripts/lib/assess_method.py` | [`history/assess_method.md`](history/assess_method.md) |
| `level_result` | `LEVEL_RESULT_VERSION` | `skills/assess/scripts/lib/scoring/level_tally.py` | [`history/level_result.md`](history/level_result.md) |
| `signals_schema` | `SIGNALS_SCHEMA_VERSION` | `skills/assess/scripts/lib/signals.py` | [`history/signals_schema.md`](history/signals_schema.md) |
| `levels_default` | `version` | `skills/assess/levels/default.toml` | [`history/levels_default.md`](history/levels_default.md) |
| `fleet_data` | `FLEET_DATA_VERSION` | `skills/assess/scripts/lib/fleet/fleet_data.py` | [`history/fleet_data.md`](history/fleet_data.md) |
| `achievements` | `ACHIEVEMENTS_VERSION` | `skills/assess/scripts/lib/fleet/fleet_achievements.py` | [`history/achievements.md`](history/achievements.md) |
| `fleet_row` | `FLEET_ROW_VERSION` | `skills/assess/scripts/lib/fleet/runner.py` | [`history/fleet_row.md`](history/fleet_row.md) |

A note bumps its counter when folded, except `fleet_row`: the fleet history readers accept only the current row version, so the fold refuses to pick its value. The release PR passes `--set fleet_row=3` to bump it (and moves the readers), or `--set fleet_row=2` to record the notes without a bump, as `head_sha` and `dirty` were added.

Each counter's history is `history/<counter>.md`, newest entry first. Above each constant one comment line points to that file; the history no longer sits in comments in the source file, so a source file does not grow at each release (#1262).

The release PR runs `scripts/fold_changes.py`, which writes each counter's notes as one entry at the top of its history file, below the marker line, sets each counter and deletes the notes. `scripts/tests/test_fold_changes.py` fails on a note that names an unknown counter or is not a bullet, on an `Unreleased` block in a source or history file, on a counter without its pointer line, and on a history entry left as a comment above a counter. Steps: [Releasing](../../../docs/contributing/release.md#version-freeze-notes).
