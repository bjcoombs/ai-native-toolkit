# Bumped when the document's envelope (stamp fields, top-level keys) changes,
# or when a scanner change alters when an existing signal reads true or null
# without a new signal name (the fleet comparison convention,
# lib/fleet_scanner_diff.py). Per-scan block shapes are not versioned here.
# 3 (3.0.0 release): one change since 2.
# - layer_grades.instruction_file_present reads true, and instructions_grade a letter,
#   for a repository whose only instruction files are nested (instruction_presence
#   nested_only, issue #821). instruction_file_present, documented as never null before,
#   now reads null (with instruction_presence) when there is no root file and the
#   nested_instructions scan did not run.
SIGNALS_SCHEMA_VERSION = 3

