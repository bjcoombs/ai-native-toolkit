---
name: ghreport
description: "Read-only org repo state report. Reuses ghsync's repo discovery (teams union org-repo-list) but, instead of cloning, queries each repo's remote GitHub state - open PRs, CI on the default branch, open security alerts (Dependabot / code-scanning / secret-scanning), and branch protection - and rolls it into a terminal summary plus a timestamped markdown file. Endpoints that need admin and return 403/404 are reported as no-access / unknown, never as a clean zero. TRIGGER when the user types /ghreport, asks 'what state are the org's repos in', wants an org health report or repo state snapshot, or asks about open PRs / failing GitHub Actions / security alerts across a whole org or personal account."
---

# ghreport: what state are an org's repos in?

`ghsync` answers "do I have all the code locally and up to date?" `ghreport`
answers the companion question: "across everything I can see, what needs
attention right now?" It is **read-only** - it never clones, pulls, or mutates
anything.

It reuses `ghsync`'s discovery so the team-union / org-repo-list logic lives in
exactly one place: `ghreport` shells out to `ghsync --porcelain` to get the
deduplicated repo list, then queries each repo's remote state over `gh api`.

## What it reports, per repo

| Signal | Source | Reported as |
|--------|--------|-------------|
| Open PRs | `repos/{o}/{r}/pulls?state=open` | count (drafts noted) |
| CI on default branch | latest completed run per workflow on the default branch | `pass` / `fail` (names the failing workflows) / `none` |
| Security alerts | Dependabot + code-scanning + secret-scanning, `?state=open` | three counts, or `n/a` (no-access) |
| Branch protection | `repos/{o}/{r}/branches/{default}/protection` | `protected` / `unprotected` / `unknown` |

### The no-access rule

Security-alert and branch-protection endpoints require admin on the repo. When
the caller is denied (`403`/`404`), `ghreport` reports `n/a` (alerts) or
`unknown` (protection) - **never** a clean `0`. Reporting "no access" as "no
problems" would be a dangerous lie, so the two are kept distinct. A repo whose
own metadata can't be read at all is listed as `could not assess`.

## How to run it

The script lives next to this file. Resolve its directory the same way the
other skills do (works whether installed as a plugin or hand-placed under
`~/.claude/skills/`):

```bash
SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/ghreport}"
SKILL_DIR="${SKILL_DIR:-$(dirname "$(realpath ~/.claude/skills/ghreport/SKILL.md)")}"

# Org is derived from the directory you run in, exactly like ghsync. To report
# on the "meridianhub" org, run from a directory named meridianhub:
cd ~/dev/github.com/meridianhub
bash "$SKILL_DIR/scripts/ghreport.sh"
```

Override the org or target directory explicitly when they differ:

```bash
bash "$SKILL_DIR/scripts/ghreport.sh" --org meridianhub --root ~/dev/github.com/meridianhub
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
| `--org NAME` | Org or personal account to report on (default: basename of `--root` / cwd) |
| `--root DIR` | Org root; also where the report file is written (default: cwd) |
| `--limit N` | Only the first N repos (quick test against a large org) |
| `--quiet` | Suppress the per-repo progress dots; keep the summary and file |
| `--no-file` | Terminal only; do not write the markdown file |
| `--render-dir DIR` | Skip discovery + querying and render a report from a directory of previously-collected per-repo JSON fragments (replay / testing) |

## Output

- **Terminal:** a one-line headline (counts that tripped a signal), a rollup
  table, and a "Needs attention" section listing only the repos with an issue.
- **File** (unless `--no-file`): `org-state-<org>-<YYYY-MM-DD>.md` in the root,
  with the full table and per-section detail.

## Requirements

- **`gh` and `jq`** on `PATH`, and `gh auth status` authenticated (the same
  prerequisites as `ghsync`; discovery is delegated to it).
- For a **GitHub Enterprise** host, set `GH_HOST` or `gh auth login --hostname`
  first, as with `ghsync`.
- Very large orgs make many REST calls (~5 per repo); use `--limit` to sample.
