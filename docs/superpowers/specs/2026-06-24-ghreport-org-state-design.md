# ghreport: org repo state report - design

## Purpose

A read-only companion to `ghsync`. It reuses `ghsync`'s repo discovery (teams
union org-repo-list, deduplicated, archived skipped) but, instead of cloning or
syncing, queries each repo's **remote** GitHub state and rolls it into a report
answering: "what state are this org's repos in right now?"

`ghsync` answers "do I have all the code locally and up to date?"
`ghreport` answers "across everything I can see, what needs attention?"

## Scope

Per repo, the report covers exactly:

| Signal | Source | Reported as |
|--------|--------|-------------|
| Open PRs | `repos/{o}/{r}/pulls?state=open` | count (drafts noted separately) |
| CI on default branch | latest workflow run per workflow on the default branch | `pass` / `fail` (lists failing workflow names) / `none` |
| Open security alerts | Dependabot + code-scanning + secret-scanning, `?state=open` | three counts, or `no-access` |
| Branch protection | `repos/{o}/{r}/branches/{default}/protection` | `protected` / `unprotected` / `unknown` |

Out of scope (deliberate YAGNI): stale-PR ageing, last-push staleness, issue
counts, local working-tree state (that is `ghsync`'s job), write actions of any
kind. `ghreport` never mutates anything.

### The 403-vs-0 rule (correctness by construction)

Security-alert and branch-protection endpoints require admin on the repo. For
many repos the caller will get `403`/`404`. Reporting those as "0 open alerts"
or "unprotected" would be a dangerous lie. They MUST be reported as a distinct
state - `no-access` for alerts, `unknown` for protection - never collapsed to a
clean zero. This distinction is a hard requirement, not a nicety.

## Architecture

Pure bash, mirroring `ghsync`'s style: no new runtime dependencies (`gh` + `jq`
only), batched-parallel workers (5 at a time), per-repo JSON fragments written
to a temp dir, then a single assembler pass. Packaged as a skill exactly like
`ghsync`: `skills/ghreport/SKILL.md` + `skills/ghreport/scripts/ghreport.sh`.

### Components

1. **Discovery (reused, not reimplemented).** `ghreport.sh` shells out to
   `ghsync.sh --porcelain` to get the deduplicated repo list. The union/dedupe
   logic stays in exactly one place.
   - This requires one **additive** change to `ghsync.sh`: a `--porcelain`
     flag that prints repo names one per line to stdout and routes all progress
     chatter (`Org:`, `Root:`, `Fetching...`, counts) to stderr, then exits -
     i.e. `--porcelain` implies the `--list-repos` early-exit but with
     machine-clean stdout. Without the flag, `ghsync.sh` behaves exactly as
     today (Chesterton's fence: the working sync path is untouched).
   - `ghreport.sh` locates `ghsync.sh` as a sibling: `$(dirname
     "$0")/../../ghsync/scripts/ghsync.sh`, falling back to the
     `CLAUDE_PLUGIN_ROOT` / `~/.claude/skills` resolution pattern the other
     skills use.

2. **Per-repo worker.** For one repo, runs the four query groups and writes a
   single JSON fragment (`$tmp/<repo>.json`) capturing: open PR count, draft
   count, CI verdict + failing workflow names, the three alert counts (or
   `no-access`), protection state, and the resolved default branch. Each query
   is individually guarded: a non-zero `gh` exit or a `403`/`404` maps to the
   appropriate "unknown / no-access" state rather than aborting the worker.

3. **Assembler.** After all workers finish, reads every fragment and emits:
   - **Terminal:** a one-line headline summary (counts of repos with failing
     CI / open alerts / unprotected default branch), a rollup table, and an
     "Needs attention" section listing only the repos that tripped a signal.
   - **File:** a timestamped `org-state-<org>-<YYYY-MM-DD>.md` written to the
     org root (the `--root` dir, default cwd), containing the full rollup table
     plus per-section detail.

### Query details

- **Default branch** is resolved once per repo via
  `repos/{o}/{r} --jq .default_branch` and reused for CI + protection.
- **CI verdict:** list workflow runs on the default branch
  (`repos/{o}/{r}/actions/runs?branch=<default>&per_page=100`), keep the latest
  `completed` run per `workflow_id`, and the repo is `fail` if any of those
  latest-per-workflow runs has `conclusion=failure` (lists their names),
  `none` if there are no workflows/runs, else `pass`.
- **Alerts:** three independent calls; each that returns non-2xx becomes
  `no-access` for that category (so a repo can be e.g. Dependabot `3`,
  code-scanning `no-access`, secret-scanning `0`).
- **Open PRs:** `pulls?state=open` count; drafts (`.draft==true`) tallied
  separately for the note.

### Flags (subset of ghsync's, same semantics)

| Flag | Effect |
|------|--------|
| `--org NAME` | Org / account (default: basename of `--root` / cwd) |
| `--root DIR` | Org root; also where the report file lands (default: cwd) |
| `--limit N` | Only the first N repos (quick test) |
| `--quiet` | Suppress per-repo progress; keep headline + file |
| `--no-file` | Terminal only; skip writing the markdown file |

Discovery, auth checks, and account-type handling are inherited by delegating
to `ghsync.sh --porcelain`, so `ghreport` does not duplicate them.

## Error handling

- **Auth / deps:** delegated to `ghsync.sh --porcelain`, which already checks
  `gh`/`jq` presence and `gh auth status`; if it exits non-zero, `ghreport`
  surfaces that and stops.
- **Per-repo query failure:** never fatal. Maps to `no-access` / `unknown` /
  `none` and the run continues. A repo whose *every* query failed is listed
  under a "could not assess" note so it is not silently dropped.
- **Rate limits:** batched-parallel 5 (same as `ghsync`); each worker makes
  ~5 `gh` calls. The SKILL.md notes that very large orgs may approach the
  REST rate limit and can use `--limit` to sample.

## Testing

Bash, so tests are script-level (matching how `ghsync` ships - no unit
harness). The plan will add a `tests/ghreport/` directory with:

- A **fixture-driven assembler test:** feed the assembler a directory of
  hand-written JSON fragments (covering pass/fail/no-access/unprotected/none and
  an all-failed repo) and assert the rendered markdown + headline counts.
  This is the core logic and is fully deterministic offline.
- A **`--porcelain` contract test** for `ghsync.sh`: assert that with
  `--porcelain` stdout contains only repo names (no `Org:`/`Fetching` lines)
  and that without it the existing `--list-repos` output is unchanged.
- A **smoke test:** `ghreport.sh --help` exits 0 and prints usage; `--limit 0`
  / empty-repo-list path produces an empty-but-valid report.

Live `gh` calls are not exercised in tests (no network in CI); the worker's
query construction is kept thin and the assembler holds all the testable logic.

## Packaging

- `skills/ghreport/SKILL.md` with frontmatter `name: ghreport` and a TRIGGER
  line covering `/ghreport`, "what state are the org's repos in", "org health
  report", "open PRs / failing actions / security alerts across the org".
- `skills/ghreport/scripts/ghreport.sh` (executable).
- One additive edit to `skills/ghsync/scripts/ghsync.sh` (`--porcelain`) and a
  one-line flag-table addition to `skills/ghsync/SKILL.md`.
- Update `.claude-plugin/plugin.json` description prose to mention `/ghreport`
  as the read-only state-report companion to `/ghsync`.

## Non-goals

- No write/remediation actions (no closing PRs, no dismissing alerts).
- No re-implementation of repo discovery - it has exactly one home in
  `ghsync.sh`.
- No Python / `uv` dependency - stays a drop-in bash sibling of `ghsync`.
