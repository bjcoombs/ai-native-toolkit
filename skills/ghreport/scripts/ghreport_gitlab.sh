# shellcheck shell=bash
# ghreport_gitlab.sh - the per-project GitLab worker and its glab api helpers.
# Sourced by ghreport.sh, never run on its own: it defines functions and
# reads the globals ghreport.sh sets (ORG, PROVIDER, GITLAB_HOST, ...).

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
