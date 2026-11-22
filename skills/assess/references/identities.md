# Confirming bot and agent accounts (issue #765)

Loaded by `/assess` after the deterministic core, on the first interactive run and whenever `ai_usage.identities_awaiting_confirmation` is above 0.

Pinned lists count only the unambiguous agent accounts (an agent app such as `claude[bot]`, a vendor address such as `noreply@anthropic.com`) and AI co-author trailers. Every other account that looks automated is **awaiting confirmation**: it counts as nothing until the human classifies it, and `ai_usage.identities_awaiting_confirmation` gives the number (a count, never a name). The answers live in a committed `[identities]` section of `.assess/config.toml`, so the level stays a function of the commit:

```toml
[identities]  # literal e-mails or logins, matched case-insensitively; never patterns
agents = ["cursor@example.com"]                 # AI agents: their commits count as agent-authored
bots = ["codecov[bot]", "renovate[bot]"]        # automation (dependency, coverage, release bots)
people = ["ann@example.com"]                    # people: never counted as agents
```

An entry matches the author e-mail, the login a noreply address carries (`22429695+codecov[bot]@users.noreply.github.com` is `codecov[bot]`) or a `[bot]` display name. A confirmed class wins over every pinned rule, so a person named "Cursor" never counts unless confirmed as an agent. The same section in a `--fleet-config` file extends each repository's (lists append, #706).

List the accounts awaiting confirmation (deterministic, never prompts, prints to the terminal only):

<!-- chat-skip:start -->
```bash
uv run "${CLAUDE_SKILL_DIR}/scripts/identity_candidates.py" "$REPO_ROOT"            # add --json for a machine-readable list
```

`--window` (default `6mo`, the headless default; `all-time` for full history) should match the run's `ai_usage.window` when it differs, so the listing covers the commits the count covered.

Each line names the account, its commits and its evidence (`bot_suffix`, `gitlab_token_user`, `dependency_bot_hint`, `agent_name_hint`, `automation_name_hint`, with `co_author_trailer`, `noreply_address` and `uniform_subjects` as supporting evidence). In an interactive run with at least one candidate, ask **one round of AskUserQuestion calls**, at most four questions per call and one question per candidate, showing the evidence, with the options **AI agent**, **Automation bot** and **Person**. Record the answers (quote each `--confirm` value: `[bot]` is a shell glob), then re-run the core so `ai_usage` reads them:

```bash
uv run "${CLAUDE_SKILL_DIR}/scripts/identity_candidates.py" "$REPO_ROOT" --confirm agent=cursor@example.com --confirm 'bot=codecov[bot]'
```

`--confirm` rewrites only the `[identities]` section of `.assess/config.toml`, regenerated at the end of the file (comments inside that section are not kept; the rest of the file is). Later runs list only accounts not yet classified. A headless or `--non-interactive` run never asks: state "N accounts awaiting confirmation" in the report as a next step, never as a score. Never copy a candidate's name or e-mail into the report, run-context, counts-only output or any other artifact; the listing is for the terminal and the config only. The config is committed, so a confirmed account's e-mail or login is in the repository: it is an identity the history already carries, but record a person by their noreply login where one exists, and tell the user that confirming a person writes their account into a committed file. Leaving a person unconfirmed is fine; they count as nothing either way.
<!-- chat-skip:end -->
