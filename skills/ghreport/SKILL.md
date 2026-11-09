---
name: ghreport
description: "Read-only org repo state report for GitHub or GitLab. Reuses ghsync's repo discovery (on GitHub, teams union org-repo-list; on GitLab, every group you belong to with nested subgroups and shared projects) but, instead of cloning, queries each repo's remote state - on GitHub open PRs, CI on the default branch, open security alerts (Dependabot / code-scanning / secret-scanning) and branch protection; on GitLab open merge requests, the latest default-branch pipeline, open vulnerabilities and default-branch protection - and rolls it into a terminal summary plus a timestamped markdown file. Endpoints the token or tier cannot read are reported as no-access / unknown, never as a clean zero. TRIGGER when the user types /ghreport, asks 'what state are the org's repos in', wants an org health report or repo state snapshot, asks about open PRs / failing GitHub Actions / security alerts across a whole org or personal account, or asks the same of GitLab: 'what state are our GitLab projects in', open merge requests / failing pipelines / vulnerabilities across a GitLab group or self-hosted GitLab host."
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

A project with no commits has a null default branch. Its Branch, Pipeline and
Protection cells read `empty`, no pipeline or protection call is made, it stays
out of Needs attention, and the headline counts it. (`ghsync`'s discovery
already skips projects listed as empty, so this covers a project emptied
between discovery and the query.)

`no-access` on vulnerabilities is the common case, not a fault: it means the
tier, the role, or the endpoint being unavailable on this GitLab version
(GitLab documents the REST `projects/:id/vulnerabilities` endpoint as
deprecated).

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
| `--render-dir DIR` | Skip discovery + querying and render a report from a directory of previously-collected per-repo JSON fragments (replay / testing) |

## Output

- **Terminal:** a one-line headline (counts that tripped a signal), a rollup
  table, and a "Needs attention" section listing only the repos with an issue.
- **File** (unless `--no-file`): `org-state-<org>-<YYYY-MM-DD>.md` in the root,
  with the full table and per-section detail. On GitLab `<org>` is the host,
  plus the group with `/` written as `-` when `--group` is given.

## Requirements

- **`gh` and `jq`** on `PATH`, and `gh auth status` authenticated (the same
  prerequisites as `ghsync`; discovery is delegated to it).
- For a **GitHub Enterprise** host, set `GH_HOST` or `gh auth login --hostname`
  first, as with `ghsync`.
- **GitLab:** `glab` and `jq` on `PATH`, and `glab auth status --hostname <host>`
  authenticated. ghsync's preflight checks this first: an unauthenticated or
  unreachable host exits non-zero with one error line naming the host, before
  any API call.
- Very large orgs make many API calls (~5 per repo); use `--limit` to sample.
