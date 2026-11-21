---
name: ghsync
description: "Bulk clone and keep in sync every repo you can access on GitHub or GitLab. GitHub: an org's repos via your teams plus the org repo list, or a personal account's repos. GitLab (--provider gitlab --host <host>): every project in every group you belong to, with nested subgroups and projects shared into those groups, laid out under its path_with_namespace; --group narrows to one subtree, and --porcelain / --from-list split discovery from cloning for hosts reachable only from another network. Deduplicates, then clones new repos and fast-forward syncs existing ones (and their worktrees) into a <path>/<repo>-main + <path>/worktree layout. TRIGGER when the user types /ghsync, wants to clone or sync all org repos, their personal repos or all GitLab group projects, asks to mirror everything they have access to on GitHub or a GitLab host, is onboarding into a new enterprise, org or GitLab group, or wants to refresh their local checkouts to latest."
---

# ghsync: mirror and sync every repo you can access on GitHub or GitLab

Onboarding into a new enterprise means finding and cloning dozens of repos by
hand, then drifting out of date. This skill does both halves: it **discovers**
everything you can reach and **keeps it in sync**.

- **First run** — clones every accessible repo into a worktree-friendly layout.
- **Every run after** — fast-forward updates repos already on their default
  branch, updates their worktrees, and clones anything new. It never clobbers
  local work: repos with uncommitted changes or on a non-default branch are
  fetched but not pulled, and reported in the summary.

It is **org-agnostic**: point it at any GitHub org, GitHub Enterprise host,
**personal account**, or a **GitLab host** (gitlab.com or self-managed) with
`--provider gitlab`; see [GitLab](#gitlab) below. On GitHub: the script detects the account type itself. For an
organization it unions the teams you belong to with the org's repo list (so
direct-collaborator and public repos are included even with no team
membership); for a personal account it uses the account's repo list (private
repos included when it's your own account).

## How to run it

The script lives next to this file. Claude Code writes this skill's directory
into the script paths below when it loads the skill, so each path is already
absolute:

```bash
# Org is derived from the directory you run in. To mirror the "meridianhub"
# org, run from a directory named meridianhub:
cd ~/dev/github.com/meridianhub
bash "${CLAUDE_SKILL_DIR}/scripts/ghsync.sh"
```

