#!/bin/bash
set -euo pipefail

# ===================================================================
# ghsync.sh - Bulk clone + sync every repo you can access on GitHub or GitLab
#
# Discovers every repository you can access, deduplicates them, and clones
# or fast-forward updates each into a worktree-friendly layout. Built for
# onboarding into a new enterprise: run it once and you have a local mirror
# of everything you can touch; run it again to bring it up to date.
#
# GitHub (--provider github, the default): discovers every repo in an org
# through both the teams you belong to and the org's repo list (which
# surfaces public repos and repos reachable via direct collaborator grants
# outside any team). When the target is a personal account rather than an
# Organization, repos come from the account's repo list instead (empty
# repos, which have no default branch, are skipped). The org defaults to the
# basename of the directory you run from, so running inside
# ~/dev/github.com/meridianhub syncs the "meridianhub" org into that
# directory. Override with --org / --root.
#
# GitLab (--provider gitlab): discovers every project in every group you
# belong to on --host, with all nested subgroups, plus projects shared into
# those groups. Each project is laid out under its own path_with_namespace,
# which is also the dedupe key, so a project shared into two groups is
# cloned once. --group narrows the run to one group subtree. The root
# defaults to ~/dev/<host>/, without any :port. Requires glab, authenticated
# for the host.
#
# Layout produced (per repo path under the root; for GitHub the path is the
# repo name, for GitLab the path_with_namespace, and the -main directory
# takes the path's last segment):
#   <root>/<path>/       # e.g. <root>/a/b/repo/ for the path a/b/repo
#     ├── <repo>-main/   # default-branch checkout
#     └── worktree/      # empty dir, ready for git worktrees
#
# GitHub Enterprise: the CLI uses its configured host. To target a GHE
# host, set GH_HOST (e.g. GH_HOST=github.example.com) or log the CLI in
# with `auth login --hostname github.example.com` first.
#
# Hosts reachable only from another network: run --porcelain where the host
# answers the API and save the list, then run --from-list <file> wherever
# git can reach the host. --from-list makes no API call.
#
# Usage: ./ghsync.sh [--provider github|gitlab] [--root DIR] [--quiet]
#                    [--limit N] [--dry-run] [--list-repos] [--porcelain]
#                    GitHub: [--org NAME] [--list-teams]
#                    GitLab: [--host HOST] [--group PATH] [--from-list FILE]
#   --provider P     github (default) or gitlab
#   --root DIR       Directory to clone into; must already exist (GitHub
#                    default: current directory; GitLab default:
#                    ~/dev/<host> without any :port)
#   --quiet          Suppress per-repository messages during sync
#   --limit N        Process only N repositories (useful for testing)
#   --dry-run        Show what would be done without making changes
#   --list-repos     List the deduplicated accessible repos and exit
#   --porcelain      Machine-readable repo list: one path per line to stdout
#                    (GitHub: the repo name; GitLab: path_with_namespace),
#                    all progress/status chatter to stderr; implies
#                    --list-repos
#   --org NAME       GitHub org to sync (default: basename of --root / cwd)
#   --list-teams     List your teams in the org and exit (GitHub orgs only)
#   --host HOST      GitLab host (default: gitlab.com); a scheme and
#                    trailing slash are accepted and stripped
#   --group PATH     Sync only this GitLab group and its subgroups,
#                    including a parent of the subgroups you belong to
#   --from-list F    Sync the GitLab paths listed in F (the --porcelain
#                    format; blank lines and # comments are ignored) instead
#                    of discovering them. Clone URLs are built from --host:
#                    https://<host>/<path>.git, or git@<host>:<path>.git when
#                    GHSYNC_GIT_PROTOCOL=ssh. Needs no glab.
# ===================================================================

# Process command line args
ORG=""
ROOT=""
QUIET_MODE=false
REPO_LIMIT=0
DRY_RUN=false
LIST_TEAMS=false
LIST_REPOS=false
PORCELAIN_MODE=false
GITLAB_HOST=""
GITLAB_GROUP=""
FROM_LIST=""
# Flags that belong to one provider only, recorded as given so the
# validation below can reject them on the other provider.
github_only_flags=()
gitlab_only_flags=()

# Provider seam: discovery, the default-branch lookup and the clone dispatch
# on PROVIDER. Each repository is identified by its path relative to the
# root, which also keys dedupe, the status files and the blacklist.
PROVIDER=github

# Repositories to never sync (oversized, checkout issues, etc.).
# Override per-machine by exporting GHSYNC_BLACKLIST as a space-separated
# list of repository paths (for GitHub, the repo name) before running.
read -r -a REPO_BLACKLIST <<< "${GHSYNC_BLACKLIST:-}"

while [[ $# -gt 0 ]]; do
  case $1 in
    --provider)
      PROVIDER=$2
      shift 2
      ;;
    --provider=*)
      PROVIDER=${1#*=}
      shift
      ;;
    --org)
      ORG=$2
      github_only_flags+=(--org)
      shift 2
      ;;
    --org=*)
      ORG=${1#*=}
      github_only_flags+=(--org)
      shift
      ;;
    --host)
      GITLAB_HOST=$2
      gitlab_only_flags+=(--host)
      shift 2
      ;;
    --host=*)
      GITLAB_HOST=${1#*=}
      gitlab_only_flags+=(--host)
      shift
      ;;
    --group)
      GITLAB_GROUP=$2
      gitlab_only_flags+=(--group)
      shift 2
      ;;
    --group=*)
      GITLAB_GROUP=${1#*=}
      gitlab_only_flags+=(--group)
      shift
      ;;
    --from-list)
      FROM_LIST=$2
      gitlab_only_flags+=(--from-list)
      shift 2
      ;;
    --from-list=*)
      FROM_LIST=${1#*=}
      gitlab_only_flags+=(--from-list)
      shift
      ;;
    --root)
      ROOT=$2
      shift 2
      ;;
    --root=*)
      ROOT=${1#*=}
      shift
      ;;
    --quiet)
      QUIET_MODE=true
      shift
      ;;
    --limit)
      REPO_LIMIT=$2
      shift 2
      ;;
    --limit=*)
      REPO_LIMIT=${1#*=}
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --list-teams)
      LIST_TEAMS=true
      github_only_flags+=(--list-teams)
      shift
      ;;
    --list-repos)
      LIST_REPOS=true
      shift
      ;;
    --porcelain)
      # Machine-readable repo list for downstream tooling (e.g. ghreport).
      # Implies the --list-repos early exit, but with a clean stdout: only
      # repo names reach stdout; every progress/status message is routed to
      # stderr by the fd swap below.
      PORCELAIN_MODE=true
      LIST_REPOS=true
      shift
      ;;
    -h|--help)
      # The header between the first two rule lines, wherever it ends.
      awk '/^# =====/ { n++; print; if (n == 2) exit; next } n == 1' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [--provider github|gitlab] [--root DIR] [--quiet] [--limit N] [--dry-run] [--list-repos] [--porcelain]"
      echo "       GitHub: [--org NAME] [--list-teams]   GitLab: [--host HOST] [--group PATH] [--from-list FILE]"
      exit 1
      ;;
  esac
