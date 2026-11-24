# shellcheck shell=bash
# ghreport_github.sh - the per-repo GitHub worker.
# Sourced by ghreport.sh, never run on its own: it defines functions and
# reads the globals ghreport.sh sets (ORG, PROVIDER, GITLAB_HOST, ...).

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
