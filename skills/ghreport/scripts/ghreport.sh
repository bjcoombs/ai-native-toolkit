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
# Usage: ./ghreport.sh [--org NAME] [--root DIR] [--limit N] [--quiet]
#                      [--no-file] [--render-dir DIR]
#   --org NAME        Org / account to report on (default: basename of --root/cwd)
#   --root DIR        Org root; where the report file lands (default: cwd)
#   --limit N         Only the first N repos (quick test)
#   --quiet           Suppress per-repo progress dots
#   --no-file         Terminal only; do not write the markdown file
#   --render-dir DIR  Render from a dir of pre-collected per-repo JSON fragments
#                     (skips discovery + querying; replay / testing)
# ===================================================================

ORG=""
ROOT=""
LIMIT=0
QUIET_MODE=false
NO_FILE=false
RENDER_DIR=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --org) ORG=$2; shift 2 ;;
    --org=*) ORG=${1#*=}; shift ;;
    --root) ROOT=$2; shift 2 ;;
    --root=*) ROOT=${1#*=}; shift ;;
    --limit) LIMIT=$2; shift 2 ;;
    --limit=*) LIMIT=${1#*=}; shift ;;
    --quiet) QUIET_MODE=true; shift ;;
    --no-file) NO_FILE=true; shift ;;
    --render-dir) RENDER_DIR=$2; shift 2 ;;
    --render-dir=*) RENDER_DIR=${1#*=}; shift ;;
    -h|--help)
      sed -n '4,28p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Usage: $0 [--org NAME] [--root DIR] [--limit N] [--quiet] [--no-file] [--render-dir DIR]" >&2
      exit 1
      ;;
  esac
done

for cmd in gh jq; do
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
ORG="${ORG:-$(basename "$ROOT")}"

# ---- per-repo worker --------------------------------------------------------
# Queries one repo's remote state and writes a single JSON fragment. Every
# query is individually guarded so a denial maps to no-access/unknown rather
# than aborting; a repo we can't even read metadata for is marked unreachable.
query_repo() {
    local repo=$1 out=$2

    local repo_json
    if ! repo_json=$(gh api "repos/$ORG/$repo" 2>/dev/null); then
        jq -n --arg repo "$repo" \
            '{repo:$repo, unreachable:true, default_branch:"?", open_prs:0, draft_prs:0,
              ci:"none", failing_workflows:"",
              dependabot:"no-access", code_scanning:"no-access", secret_scanning:"no-access",
              protection:"unknown"}' > "$out"
        return
    fi

    local default_branch
    default_branch=$(echo "$repo_json" | jq -r '.default_branch // "main"')

    # Open PRs (count + drafts).
    local pr_json open_prs draft_prs
    pr_json=$(gh api "repos/$ORG/$repo/pulls?state=open&per_page=100" 2>/dev/null || echo '[]')
    open_prs=$(echo "$pr_json" | jq 'length' 2>/dev/null || echo 0)
    draft_prs=$(echo "$pr_json" | jq '[.[] | select(.draft==true)] | length' 2>/dev/null || echo 0)

    # CI: latest completed run per workflow on the default branch.
    local runs_json ci failing
    runs_json=$(gh api "repos/$ORG/$repo/actions/runs?branch=$default_branch&per_page=100" \
                2>/dev/null || echo '{"workflow_runs":[]}')
    ci=$(echo "$runs_json" | jq -r '
        [.workflow_runs[]? | select(.status=="completed")]
        | group_by(.workflow_id) | map(max_by(.created_at))
        | if length==0 then "none"
          elif any(.[]; .conclusion=="failure") then "fail"
          else "pass" end' 2>/dev/null || echo "none")
    failing=$(echo "$runs_json" | jq -r '
        [.workflow_runs[]? | select(.status=="completed")]
        | group_by(.workflow_id) | map(max_by(.created_at))
        | [.[] | select(.conclusion=="failure") | .name] | unique | join(", ")' \
        2>/dev/null || echo "")

    # Security alerts: 403/404 -> no-access (distinct from a real 0).
    local dependabot code_scanning secret_scanning
    dependabot=$(gh api "repos/$ORG/$repo/dependabot/alerts?state=open&per_page=100" --jq 'length' 2>/dev/null) || dependabot="no-access"
    code_scanning=$(gh api "repos/$ORG/$repo/code-scanning/alerts?state=open&per_page=100" --jq 'length' 2>/dev/null) || code_scanning="no-access"
    secret_scanning=$(gh api "repos/$ORG/$repo/secret-scanning/alerts?state=open&per_page=100" --jq 'length' 2>/dev/null) || secret_scanning="no-access"

    # Branch protection, in a single API call: success -> protected; a 404
    # ("Branch not protected (HTTP 404)") means genuinely no protection ->
    # unprotected; anything else (typically 403) means we were denied and must
    # stay "unknown" rather than claim a clean state.
    local protection="unknown" prot_out
    if prot_out=$(gh api "repos/$ORG/$repo/branches/$default_branch/protection" 2>&1); then
        protection="protected"
    elif echo "$prot_out" | grep -q "HTTP 404"; then
        protection="unprotected"
    fi

    jq -n \
        --arg repo "$repo" --arg branch "$default_branch" \
        --argjson open_prs "${open_prs:-0}" --argjson draft_prs "${draft_prs:-0}" \
        --arg ci "$ci" --arg failing "$failing" \
        --arg dep "$dependabot" --arg code "$code_scanning" --arg secret "$secret_scanning" \
        --arg protection "$protection" \
        '{repo:$repo, unreachable:false, default_branch:$branch,
          open_prs:$open_prs, draft_prs:$draft_prs,
          ci:$ci, failing_workflows:$failing,
          dependabot:(if $dep=="no-access" then "no-access" else ($dep|tonumber) end),
          code_scanning:(if $code=="no-access" then "no-access" else ($code|tonumber) end),
          secret_scanning:(if $secret=="no-access" then "no-access" else ($secret|tonumber) end),
          protection:$protection}' > "$out"
}