done

# Porcelain mode: keep stdout clean for machine consumers. Save the real
# stdout on fd 3 and redirect fd 1 to stderr, so every informational echo,
# warning, and progress line in the rest of the script lands on stderr
# without having to touch each call site. Only the repo-name list is then
# explicitly written to fd 3 (real stdout) in the --list-repos block below.
# Without --porcelain this is a no-op and behaviour is unchanged.
if [ "$PORCELAIN_MODE" = true ]; then
    exec 3>&1 1>&2
fi

# Reject flags that belong to the other provider rather than ignoring them.
case "$PROVIDER" in
    github)
        if [ ${#gitlab_only_flags[@]} -gt 0 ]; then
            echo "Error: ${gitlab_only_flags[0]} applies to --provider gitlab only" >&2
            exit 1
        fi
        ;;
    gitlab)
        if [ ${#github_only_flags[@]} -gt 0 ]; then
            echo "Error: ${github_only_flags[0]} applies to --provider github only" >&2
            exit 1
        fi
        if [ -n "$GITLAB_GROUP" ] && [ -n "$FROM_LIST" ]; then
            echo "Error: --group and --from-list cannot be combined; filter the list file instead" >&2
            exit 1
        fi
        # Accept a host written as a URL: drop the scheme and any trailing
        # slash, but keep an http scheme for the clone URLs built from it.
        GITLAB_HOST="${GITLAB_HOST:-gitlab.com}"
        GITLAB_SCHEME=https
        case "$GITLAB_HOST" in
            http://*) GITLAB_SCHEME=http; GITLAB_HOST=${GITLAB_HOST#http://} ;;
            https://*) GITLAB_HOST=${GITLAB_HOST#https://} ;;
        esac
        GITLAB_HOST=${GITLAB_HOST%%/*}
        if [ -z "$GITLAB_HOST" ]; then
            echo "Error: --host is empty" >&2
            exit 1
        fi
        ;;
    *)
        echo "Error: unknown provider '$PROVIDER' (expected github or gitlab)" >&2
        exit 1
        ;;
esac

# ---- provider seam ------------------------------------------------------
# Every provider call sits behind the functions below, dispatched on
# PROVIDER. The sync loop sees only repository paths relative to the root.

# Fail fast when the provider's CLI is missing or not authenticated, before
# discovery makes a flurry of API calls.
github_preflight() {
    for cmd in gh jq; do
        if ! command -v "$cmd" &> /dev/null; then
            echo "$cmd command not found. Install it using: brew install $cmd"
            exit 1
        fi
    done

    if ! gh auth status &> /dev/null; then
        echo "Error: gh is not authenticated. Run: gh auth login"
        [[ -n "${GH_HOST:-}" ]] && echo "  (targeting host: $GH_HOST)"
        exit 1
    fi
}

# Discover every accessible repository. Appends one relative path per line
# to $all_repos_file (active) and $archived_repos_file (archived), and sets
# account_type and user_teams for the summary. For GitHub the path is the
# bare repo name. Runs in the main shell, not a subshell: it sets globals and
# owns the --list-teams early exit.
discover_github() {
    # Detect account type: organizations are discovered via team membership,
    # personal accounts via their repo list. A 404 here means the name itself
    # is wrong (typo, or the directory name doesn't match any GitHub account).
    account_type=$(gh api "users/$ORG" --jq '.type' 2>/dev/null) || {
        echo "Error: GitHub account '$ORG' not found"
        echo "  - Check the org name (it defaults to the directory you ran from: $(basename "$ROOT"))"
        echo "  - Override with --org NAME if the directory name differs from the org"
        exit 1
    }

    if [ "$account_type" = "User" ]; then
        if [ "$LIST_TEAMS" = true ]; then
            echo "$ORG is a personal account; teams don't apply. Use --list-repos instead."
            exit 0
        fi

        # Personal account: list the account's repos directly. Empty repos have
        # no default branch and nothing to check out, so they are skipped
        # (gh renders their defaultBranchRef as null or {"name": ""}).
        # gh repo list paginates internally up to --limit; 4000 comfortably
        # covers any personal account. Fetch once and filter locally so the
        # truncation check sees the raw count, before any filtering.
        repo_list_limit=4000
        echo "Fetching repositories for personal account $ORG..."
        repo_list_json=$(mktemp)
        gh repo list "$ORG" --limit "$repo_list_limit" --json name,isArchived,defaultBranchRef > "$repo_list_json"
        jq -r '.[] | select(.isArchived == false and (.defaultBranchRef.name // "") != "") | .name' "$repo_list_json" >> "$all_repos_file"
        jq -r '.[] | select(.isArchived == true) | .name' "$repo_list_json" >> "$archived_repos_file"

        if [ "$(jq 'length' "$repo_list_json")" -ge "$repo_list_limit" ]; then
            echo "Warning: hit the $repo_list_limit-repo listing cap; some repos may be missing"
        fi
        rm -f "$repo_list_json"
    else
        # Organization: discover repos by unioning two sources, so team
        # membership is not a precondition for sync:
        #   1. Every team you belong to (gh api user/teams).
        #   2. The org's repo list (gh repo list), which also surfaces public
        #      repos and repos reachable via direct collaborator grants outside
        #      any team.
        # A user on no team can still sync the repos they can otherwise access.
        echo "Fetching your teams in $ORG..."
        # Guard the team fetch: under `set -euo pipefail` an unguarded failure
        # (transient API/network error, or a token without the read:org scope)
        # would abort the whole script before the org repo-list fallback below
        # ever runs - defeating the union discovery this path exists to provide.
        # Capture the command's exit status directly (a process-substitution feed
        # into mapfile would mask gh's failure), then fall back to no teams.
        team_output=$(gh api user/teams --paginate --jq ".[] | select(.organization.login == \"$ORG\") | .slug") || {
            echo "Note: unable to fetch your team memberships in $ORG; continuing with org repo list discovery"
            team_output=""
        }
        user_teams=()
        [ -n "$team_output" ] && mapfile -t user_teams <<< "$team_output"

        if [ ${#user_teams[@]} -eq 0 ]; then
            echo "Note: you don't belong to any teams in $ORG; discovering repos via the org repo list instead"
        else
            echo "Found ${#user_teams[@]} team(s): ${user_teams[*]}"
        fi

        if [ "$LIST_TEAMS" = true ]; then
            echo ""
            if [ ${#user_teams[@]} -eq 0 ]; then
                echo "You don't belong to any teams in $ORG."
                echo "Run with --list-repos to see the repos discovered via the org repo list."
            else
                echo "Teams you belong to in $ORG:"
                for team in "${user_teams[@]}"; do
                    repo_count=$(gh api "orgs/$ORG/teams/$team/repos" --paginate --jq 'length' 2>/dev/null || echo "?")
                    echo "  - $team ($repo_count repos)"
                done
            fi
            exit 0
        fi

        # Source 1: repos from every team you belong to.
        if [ ${#user_teams[@]} -gt 0 ]; then
            echo "Fetching repositories from all teams..."
            for team in "${user_teams[@]}"; do
                echo "  - Fetching from $team..."
                # Fetch all repos and split into active vs archived
                gh api "orgs/$ORG/teams/$team/repos" --paginate --jq '.[] | select(.archived == false) | .name' 2>/dev/null >> "$all_repos_file" || true
                gh api "orgs/$ORG/teams/$team/repos" --paginate --jq '.[] | select(.archived == true) | .name' 2>/dev/null >> "$archived_repos_file" || true
            done
        fi

        # Source 2: the org repo list (public + direct-collaborator repos). gh
        # returns names without the org prefix, matching the team source's
        # format, so the later sort -u dedupes cleanly across both sources.
        # Empty repos have no default branch and nothing to check out, so they
        # are skipped - mirroring the personal-account path above.
        echo "Fetching the $ORG repo list..."
        org_repo_list_limit=4000
        org_repo_list_json=$(mktemp)
        gh repo list "$ORG" --limit "$org_repo_list_limit" --json name,isArchived,defaultBranchRef > "$org_repo_list_json"
        jq -r '.[] | select(.isArchived == false and (.defaultBranchRef.name // "") != "") | .name' "$org_repo_list_json" >> "$all_repos_file"
        jq -r '.[] | select(.isArchived == true) | .name' "$org_repo_list_json" >> "$archived_repos_file"

        if [ "$(jq 'length' "$org_repo_list_json")" -ge "$org_repo_list_limit" ]; then
            echo "Warning: hit the $org_repo_list_limit-repo listing cap; some repos may be missing"
        fi
        rm -f "$org_repo_list_json"
    fi
}

# One line naming the host and the check that failed, then exit. No retry:
# a host that fails a check once fails it on the next attempt too.
gitlab_fail() {
    echo "Error: GitLab host $GITLAB_HOST failed the $1 check: $2" >&2
    exit 1
}

# Fail fast before discovery: glab and jq present, glab authenticated for
# the host. `glab auth status` contacts the host, so a network failure shows
# up here too and is reported as unreachable. No other probe runs, and no
# `glab api` call happens until these pass. --from-list skips this: it
# clones with git and needs no glab.
gitlab_preflight() {
    command -v jq &> /dev/null || gitlab_fail "jq installed" "jq not found. Install it using: brew install jq"
    [ -n "$FROM_LIST" ] && return 0
    command -v glab &> /dev/null || gitlab_fail "glab installed" "glab not found. Install it using: brew install glab"
    local status_out
    if ! status_out=$(glab auth status --hostname "$GITLAB_HOST" 2>&1); then
        if grep -qiE 'no such host|dial tcp|connection refused|timed? ?out|could not resolve|network is unreachable|tls handshake|i/o timeout' <<< "$status_out"; then
            gitlab_fail "reachability (glab auth status --hostname $GITLAB_HOST)" "host unreachable from this network"
        fi
        gitlab_fail "authentication (glab auth status --hostname $GITLAB_HOST)" "not authenticated. Run: glab auth login --hostname $GITLAB_HOST"
    fi
}

# glab api against the run's host, every page.
gitlab_api() {
    glab api --hostname "$GITLAB_HOST" --paginate "$1"
}

gitlab_encode() {
    jq -rn --arg p "$1" '$p | @uri'
}

# Append one group's projects and the projects shared into it. $1 is the
# group's API reference (numeric id or URL-encoded path), $2 its full path
# for messages. A group whose listing fails is warned about and skipped,
# so one unreadable subgroup does not hide the rest.
gitlab_group_projects() {
    local ref=$1 full_path=$2 endpoint json
    for endpoint in "groups/$ref/projects?per_page=100" "groups/$ref/projects/shared?per_page=100"; do
        if ! json=$(gitlab_api "$endpoint" 2>/dev/null); then
            echo "Warning: could not list ${endpoint%%\?*} for $full_path; skipping it" >&2
            continue
        fi
        # Empty projects have no default branch and nothing to check out,
        # mirroring the GitHub rule; archived ones go to the archive cache.
        jq -r '.[] | select(.archived != true and (.default_branch // "") != "" and .empty_repo != true) | .path_with_namespace' <<< "$json" >> "$all_repos_file"
        jq -r '.[] | select(.archived == true) | .path_with_namespace' <<< "$json" >> "$archived_repos_file"
    done
}

# Discover every project in the groups you belong to (or in --group's
# subtree), their nested subgroups, and the projects shared into them. Lines
# of the groups file are "<api ref><TAB><full path>"; dedupe is on the path.
discover_gitlab() {
    local groups_file member_json g ref full_path desc_json covered
    groups_file=$(mktemp)

    echo "Fetching your groups on $GITLAB_HOST..."
    member_json=$(gitlab_api "groups?min_access_level=10&per_page=100") || {
        rm -f "$groups_file"
        echo "Error: could not list your groups on $GITLAB_HOST (glab api groups failed)" >&2
        exit 1
    }
    jq -r '.[] | "\(.id)\t\(.full_path)"' <<< "$member_json" > "$groups_file.members"
    cp "$groups_file.members" "$groups_file"
    while IFS=$'\t' read -r ref full_path; do
        [ -n "$ref" ] || continue
        if desc_json=$(gitlab_api "groups/$ref/descendant_groups?per_page=100" 2>/dev/null); then
            jq -r '.[] | "\(.id)\t\(.full_path)"' <<< "$desc_json" >> "$groups_file"
        else
            echo "Warning: could not list the subgroups of $full_path; skipping them" >&2
        fi
    done < "$groups_file.members"
    rm -f "$groups_file.members"

    if [ -n "$GITLAB_GROUP" ]; then
        g=${GITLAB_GROUP%/}
        awk -F'\t' -v g="$g" '$2 == g || index($2, g "/") == 1' "$groups_file" > "$groups_file.sub"
        # The group itself is one of yours (or under one): its subtree is
        # already complete. Otherwise (a public group, or a parent of the
        # subgroups you belong to) look it up by path and add everything it
        # shows you, so projects directly in it and in sibling subgroups
        # count too. The lookup failing is fatal only when you belong to
        # nothing under it; otherwise one line names what was left out.
        if ! cut -f2 "$groups_file.sub" | grep -qxF -- "$g"; then
            ref=$(gitlab_encode "$g")
            if desc_json=$(gitlab_api "groups/$ref/descendant_groups?per_page=100" 2>/dev/null); then
                printf '%s\t%s\n' "$ref" "$g" >> "$groups_file.sub"
                jq -r '.[] | "\(.id)\t\(.full_path)"' <<< "$desc_json" >> "$groups_file.sub"
            elif [ -s "$groups_file.sub" ]; then
                covered=$(cut -f2 "$groups_file.sub" | sort -u | paste -sd, - | sed 's/,/, /g')
                echo "Warning: could not read group $g on $GITLAB_HOST; syncing only your" \
                    "subgroups under it ($covered) and skipping projects directly in $g" \
                    "or in its other subgroups" >&2
            else
                rm -f "$groups_file" "$groups_file.sub"
                echo "Error: group $g not found or not accessible on $GITLAB_HOST" >&2
                exit 1
            fi
        fi
        mv "$groups_file.sub" "$groups_file"
    fi

    sort -t$'\t' -k2,2 -u "$groups_file" -o "$groups_file"
    gitlab_group_count=$(wc -l < "$groups_file" | tr -d ' ')
    echo "Fetching projects from $gitlab_group_count group(s)..."
    while IFS=$'\t' read -r ref full_path; do
        [ -n "$ref" ] || continue
        gitlab_group_projects "$ref" "$full_path"
    done < "$groups_file"
    rm -f "$groups_file"
}

# --from-list: the --porcelain format, one path per line. Surrounding
# whitespace, CRs, blank lines and # comments are dropped; dedupe happens
# with the discovered paths later.
read_from_list() {
    if [ ! -f "$FROM_LIST" ] || [ ! -r "$FROM_LIST" ]; then
        echo "Error: cannot read --from-list file: $FROM_LIST" >&2
        exit 1
    fi
    echo "Reading repository paths from $FROM_LIST (no discovery)"
    sed -e 's/\r$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's#/*$##' "$FROM_LIST" \
        | grep -v -e '^$' -e '^#' >> "$all_repos_file" || true
}

# Keep only paths that are safe to use as directories and to splice into
# the per-repo `bash -c` string. GitLab's own rule (lib/gitlab/path_regex.rb:
# PATH_START_CHAR = '[a-zA-Z0-9_\.]', then [a-zA-Z0-9_\-\.], and
# NO_SUFFIX_REGEX = /(?<!\.git|\.atom)/) allows a leading dot, so
# grp/.dotfiles is kept. On top of that: no empty, "." or ".." segment, no
# segment ending in .git or .atom (GitLab's suffix rule), and no segment
# that is .git in any case, since a case-folding filesystem (the macOS
# default) would read .GIT as a stray git directory. A real project never
# trips this; a hand-edited --from-list line with a quote, a space, an
# absolute path or ../ does.
gitlab_reject_unsafe_paths() {
    local kept line folded
    kept=$(mktemp)
    while IFS= read -r line; do
        folded=$(tr '[:upper:]' '[:lower:]' <<< "/$line/")
        if [[ "$line" =~ ^[A-Za-z0-9_.][A-Za-z0-9._-]*(/[A-Za-z0-9_.][A-Za-z0-9._-]*)*$ ]] \
            && [[ "/$line/" != */../* && "/$line/" != */./* ]] \
            && [[ "$line/" != *.git/* && "$line/" != *.atom/* ]] \
            && [[ "$folded" != */.git/* ]]; then
            echo "$line" >> "$kept"
        else
            echo "Warning: skipping unsafe repository path: $line" >&2
        fi
    done < "$all_repos_file"
    mv "$kept" "$all_repos_file"
}

# Clone URL for one GitLab path, built from the host. Used for both
# discovery and --from-list runs, so both produce the same checkouts. A port
# on --host (host:8443) is the web and API port, so it stays in the HTTPS
# URL but is dropped for SSH; set a custom SSH port in ~/.ssh/config.
gitlab_clone_url() {
    if [ "${GHSYNC_GIT_PROTOCOL:-https}" = ssh ]; then
        echo "git@${GITLAB_HOST%%:*}:$1.git"
    else
        echo "$GITLAB_SCHEME://$GITLAB_HOST/$1.git"
    fi
}

# Default branch of one repository, for checkouts whose origin HEAD is unset.
provider_default_branch() {
    local path=$1
    case "$PROVIDER" in
        github)
            gh repo view "$ORG/$path" --json defaultBranchRef --jq '.defaultBranchRef.name' 2>/dev/null || echo "main"
            ;;
        gitlab)
            # A fresh clone sets origin HEAD, so this is rarely reached. No
            # API call: a --from-list run must work while the API is down.
            echo "main"
            ;;
        *)
            echo "main"
            ;;
    esac
}

# Clone one repository (by relative path) into an existing empty directory.
provider_clone() {
    local path=$1
    local dest=$2
    case "$PROVIDER" in
        github)
            gh repo clone "$ORG/$path" "$dest" 2>&1
            ;;
        gitlab)
            git clone -q "$(gitlab_clone_url "$path")" "$dest" 2>&1
            ;;
        *)
            echo "Unknown provider: $PROVIDER"
            return 1
            ;;
    esac
}

provider_preflight() {
    case "$PROVIDER" in
        github) github_preflight ;;
        gitlab) gitlab_preflight ;;
        *) echo "Unknown provider: $PROVIDER"; exit 1 ;;
    esac
}

# Discovery and summary wording, which name the provider's own concepts.
report_no_repos() {
    case "$PROVIDER" in
        github)
            if [ "$account_type" = "User" ]; then
                echo "No repositories found for personal account $ORG"
            else
                echo "No repositories found in $ORG (no team repos and an empty org repo list)"
            fi
            ;;
        gitlab)
            if [ -n "$FROM_LIST" ]; then
                echo "No repositories listed in $FROM_LIST"
            else
                echo "No repositories found on $GITLAB_HOST${GITLAB_GROUP:+ in $GITLAB_GROUP}"
            fi
            ;;
    esac
}

report_found_repos() {
    case "$PROVIDER" in
        github)
            if [ "$account_type" = "User" ]; then
                echo "Found $total_repos repositories for personal account $ORG"
            elif [ ${#user_teams[@]} -gt 0 ]; then
                echo "Found $total_repos unique repositories across ${#user_teams[@]} team(s) and the $ORG repo list"
            else
                echo "Found $total_repos repositories in $ORG"
            fi
            ;;
        gitlab)
            if [ -n "$FROM_LIST" ]; then
                echo "Found $total_repos repositories in $FROM_LIST"
            else
                echo "Found $total_repos unique projects across $gitlab_group_count group(s) on $GITLAB_HOST"
            fi
            ;;
    esac
}

report_summary_header() {
    case "$PROVIDER" in
        github)
            if [ "$account_type" = "User" ]; then
                echo -e "\n====== GitHub Sync Summary ($ORG - personal account) ======"
            elif [ ${#user_teams[@]} -gt 0 ]; then
                echo -e "\n====== GitHub Sync Summary ($ORG - ${#user_teams[@]} teams) ======"
                echo "Teams: ${user_teams[*]}"
            else
                echo -e "\n====== GitHub Sync Summary ($ORG) ======"
            fi
            ;;
        gitlab)
            echo -e "\n====== GitLab Sync Summary ($GITLAB_HOST${GITLAB_GROUP:+ - $GITLAB_GROUP}) ======"
            ;;
    esac
}

discover_repos() {
    case "$PROVIDER" in
        github) discover_github ;;
        gitlab)
            if [ -n "$FROM_LIST" ]; then
                read_from_list
            else
                discover_gitlab
            fi
            gitlab_reject_unsafe_paths
            ;;
        *) echo "Unknown provider: $PROVIDER"; exit 1 ;;
    esac
}

provider_preflight

# Derive the root, after preflight so a failed host check leaves no
# directory behind. An explicit --root must exist on either provider and in
# every mode, so a typo fails rather than starting a new tree. GitHub: the
# root defaults to cwd, and the org defaults to its basename, so dropping
# into ~/dev/github.com/<org> just works. GitLab: the root defaults to
# ~/dev/<host> with any :port dropped (gitlab.example.com:8443 syncs into
# ~/dev/gitlab.example.com), created on the first real sync. A GitLab
# preview run (--list-repos, --porcelain, --dry-run) creates no directory
# and writes no file under the root: the list may be produced on a machine
# that will never hold the checkouts.
gitlab_preview=false
if [ "$PROVIDER" = gitlab ] && { [ "$LIST_REPOS" = true ] || [ "$DRY_RUN" = true ]; }; then
    gitlab_preview=true
fi
if [ "$PROVIDER" = gitlab ] && [ -z "$ROOT" ]; then
    ROOT="$HOME/dev/${GITLAB_HOST%%:*}"
    [ "$gitlab_preview" = true ] || mkdir -p "$ROOT"
elif [ -z "$ROOT" ]; then
    ROOT="$PWD"
elif [[ ! -d "$ROOT" ]]; then
    echo "Error: root directory does not exist: $ROOT (create it first: mkdir -p \"$ROOT\")"
    exit 1
fi
# A previewed default root may not exist yet; keep it as written.
[[ -d "$ROOT" ]] && ROOT="$(cd "$ROOT" && pwd)"
if [ "$PROVIDER" = gitlab ]; then
    ORG="$GITLAB_HOST"
else
    ORG="${ORG:-$(basename "$ROOT")}"
fi

GITHUB_DEV_PATH="$ROOT"

# Check for timeout command
if ! command -v timeout &> /dev/null; then
    if command -v gtimeout &> /dev/null; then
        timeout() { gtimeout "$@"; }
        export -f timeout
    else
        echo "Note: timeout command not found. Install coreutils for timeout protection: brew install coreutils"
        timeout() { shift; "$@"; }
        export -f timeout
    fi
fi

if [ "$PROVIDER" = gitlab ]; then
    echo "Host: $GITLAB_HOST"
    [ -n "$GITLAB_GROUP" ] && echo "Group: $GITLAB_GROUP"
else
    echo "Org:  $ORG"
fi
echo "Root: $GITHUB_DEV_PATH"

account_type=""
user_teams=()
gitlab_group_count=0
all_repos_file=$(mktemp)
archived_repos_file=$(mktemp)
# Remove the temp files on any exit path, including the --list-teams early
# exits below which return before the explicit rm -f calls.
trap 'rm -f "$all_repos_file" "$archived_repos_file"' EXIT

discover_repos

# Deduplicate and sort on the relative path, so two repos that share a
# basename under different parents stay distinct.
mapfile -t repo_paths < <(sort -u "$all_repos_file")
rm -f "$all_repos_file"

# Save archived repos to persistent cache (handy for downstream tooling).
# A GitLab preview run writes nothing under the root.
if [ "$gitlab_preview" = true ]; then
    archived_count=$(sort -u "$archived_repos_file" | wc -l | tr -d ' ')
    echo "Found $archived_count archived repositories (skipped)"
else
    archived_repos_cache="${GITHUB_DEV_PATH}/.gh_archived_repos"
    sort -u "$archived_repos_file" > "$archived_repos_cache"
    archived_count=$(wc -l < "$archived_repos_cache" | tr -d ' ')
    echo "Found $archived_count archived repositories (cached to .gh_archived_repos)"
fi
rm -f "$archived_repos_file"

# Filter out blacklisted repos
filtered_repos=()
for repo in "${repo_paths[@]}"; do
    is_blacklisted=false
    for blacklisted in "${REPO_BLACKLIST[@]}"; do
        if [ "$repo" = "$blacklisted" ]; then
            is_blacklisted=true
            break
        fi
    done
    if [ "$is_blacklisted" = false ]; then
        filtered_repos+=("$repo")
    fi
done
repo_paths=("${filtered_repos[@]}")

if [ ${#REPO_BLACKLIST[@]} -gt 0 ]; then
    echo "Excluded ${#REPO_BLACKLIST[@]} blacklisted repo(s): ${REPO_BLACKLIST[*]}"
fi

total_repos=${#repo_paths[@]}

if [ "$total_repos" -eq 0 ]; then
    report_no_repos
    exit 0
fi

report_found_repos

if [ "$LIST_REPOS" = true ]; then
    if [ "$PORCELAIN_MODE" = true ]; then
        # Machine-readable: one repo name per line to real stdout (fd 3),
        # no prefix, no surrounding chatter.
        for repo in "${repo_paths[@]}"; do
            echo "$repo" >&3
        done
    else
        echo ""
        echo "Repositories accessible to you:"
        for repo in "${repo_paths[@]}"; do
            echo "  - $repo"
        done
    fi
    exit 0
fi

# Per-run status files live in a temp directory outside the root, so a dry
# run leaves the root untouched (and a missing default root missing).
# A real run also clears the in-root status directory older versions used.
[ "$DRY_RUN" = true ] || rm -rf "${GITHUB_DEV_PATH}/.gh_sync_tmp"
temp_dir=$(mktemp -d)
mkdir -p "$temp_dir/status"
status_dir="$temp_dir/status"
GHSYNC_STATUS_DIR="$status_dir"
trap 'rm -f "$all_repos_file" "$archived_repos_file"; rm -rf "$temp_dir"' EXIT

export QUIET_MODE DRY_RUN GITHUB_DEV_PATH ORG PROVIDER GITLAB_HOST GITLAB_SCHEME GHSYNC_STATUS_DIR

# Progress indicator
show_progress() {
    if [ "$QUIET_MODE" = false ]; then
        printf "."
    fi
}

clear_line() {
    printf "\r%*s\r" 80 ""
}

# Update worktrees for a repository. $1 is the repository's path relative to
# the root; the checkout directory takes the path's last segment.
update_worktrees() {
    local repo=$1
    local default_branch=$2
    local name="${repo##*/}"
    local main_path="${GITHUB_DEV_PATH}/$repo/${name}-main"
    local worktree_dir="${GITHUB_DEV_PATH}/$repo/worktree"

    if [ ! -d "$worktree_dir" ]; then
        return 0
    fi

    if [ -d "$main_path" ] && [ -d "$main_path/.git" ]; then
        (
        cd "$main_path" || return 1
        local worktrees=()
        while read -r line; do
            worktrees+=("$line")
        done < <(git worktree list | grep -v "$(pwd)" | awk '{print $1}')

        if [ ${#worktrees[@]} -eq 0 ]; then
            return 0
        fi

        for worktree in "${worktrees[@]}"; do
            if [ -d "$worktree" ]; then
                (
                cd "$worktree" || exit 1

                if ! git fetch origin &>/dev/null; then
                    clear_line
                    echo "*** Failed to fetch updates for worktree: $worktree"
                    return 1
                fi

                local current_branch
                current_branch=$(git branch --show-current)
                local can_pull=true

                # Any tracked change, staged or not, blocks the pull. The
                # status code has two columns (index, worktree), so match
                # either rather than only the first.
                if git status --porcelain -uno | grep -v '\.DS_Store' | grep -qE '^([MADRCU].|.[MADRCU])'; then
                    can_pull=false
                fi

                if [ "$current_branch" != "$default_branch" ]; then
                    can_pull=false
                fi

                if [ "$can_pull" = true ]; then
                    local_commit=$(git rev-parse HEAD)
                    remote_commit=$(git rev-parse "origin/$current_branch" 2>/dev/null || echo "")

                    if [ -n "$remote_commit" ] && [ "$local_commit" != "$remote_commit" ]; then
                        if [ "$DRY_RUN" = true ]; then
                            clear_line
                            echo "*** [DRY-RUN] Would update worktree: $repo:$worktree"
                        elif git pull --ff-only origin "$current_branch" &>/dev/null; then
                            clear_line
                            echo "*** Updated worktree: $repo:$worktree"
                            echo "$repo:$worktree" >> "${GHSYNC_STATUS_DIR}/worktree_updated"
                        else
                            clear_line
                            echo "*** Failed to update worktree: $repo:$worktree"
                        fi
                    fi
                fi
                )
            fi
        done
        )
    fi
}

# Ensure repository has the correct worktree structure
ensure_worktree_structure() {
    local repo=$1
    local name="${repo##*/}"
    local repo_path="${GITHUB_DEV_PATH}/$repo"

    if [ -d "$repo_path/${name}-main" ] && [ -d "$repo_path/${name}-main/.git" ]; then
        return 0
    fi

    if [ -d "$repo_path" ] && [ -d "$repo_path/.git" ]; then
        clear_line
        echo "*** Repository $repo exists but doesn't have the worktree structure"
        echo "*** To fix: move it aside and run this script again"
        echo "$repo" >> "${GHSYNC_STATUS_DIR}/wrong_structure"
        return 1
    fi

    return 1
}

# Clone or sync one repository. $1 is its path relative to the root
# (a/b/repo), laid out as <root>/a/b/repo/repo-main plus <root>/a/b/repo/worktree.
update_repo() {
    local repo=$1
    local name="${repo##*/}"
    local repo_path="${GITHUB_DEV_PATH}/$repo"
    local main_path="${repo_path}/${name}-main"
    local subshell_status=0

    show_progress

    if [ -d "$repo_path" ]; then
        if [ -d "$main_path" ] && [ -d "$main_path/.git" ]; then
            (
            cd "$main_path" &>/dev/null || {
                clear_line
                echo "*** Failed to cd to $main_path"
                echo "$repo" >> "${GHSYNC_STATUS_DIR}/failed"
                exit 1
            }

            local has_uncommitted=false
            local non_default_branch=false
            local can_update=true

            if ! git rev-parse --git-dir > /dev/null 2>&1; then
                clear_line
                echo "*** $repo is not a valid git repository"
                echo "$repo" >> "${GHSYNC_STATUS_DIR}/failed"
                exit 1
            fi

            # Ensure long path support is enabled (no-op if already set;
            # required on Windows for repos with deep directory trees)
            git config core.longpaths true 2>/dev/null || true

            # Any tracked change, staged or not, means fetch but do not pull.
            # The status code has two columns (index, worktree), so match
            # either rather than only the first.
            if git status --porcelain -uno | grep -qE '^([MADRCU].|.[MADRCU])'; then
                if [ "$QUIET_MODE" = false ]; then
                    clear_line
                    echo "*** $repo has uncommitted changes (will fetch but not pull)"
                fi
                echo "$repo" >> "${GHSYNC_STATUS_DIR}/uncommitted"
                has_uncommitted=true
                can_update=false
            fi

            current_branch=$(git branch --show-current)
            default_branch=$(git remote show origin 2>/dev/null | grep 'HEAD branch' | cut -d' ' -f5)

            if [ -z "$default_branch" ]; then
                default_branch=$(provider_default_branch "$repo")
            fi

            if [ -z "$default_branch" ]; then
                default_branch="main"
            fi

            if [ "$current_branch" != "$default_branch" ]; then
                if [ "$QUIET_MODE" = false ]; then
                    clear_line
                    echo "*** $repo is not on default branch ($current_branch != $default_branch)"
                fi
                echo "$repo ($current_branch)" >> "${GHSYNC_STATUS_DIR}/skipped"
                non_default_branch=true
                can_update=false
            fi

            if ! git fetch origin &>/dev/null; then
                if [ "$QUIET_MODE" = false ]; then
                    clear_line
                    echo "*** Failed to fetch updates for $repo"
                fi
                echo "$repo" >> "${GHSYNC_STATUS_DIR}/failed"
                exit 1
            fi

            if [ "$can_update" = true ]; then
                if ! git branch -r | grep -q "origin/$default_branch"; then
                    clear_line
                    echo "*** Remote branch $default_branch does not exist for $repo"
                    echo "$repo" >> "${GHSYNC_STATUS_DIR}/failed"
                    exit 1
                fi

                local_commit=$(git rev-parse HEAD)
                remote_commit=$(git rev-parse "origin/$default_branch")

                if [ "$local_commit" != "$remote_commit" ]; then
                    if [ "$DRY_RUN" = true ]; then
                        clear_line
                        echo "*** [DRY-RUN] Would update $repo"
                        echo "$repo" >> "${GHSYNC_STATUS_DIR}/updated"
                    elif git pull --ff-only origin "$default_branch" &>/dev/null; then
                        if [ "$QUIET_MODE" = false ]; then
                            clear_line
                            echo "*** Updated $repo"
                        fi
                        echo "$repo" >> "${GHSYNC_STATUS_DIR}/updated"
                    else
                        if [ "$QUIET_MODE" = false ]; then
                            clear_line
                            echo "*** Failed to update $repo"
                        fi
                        echo "$repo" >> "${GHSYNC_STATUS_DIR}/failed"
                        exit 1
                    fi
                else
                    echo "$repo" >> "${GHSYNC_STATUS_DIR}/current"
                fi

                update_worktrees "$repo" "$default_branch"
            fi
            exit 0
            )
            subshell_status=$?

            if [ $subshell_status -ne 0 ]; then
                clear_line
                echo "*** Subshell operations failed for $repo"
            fi

        elif [ -d "$repo_path/.git" ]; then
            clear_line
            echo "*** Repository $repo has unexpected structure"
            ensure_worktree_structure "$repo"
        else
            clear_line
            echo "*** $repo exists but is not a valid git repository"
            echo "$repo" >> "${GHSYNC_STATUS_DIR}/failed"
        fi
    else
        # Clone new repo
        clear_line
        echo "*** Cloning $repo"

        if [ "$DRY_RUN" = true ]; then
            echo "*** [DRY-RUN] Would clone $repo into proper structure"
            echo "$repo" >> "${GHSYNC_STATUS_DIR}/updated"
        else
            mkdir -p "$main_path"
            mkdir -p "$repo_path/worktree"

            if provider_clone "$repo" "$main_path"; then
                # Enable long path support — required on Windows for repos with
                # deep directory trees (200+ char filenames)
                git -C "$main_path" config core.longpaths true
                echo "$repo" >> "${GHSYNC_STATUS_DIR}/updated"
                clear_line
                echo "*** Cloned $repo"
            else
                clear_line
                echo "*** Failed to clone $repo"
                echo "$repo" >> "${GHSYNC_STATUS_DIR}/failed"
                # Remove only what this clone created: the checkout, the empty
                # worktree dir, then each parent directory left empty up to
                # the root. A shared parent (a/b for a/b/repo) that holds
                # another checkout is not empty, so rmdir leaves it alone.
                rm -rf "$main_path" 2>/dev/null || true
                rmdir "$repo_path/worktree" 2>/dev/null || true
                local dir="$repo_path"
                while [ "$dir" != "$GITHUB_DEV_PATH" ] && rmdir "$dir" 2>/dev/null; do
                    dir="${dir%/*}"
                done
            fi
        fi
    fi
}

# Clean up status files
rm -f "${GHSYNC_STATUS_DIR}"/{updated,failed,skipped,uncommitted,current,worktree_updated,wrong_structure} 2>/dev/null || true

# Apply limit if specified
if [ "$REPO_LIMIT" -gt 0 ] && [ "$REPO_LIMIT" -lt "$total_repos" ]; then
    repo_paths=("${repo_paths[@]:0:$REPO_LIMIT}")
    echo "Limited to $REPO_LIMIT repositories for processing"
fi

if [ "$DRY_RUN" = true ]; then
    echo "[DRY-RUN MODE - no changes will be made]"
fi

# Handle interruptions
trap 'echo -e "\n\nInterrupted. Showing partial results..."; wait; break_early=true' INT TERM

break_early=false
i=0
batch_size=5

if [ "$QUIET_MODE" = false ]; then
    printf "Processing repositories "
fi

for repo in "${repo_paths[@]}"; do
    if [ "$break_early" = true ]; then
        break
    fi

    {
        export GITHUB_DEV_PATH="$GITHUB_DEV_PATH"
        export ORG="$ORG"
        export DRY_RUN="$DRY_RUN"
        export PROVIDER="$PROVIDER"
        timeout 300 bash -c "
            $(declare -f update_repo update_worktrees ensure_worktree_structure clear_line show_progress provider_clone provider_default_branch gitlab_clone_url)
            update_repo '$repo'
        " || {
            clear_line
            echo "*** Timeout processing $repo"
            echo "$repo" >> "${GHSYNC_STATUS_DIR}/failed"
            show_progress
        }
    } &

    if (( ++i % batch_size == 0 )); then
        wait
    fi
done

wait

if [ "$QUIET_MODE" = false ]; then
    printf " done!\n"
fi

# Print summary
report_summary_header

updated_count=$([ -f "$status_dir/updated" ] && wc -l < "$status_dir/updated" | tr -d ' ' || echo 0)
current_count=$([ -f "$status_dir/current" ] && wc -l < "$status_dir/current" | tr -d ' ' || echo 0)
failed_count=$([ -f "$status_dir/failed" ] && wc -l < "$status_dir/failed" | tr -d ' ' || echo 0)
skipped_count=$([ -f "$status_dir/skipped" ] && wc -l < "$status_dir/skipped" | tr -d ' ' || echo 0)
uncommitted_count=$([ -f "$status_dir/uncommitted" ] && wc -l < "$status_dir/uncommitted" | tr -d ' ' || echo 0)
worktree_count=$([ -f "$status_dir/worktree_updated" ] && wc -l < "$status_dir/worktree_updated" | tr -d ' ' || echo 0)
wrong_structure_count=$([ -f "$status_dir/wrong_structure" ] && wc -l < "$status_dir/wrong_structure" | tr -d ' ' || echo 0)

echo "Up-to-date: $current_count | Updated: $updated_count | Failed: $failed_count | Uncommitted: $uncommitted_count | Branch issues: $skipped_count | Worktrees: $worktree_count | Wrong structure: $wrong_structure_count"

if [ -f "$status_dir/failed" ]; then
    echo -e "\nFailed ($failed_count):"
    sort "$status_dir/failed"
fi

if [ -f "$status_dir/skipped" ]; then
    echo -e "\nNot on default branch ($skipped_count):"
    sort "$status_dir/skipped"
fi

if [ -f "$status_dir/uncommitted" ]; then
    echo -e "\nUncommitted changes ($uncommitted_count):"
    sort "$status_dir/uncommitted"
fi

if [ -f "$status_dir/wrong_structure" ]; then
    echo -e "\nWrong structure ($wrong_structure_count):"
    sort "$status_dir/wrong_structure"
fi

if [ -f "$status_dir/updated" ] && [ "$updated_count" -gt 0 ]; then
    echo -e "\nUpdated ($updated_count):"
    sort "$status_dir/updated"
fi

if [ -f "$status_dir/worktree_updated" ] && [ "$worktree_count" -gt 0 ]; then
    echo -e "\nWorktrees updated ($worktree_count):"
    sort "$status_dir/worktree_updated"
fi

# Cleanup
rm -rf "$temp_dir"
