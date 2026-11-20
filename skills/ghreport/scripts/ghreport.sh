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
# (every group you belong to on --host, nested subgroups and shared projects,
# or --group's subtree). Per project, through `glab api`: open merge requests,
# the latest finished (success or failed) pipeline on the default branch, open
# vulnerabilities (a paid-tier endpoint, so no-access is the common case) and
# default-branch protection.
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
# Usage: ./ghreport.sh [--provider github|gitlab] [--root DIR] [--limit N]
#                      [--quiet] [--no-file] [--render-dir DIR]
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
# ===================================================================

ORG=""
ROOT=""
LIMIT=0
QUIET_MODE=false
NO_FILE=false
RENDER_DIR=""
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
    -h|--help)
      # The header runs from the first rule line to the next one.
      awk '/^# ====/ { print; if (++rules == 2) exit; next } rules == 1' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Usage: $0 [--provider github|gitlab] [--root DIR] [--limit N] [--quiet] [--no-file] [--render-dir DIR]" >&2
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

# ---- GitLab per-project worker ----------------------------------------------
# glab api against the run's host. gitlab_api_all follows every page; a page
# may arrive as one merged array or as concatenated arrays, so every reader
# below slurps (jq -s) and flattens.
gitlab_api() {
    glab api --hostname "$GITLAB_HOST" "$1"
}
gitlab_api_all() {
    glab api --hostname "$GITLAB_HOST" --paginate "$1"
}

gitlab_encode() {
    jq -rn --arg p "$1" '$p | @uri'
}

# Count the items of a (possibly multi-page) array response on stdin that
# match a jq filter. A non-array value (an error body) fails, so the caller
# reports it as unreadable instead of 0.
gitlab_count() {
    jq -s "if all(.[]; type==\"array\") then [.[][] | select($1)] | length else error(\"not a list\") end"
}

# True when the body ($2) is one JSON object carrying the key $1. Reads the
# body through `input` under -n, so an empty or blank body is an error on
# every jq: jq 1.6 `-e` exits 0 when there is no input at all (1.7 exits 4),
# which would let an empty body pass as readable. A second value after the
# object fails too, as `--argjson` would reject the body later.
json_object_with() {
    jq -en --arg k "$1" 'input as $b | ($b | type=="object" and has($k)) and ([inputs] | length == 0)' \
        <<< "$2" >/dev/null 2>&1
}

