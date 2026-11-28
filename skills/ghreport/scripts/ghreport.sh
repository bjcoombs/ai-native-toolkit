#!/bin/bash
set -euo pipefail

# ===================================================================
# ghreport.sh - read-only org repo state report
#
# Reuses ghsync's repo discovery (via `ghsync --porcelain`) but, instead of
# cloning, queries each repo's remote GitHub state and rolls it into a report:
#   - open PRs (count, drafts noted)
#   - CI on the default branch (latest completed run per workflow: pass/fail/none)
#   - open security alerts: Dependabot + code-scanning + secret-scanning
#   - branch protection on the default branch
#
# The no-access rule: security-alert and branch-protection endpoints need admin.
# A denied (403/404) call is reported as "no-access" / "unknown", NEVER as a
# clean zero - reporting denied access as "no problems" would be a lie.
#
# Org detection mirrors ghsync: defaults to the basename of --root / cwd.
#
# GitLab (--provider gitlab): discovery runs through ghsync's GitLab provider
# (every group you belong to on --host, nested subgroups, shared projects and
# your direct project memberships, or --group's subtree). Per project, through
# `glab api`: open merge requests, the latest finished (success or failed)
# pipeline on the default branch, open vulnerabilities (a paid-tier endpoint,
# so no-access is the common case) and default-branch protection.
# Any failed call is no-access / unknown, never 0.
#
# Org / group settings: one section per GitHub org (`orgs/:org`,
# `orgs/:org/rulesets`) or GitLab group (`groups/:id`, `groups/:id/push_rule`;
# the --group, else each top-level group you belong to): two_factor,
# push_rules, default_branch_protection and rulesets, the same field names on
# both providers. Each is the setting's value, `off`, or `unknown` with a
# reason; an unreadable value is never `off`. Replay reads them from
# <render-dir>/settings/*.json, which the per-repo glob never matches.
#
# Capture then replay: a live run with --keep-fragments DIR keeps its per-repo
# fragments, and the settings/ subdirectory, in DIR in exactly the layout
# --render-dir reads, so `--render-dir DIR` later renders the same report
# without any API call. A finished capture ends with a CAPTURE_COMPLETE marker
# file (not *.json, so no fragment reader takes it as a row); replaying a dir
# without one warns that the fragment set may be partial.
#
# Usage: ./ghreport.sh [--provider github|gitlab] [--root DIR] [--limit N]
#                      [--quiet] [--no-file] [--keep-fragments DIR]
#                      [--render-dir DIR]
#                      GitHub: [--org NAME]   GitLab: [--host HOST] [--group PATH]
#   --provider P      github (default) or gitlab
#   --org NAME        Org / account to report on (default: basename of --root/cwd)
#   --host HOST       GitLab host (default: gitlab.com)
#   --group PATH      GitLab: report on this group's subtree only
#   --root DIR        Org root; where the report file lands (default: cwd)
#   --limit N         Only the first N repos (quick test)
#   --quiet           Suppress per-repo progress dots
#   --no-file         Terminal only; do not write the markdown file
#   --render-dir DIR  Render from a dir of pre-collected per-repo JSON fragments
#                     (skips discovery + querying; replay / testing)
#   --keep-fragments DIR  Live run: keep the per-repo fragments and settings/
#                     in DIR (new or empty) for a later --render-dir DIR replay
# ===================================================================

# Under Git Bash on Windows a native jq.exe writes CRLF line endings, and every
# name read back from it would end in a carriage return. --binary keeps LF.
# A POSIX jq 1.6 (Linux, macOS, or an MSYS2 or Cygwin package) rejects the
# flag, so the wrapper is defined only on Windows and only when jq accepts it.
case "${OSTYPE:-}" in
    msys* | cygwin*)
        if command jq --binary -n 'empty' >/dev/null 2>&1; then
            jq() { command jq --binary "$@"; }
            export -f jq
        fi
        ;;
esac

ORG=""
ROOT=""
LIMIT=0
QUIET_MODE=false
NO_FILE=false
RENDER_DIR=""
KEEP_DIR=""
PROVIDER=github
GITLAB_HOST=""
GITLAB_GROUP=""
github_only_flags=()
gitlab_only_flags=()

