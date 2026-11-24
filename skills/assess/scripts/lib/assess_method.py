"""The finding method version every /assess run stamps: which rules decide the findings.

`ASSESS_METHOD_VERSION` is an integer, independent of plugin releases and of
`ARTIFACT_SCHEMA_VERSION`, bumped by one in any change that moves which findings a run
produces on unchanged code. `assess_core` stamps it in run-context.json and in
`findings.json`. Its version history is `skills/assess/changes/history/assess_method.md`,
newest entry first (#1262). `lib.artifact_schema`
re-exports it, so `from lib.artifact_schema import ASSESS_METHOD_VERSION` still works.

During a version freeze a change to the counter adds a note file under
`skills/assess/changes/` instead of editing the history file; the release PR folds the
notes in with `make fold` (#1011). It moved here from `artifact_schema.py` in the 4.0.0
release, when the two history comments together passed the file-size limit.
"""

# The finding method version: which rules decide the findings a run produces.
# Independent of plugin releases and of ARTIFACT_SCHEMA_VERSION (which versions
# artifact shape, not finding semantics). Bump it, by one, in any change that
# moves which findings a run produces on unchanged code; the CI gate compares it
# with the version in the committed .assess/findings.json and explains the
# finding moves instead of failing on them unexplained.
# History: skills/assess/changes/history/assess_method.md, newest entry first.
ASSESS_METHOD_VERSION = 39
