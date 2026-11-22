---
name: ghreport
description: "Read-only org repo state report for GitHub or GitLab. Reuses ghsync's repo discovery (on GitHub, teams union org-repo-list; on GitLab, every group you belong to with nested subgroups and shared projects) but, instead of cloning, queries each repo's remote state - on GitHub open PRs, CI on the default branch, open security alerts (Dependabot / code-scanning / secret-scanning) and branch protection; on GitLab open merge requests, the latest default-branch pipeline, open vulnerabilities and default-branch protection - plus org or group settings (two-factor enforcement, push rules, default branch protection for new projects, org rulesets), and rolls it into a terminal summary plus a timestamped markdown file. Endpoints the token or tier cannot read are reported as no-access / unknown, never as a clean zero. TRIGGER when the user types /ghreport, asks 'what state are the org's repos in', wants an org health report or repo state snapshot, asks about open PRs / failing GitHub Actions / security alerts across a whole org or personal account, or asks the same of GitLab: 'what state are our GitLab projects in', open merge requests / failing pipelines / vulnerabilities across a GitLab group or self-hosted GitLab host."
---

# ghreport: what state are an org's repos in?

`ghsync` answers "do I have all the code locally and up to date?" `ghreport`
answers the companion question: "across everything I can see, what needs
attention right now?" It is **read-only** - it never clones, pulls, or mutates
anything.

It reuses `ghsync`'s discovery so the team-union / org-repo-list logic lives in
exactly one place: `ghreport` shells out to `ghsync --porcelain` to get the
deduplicated repo list, then queries each repo's remote state over `gh api`
(GitHub) or `glab api` (GitLab, `--provider gitlab`).

## What it reports, per repo

| Signal | Source | Reported as |
|--------|--------|-------------|
| Open PRs | `repos/{o}/{r}/pulls?state=open` | count (drafts noted) |
| CI on default branch | latest completed run per workflow on the default branch | `pass` / `fail` (names the failing workflows) / `none` |
| Security alerts | Dependabot + code-scanning + secret-scanning, `?state=open` | three counts, or `n/a` (no-access) |
| Branch protection | `repos/{o}/{r}/branches/{default}/protection` | `protected` / `unprotected` / `unknown` |

### On GitLab

| Signal | Source (`projects/{url-encoded path}/...`) | Reported as |
|--------|--------|-------------|
| Open MRs | `merge_requests?state=opened`, every page | count (drafts noted) |
| Pipeline on default branch | `pipelines?ref={default}`, newest finished of the last 20 | `success` / `failed` / `none` / `unknown` / `empty` |
| Vulnerabilities | `vulnerabilities`, state `detected` or `confirmed` | count, or `no-access` |
| Branch protection | `protected_branches` (wildcards such as `release/*` honoured), then `protected_branches/{default}` | `protected` / `unprotected` / `unknown` / `empty` |

The table gains a Branch column naming each project's default branch, and the
PRs column becomes MRs.

The pipeline mirrors the GitHub latest-completed rule: only `success` and
`failed` count as finished. `running`, `pending`, `canceled`, `skipped` and
`manual` pipelines are passed over, so a newer in-flight or cancelled pipeline
cannot hide a failure from Needs attention. A cancelled pipeline says nothing
about whether the code passes, which is why it is skipped like `skipped` and
`manual`. With no finished pipeline among the last 20 the column reads `none`.

A project with no commits (a null default branch, or `empty_repo: true` from
GitLab even when a branch name is set) is empty. Its Branch, Pipeline and
Protection cells read `empty`, no pipeline or protection call is made, and the
headline counts it. Emptiness alone does not put it under Needs attention:
pipeline and protection are not judged, but merge requests and vulnerabilities
are still queried, and open vulnerabilities GitLab kept from earlier scans
still list it. (`ghsync`'s discovery already skips projects listed as empty,
so this covers a project emptied between discovery and the query.)

`no-access` on vulnerabilities is the common case, not a fault: it means the
tier, the role, or the endpoint being unavailable on this GitLab version
(GitLab documents the REST `projects/:id/vulnerabilities` endpoint as
deprecated).

## Org and group settings

After the per-repo table, the report adds one section for settings that are
set once for a whole org or group rather than per repository. It never
changes the per-repo table, headline or Needs attention list.

