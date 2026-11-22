## Fleet settings: `--fleet-config`

One TOML file of fleet-wide lists and settings, with one section per consumer:

```toml
[ai_usage]   # agent identities (#695)
[review]     # AI review components in CI (#696)
[platform]   # --platform settings (#714)
[identities] # confirmed bot and agent accounts (#765)
```

The sections are known and may be empty or left out. Each consumer adds its keys;
until it does, any key in a section is unknown and exits 2.

| Key | Meaning |
|-----|---------|
| `[review] ai_review_markers` | Strings an AI reviewer prints in its GitLab merge-request notes (a list; case-insensitive substrings). With `--platform`, a note carrying one counts as AI review in `review_reality.bot_review_share`, on top of the pinned markers (an "AI Code Review" heading, an HTML marker comment) and agent accounts. An access-token user's unmarked note is not review unless `[ai_usage] token_users_as_agents` or an identity rule makes it an agent: coverage, Danger and release bots post through tokens too. |
| `[review] ai_review_components` | GitLab CI component or project paths, and GitHub `owner/repo` actions, that run an AI review in CI, beside the pinned list in `lib/review_automation.py` (#696). Compared lowercased with the host and `@version` dropped; a project path covers the components under it. Read by `review_automation`; a match reports `rule: config` and never the entry itself. |
| `[identities] agents`, `bots`, `people` | Accounts the human confirmed (#765): literal e-mails or logins such as `codecov[bot]`, matched case-insensitively, never patterns. `agents` count as agent authors in `ai_usage`; `bots` and `people` never do, whatever a pinned agent rule says. A dependency bot (Renovate, Dependabot) stays in `dependency_bot_commits` whatever the answer. The fleet lists extend each repository's own `[identities]` (`/assess` SKILL.md and `skills/assess/references/identities.md`). |
| `[platform] jobs` | Default for `--platform-jobs` (a whole number of at least 1); the flag wins. |
| `[platform] timeout` | Default for `--platform-timeout`, in seconds; the flag wins. |

**Confirming accounts across the fleet.** The rows carry only `ai_usage.identities_awaiting_confirmation`, a count. To see the accounts, run the listing over every repository at once; it never prompts and prints to the terminal only, aggregated by account with the commits summed and the repositories counted:

<!-- chat-skip:start -->
```bash
uv run "${CLAUDE_SKILL_DIR}/../assess/scripts/identity_candidates.py" --from-list repos.txt --fleet-config fleet.toml [--json]
uv run "${CLAUDE_SKILL_DIR}/../assess/scripts/identity_candidates.py" --from-list repos.txt --confirm 'bot=codecov[bot]' --config fleet.toml
```
<!-- chat-skip:end -->

In an interactive session, ask one AskUserQuestion round over the aggregated list (AI agent, automation bot or person, with the evidence shown) and record the answers in the `--fleet-config` file with `--confirm ... --config`. The next run lists only accounts not yet classified. A headless fleet run never asks.

The `[platform]` keys are read from the `--fleet-config` file only: the pass is fleet-wide, so a
repository's own `[platform]` values have no effect.

`[platform] jobs` and `timeout` say how a run goes, not what it measures, so they
are left out of `fleet_config_sha256`: changing them never breaks comparability. The loader and the key
tables are `lib/fleet_config.py` (`SECTION_KEYS`).

- **Validation.** `fleet.py` validates the file before any scan. A TOML error, an
  unknown section or key, or a value of the wrong kind exits 2 with the key named
  (`error: --fleet-config FILE: review.foo: unknown key ...`). The message never
  quotes a value.
- **Same config everywhere.** Every repository scan gets one private copy of the
  validated file (`assess_signals.py --fleet-config`), so an edit during the run
  reaches no scan.
- **Per-repository extension.** A repository's `.assess/config.toml` may carry the
  same sections. They extend the fleet file for that repository and never replace
  it. A list key is the fleet's entries in order, then the repository's entries
  not already present: a repository can add an entry, never remove one, and a
  fleet entry keeps its index everywhere. A scalar the fleet file sets keeps the
  fleet value; a different repository value is ignored with a warning naming the
  key. A scalar the fleet file leaves unset takes the repository's value. A key
  that does not validate in a repository file is skipped with a warning, never a
  failed row. Interactive `/assess` reads the repository's sections alone.
- **Stamp and comparison.** `run.json` records `fleet_config_sha256` and
  `fleet_config_entries`. The hash covers the validated content as sorted-key
  JSON with empty sections left out: comments, whitespace, key order and a new
  empty section do not change it, and list order does. A file with only empty
  sections still differs from no file (`null`).
  Runs with different hashes are not comparable (`fleet config changed ...`), a
  same-day run with a different config gets its own history entry, and the trend
  lines join only runs with the same hash.
- **Counts-only.** No config value reaches `repos.jsonl`, `run.json` or any
  other output: consumers report per-rule counts by position, never by pattern.

### `[ai_usage]`: agent identities

Read by the `ai_usage` scan (`lib/agent_identity.py`, `AuthorMatcher`) to count the
commits an in-house agent authored, beside the pinned agent apps (Claude, the
Copilot coding agent, Devin, Jules, Codex, the Cursor background agent, OpenHands,
Amazon Q Developer).

```toml
[ai_usage]
token_users_as_agents = false   # GitLab access-token users count as agents (default false)

[[ai_usage.agent_identities]]
pattern = "^inhouse-agent"      # a regular expression, case-insensitive, searched
# field = "email"               # optional: "email", "username" or "name"
```

| Key | Kind | Meaning |
|---|---|---|
| `agent_identities` | list of tables | One rule per in-house agent. `pattern` (required) is a Python regular expression; a bad one exits 2 naming the key, never the pattern. Without `field` a rule reads the author e-mail and the username; `field = "name"` opts in to the display name, which no pinned default reads. A commit is credited to the first rule that matches, so `ai_usage.agent_identity_rule_commits[i]` counts rule `i` once per commit. |
| `token_users_as_agents` | boolean | Count every GitLab project or group access-token user (`project_<id>_bot_<hex>`, `group_<id>_bot_<hex>`, their `@noreply.<host>` e-mail) as an agent. Without it they are counted apart in `token_user_commits`; a rule such as `pattern = "^project_42_bot_"` promotes one project's token users only. |

The username is the platform login a noreply e-mail carries (GitHub
`[<id>+]login@users.noreply.<host>`, GitLab `<id>-login@users.noreply.<host>`, a
token user's own address). Dependency bots (Renovate, a plain `renovate-bot` user,
Dependabot) are always `dependency_bot_commits`, whatever a rule says. Per-rule
counts are by position in the merged list, so a fleet rule keeps its index in every
repository; the block never carries a pattern or a matched identity.