while [[ $# -gt 0 ]]; do
  case $1 in
    --provider) PROVIDER=$2; shift 2 ;;
    --provider=*) PROVIDER=${1#*=}; shift ;;
    --org) ORG=$2; github_only_flags+=(--org); shift 2 ;;
    --org=*) ORG=${1#*=}; github_only_flags+=(--org); shift ;;
    --host) GITLAB_HOST=$2; gitlab_only_flags+=(--host); shift 2 ;;
    --host=*) GITLAB_HOST=${1#*=}; gitlab_only_flags+=(--host); shift ;;
    --group) GITLAB_GROUP=$2; gitlab_only_flags+=(--group); shift 2 ;;
    --group=*) GITLAB_GROUP=${1#*=}; gitlab_only_flags+=(--group); shift ;;
    --root) ROOT=$2; shift 2 ;;
    --root=*) ROOT=${1#*=}; shift ;;
    --limit) LIMIT=$2; shift 2 ;;
    --limit=*) LIMIT=${1#*=}; shift ;;
    --quiet) QUIET_MODE=true; shift ;;
    --no-file) NO_FILE=true; shift ;;
    --render-dir) RENDER_DIR=$2; shift 2 ;;
    --render-dir=*) RENDER_DIR=${1#*=}; shift ;;
    --keep-fragments) KEEP_DIR=$2; shift 2 ;;
    --keep-fragments=*) KEEP_DIR=${1#*=}; shift ;;
    -h|--help)
      # The header runs from the first rule line to the next one.
      awk '/^# ====/ { print; if (++rules == 2) exit; next } rules == 1' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Usage: $0 [--provider github|gitlab] [--root DIR] [--limit N] [--quiet] [--no-file] [--keep-fragments DIR] [--render-dir DIR]" >&2
      echo "       GitHub: [--org NAME]   GitLab: [--host HOST] [--group PATH]" >&2
      exit 1
      ;;
  esac
done

