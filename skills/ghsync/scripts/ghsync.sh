#!/bin/bash
set -euo pipefail

# ===================================================================
# ghsync.sh - Bulk clone + sync every repo you can access in an org
#
# Discovers every GitHub repository you can access in an org - both through
# the teams you belong to and through the org's repo list (which surfaces
# public repos and repos reachable via direct collaborator grants outside
# any team) - deduplicates them, and clones or fast-forward updates each
# into a worktree-friendly layout. Built for onboarding into a new
# enterprise: drop into the org directory, run it, and you have a local
# mirror of everything you can touch.
#
# Personal accounts work too: when the target is a User rather than an
# Organization, repos are discovered from the account's repo list instead
# of team membership (empty repos, which have no default branch, are
# skipped).
#
# Org detection:
#   The org defaults to the basename of the directory you run from, so
#   running inside ~/dev/github.com/meridianhub syncs the "meridianhub"
#   org into that directory. Override with --org / --root.
#
# Layout produced (per repo, under the org root):
#   <root>/<repo>/
#     ├── <repo>-main/   # default-branch checkout
#     └── worktree/      # empty dir, ready for git worktrees
#
# GitHub Enterprise: gh uses its configured host. To target a GHE host,
# set GH_HOST (e.g. GH_HOST=github.example.com) or run `gh auth login
# --hostname github.example.com` first.
#
# Usage: ./ghsync.sh [--org NAME] [--root DIR] [--quiet] [--limit N]
#                    [--dry-run] [--list-teams] [--list-repos]
#   --org NAME     Org to sync (default: basename of --root / cwd)
#   --root DIR     Directory to clone into (default: current directory)
#   --quiet        Suppress per-repository messages during sync
#   --limit N      Process only N repositories (useful for testing)
#   --dry-run      Show what would be done without making changes
#   --list-teams   List your teams in the org and exit (orgs only)
#   --list-repos   List the deduplicated accessible repos and exit
# ===================================================================

# Process command line args
ORG=""
ROOT=""
QUIET_MODE=false
REPO_LIMIT=0
DRY_RUN=false
LIST_TEAMS=false
LIST_REPOS=false

# Repositories to never sync (oversized, checkout issues, etc.).
# Override per-machine by exporting GHSYNC_BLACKLIST as a space-separated
# list before running.
read -r -a REPO_BLACKLIST <<< "${GHSYNC_BLACKLIST:-}"

while [[ $# -gt 0 ]]; do
  case $1 in
    --org)
      ORG=$2
      shift 2
      ;;
    --org=*)
      ORG=${1#*=}
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
      shift
      ;;
    --list-repos)
      LIST_REPOS=true
      shift
      ;;
    -h|--help)
      sed -n '4,41p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [--org NAME] [--root DIR] [--quiet] [--limit N] [--dry-run] [--list-teams] [--list-repos]"
      exit 1
      ;;
  esac
done

# Derive root and org. Root is where repos land (default: cwd). Org defaults
# to the basename of root, so dropping into ~/dev/github.com/<org> just works.
ROOT="${ROOT:-$PWD}"
if [[ ! -d "$ROOT" ]]; then
    echo "Error: root directory does not exist: $ROOT"
    exit 1
fi
ROOT="$(cd "$ROOT" && pwd)"
ORG="${ORG:-$(basename "$ROOT")}"

GITHUB_DEV_PATH="$ROOT"

# Check dependencies
for cmd in gh jq; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "$cmd command not found. Install it using: brew install $cmd"
        exit 1
    fi
done

# Verify gh is authenticated before we make a flurry of API calls
if ! gh auth status &> /dev/null; then
    echo "Error: gh is not authenticated. Run: gh auth login"
    [[ -n "${GH_HOST:-}" ]] && echo "  (targeting host: $GH_HOST)"
    exit 1
fi

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

echo "Org:  $ORG"
echo "Root: $GITHUB_DEV_PATH"

# Detect account type: organizations are discovered via team membership,
# personal accounts via their repo list. A 404 here means the name itself
# is wrong (typo, or the directory name doesn't match any GitHub account).
account_type=$(gh api "users/$ORG" --jq '.type' 2>/dev/null) || {
    echo "Error: GitHub account '$ORG' not found"
    echo "  - Check the org name (it defaults to the directory you ran from: $(basename "$ROOT"))"
    echo "  - Override with --org NAME if the directory name differs from the org"
    exit 1
}

user_teams=()
all_repos_file=$(mktemp)
archived_repos_file=$(mktemp)
# Remove the temp files on any exit path, including the --list-teams early
# exits below which return before the explicit rm -f calls.
trap 'rm -f "$all_repos_file" "$archived_repos_file"' EXIT

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