# ---- assembler --------------------------------------------------------------
# Shared jq helpers, so the headline/table/attention views agree on what
# "has an open alert" means and how a no-access cell renders.
JQ_DEFS='
  def has_open_alerts:
    ((.dependabot|type)=="number" and .dependabot>0)
    or ((.code_scanning|type)=="number" and .code_scanning>0)
    or ((.secret_scanning|type)=="number" and .secret_scanning>0);
  def needs_attention:
    .ci=="fail" or has_open_alerts or .protection=="unprotected" or (.unreachable==true);
  def cell(x): if x=="no-access" then "n/a" else (x|tostring) end;
'

# Emit the markdown table (header + one row per repo) for a JSON array on stdin.
render_table() {
    echo "| Repo | PRs | CI | Dependabot | CodeQL | Secrets | Protection |"
    echo "|------|-----|----|-----------|--------|---------|------------|"
    jq -r "$JQ_DEFS"'
      sort_by(.repo)[] |
      "| \(.repo) | "
      + (if .unreachable then "- | - | - | - | - | could not assess |"
         else
           "\(.open_prs)\(if .draft_prs>0 then " (\(.draft_prs) draft)" else "" end) | "
           + "\(.ci)\(if .ci=="fail" and (.failing_workflows|length)>0 then " ⚠" else "" end) | "
           + "\(cell(.dependabot)) | \(cell(.code_scanning)) | \(cell(.secret_scanning)) | "
           + "\(.protection) |"
         end)'
}

# Emit the "Needs attention" bullet list for a JSON array on stdin.
render_attention() {
    jq -r "$JQ_DEFS"'
      sort_by(.repo)[] | select(needs_attention) |
      "- **\(.repo)**: " + ([
        (if .unreachable then "could not assess (repo metadata unreadable)" else empty end),
        (if .ci=="fail" then "CI failing" + (if (.failing_workflows|length)>0 then " (\(.failing_workflows))" else "" end) else empty end),
        (if ((.dependabot|type)=="number" and .dependabot>0) then "\(.dependabot) Dependabot alert(s)" else empty end),
        (if ((.code_scanning|type)=="number" and .code_scanning>0) then "\(.code_scanning) code-scanning alert(s)" else empty end),
        (if ((.secret_scanning|type)=="number" and .secret_scanning>0) then "\(.secret_scanning) secret-scanning alert(s)" else empty end),
        (if .protection=="unprotected" then "default branch unprotected" else empty end)
      ] | join("; "))'
}

