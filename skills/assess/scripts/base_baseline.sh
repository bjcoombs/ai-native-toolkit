#!/usr/bin/env bash
# Compare a change with its target's gate baseline, not its own (#1152).
#
# Usage: base_baseline.sh <project dir> <target branch> [<base commit sha>]
#
# The gate-loss check reads the Step gates held at the committed baseline,
# .assess/prior-level.json. Read from the change's own tree, a change could
# switch off its own gate: delete the file (no baseline, so it passes), or run
# /assess on the branch and commit a baseline that already holds the loss.
# Before the render, this script replaces the working copy's file with the
# target's, so the change is measured against what the target held:
#
# - The target is the base commit the host fixed for the change
#   (CI_MERGE_REQUEST_DIFF_BASE_SHA on GitLab, pull_request.base.sha on
#   GitHub). On a fork's merge request the pipeline runs in the fork, where
#   origin/<branch> is a branch the author controls; the host's SHA is not.
#   Only without a usable SHA does it fall back to refs/remotes/origin/<branch>
#   (fetched when missing), with a notice. A target that cannot be read leaves
#   the working copy as it is, with a notice (infrastructure, never a red job).
# - No baseline on the target removes the working copy's file: no baseline yet.
# - A change that edits or deletes .assess/prior-level.json gets a warning line;
#   with no merge base to diff against (a shallow checkout) a notice says so instead.
#
# Called by gitlab_gate.sh and by action.yml whenever a target is known, with
# or without the gate-loss opt-in (config.toml can turn it on). It always exits 0.
# Tests: skills/assess/tests/test_gitlab_gate.py.
set -u

PROJECT="$1"
TARGET="${2:-}"
BASE_SHA="${3:-}"
FILE=".assess/prior-level.json"

notice() { echo "assess-gate: $*"; }

REF=""
NAME=""
if [ -n "$BASE_SHA" ]; then
    if printf '%s' "$BASE_SHA" | grep -Eq '^[0-9a-f]{7,64}$' \
        && git -C "$PROJECT" rev-parse -q --verify "$BASE_SHA^{commit}" >/dev/null 2>&1; then
        REF="$BASE_SHA"
        NAME="the target's base commit ${BASE_SHA:0:12}"
    else
        notice "the target's base commit could not be read, so the gate falls back to the target branch."
    fi
fi
if [ -z "$REF" ]; then
    [ -n "$TARGET" ] || exit 0
    if ! git check-ref-format --branch "$TARGET" >/dev/null 2>&1; then
        notice "the target branch name could not be read, so the gate compares with the baseline in this branch."
        exit 0
    fi
    [ -n "$BASE_SHA" ] || notice "no base commit was given, so the gate reads the baseline from the target branch $TARGET."
    REF="refs/remotes/origin/$TARGET"
    NAME="$TARGET"
    if ! git -C "$PROJECT" rev-parse -q --verify "$REF^{commit}" >/dev/null 2>&1; then
        git -C "$PROJECT" fetch -q --no-tags origin "+refs/heads/$TARGET:$REF" >/dev/null 2>&1
    fi
    if ! git -C "$PROJECT" rev-parse -q --verify "$REF^{commit}" >/dev/null 2>&1; then
        notice "the target branch $TARGET could not be read, so the gate compares with the baseline in this branch."
        exit 0
    fi
fi
git -C "$PROJECT" diff --quiet "$REF...HEAD" -- "$FILE" >/dev/null 2>&1
case $? in
    0) ;;
    1) notice "warning: this change edits $FILE. The gate compares with the baseline on $NAME instead." ;;
    *) notice "could not compare this change with $NAME (no merge base, such as a shallow checkout), so edits to $FILE are not reported." ;;
esac
mkdir -p "$PROJECT/.assess"
if git -C "$PROJECT" cat-file -e "$REF:$FILE" 2>/dev/null; then
    git -C "$PROJECT" show "$REF:$FILE" >"$PROJECT/$FILE"
    notice "comparing with the gate baseline committed on $NAME."
else
    rm -f "$PROJECT/$FILE"
    notice "$NAME has no committed $FILE, so there is no baseline yet."
fi
exit 0
