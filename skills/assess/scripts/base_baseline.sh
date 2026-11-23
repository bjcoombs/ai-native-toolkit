#!/usr/bin/env bash
# Compare a change with its target branch's gate baseline, not its own (#1152).
#
# Usage: base_baseline.sh <project dir> <target branch>
#
# The gate-loss check reads the Step gates held at the committed baseline,
# .assess/prior-level.json. Read from the change's own tree, a change could
# switch off its own gate: delete the file (no baseline, so it passes), or run
# /assess on the branch and commit a baseline that already holds the loss.
# Before the render, this script replaces the working copy's file with the
# target branch's, so the change is measured against what the target held:
#
# - The target is read from refs/remotes/origin/<branch>, fetched when the
#   checkout lacks it. A target that cannot be read leaves the working copy as
#   it is, with a notice (infrastructure, never a red job).
# - No baseline on the target removes the working copy's file: no baseline yet.
# - A change that edits or deletes .assess/prior-level.json gets a warning line.
#
# Called by templates/assess-gate.yml (through gitlab_gate.sh) and by
# action.yml when fail_on_gate_loss is on. It always exits 0.
# Tests: skills/assess/tests/test_gitlab_gate.py.
set -u

PROJECT="$1"
TARGET="${2:-}"
FILE=".assess/prior-level.json"

notice() { echo "assess-gate: $*"; }

[ -n "$TARGET" ] || exit 0
if ! git check-ref-format --branch "$TARGET" >/dev/null 2>&1; then
    notice "the target branch name could not be read, so the gate compares with the baseline in this branch."
    exit 0
fi
REF="refs/remotes/origin/$TARGET"
if ! git -C "$PROJECT" rev-parse -q --verify "$REF^{commit}" >/dev/null 2>&1; then
    git -C "$PROJECT" fetch -q --no-tags origin "+refs/heads/$TARGET:$REF" >/dev/null 2>&1
fi
if ! git -C "$PROJECT" rev-parse -q --verify "$REF^{commit}" >/dev/null 2>&1; then
    notice "the target branch $TARGET could not be read, so the gate compares with the baseline in this branch."
    exit 0
fi
if ! git -C "$PROJECT" diff --quiet "$REF...HEAD" -- "$FILE" >/dev/null 2>&1; then
    notice "warning: this change edits $FILE. The gate compares with the baseline on $TARGET instead."
fi
mkdir -p "$PROJECT/.assess"
if git -C "$PROJECT" cat-file -e "$REF:$FILE" 2>/dev/null; then
    git -C "$PROJECT" show "$REF:$FILE" >"$PROJECT/$FILE"
    notice "comparing with the gate baseline committed on $TARGET."
else
    rm -f "$PROJECT/$FILE"
    notice "$TARGET has no committed $FILE, so there is no baseline yet."
fi
exit 0
