# Pending version notes

During a version freeze, a PR that would bump a version counter adds one file here instead of editing the counter's history comment. Each PR writes its own file, so two PRs that change the same counter never conflict.

- **Name:** `<issue>.<counter>.md`, for example `927.artifact_schema.md`.
- **Content:** one or more markdown bullets, `- ` then the note, continuation lines indented two spaces. Write the note as it should read in the history comment; the fold copies each line verbatim behind `# `.
- **Counters:**

| Counter | Constant | History comment in |
|---------|----------|--------------------|
| `artifact_schema` | `ARTIFACT_SCHEMA_VERSION` | `skills/assess/scripts/lib/artifact_schema.py` |
| `assess_method` | `ASSESS_METHOD_VERSION` | `skills/assess/scripts/lib/artifact_schema.py` |
| `level_result` | `LEVEL_RESULT_VERSION` | `skills/assess/scripts/lib/level_tally.py` |
| `signals_schema` | `SIGNALS_SCHEMA_VERSION` | `skills/assess/scripts/lib/signals.py` |
| `levels_default` | `version` | `skills/assess/levels/default.toml` |
| `fleet_data` | `FLEET_DATA_VERSION` | `skills/assess/scripts/lib/fleet_data.py` |
| `achievements` | `ACHIEVEMENTS_VERSION` | `skills/assess/scripts/lib/fleet_achievements.py` |
| `fleet_row` | `FLEET_ROW_VERSION` | `skills/assess/scripts/lib/fleet.py` |

A note bumps its counter when folded. `932.fleet_row.md` records a key added without a bump, as `head_sha` and `dirty` were, and leaves the bump to the release PR. If that PR keeps `FLEET_ROW_VERSION` at 2, it removes the note before folding and writes the line into the history comment by hand.

The release PR runs `scripts/fold_changes.py`, which writes the notes into each history comment, sets each counter and deletes the notes. `scripts/tests/test_fold_changes.py` fails on a note that names an unknown counter or is not a bullet, and on an `Unreleased` block left in a history comment. Steps: [Releasing](../../../docs/contributing/release.md#version-freeze-notes).
