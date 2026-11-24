"""The artifact schema and method versions every /assess run stamps on its outputs.

A leaf module so the orchestrator and the lib modules that write artifacts
(the hotspot wiki pages, the index, the log, the findings snapshot) read one
constant each.

`ARTIFACT_SCHEMA_VERSION`, the run_id provenance schema stamped on every artifact a run
writes (run-context.json, the badge, the wiki pages); its version history is
`skills/assess/changes/history/artifact_schema.md`, newest entry first (#1262).
`complexity-treemap.py` carries a copy that
`tests/test_complexity_treemap_schema_versions.py` holds equal to it.
It re-exports `ASSESS_METHOD_VERSION`, the finding method version, from
`lib/assess_method.py`, which holds that counter (moved there in the 4.0.0 release, when the
two history comments together passed the file-size limit).

During a version freeze a change to this counter, or to another counter
`scripts/fold_changes.py` knows, adds a note file under `skills/assess/changes/`
instead of editing the history file; the release PR folds the notes into the history
files with `make fold` (#1011).
"""

from lib.assess_method import ASSESS_METHOD_VERSION as ASSESS_METHOD_VERSION

# Artifact schema version, stamped on every artifact the run produces
# (run-context.json, the badge, the wiki pages, complexity-stats). Distinct from
# the stats-layout `schema_version` (from #244) that versions the sidecar shape
# for diff comparability: this one versions the run_id provenance envelope.
# Bumped when the cross-artifact provenance schema changes shape in a way a
# consumer must adapt to.
# History: skills/assess/changes/history/artifact_schema.md, newest entry first.
ARTIFACT_SCHEMA_VERSION = "1.44.0"