# Reject flags that belong to the other provider rather than ignoring them,
# matching ghsync.
case "$PROVIDER" in
    github)
        if [ ${#gitlab_only_flags[@]} -gt 0 ]; then
            echo "Error: ${gitlab_only_flags[0]} applies to --provider gitlab only" >&2
            exit 1
        fi
        required_cmds=(gh jq)
        ;;
    gitlab)
        if [ ${#github_only_flags[@]} -gt 0 ]; then
            echo "Error: ${github_only_flags[0]} applies to --provider github only" >&2
            exit 1
        fi
        # Normalise the host as ghsync does: ghreport calls `glab api
        # --hostname` itself, so a URL-form --host must not reach it.
        GITLAB_HOST="${GITLAB_HOST:-gitlab.com}"
        GITLAB_HOST=${GITLAB_HOST#http://}
        GITLAB_HOST=${GITLAB_HOST#https://}
        GITLAB_HOST=${GITLAB_HOST%%/*}
        if [ -z "$GITLAB_HOST" ]; then
            echo "Error: --host is empty" >&2
            exit 1
        fi
        # glab itself is checked, with auth, by ghsync's preflight.
        required_cmds=(jq)
        ;;
    *)
        echo "Error: unknown provider '$PROVIDER' (expected github or gitlab)" >&2
        exit 1
        ;;
esac

for cmd in "${required_cmds[@]}"; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Error: $cmd not found. Install it (e.g. brew install $cmd)." >&2
        exit 1
    fi
done

ROOT="${ROOT:-$PWD}"
if [[ ! -d "$ROOT" ]]; then
    echo "Error: root directory does not exist: $ROOT" >&2
    exit 1
fi
ROOT="$(cd "$ROOT" && pwd)"
if [ "$PROVIDER" = gitlab ]; then
    # The report names the host and, when narrowed, the group.
    ORG="$GITLAB_HOST${GITLAB_GROUP:+/$GITLAB_GROUP}"
else
    ORG="${ORG:-$(basename "$ROOT")}"
fi

# ---- helpers ---------------------------------------------------------------
# The functions live in files beside this script, one per seam:
#   ghreport_github.sh    per-repo GitHub worker (query_repo)
#   ghreport_gitlab.sh    per-project GitLab worker and the glab api helpers
#   ghreport_settings.sh  org / group settings: the queries and their rendering
#   ghreport_render.sh    the assembler: tables, attention lists, the report
# A replay sources the last two. A live run sources all four once ghsync is
# found, so a missing ghsync stays the first error a live run reports.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- render-only (replay / testing) ----------------------------------------
if [ -n "$RENDER_DIR" ] && [ -n "$KEEP_DIR" ]; then
    echo "Error: --keep-fragments captures a live run; it cannot be combined with --render-dir" >&2
    exit 1
fi
if [ -n "$RENDER_DIR" ]; then
    if [ ! -d "$RENDER_DIR" ]; then
        echo "Error: --render-dir not a directory: $RENDER_DIR" >&2
        exit 1
    fi
    if [ ! -f "$RENDER_DIR/CAPTURE_COMPLETE" ]; then
        echo "Warning: $RENDER_DIR has no CAPTURE_COMPLETE marker: if it is an interrupted --keep-fragments capture, this report may be partial; delete it and capture again." >&2
    fi
    # shellcheck source=ghreport_settings.sh
    source "$SCRIPT_DIR/ghreport_settings.sh"
    # shellcheck source=ghreport_render.sh
    source "$SCRIPT_DIR/ghreport_render.sh"
    assemble "$RENDER_DIR"
    exit 0
fi

# A capture directory must be new or empty: a replay reads every *.json in it,
# so a stale fragment from another run would become a report row. Checked
# before discovery, so a bad path fails before any API call.
if [ -n "$KEEP_DIR" ]; then
    if [ -e "$KEEP_DIR" ] && [ ! -d "$KEEP_DIR" ]; then
        echo "Error: --keep-fragments not a directory: $KEEP_DIR" >&2
        exit 1
    fi
    if [ -d "$KEEP_DIR" ] && [ -n "$(ls -A "$KEEP_DIR")" ]; then
        echo "Error: --keep-fragments directory is not empty: $KEEP_DIR" >&2
        exit 1
    fi
    mkdir -p "$KEEP_DIR" || {
        echo "Error: cannot create --keep-fragments directory: $KEEP_DIR" >&2
        exit 1
    }
fi

# ---- discovery (delegated to ghsync --porcelain) ---------------------------
GHSYNC_SH="$SCRIPT_DIR/../../ghsync/scripts/ghsync.sh"
if [ ! -f "$GHSYNC_SH" ]; then
    GHSYNC_SH="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/skills}/ghsync/scripts/ghsync.sh"
fi
if [ ! -f "$GHSYNC_SH" ]; then
    echo "Error: ghsync.sh not found (looked next to this script and under CLAUDE_PLUGIN_ROOT / ~/.claude/skills)." >&2
    exit 1
fi
# shellcheck source=ghreport_github.sh
source "$SCRIPT_DIR/ghreport_github.sh"
# shellcheck source=ghreport_gitlab.sh
source "$SCRIPT_DIR/ghreport_gitlab.sh"
# shellcheck source=ghreport_settings.sh
source "$SCRIPT_DIR/ghreport_settings.sh"
# shellcheck source=ghreport_render.sh
source "$SCRIPT_DIR/ghreport_render.sh"

echo "Discovering repos in $ORG ..." >&2
# Capture discovery output and exit status separately. Process substitution
# would discard ghsync's exit code (it is not a pipeline, so pipefail does not
# apply), masking an auth/deps failure as "no repositories". Capturing lets us
# distinguish a genuine empty org from a failed discovery.
if [ "$PROVIDER" = gitlab ]; then
    # ghsync's preflight checks glab and its auth for the host, and its one
    # error line already names the host, so a failure adds nothing here.
    gl_args=(--provider gitlab --host "$GITLAB_HOST" --porcelain)
    [ -n "$GITLAB_GROUP" ] && gl_args+=(--group "$GITLAB_GROUP")
    disc_out=$(bash "$GHSYNC_SH" "${gl_args[@]}") || exit 1
else
    disc_out=$(bash "$GHSYNC_SH" --porcelain --org "$ORG" --root "$ROOT") || {
        echo "Error: repo discovery via ghsync failed (see its output above)." >&2
        exit 1
    }
fi
repos=()
# A read loop rather than mapfile, which bash 3.2 (macOS /bin/bash) lacks.
if [ -n "$disc_out" ]; then
    while IFS= read -r repo; do
        repos+=("$repo")
    done <<< "$disc_out"
fi
if [ ${#repos[@]} -eq 0 ]; then
    echo "No repositories discovered for $ORG (the org is empty or nothing is accessible)." >&2
    exit 0
fi

# --limit caps the expensive querying (~5 API calls per repo), so apply it
# here rather than passing it to ghsync: ghsync's --porcelain implies
# --list-repos, which exits before ghsync applies its own --limit, so a
# passed-through limit would be silently ignored.
if [ "$LIMIT" -gt 0 ] && [ "$LIMIT" -lt ${#repos[@]} ]; then
    echo "Limiting to the first $LIMIT of ${#repos[@]} discovered repo(s)." >&2
    repos=("${repos[@]:0:$LIMIT}")
fi
echo "Querying state for ${#repos[@]} repo(s) ..." >&2

# Fragments go to a temp dir that is removed on exit, or with
# --keep-fragments straight into the capture dir, which is kept.
if [ -n "$KEEP_DIR" ]; then
    TMP_DIR=$KEEP_DIR
else
    TMP_DIR=$(mktemp -d)
    trap 'rm -rf "$TMP_DIR"' EXIT
fi

# Org / group settings, into a subdirectory the per-repo glob skips. A
# settings query that fails outright degrades to all-unknown for its scope
# rather than aborting the per-repository report. Called on the left of `||`,
# the query functions run with errexit off: a step inside them must turn its
# own failure into an `unknown` with a reason (as each API read does), never
# fall through to a verdict such as `off`.
mkdir -p "$TMP_DIR/settings"
if [ "$PROVIDER" = gitlab ]; then
    if settings_groups=$(gitlab_settings_groups); then
        s_i=0
        while IFS= read -r settings_group; do
            [ -n "$settings_group" ] || continue
            query_group_settings_gitlab "$settings_group" "$TMP_DIR/settings/$s_i.json" \
                || settings_all_unknown "$settings_group" "settings query failed" "$TMP_DIR/settings/$s_i.json"
            s_i=$((s_i + 1))
        done <<< "$settings_groups"
    else
        settings_all_unknown "$GITLAB_HOST" "group list unreadable" "$TMP_DIR/settings/0.json"
    fi
else
    query_org_settings_github "$TMP_DIR/settings/org.json" \
        || settings_all_unknown "$ORG" "settings query failed" "$TMP_DIR/settings/org.json"
    add_org_teams_github "$TMP_DIR/settings/org.json" || true
fi

i=0
for repo in "${repos[@]}"; do
    # Fragment filenames must be flat. GitHub names have no slashes
    # (single-org); a GitLab path does, and no flattening of it is
    # collision-free (`grp/a__b` and `grp__a/b`), so GitLab fragments are
    # named by index. The report reads the path from inside each fragment.
    if [ "$PROVIDER" = gitlab ]; then
        query_project_gitlab "$repo" "$TMP_DIR/$i.json" &
    else
        query_repo "$repo" "$TMP_DIR/$repo.json" &
    fi
    if (( ++i % 5 == 0 )); then
        wait
    fi
    [ "$QUIET_MODE" = false ] && printf "." >&2
done
wait
[ "$QUIET_MODE" = false ] && printf "\n" >&2

assemble "$TMP_DIR"
if [ -n "$KEEP_DIR" ]; then
    # Written last: a capture that stopped earlier has no marker, and a
    # replay of it warns.
    date -u +%Y-%m-%dT%H:%M:%SZ > "$KEEP_DIR/CAPTURE_COMPLETE"
    echo "Fragments kept in: $KEEP_DIR (replay with --render-dir $KEEP_DIR)" >&2
fi