| Field | GitHub (per org) | GitLab (per group) |
|-------|------------------|--------------------|
| `two_factor` | `orgs/{org}` `two_factor_requirement_enabled` (owners only) | `groups/{id}` `require_two_factor_authentication` |
| `push_rules` | org rulesets with target `push`; `off` unless one is `active` | `groups/{id}/push_rule`: the enabled rules (a paid tier) |
| `default_branch_protection` | active org branch rulesets on `~DEFAULT_BRANCH` (or `~ALL`) of `~ALL` repositories, read in full from `orgs/{org}/rulesets/{id}` | `groups/{id}` `default_branch_protection`, else `default_branch_protection_defaults` |
| `rulesets` | `orgs/{org}/rulesets`: count, names and enforcement | always `unknown`: GitLab has no group rulesets |

On GitLab the section covers `--group`, or each top-level group you belong
to. A GitHub personal account has no org settings, so its four fields read
`unknown (personal account: no org settings)`. If the GitLab group list
cannot be read, the section still appears, as one entry for the host with
every field `unknown (group list unreadable)`. On GitLab,
`default_branch_protection` reads `off` only when developers can push and
force push is allowed; force push allowed with stricter push access is
appended as `force push: allowed`. The field names are the same on both providers.

On GitHub, `push_rules` reads `off` when no push ruleset is `active`; any
`disabled` or `evaluate` push rulesets are named after it, as in
`off (not active: no-big-files (disabled))`, and still count as off.
`default_branch_protection` matches rulesets by name targeting only: a
ruleset counts when `conditions.repository_name.include` contains `~ALL`.
Exclusions (`repository_name.exclude`) are not evaluated, so `ruleset: <name>`
can overstate coverage, and a ruleset that reaches every repository through
`repository_property` or `repository_id` targeting is not counted, so the
field can read `off` while such a ruleset applies.

Every value is the setting's value, `off`, or `unknown` with the reason
(`denied: no access or tier (HTTP 403)`, `not found: no access, tier, or
endpoint missing (HTTP 404)`, `no access: readable by org owners only`,
`unreadable response`). An unreadable value is never `off`: a non-owner on
GitHub reads `two_factor: unknown`, and on GitLab a `404` from `push_rule`
(which GitLab also answers when no rule is set) is `unknown`. The section's
own headline counts `off` and `unknown` separately, so an unreadable setting
is never counted as off.

**GitHub teams for `/fleet`.** The GitHub org fragment (`settings/org.json` in a
`--keep-fragments` capture) also carries `teams`, `[{slug, parent, repos}]`,
from `orgs/{org}/teams` and each team's `orgs/{org}/teams/{slug}/repos`:
`parent` is the parent team's slug or `null`, `repos` the repository names.
`fleet.py --settings` reads it to place repositories in the units tree. It is
not a setting, so the report does not show it. The key is written only when
the team list and every team's repository list were read; an unreadable one
leaves it out (with a note on stderr), so an unread team list never reads as
no teams. A personal account has no teams, so its fragment has no `teams` key
and no note. GitHub's "List team repositories" docs do not say whether a child
team's list also holds the repositories it inherits from its parent (only
"Check team permissions" says inherited repositories are checked), so a repository a team lists with the same role as its parent
team is taken as inherited and kept on the parent only: each repository stays
with the most specific team that holds it directly. A child granted another
role keeps it; one granted directly with the parent's role cannot be told
apart from inheritance and stays on the parent. Team slugs are identities, and the fragment holds them in clear. In
`/fleet`'s `fleet-data.json`, each team unit's key carries its slug
(`ownership/acme/platform`) in clear by default; `--full` also writes the
unit's name (the slug) and owners (`@acme/platform`), which counts-only output
drops. With `--hash-paths` or `--hash-salt` the slug is hashed with the unit
key and no name or owner is written.

### The no-access rule

Security-alert and branch-protection endpoints require admin on the repo. When
the caller is denied (`403`/`404`), `ghreport` reports `n/a` (alerts) or
`unknown` (protection) - **never** a clean `0`. Reporting "no access" as "no
problems" would be a dangerous lie, so the two are kept distinct. A repo whose
own metadata can't be read at all is listed as `could not assess`.

On GitLab the rule is stricter: any failed call (`401`, `403`, `404`, `500`,
or a body that is not a list) is `no-access` or `unknown`. GitLab answers
`404` for a resource the token cannot see, so a `404` never means
"unprotected" or "zero". The headline names how many projects had an
unreadable MR or vulnerability count, so a total of `0` is not read as clean.

## How to run it

The script lives next to this file. Claude Code writes this skill's directory
into the script paths below when it loads the skill, so each path is already
absolute:

```bash
# Org is derived from the directory you run in, exactly like ghsync. To report
# on the "meridianhub" org, run from a directory named meridianhub:
cd ~/dev/github.com/meridianhub
bash "${CLAUDE_SKILL_DIR}/scripts/ghreport.sh"
```