# Deduplicate and sort
github_repos=($(sort -u "$all_repos_file"))
rm -f "$all_repos_file"

# Save archived repos to persistent cache (handy for downstream tooling)
archived_repos_cache="${GITHUB_DEV_PATH}/.gh_archived_repos"
sort -u "$archived_repos_file" > "$archived_repos_cache"
archived_count=$(wc -l < "$archived_repos_cache" | tr -d ' ')
rm -f "$archived_repos_file"
echo "Found $archived_count archived repositories (cached to .gh_archived_repos)"

# Filter out blacklisted repos
filtered_repos=()
for repo in "${github_repos[@]}"; do
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
github_repos=("${filtered_repos[@]}")

if [ ${#REPO_BLACKLIST[@]} -gt 0 ]; then
    echo "Excluded ${#REPO_BLACKLIST[@]} blacklisted repo(s): ${REPO_BLACKLIST[*]}"
fi

total_repos=${#github_repos[@]}

if [ "$total_repos" -eq 0 ]; then
    if [ "$account_type" = "User" ]; then
        echo "No repositories found for personal account $ORG"
    else
        echo "No repositories found in $ORG (no team repos and an empty org repo list)"
    fi
    exit 0
fi

if [ "$account_type" = "User" ]; then
    echo "Found $total_repos repositories for personal account $ORG"
elif [ ${#user_teams[@]} -gt 0 ]; then
    echo "Found $total_repos unique repositories across ${#user_teams[@]} team(s) and the $ORG repo list"
else
    echo "Found $total_repos repositories in $ORG"
fi

if [ "$LIST_REPOS" = true ]; then
    echo ""
    echo "Repositories accessible to you:"
    for repo in "${github_repos[@]}"; do
        echo "  - $repo"
    done
    exit 0
fi

# Create temp directory for status files
temp_dir="${GITHUB_DEV_PATH}/.gh_sync_tmp"
rm -rf "$temp_dir"
mkdir -p "$temp_dir/status"
status_dir="$temp_dir/status"

export QUIET_MODE DRY_RUN GITHUB_DEV_PATH ORG

# Progress indicator
show_progress() {
    if [ "$QUIET_MODE" = false ]; then
        printf "."
    fi
}

clear_line() {
    printf "\r%*s\r" 80 ""
}

# Update worktrees for a repository
update_worktrees() {
    local repo=$1
    local default_branch=$2
    local main_path="${GITHUB_DEV_PATH}/$repo/${repo}-main"
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

                if git status --porcelain -uno | grep -v '\.DS_Store' | grep -q '^[MADRCU]'; then
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
                            echo "$repo:$worktree" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/worktree_updated"
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
    local repo_path="${GITHUB_DEV_PATH}/$repo"

    if [ -d "$repo_path/${repo}-main" ] && [ -d "$repo_path/${repo}-main/.git" ]; then
        return 0
    fi

    if [ -d "$repo_path" ] && [ -d "$repo_path/.git" ]; then
        clear_line
        echo "*** Repository $repo exists but doesn't have the worktree structure"
        echo "*** To fix: move it aside and run this script again"
        echo "$repo" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/wrong_structure"
        return 1
    fi

    return 1
}

update_repo() {
    local repo=$1
    local repo_path="${GITHUB_DEV_PATH}/$repo"
    local main_path="${repo_path}/${repo}-main"
    local subshell_status=0

    show_progress

    if [ -d "$repo_path" ]; then
        if [ -d "$main_path" ] && [ -d "$main_path/.git" ]; then
            (
            cd "$main_path" &>/dev/null || {
                clear_line
                echo "*** Failed to cd to $main_path"
                echo "$repo" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/failed"
                exit 1
            }

            local has_uncommitted=false
            local non_default_branch=false
            local can_update=true

            if ! git rev-parse --git-dir > /dev/null 2>&1; then
                clear_line
                echo "*** $repo is not a valid git repository"
                echo "$repo" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/failed"
                exit 1
            fi

            # Ensure long path support is enabled (no-op if already set;
            # required on Windows for repos with deep directory trees)
            git config core.longpaths true 2>/dev/null || true

            if git status --porcelain -uno | grep -q '^[MADRCU]'; then
                if [ "$QUIET_MODE" = false ]; then
                    clear_line
                    echo "*** $repo has uncommitted changes (will fetch but not pull)"
                fi
                echo "$repo" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/uncommitted"
                has_uncommitted=true
                can_update=false
            fi

            current_branch=$(git branch --show-current)
            default_branch=$(git remote show origin 2>/dev/null | grep 'HEAD branch' | cut -d' ' -f5)

            if [ -z "$default_branch" ]; then
                default_branch=$(gh repo view "$ORG/$repo" --json defaultBranchRef --jq '.defaultBranchRef.name' 2>/dev/null || echo "main")
            fi

            if [ -z "$default_branch" ]; then
                default_branch="main"
            fi

            if [ "$current_branch" != "$default_branch" ]; then
                if [ "$QUIET_MODE" = false ]; then
                    clear_line
                    echo "*** $repo is not on default branch ($current_branch != $default_branch)"
                fi
                echo "$repo ($current_branch)" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/skipped"
                non_default_branch=true
                can_update=false
            fi

            if ! git fetch origin &>/dev/null; then
                if [ "$QUIET_MODE" = false ]; then
                    clear_line
                    echo "*** Failed to fetch updates for $repo"
                fi
                echo "$repo" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/failed"
                exit 1
            fi

            if [ "$can_update" = true ]; then
                if ! git branch -r | grep -q "origin/$default_branch"; then
                    clear_line
                    echo "*** Remote branch $default_branch does not exist for $repo"
                    echo "$repo" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/failed"
                    exit 1
                fi

                local_commit=$(git rev-parse HEAD)
                remote_commit=$(git rev-parse "origin/$default_branch")

                if [ "$local_commit" != "$remote_commit" ]; then
                    if [ "$DRY_RUN" = true ]; then
                        clear_line
                        echo "*** [DRY-RUN] Would update $repo"
                        echo "$repo" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/updated"
                    elif git pull --ff-only origin "$default_branch" &>/dev/null; then
                        if [ "$QUIET_MODE" = false ]; then
                            clear_line
                            echo "*** Updated $repo"
                        fi
                        echo "$repo" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/updated"
                    else
                        if [ "$QUIET_MODE" = false ]; then
                            clear_line
                            echo "*** Failed to update $repo"
                        fi
                        echo "$repo" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/failed"
                        exit 1
                    fi
                else
                    echo "$repo" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/current"
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
            echo "$repo" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/failed"
        fi
    else
        # Clone new repo
        clear_line
        echo "*** Cloning $repo"

        if [ "$DRY_RUN" = true ]; then
            echo "*** [DRY-RUN] Would clone $repo into proper structure"
            echo "$repo" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/updated"
        else
            mkdir -p "$repo_path/${repo}-main"
            mkdir -p "$repo_path/worktree"

            if gh repo clone "$ORG/$repo" "$repo_path/${repo}-main" 2>&1; then
                # Enable long path support — required on Windows for repos with
                # deep directory trees (200+ char filenames)
                git -C "$repo_path/${repo}-main" config core.longpaths true
                echo "$repo" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/updated"
                clear_line
                echo "*** Cloned $repo"
            else
                clear_line
                echo "*** Failed to clone $repo"
                echo "$repo" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/failed"
                rm -rf "$repo_path" 2>/dev/null || true
            fi
        fi
    fi
}

# Clean up status files
rm -f "${GITHUB_DEV_PATH}/.gh_sync_tmp/status"/{updated,failed,skipped,uncommitted,current,worktree_updated,wrong_structure} 2>/dev/null || true

# Apply limit if specified
if [ "$REPO_LIMIT" -gt 0 ] && [ "$REPO_LIMIT" -lt "$total_repos" ]; then
    github_repos=("${github_repos[@]:0:$REPO_LIMIT}")
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

for repo in "${github_repos[@]}"; do
    if [ "$break_early" = true ]; then
        break
    fi

    {
        export GITHUB_DEV_PATH="$GITHUB_DEV_PATH"
        export ORG="$ORG"
        export DRY_RUN="$DRY_RUN"
        timeout 300 bash -c "
            $(declare -f update_repo update_worktrees ensure_worktree_structure clear_line show_progress)
            update_repo '$repo'
        " || {
            clear_line
            echo "*** Timeout processing $repo"
            echo "$repo" >> "${GITHUB_DEV_PATH}/.gh_sync_tmp/status/failed"
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
if [ "$account_type" = "User" ]; then
    echo -e "\n====== GitHub Sync Summary ($ORG - personal account) ======"
elif [ ${#user_teams[@]} -gt 0 ]; then
    echo -e "\n====== GitHub Sync Summary ($ORG - ${#user_teams[@]} teams) ======"
    echo "Teams: ${user_teams[*]}"
else
    echo -e "\n====== GitHub Sync Summary ($ORG) ======"
fi

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