assemble() {
    local dir=$1
    shopt -s nullglob
    local frags=("$dir"/*.json)
    local data
    if [ ${#frags[@]} -eq 0 ]; then
        data='[]'
    else
        data=$(jq -s '.' "${frags[@]}")
    fi

    # Headline counts.
    local total failing_ci with_alerts unprotected unreachable open_prs_total
    read -r total failing_ci with_alerts unprotected unreachable open_prs_total < <(
        echo "$data" | jq -r "$JQ_DEFS"'
          [ (length),
            ([.[]|select(.ci=="fail")]|length),
            ([.[]|select(has_open_alerts)]|length),
            ([.[]|select(.protection=="unprotected")]|length),
            ([.[]|select(.unreachable==true)]|length),
            ([.[]|.open_prs // 0]|add // 0)
          ] | @tsv')

    local today
    today=$(date +%Y-%m-%d)
    local headline="$total repo(s): $failing_ci with failing CI, $with_alerts with open security alerts, $unprotected with an unprotected default branch, $open_prs_total open PR(s) total"
    [ "$unreachable" -gt 0 ] && headline="$headline, $unreachable could not be assessed"

    # ---- terminal ----
    echo ""
    echo "====== Org State: $ORG ($today) ======"
    echo "$headline"
    echo ""
    echo "$data" | render_table
    echo ""
    local attention
    attention=$(echo "$data" | render_attention)
    if [ -n "$attention" ]; then
        echo "Needs attention:"
        echo "$attention"
    else
        echo "Needs attention: none - all assessed repos are clean."
    fi

    # ---- file ----
    if [ "$NO_FILE" = false ]; then
        local report_file="$ROOT/org-state-$ORG-$today.md"
        {
            echo "# Org state: $ORG"
            echo ""
            echo "_Generated $today by ghreport (read-only)._"
            echo ""
            echo "$headline"
            echo ""
            echo "## Repos"
            echo ""
            echo "$data" | render_table
            echo ""
            echo "## Needs attention"
            echo ""
            if [ -n "$attention" ]; then
                echo "$data" | render_attention
            else
                echo "None - all assessed repos are clean."
            fi
            echo ""
            echo "## Legend"
            echo ""
            echo "- **CI**: latest completed run per workflow on the default branch (\`pass\` / \`fail\` / \`none\`)."
            echo "- **n/a** in an alert column: the alert API returned no access - the feature is disabled on the repo or admin access is required. Either way it is not the same as a confirmed zero."
            echo "- **Protection**: \`unknown\` means the protection API denied access; \`unprotected\` means it confirmed no protection."
        } > "$report_file"
        echo ""
        echo "Report written to: $report_file"
    fi
}

# ---- render-only (replay / testing) ----------------------------------------
if [ -n "$RENDER_DIR" ]; then
    if [ ! -d "$RENDER_DIR" ]; then
        echo "Error: --render-dir not a directory: $RENDER_DIR" >&2
        exit 1
    fi
    assemble "$RENDER_DIR"
    exit 0
fi

# ---- discovery (delegated to ghsync --porcelain) ---------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GHSYNC_SH="$SCRIPT_DIR/../../ghsync/scripts/ghsync.sh"
if [ ! -f "$GHSYNC_SH" ]; then
    GHSYNC_SH="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/skills}/ghsync/scripts/ghsync.sh"
fi
if [ ! -f "$GHSYNC_SH" ]; then
    echo "Error: ghsync.sh not found (looked next to this script and under CLAUDE_PLUGIN_ROOT / ~/.claude/skills)." >&2
    exit 1
fi

echo "Discovering repos in $ORG ..." >&2
# Capture discovery output and exit status separately. Process substitution
# would discard ghsync's exit code (it is not a pipeline, so pipefail does not
# apply), masking an auth/deps failure as "no repositories". Capturing lets us
# distinguish a genuine empty org from a failed discovery.
disc_out=$(bash "$GHSYNC_SH" --porcelain --org "$ORG" --root "$ROOT") || {
    echo "Error: repo discovery via ghsync failed (see its output above)." >&2
    exit 1
}
repos=()
[ -n "$disc_out" ] && mapfile -t repos <<< "$disc_out"
if [ ${#repos[@]} -eq 0 ]; then
    echo "No repositories discovered for $ORG (the org is empty or nothing is accessible)." >&2
    exit 0
fi

# --limit caps the expensive querying (~5 REST calls per repo), so apply it
# here rather than passing it to ghsync: ghsync's --porcelain implies
# --list-repos, which exits before ghsync applies its own --limit, so a
# passed-through limit would be silently ignored.
if [ "$LIMIT" -gt 0 ] && [ "$LIMIT" -lt ${#repos[@]} ]; then
    echo "Limiting to the first $LIMIT of ${#repos[@]} discovered repo(s)." >&2
    repos=("${repos[@]:0:$LIMIT}")
fi
echo "Querying state for ${#repos[@]} repo(s) ..." >&2

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

i=0
for repo in "${repos[@]}"; do
    # Fragment filenames must be flat; repo names have no slashes (single-org).
    query_repo "$repo" "$TMP_DIR/$repo.json" &
    if (( ++i % 5 == 0 )); then
        wait
    fi
    [ "$QUIET_MODE" = false ] && printf "." >&2
done
wait
[ "$QUIET_MODE" = false ] && printf "\n" >&2

assemble "$TMP_DIR"