Override the org or target directory explicitly when they differ:

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/ghreport.sh" --org meridianhub --root ~/dev/github.com/meridianhub
```

On GitLab, name the host; `--group` narrows the run to one group's subtree:

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/ghreport.sh" --provider gitlab --host gitlab.example.com
bash "${CLAUDE_SKILL_DIR}/scripts/ghreport.sh" --provider gitlab --host gitlab.example.com --group platform/backend
```

## What to do when invoked

1. **Run it** from (or pointed at) the org directory. It streams a progress dot
   per repo, then prints the summary.
2. **Read the headline** - the counts of repos with failing CI, open alerts,
   and an unprotected default branch. These are the repos that need attention.
3. **Surface the "Needs attention" list** to the user verbatim; that is the
   actionable core of the report.
4. **Point at the saved file** (`org-state-<org>-<date>.md` in the root) for the
   full per-repo table.

## Flags

| Flag | Effect |
|------|--------|
| `--provider P` | `github` (default) or `gitlab` |
| `--org NAME` | GitHub: org or personal account to report on (default: basename of `--root` / cwd) |
| `--host HOST` | GitLab: the host (default `gitlab.com`); a URL form such as `https://gl.example.com/` is accepted |
| `--group PATH` | GitLab: report on this group and its subgroups only |
| `--root DIR` | Org root; also where the report file is written (default: cwd) |
| `--limit N` | Only the first N repos (quick test against a large org) |
| `--quiet` | Suppress the per-repo progress dots; keep the summary and file |
| `--no-file` | Terminal only; do not write the markdown file |
| `--render-dir DIR` | Skip discovery + querying and render a report from a directory of previously-collected per-repo JSON fragments (replay / testing); org or group settings fragments are read from its `settings/` subdirectory |
| `--keep-fragments DIR` | Live run: keep the per-repo fragments and the `settings/` subdirectory in `DIR` (created if missing; must be empty) in exactly the layout `--render-dir` reads. See [Capture and replay](#capture-and-replay) |

## Capture and replay

To keep a live run's data and re-render it later without any API call, capture
it with `--keep-fragments`, then replay it with `--render-dir`:

```bash
ghreport.sh --org acme --keep-fragments ./acme-capture     # live run, fragments kept
ghreport.sh --org acme --render-dir ./acme-capture         # same report, no API calls
```

Pass the same `--org` (GitHub) or `--provider gitlab --host HOST [--group PATH]`
(GitLab) to the replay: the report title and file name come from those flags,
not from the fragments. On the same day the replay prints the same per-repo
rows, headline and settings section as the live run, and writes the same
markdown file. The capture directory must be new or empty, because a replay
reads every `*.json` in it as a repository row; a stale fragment from an
earlier run would otherwise appear in the report. `--keep-fragments` cannot be
combined with `--render-dir`.

A capture writes a `CAPTURE_COMPLETE` marker file (not `*.json`, so it is never
read as a fragment) once every fragment is in place. A run that stops earlier
leaves no marker, and `--render-dir` on that directory prints a warning on
stderr that the report may be partial. Delete an interrupted capture and
capture again. A hand-written fragment directory has no marker either, so
replaying one shows the same warning; the report itself is unchanged.

## Output

- **Terminal:** a one-line headline (counts that tripped a signal), a rollup
  table, a "Needs attention" section listing only the repos with an issue,
  and the org or group settings section.
- **File** (unless `--no-file`): `org-state-<org>-<YYYY-MM-DD>.md` in the root,
  with the full table and per-section detail. On GitLab `<org>` is the host,
  plus the group with `/` written as `-` when `--group` is given.

## Requirements

- **GitHub:** `gh` and `jq` on `PATH`, and `gh auth status` authenticated (the
  same prerequisites as `ghsync`; discovery is delegated to it). A GitLab run
  never calls `gh`.
- For a **GitHub Enterprise** host, set `GH_HOST` or `gh auth login --hostname`
  first, as with `ghsync`.
- **GitLab:** `glab` and `jq` on `PATH`, and `glab auth status --hostname <host>`
  authenticated. ghsync's preflight checks this first: an unauthenticated or
  unreachable host exits non-zero with one error line naming the host, before
  any API call.
- **Windows:** the script is bash, so run it under Git Bash, which ships with
  Git for Windows (Claude Code on Windows already needs it). Install `jq`
  separately (`winget install jqlang.jq`); Git Bash does not include it.
- `jq` 1.6 or later. An endpoint that answers with an empty or non-JSON body
  reads as unreadable (`unknown` / `no-access`) on every jq version.
- Very large orgs make many API calls (~5 per repo); use `--limit` to sample.
