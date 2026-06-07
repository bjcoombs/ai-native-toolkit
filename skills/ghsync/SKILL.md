---
name: ghsync
description: "Bulk clone and keep in sync every GitHub repo you can access across an org or personal account. Discovers all repos reachable through the teams you belong to (or the account's repo list for a personal account), deduplicates them, then clones new ones and fast-forward syncs existing ones (and their worktrees) into a consistent <repo>/<repo>-main + <repo>/worktree layout. The org defaults to the directory you run from, so dropping into ~/dev/github.com/<org> mirrors that org. TRIGGER when the user types /ghsync, wants to clone or sync all org repos or their own personal repos, asks to mirror everything they have access to, is onboarding into a new enterprise/org, or wants to refresh their local checkouts to latest."
---

# ghsync: mirror and sync every repo you can access

Onboarding into a new enterprise means finding and cloning dozens of repos by
hand, then drifting out of date. This skill does both halves: it **discovers**
everything you can reach and **keeps it in sync**.

- **First run** — clones every accessible repo into a worktree-friendly layout.
- **Every run after** — fast-forward updates repos already on their default
  branch, updates their worktrees, and clones anything new. It never clobbers
  local work: repos with uncommitted changes or on a non-default branch are
  fetched but not pulled, and reported in the summary.

It is **org-agnostic** — point it at any GitHub org, GitHub Enterprise host, or
**personal account**. The script detects the account type itself: organizations
are discovered through team membership, personal accounts through the account's
repo list (private repos included when it's your own account).

## How to run it

The script lives next to this file. Resolve its directory the same way the
other skills do (works whether installed as a plugin or hand-placed under
`~/.claude/skills/`):

```bash
SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/ghsync}"
SKILL_DIR="${SKILL_DIR:-$(dirname "$(realpath ~/.claude/skills/ghsync/SKILL.md)")}"

# Org is derived from the directory you run in. To mirror the "meridianhub"
# org, run from a directory named meridianhub:
cd ~/dev/github.com/meridianhub
bash "$SKILL_DIR/scripts/ghsync.sh"
```

The org defaults to the **basename of the directory you launch from**. So
running inside `~/dev/github.com/meridianhub` syncs the `meridianhub` org into
that directory. Override the org name or target directory explicitly when they
differ:

```bash
bash "$SKILL_DIR/scripts/ghsync.sh" --org meridianhub --root ~/dev/github.com/meridianhub
```

## What to do when invoked

1. **Confirm the target.** Run with `--list-repos` first (or `--list-teams`
   for an org) so the user sees which account and how many repos before any
   cloning. This doubles as a check that the derived account name is correct.
2. **Dry run on first use against a new org** (`--dry-run`) to preview clones
   and updates without touching disk.
3. **Run the real sync.** Stream the output; the run ends with a summary
   (up-to-date / updated / failed / uncommitted / branch issues / worktrees).
4. **Surface the exceptions.** Call out anything under *Failed*, *Uncommitted
   changes*, or *Not on default branch* — these are the repos the user may need
   to deal with manually.

## Flags

| Flag | Effect |
|------|--------|
| `--org NAME` | Org or personal account to sync (default: basename of `--root` / cwd) |
| `--root DIR` | Directory to clone into (default: current directory) |
| `--list-teams` | List your teams in the org (with repo counts) and exit; orgs only |
| `--list-repos` | List the deduplicated accessible repos and exit |
| `--dry-run` | Show what would be cloned/updated without making changes |
| `--limit N` | Process only the first N repos (useful for a quick test) |
| `--quiet` | Suppress per-repository chatter; keep the final summary |

## Layout produced

```
<root>/<repo>/
  ├── <repo>-main/   # default-branch checkout, kept clean and up to date
  └── worktree/      # ready for `git worktree add ...`
```

This matches the worktree convention the rest of the toolkit assumes:
`<repo>-main` stays on the default branch and clean, and feature work happens in
sibling worktrees.

## Sync safety rules

- Repos with **uncommitted changes** are fetched but not pulled (reported under
  *Uncommitted changes*).
- Repos **not on their default branch** are fetched but not pulled (reported
  under *Not on default branch*).
- Updates are **fast-forward only** (`git pull --ff-only`) — no merge commits,
  no rebases, never a force.
- Worktrees on the default branch with no local changes are fast-forwarded too.
- A repo that exists locally without the `<repo>-main` structure is flagged
  (*Wrong structure*) rather than touched.

## Requirements & configuration

- **`gh` and `jq`** on `PATH`, and `gh auth status` must be authenticated. The
  script checks both up front.
- **GitHub Enterprise:** `gh` uses its configured host. Target a GHE instance by
  setting `GH_HOST=github.example.com` or running
  `gh auth login --hostname github.example.com` first.
- **Access model:** for an **organization**, repos are discovered through the
  **teams you belong to** (`gh api user/teams`), then deduplicated — repos you
  can only reach via direct collaborator grants outside any team are not
  included. For a **personal account**, repos come from `gh repo list <account>`
  (includes private repos when authenticated as that account); empty repos with
  no default branch are skipped.
- **Blacklist:** export `GHSYNC_BLACKLIST="repo-a repo-b"` to skip oversized or
  problematic repos.
- **`timeout`:** optional but recommended (`brew install coreutils`) — caps each
  repo at 5 minutes so one hung clone can't stall the run.
- **Archived repos** are skipped from cloning and cached to
  `<root>/.gh_archived_repos` for reference.