The org defaults to the **basename of the directory you launch from**. So
running inside `~/dev/github.com/meridianhub` syncs the `meridianhub` org into
that directory. Override the org name or target directory explicitly when they
differ:

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/ghsync.sh" --org meridianhub --root ~/dev/github.com/meridianhub
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
| `--provider github\|gitlab` | Which host type to sync (default: `github`) |
| `--root DIR` | Directory to clone into; an explicit `--root` must already exist, in every mode (GitHub default: current directory; GitLab default: `~/dev/<host>` with any `:port` dropped, created on the first real sync) |
| `--list-repos` | List the deduplicated accessible repos and exit |
| `--porcelain` | Machine-readable repo list: one path per line to stdout (GitHub: the repo name; GitLab: `path_with_namespace`), all chatter to stderr; implies `--list-repos`. Consumed by `/ghreport` and by `--from-list`. |
| `--dry-run` | Show what would be cloned/updated without making changes. On GitLab it creates no directory and writes no `.gh_archived_repos`; on GitHub it still refreshes `.gh_archived_repos` in the root |
| `--limit N` | Process only the first N repos (useful for a quick test) |
| `--quiet` | Suppress per-repository chatter, including the `Cloning` / `Cloned` lines; keep failures and the final summary |
| `--porcelain-json` | Like `--porcelain`, but one JSON object per line: `path`, `default_branch`, `last_activity_at`, `head_committed_at`, `archived`, `forked_from` (path or `null`), `mirror` and `head_sha`. Reads each default-branch head (one API call per repository). See [Active repositories and metadata](#active-repositories-and-metadata) |
| `--active-since DURATION` | Keep only repositories with a default-branch commit inside the window (`90d`, `2w`, `6mo`, `1y`). With `--dry-run`, prints qualifying and total counts per group. See [Active repositories and metadata](#active-repositories-and-metadata) |
| `--partial` | Shorthand for `--filter blob:none` |
| `--filter SPEC` | New clones only: pass `--filter=SPEC` to `git clone` (after `--` on `gh repo clone`). `blob:none` keeps full history and fetches file contents only for the checkout. See [Analysis-only mirrors](#analysis-only-mirrors) |
| `--shallow-since DATE` | New clones only: pass `--shallow-since=DATE` to `git clone`, cutting history before DATE (single-branch). See [Analysis-only mirrors](#analysis-only-mirrors) |
| `--org NAME` | GitHub only. Org or personal account to sync (default: basename of `--root` / cwd) |
| `--list-teams` | GitHub only. List your teams in the org (with repo counts) and exit; orgs only |
| `--host HOST` | GitLab only. Host to sync (default: `gitlab.com`); `https://` and a trailing `/` are accepted, and so is a web port (`host:8443`) |
| `--group PATH` | GitLab only. Sync one group and its subgroups instead of every group you belong to. Covers every project the group shows you, including when you belong only to a subgroup of it (projects directly in the group and in sibling subgroups count). If the group itself cannot be read, the run syncs your subgroups under it and prints one stderr warning naming them and what was skipped; it fails only when nothing under the group is reachable |
| `--from-list FILE` | GitLab only. Sync the paths in FILE (the `--porcelain` format; blank lines and `#` comments ignored) with no discovery and no `glab` |

A flag that belongs to the other provider is rejected with an error, not
ignored.

## GitLab

```bash
# Every project in every group you belong to on gitlab.com, into ~/dev/gitlab.com/
bash "${CLAUDE_SKILL_DIR}/scripts/ghsync.sh" --provider gitlab --list-repos
bash "${CLAUDE_SKILL_DIR}/scripts/ghsync.sh" --provider gitlab

# One group subtree on a self-managed host, into ~/dev/gitlab.example.com/
bash "${CLAUDE_SKILL_DIR}/scripts/ghsync.sh" --provider gitlab --host gitlab.example.com --group platform/infra
```

- **Discovery** runs through `glab api --paginate`: the groups you belong to
  (`groups?min_access_level=10`), each group's `descendant_groups`, and each
  group's `projects` and `projects/shared`. Dedupe is on
  `path_with_namespace`, so a project reachable directly and through sharing,
  or shared into two groups, is cloned once.
- **Layout:** each project lands under its own `path_with_namespace`, so
  nested subgroups become nested directories and a shared project from
  another namespace sits under that namespace, not under the group it was
  shared into: `~/dev/<host>/<group>/<subgroup>/<repo>/<repo>-main` plus a
  sibling `worktree/`.
- **Archived** and **empty** projects are skipped, as on GitHub.
- **Clone URLs** are built from the host: `https://<host>/<path>.git`, or
  `git@<host>:<path>.git` with `GHSYNC_GIT_PROTOCOL=ssh`. For HTTPS on a
  private project, let `glab auth login` set up git credentials. A port on
  `--host` is the web and API port: it stays in the HTTPS URL and is dropped
  from the SSH URL. Set a non-standard SSH port for the host in
  `~/.ssh/config`.
- **Default root:** `~/dev/<host>`, with any port dropped, so
  `--host gitlab.example.com:8443` syncs into `~/dev/gitlab.example.com/`.
  Two instances on one host name but different ports would share that
  root: give one an explicit `--root`.
- **Preview runs** (`--list-repos`, `--porcelain`, `--dry-run`) create no
  directory and write no `.gh_archived_repos`, so the list can be produced
  on a machine that will never hold the checkouts. An explicit `--root`
  must still exist.
- **Path rules** follow GitLab's (`lib/gitlab/path_regex.rb`): a segment
  starts with a letter, digit, `_` or `.` (so `grp/.dotfiles` syncs), then
  letters, digits, `_`, `-` and `.`. Segments `.` and `..`, any segment
  ending in `.git` or `.atom`, and `.git` in any case are skipped with a
  warning. So is a first segment, in any case, that names one of ghsync's
  own root entries (`.gh_archived_repos`, or `.gh_sync_tmp`, which older
  versions delete on every run); deeper segments may use those names.
- **Fail fast:** before any discovery call, the script checks that `glab` and
  `jq` are installed and that `glab auth status --hostname <host>` passes. A
  failure prints one line naming the host and the check (installed,
  authentication or reachability) and exits non-zero. It does not retry, and
  it never tunnels or works around network controls.

### Hosts reachable only from another network

Discovery needs the GitLab API; cloning needs only git. When the two are
reachable from different places, split the run:

```bash
# 1. Where the API answers: save the project list.
bash "${CLAUDE_SKILL_DIR}/scripts/ghsync.sh" --provider gitlab --host gitlab.example.com --porcelain > projects.txt

# 2. Where git reaches the host: clone and sync from the list. No glab needed.
bash "${CLAUDE_SKILL_DIR}/scripts/ghsync.sh" --provider gitlab --host gitlab.example.com --from-list projects.txt
```

Step 2 builds the same layout as a discovery run and makes no API call.
Edit the list to drop projects; `--limit` applies to it too.

## Active repositories and metadata

For a fleet measurement across hundreds of repositories, `--active-since`
drops the dormant ones and `--porcelain-json` hands each repository's
metadata to the next tool, so it does not have to call the API again:

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/ghsync.sh" --provider gitlab --host gitlab.example.com --porcelain-json --active-since 6mo > projects.jsonl
bash "${CLAUDE_SKILL_DIR}/scripts/ghsync.sh" --org my-org --dry-run --active-since 90d
```

- **Active means a commit on the default branch.** Discovery's own
  activity date is only a prefilter: GitHub's `pushed_at` moves for a
  push to any branch, and GitLab's `last_activity_at` moves for issue and
  merge request activity too. A repository whose activity date is older
  than the window is dropped without another call; every other one is
  confirmed with the committed date of its default branch's head commit,
  one API call each. A repository whose head cannot be read is dropped
  with a warning on stderr.
- **Durations** are `Nd`, `Nw`, `Nmo` (30 days) or `Ny` (365 days),
  counted back from now.
- **`--dry-run --active-since`** prints the qualifying and total counts per
  GitLab group, where a project counts in each group whose listing
  returned it (its own group and any group it is shared into). On GitHub,
  where teams overlap, it prints one count for the whole org or account.
  These lines print under `--quiet` too.
- **`--porcelain-json` fields.** `last_activity_at` is GitHub's `pushed_at`
  or GitLab's `last_activity_at`. `forked_from` is the parent's path
  (`owner/name` on GitHub) or `null`. `mirror` is `true` or `false`, or
  `null` when the token cannot read it (GitLab shows it only to some
  roles); `null` never means "not a mirror". `head_sha` and
  `head_committed_at` are `null` when the head could not be read.
- **Plain `--porcelain` is unchanged**: one path per line, no extra API
  calls, so `/ghreport` and `--from-list` keep working. Neither new flag
  works with `--from-list`, which makes no API call to read metadata from.

## Analysis-only mirrors

For a root that is cloned to be scanned once (for example by `/assess`)
rather than worked in, `--partial` and `--shallow-since` cut clone time
and disk:

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/ghsync.sh" --org my-org --partial
bash "${CLAUDE_SKILL_DIR}/scripts/ghsync.sh" --org my-org --shallow-since 2025-01-01
```

- **Partial clones still fetch blobs for content-reading scans.** A
  `blob:none` clone has every commit and tree, so `git log --name-only` and
  churn and staleness scans match a full clone. Any git command that reads
  file contents from history fetches the missing blobs lazily from the
  remote, one round trip at a time: `git log -M` rename detection,
  `--numstat`, and `git blame` in the `/assess` scans all do. Expect those
  scans to be slow and to need network access.
- **`--shallow-since` drops history before DATE**, which changes churn and
  staleness results. The clone is single-branch, so a worktree added
  later for another branch never sees that branch move. A repository with no commits after DATE may come back
  with its full history instead.
- **Existing clones are never converted.** These flags apply only to
  repositories cloned in this run; a sync of an existing clone only
  fetches and fast-forwards, so a full clone stays full. Git records the
  mode in each clone, so downstream tools read it there rather than from a
  marker file: `git config remote.origin.partialclonefilter` and
  `git rev-parse --is-shallow-repository`.

## Layout produced

```
<root>/<path>/       # GitHub: the repo name; GitLab: path_with_namespace
  ├── <repo>-main/   # default-branch checkout, kept clean and up to date
  └── worktree/      # ready for `git worktree add ...`
```

This matches the worktree convention the rest of the toolkit assumes:
`<repo>-main` stays on the default branch and clean, and feature work happens in
sibling worktrees.

## Sync safety rules

- Repos with **uncommitted changes** (staged or unstaged edits to tracked files;
  untracked files do not count) are fetched but not pulled (reported under
  *Uncommitted changes*).
- Repos **not on their default branch** are fetched but not pulled (reported
  under *Not on default branch*).
- Updates are **fast-forward only** (`git pull --ff-only`) — no merge commits,
  no rebases, never a force.
- Worktrees on the default branch with no local changes are fast-forwarded too.
- A repo that exists locally without the `<repo>-main` structure is flagged
  (*Wrong structure*) rather than touched.

## Requirements & configuration

- **GitHub:** `gh` and `jq` on `PATH`, and `gh auth status` must be
  authenticated. The script checks both up front.
- **GitLab:** `glab` and `jq` on `PATH`, and `glab auth status --hostname
  <host>` must pass (`--from-list` needs only `git` and `jq`).
- **Windows:** the script is bash, so run it under Git Bash, which ships with
  Git for Windows (Claude Code on Windows already needs it). Install `jq`
  separately (`winget install jqlang.jq`); Git Bash does not include it.
  `timeout` is part of Git Bash.
- **GitHub Enterprise:** `gh` uses its configured host. Target a GHE instance by
  setting `GH_HOST=github.example.com` or running
  `gh auth login --hostname github.example.com` first.
- **Access model:** for an **organization**, repos are discovered by unioning
  two sources and deduplicating: the **teams you belong to** (`gh api
  user/teams`) and the **org repo list** (`gh repo list <org>`), which also
  surfaces public repos and repos reachable via direct collaborator grants
  outside any team. Team membership is **not** a precondition — a user on no
  team still syncs every repo they can otherwise access. For a **personal
  account**, repos come from `gh repo list <account>` (includes private repos
  when authenticated as that account). Empty repos with no default branch are
  skipped in both cases.
- **Blacklist:** export `GHSYNC_BLACKLIST="repo-a repo-b"` to skip oversized or
  problematic repos. Entries are repository paths (`group/sub/repo` on
  GitLab).
- **`timeout`:** optional but recommended (`brew install coreutils`) — caps each
  repo at 5 minutes so one hung clone can't stall the run.
- **Archived repos** are skipped from cloning and cached to
  `<root>/.gh_archived_repos` for reference.