# Same contract as query_repo, with GitLab fields: open_mrs / draft_mrs,
# pipeline ("success" or "failed" from the newest finished pipeline,
# "none" with no finished pipeline), vulnerabilities, and empty_repo. An
# empty repository (null default branch) writes default_branch null,
# empty_repo true, and "empty" for pipeline and protection. Every call is
# judged by its exit status and its body shape only: any failure is
# "no-access" (counts) or "unknown" (status, protection), never 0.
query_project_gitlab() {
    local path=$1 out=$2 enc
    enc=$(gitlab_encode "$path")

    local proj_json
    if ! proj_json=$(gitlab_api "projects/$enc" 2>/dev/null) \
        || ! json_object_with path_with_namespace "$proj_json"; then
        jq -n --arg repo "$path" \
            '{repo:$repo, unreachable:true, default_branch:"?", open_mrs:"no-access", draft_mrs:0,
              pipeline:"unknown", vulnerabilities:"no-access", protection:"unknown"}' > "$out"
        return
    fi

    # GitLab gives an empty repository (no commits) a null default branch,
    # and can also report empty_repo: true alongside a branch name. Either
    # way there is no branch to judge, so pipeline and protection read
    # "empty" and no per-branch call is made: guessing "main" would report a
    # verdict for a branch that does not exist. This is the same predicate
    # ghsync discovery uses to drop empty projects.
    local branch empty=false
    branch=$(jq -r 'if .empty_repo == true then "" else (.default_branch // "") end' <<< "$proj_json")
    [ -z "$branch" ] && empty=true
    local benc
    benc=$(gitlab_encode "$branch")

    # Open merge requests (count + drafts). The state filter is repeated
    # client-side so a server that ignores the query string still counts
    # only open ones.
    local mr_json open_mrs="no-access" draft_mrs=0
    if mr_json=$(gitlab_api_all "projects/$enc/merge_requests?state=opened&per_page=100" 2>/dev/null) \
        && open_mrs=$(gitlab_count '(.state // "opened")=="opened"' <<< "$mr_json" 2>/dev/null); then
        draft_mrs=$(gitlab_count '(.state // "opened")=="opened" and (.draft==true or .work_in_progress==true)' <<< "$mr_json" 2>/dev/null || echo 0)
    else
        open_mrs="no-access"
    fi

    # Latest finished pipeline on the default branch, matching the GitHub
    # path's latest-completed run: the newest of the last 20 whose status is
    # success or failed. running, pending, canceled, skipped and manual are
    # passed over, so an in-flight or cancelled run cannot hide a failure.
    # The ref filter is repeated client-side for the same reason as above.
    local pl_json pipeline="unknown"
    if [ "$empty" = true ]; then
        pipeline="empty"
    elif pl_json=$(gitlab_api "projects/$enc/pipelines?ref=$benc&order_by=id&sort=desc&per_page=20" 2>/dev/null); then
        pipeline=$(jq -rs --arg b "$branch" '
            if all(.[]; type=="array") then
              [.[][] | select((.ref // $b)==$b and (.status=="success" or .status=="failed"))]
              | if length==0 then "none" else (max_by(.id // 0) | .status) end
            else "unknown" end' <<< "$pl_json" 2>/dev/null || echo "unknown")
    fi

    # Open vulnerabilities (detected or confirmed). Ultimate-tier only, and the
    # REST endpoint is deprecated, so a 403 or 404 here is the normal case on
    # most hosts: no-access, not 0.
    local vuln_json vulns="no-access"
    if vuln_json=$(gitlab_api_all "projects/$enc/vulnerabilities?per_page=100" 2>/dev/null); then
        vulns=$(gitlab_count '((.state // "detected") | . == "detected" or . == "confirmed")' <<< "$vuln_json" 2>/dev/null) || vulns="no-access"
    fi

    # Protection. On GitLab a 404 usually means "no access", not "not
    # protected", so only a readable list can say "unprotected". Names may
    # be wildcards (`release/*`); `*` is the only wildcard GitLab supports.
    local prot_json protection="unknown"
    if [ "$empty" = true ]; then
        protection="empty"
    elif prot_json=$(gitlab_api_all "projects/$enc/protected_branches?per_page=100" 2>/dev/null) \
        && protection=$(jq -rs --arg b "$branch" '
            def glob_re: gsub("(?<c>[.+?^$()\\[\\]{}|\\\\])"; "\\\(.c)") | gsub("\\*"; ".*") | "^" + . + "$";
            if all(.[]; type=="array") then
              if any(.[][]; (.name // "") as $n | $n==$b or ($b | test($n | glob_re)))
              then "protected" else "unprotected" end
            else error("not a list") end' <<< "$prot_json" 2>/dev/null); then
        :
    elif gitlab_api "projects/$enc/protected_branches/$benc" >/dev/null 2>&1; then
        protection="protected"
    else
        protection="unknown"
    fi

    jq -n \
        --arg repo "$path" --arg branch "$branch" --argjson empty "$empty" \
        --arg mrs "$open_mrs" --argjson drafts "${draft_mrs:-0}" \
        --arg pipeline "$pipeline" --arg vulns "$vulns" --arg protection "$protection" \
        '{repo:$repo, unreachable:false,
          default_branch:(if $empty then null else $branch end), empty_repo:$empty,
          open_mrs:(if $mrs=="no-access" then "no-access" else ($mrs|tonumber) end),
          draft_mrs:$drafts,
          pipeline:$pipeline,
          vulnerabilities:(if $vulns=="no-access" then "no-access" else ($vulns|tonumber) end),
          protection:$protection}' > "$out"
}

# ---- org / group settings ---------------------------------------------------
# One JSON fragment per org (GitHub) or group (GitLab):
#   {scope, provider, two_factor, push_rules, default_branch_protection, rulesets}
# Each setting is {value, reason}: value is the setting, "off", or "unknown";
# reason is null unless value is "unknown". An optional note qualifies a
# readable value (push_rules "off" names its non-active rulesets in it).
# A failed or unreadable call is always "unknown" with the reason, never "off".

# The reason for an unreadable response, from a call's captured output.
http_reason() {
    local code
    code=$(grep -o 'HTTP [0-9][0-9][0-9]' <<< "$1" | tail -1 || true)
    case "$code" in
        "HTTP 401"|"HTTP 403") echo "denied: no access or tier ($code)" ;;
        "HTTP 404") echo "not found: no access, tier, or endpoint missing ($code)" ;;
        "") echo "unreadable response" ;;
        *) echo "request failed ($code)" ;;
    esac
}

JQ_SETTING_DEFS='
  def unk($r): {value:"unknown", reason:$r};
  def val($v): {value:$v, reason:null};
'

# A fragment with every setting unknown for one reason.
settings_all_unknown() {
    jq -n --arg scope "$1" --arg provider "$PROVIDER" --arg r "$2" '
      {value:"unknown", reason:$r} as $u
      | {scope:$scope, provider:$provider, two_factor:$u, push_rules:$u,
         default_branch_protection:$u, rulesets:$u}' > "$3"
}

query_org_settings_github() {
    local out=$1 raw org_json='{}' org_err="" rs_json='[]' rs_err="" details='[]' detail_err=""
    if raw=$(gh api "orgs/$ORG" 2>&1) && json_object_with login "$raw"; then
        org_json=$raw
    else
        org_err=$(http_reason "$raw")
        # A personal account answers orgs/ with a 404 because it has no org
        # settings at all; say so rather than suggest missing access.
        if [ "$(gh api "users/$ORG" --jq '.type' 2>/dev/null || true)" = "User" ]; then
            settings_all_unknown "$ORG" "personal account: no org settings" "$out"
            return
        fi
    fi

    # Rulesets carry push rules (an active ruleset with target "push"; when
    # every push ruleset is non-active, off with them named in a note) and
    # default-branch protection for new repositories (an active branch ruleset on ~DEFAULT_BRANCH or ~ALL
    # of every repository). The list omits conditions, so each active branch
    # ruleset is read in full; one unreadable detail makes the verdict unknown.
    if raw=$(gh api "orgs/$ORG/rulesets?per_page=100" --paginate 2>&1) \
        && raw=$(jq -s 'if length>0 and all(.[]; type=="array") then add else error("not a list") end' <<< "$raw" 2>/dev/null); then
        rs_json=$raw
        local id d
        for id in $(jq -r '.[] | select(.target=="branch" and .enforcement=="active") | .id' <<< "$rs_json"); do
            if d=$(gh api "orgs/$ORG/rulesets/$id" 2>&1) && json_object_with id "$d"; then
                details=$(jq -c --argjson d "$d" '. + [$d]' <<< "$details")
            else
                detail_err="ruleset $id unreadable: $(http_reason "$d")"
                break
            fi
        done
    else
        rs_err=$(http_reason "$raw")
    fi

    jq -n "$JQ_SETTING_DEFS"'
      def names: map("\(.name) (\(.enforcement))") | join(", ");
      {scope:$scope, provider:"github",
       two_factor:(
         if $org_err!="" then unk($org_err)
         elif ($org.two_factor_requirement_enabled|type)=="boolean" then
           (if $org.two_factor_requirement_enabled then val("on") else val("off") end)
         else unk("no access: readable by org owners only") end),
       push_rules:(
         if $rs_err!="" then unk($rs_err)
         else ([$rs[] | select(.target=="push")]) as $p
           | if ($p|length)==0 then val("off")
             elif ([$p[] | select(.enforcement=="active")]|length)==0 then
               val("off") + {note:("not active: " + ($p|names))}
             else val($p|names) end end),
       default_branch_protection:(
         if $rs_err!="" then unk($rs_err)
         elif $detail_err!="" then unk($detail_err)
         else ([$details[] | select(
                 ((.conditions.ref_name.include // []) | any(.=="~DEFAULT_BRANCH" or .=="~ALL"))
                 and ((.conditions.repository_name.include // []) | index("~ALL") != null))]) as $c
           | if ($c|length)==0 then val("off") else val("ruleset: " + ($c|map(.name)|join(", "))) end end),
       rulesets:(
         if $rs_err!="" then unk($rs_err)
         elif ($rs|length)==0 then val("off")
         else val("\($rs|length): " + ($rs|names)) end)}' \
        --arg scope "$ORG" --argjson org "$org_json" --arg org_err "$org_err" \
        --argjson rs "$rs_json" --arg rs_err "$rs_err" \
        --argjson details "$details" --arg detail_err "$detail_err" > "$out"
}

query_group_settings_gitlab() {
    local group=$1 out=$2 enc raw g_json='{}' g_err="" pr_json='{}' pr_err=""
    enc=$(gitlab_encode "$group")
    if raw=$(gitlab_api "groups/$enc" 2>&1) && json_object_with full_path "$raw"; then
        g_json=$raw
    else
        g_err=$(http_reason "$raw")
    fi
    # A paid-tier endpoint. GitLab answers 404 both for a group with no push
    # rule and for one the token cannot see, so any failure stays unknown.
    if raw=$(gitlab_api "groups/$enc/push_rule" 2>&1) && json_object_with id "$raw"; then
        pr_json=$raw
    else
        pr_err=$(http_reason "$raw")
    fi

    jq -n "$JQ_SETTING_DEFS"'
      def level: {"0":"no one","30":"developers","40":"maintainers","60":"admins"}[tostring] // "level \(.)";
      def levels: map(.access_level | level) | join(", ");
      {scope:$scope, provider:"gitlab",
       two_factor:(
         if $g_err!="" then unk($g_err)
         elif ($g.require_two_factor_authentication|type)=="boolean" then
           (if $g.require_two_factor_authentication then val("on") else val("off") end)
         else unk("field missing from the groups API") end),
       push_rules:(
         if $pr_err!="" then unk($pr_err)
         else ([
             ($pr | to_entries[]
               | select((.key | IN("deny_delete_tag","member_check","prevent_secrets",
                          "commit_committer_check","commit_committer_name_check",
                          "reject_unsigned_commits","reject_non_dco_commits")) and .value==true)
               | .key),
             ($pr | to_entries[]
               | select((.key | endswith("_regex")) and ((.value // "") != ""))
               | .key),
             (if ($pr.max_file_size // 0) > 0 then "max_file_size=\($pr.max_file_size)MB" else empty end)
           ]) as $on
           | if ($on|length)==0 then val("off") else val($on|join(", ")) end end),
       default_branch_protection:(
         if $g_err!="" then unk($g_err)
         elif ($g.default_branch_protection|type)=="number" then
           ({"0":"off","1":"partial (developers can push, no force push)","2":"full",
             "3":"protected against pushes (developers can merge)",
             "4":"full after the initial push"}[$g.default_branch_protection|tostring]) as $v
           | if $v==null then unk("unrecognised level \($g.default_branch_protection)") else val($v) end
         elif ($g.default_branch_protection_defaults|type)=="object"
              and ($g.default_branch_protection_defaults|length)>0 then
           $g.default_branch_protection_defaults as $d
           | ($d.allowed_to_push // [] | any(.[]; (.access_level // 0) > 0 and (.access_level // 0) <= 30)) as $dev_push
           | if $d.allow_force_push==true and $dev_push then val("off")
             else val("push: \($d.allowed_to_push // [] | levels), merge: \($d.allowed_to_merge // [] | levels)"
                      + (if $d.allow_force_push==true then ", force push: allowed" else "" end)) end
         else unk("field missing from the groups API") end),
       rulesets:unk("endpoint missing: GitLab has no group rulesets")}' \
        --arg scope "$group" --argjson g "$g_json" --arg g_err "$g_err" \
        --argjson pr "$pr_json" --arg pr_err "$pr_err" > "$out"
}

# The groups a settings section covers: --group, else each group you belong
# to whose parent you do not, as discovery starts from them. Fails when the
# group list cannot be read, so the caller can report that rather than drop
# the section.
gitlab_settings_groups() {
    if [ -n "$GITLAB_GROUP" ]; then
        echo "$GITLAB_GROUP"
        return
    fi
    local raw
    raw=$(gitlab_api_all "groups?min_access_level=10&per_page=100" 2>/dev/null) || return 1
    jq -e -s 'length>0 and all(.[]; type=="array")' <<< "$raw" >/dev/null 2>&1 || return 1
    jq -rs '[.[][] | .full_path // empty]
            | . as $all | .[] | select(. as $p | any($all[]; . as $q | $q != $p and ($p | startswith($q + "/"))) | not)' \
        <<< "$raw" 2>/dev/null | sort -u
}

# Settings section for a JSON array of settings fragments on stdin. A count of
# "off" covers only readable settings; unknown values are counted apart.
SETTING_FIELDS='["two_factor","push_rules","default_branch_protection","rulesets"]'
settings_headline() {
    jq -r --argjson f "$SETTING_FIELDS" '
      [.[] as $s | $f[] | $s[.].value // "unknown"] as $v
      | "\(length) \(if .[0].provider=="gitlab" then "group" else "org" end)(s): "
        + "\([$v[]|select(.=="off")]|length) setting(s) off, "
        + "\([$v[]|select(.=="unknown")]|length) unknown"'
}
render_settings() {
    jq -r --argjson f "$SETTING_FIELDS" '
      sort_by(.scope)[] as $s
      | "- \($s.scope)",
        ($f[] as $k | ($s[$k] // {value:"unknown", reason:"missing from the fragment"})
         | "  - \($k): \(.value)\(if .value=="unknown" then " (\(.reason // "no reason given"))"
                                   elif (.note|type)=="string" then " (\(.note))" else "" end)")'
}
settings_legend() {
    echo "Each setting is its value, \`off\`, or \`unknown\` with the reason it could not be read (no access, tier, or endpoint missing). \`unknown\` is never counted as \`off\`."
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

# GitLab views. Unreadable cells render as the literal "no-access" /
# "unknown" so the report says exactly what the API said.
JQ_DEFS_GITLAB='
  def has_open_vulns: (.vulnerabilities|type)=="number" and .vulnerabilities>0;
  def needs_attention:
    .pipeline=="failed" or has_open_vulns or .protection=="unprotected" or (.unreachable==true);
'

render_table_gitlab() {
    echo "| Project | Branch | MRs | Pipeline | Vulnerabilities | Protection |"
    echo "|---------|--------|-----|----------|-----------------|------------|"
    jq -r "$JQ_DEFS_GITLAB"'
      sort_by(.repo)[] |
      "| \(.repo) | "
      + (if .unreachable then "- | - | - | - | could not assess |"
         else
           "\(if .empty_repo==true then "empty" else .default_branch end) | "
           + "\(.open_mrs)\(if (.draft_mrs // 0)>0 then " (\(.draft_mrs) draft)" else "" end) | "
           + "\(.pipeline) | \(.vulnerabilities) | \(.protection) |"
         end)'
}

render_attention_gitlab() {
    jq -r "$JQ_DEFS_GITLAB"'
      sort_by(.repo)[] | select(needs_attention) |
      "- **\(.repo)**: " + ([
        (if .unreachable then "could not assess (project metadata unreadable)" else empty end),
        (if .pipeline=="failed" then "pipeline failed on \(.default_branch)" else empty end),
        (if has_open_vulns then "\(.vulnerabilities) open vulnerabilit\(if .vulnerabilities==1 then "y" else "ies" end)" else empty end),
        (if .protection=="unprotected" then "default branch unprotected" else empty end)
      ] | join("; "))'
}

# One line of headline counts for a JSON array on stdin. Unreadable MR and
# vulnerability counts are named, so a total of 0 is never read as clean.
headline_gitlab() {
    jq -r "$JQ_DEFS_GITLAB"'
      ([.[]|select(.unreachable!=true)]) as $ok
      | "\(length) project(s): \([.[]|select(.pipeline=="failed")]|length) with a failed pipeline, "
        + "\([.[]|select(has_open_vulns)]|length) with open vulnerabilities, "
        + "\([.[]|select(.protection=="unprotected")]|length) with an unprotected default branch, "
        + "\([.[]|.open_mrs|numbers]|add // 0) open MR(s) total"
        + (([$ok[]|select(.open_mrs=="no-access")]|length) as $n | if $n>0 then ", MRs unreadable on \($n)" else "" end)
        + (([$ok[]|select(.vulnerabilities=="no-access")]|length) as $n | if $n>0 then ", vulnerabilities no-access on \($n)" else "" end)
        + (([.[]|select(.unreachable==true)]|length) as $n | if $n>0 then ", \($n) could not be assessed" else "" end)
        + (([$ok[]|select(.empty_repo==true)]|length) as $n | if $n>0 then ", \($n) empty" else "" end)'
}

legend_gitlab() {
    echo "- **Pipeline**: status of the latest finished pipeline (\`success\` or \`failed\`) among the last 20 on the default branch; running, pending, canceled, skipped and manual pipelines are passed over. \`none\` means no finished pipeline was found, \`unknown\` that the pipelines API could not be read."
    echo "- **no-access** in a count column: the API refused or failed. For vulnerabilities that is the tier, the role, or the endpoint being unavailable on this GitLab version (GitLab documents the REST vulnerabilities endpoint as deprecated). It is not the same as a confirmed zero."
    echo "- **Protection**: \`unknown\` means the protected-branches API could not be read; \`unprotected\` means it listed no rule covering the default branch."
    echo "- \`empty\` in Branch, Pipeline and Protection: the repository has no commits, so GitLab reports no default branch. Pipeline and protection are not checked, so emptiness alone does not put a project under Needs attention; its open vulnerabilities, which GitLab keeps from earlier scans, still do."
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
    # Settings live in their own subdirectory, so the glob above never reads
    # them as repository rows.
    local sfrags=("$dir"/settings/*.json)
    SETTINGS_DATA=""
    [ ${#sfrags[@]} -gt 0 ] && SETTINGS_DATA=$(jq -s '.' "${sfrags[@]}")

    if [ "$PROVIDER" = gitlab ]; then
        emit_report "$data" "$(echo "$data" | headline_gitlab)" \
            render_table_gitlab render_attention_gitlab legend_gitlab "Projects" "projects"
        return
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

    local headline="$total repo(s): $failing_ci with failing CI, $with_alerts with open security alerts, $unprotected with an unprotected default branch, $open_prs_total open PR(s) total"
    [ "$unreachable" -gt 0 ] && headline="$headline, $unreachable could not be assessed"

    emit_report "$data" "$headline" render_table render_attention legend_github "Repos" "repos"
}

legend_github() {
    echo "- **CI**: latest completed run per workflow on the default branch (\`pass\` / \`fail\` / \`none\`)."
    echo "- **n/a** in an alert column: the alert API returned no access - the feature is disabled on the repo or admin access is required. Either way it is not the same as a confirmed zero."
    echo "- **Protection**: \`unknown\` means the protection API denied access; \`unprotected\` means it confirmed no protection."
}

# Terminal summary plus the markdown file, shared by both providers.
# $1 data, $2 headline, $3 table fn, $4 attention fn, $5 legend fn,
# $6 table heading, $7 noun for the clean message.
emit_report() {
    local data=$1 headline=$2 table_fn=$3 attention_fn=$4 legend_fn=$5 heading=$6 noun=$7
    local today
    today=$(date +%Y-%m-%d)

    # ---- terminal ----
    echo ""
    echo "====== Org State: $ORG ($today) ======"
    echo "$headline"
    echo ""
    echo "$data" | "$table_fn"
    echo ""
    local attention
    attention=$(echo "$data" | "$attention_fn")
    if [ -n "$attention" ]; then
        echo "Needs attention:"
        echo "$attention"
    else
        echo "Needs attention: none - all assessed $noun are clean."
    fi
    local settings_title="Org settings"
    [ "$PROVIDER" = gitlab ] && settings_title="Group settings"
    if [ -n "$SETTINGS_DATA" ]; then
        echo ""
        echo "$settings_title:"
        echo "$SETTINGS_DATA" | settings_headline
        echo "$SETTINGS_DATA" | render_settings
    fi

    # ---- file ----
    if [ "$NO_FILE" = false ]; then
        # A GitLab label can hold a group path; keep the file name flat.
        local report_file="$ROOT/org-state-${ORG//\//-}-$today.md"
        {
            echo "# Org state: $ORG"
            echo ""
            echo "_Generated $today by ghreport (read-only)._"
            echo ""
            echo "$headline"
            echo ""
            echo "## $heading"
            echo ""
            echo "$data" | "$table_fn"
            echo ""
            echo "## Needs attention"
            echo ""
            if [ -n "$attention" ]; then
                echo "$attention"
            else
                echo "None - all assessed $noun are clean."
            fi
            echo ""
            if [ -n "$SETTINGS_DATA" ]; then
                echo "## $settings_title"
                echo ""
                echo "$SETTINGS_DATA" | settings_headline
                echo ""
                echo "$SETTINGS_DATA" | render_settings
                echo ""
                settings_legend
                echo ""
            fi
            echo "## Legend"
            echo ""
            "$legend_fn"
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
[ -n "$disc_out" ] && mapfile -t repos <<< "$disc_out"
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

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

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
