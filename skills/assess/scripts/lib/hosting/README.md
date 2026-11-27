# lib/hosting

Everything `/assess` reads from GitHub or GitLab rather than from the checkout: review
history, delivery figures, security settings and the live CI configuration. The live host reads
in `lib/` go through this package, and the headless runner makes them only under `--platform`.

The folder is called `hosting`, not `platform`, because a `lib/platform/` package would
hide Python's own `platform` module from any script run from `lib/`.

## Where to start

- `platform_cli.py` runs `gh` and `glab` for every other module: no prompts, backoff on a
  rate limit, and an optional per-run response cache (`CACHE_ENV`, which `/fleet` sets for its platform pass only). Start here to see how a read can fail.
- `gh_cli.py` and `gitlab_cli.py` are the reads themselves, one per host. Each returns
  `available: False` with a reason when the CLI is missing, signed out or refused. A failed
  read never looks like a clean result.
- `merged_sample.py` picks the sample of merged changes the review and delivery blocks
  share.

## The blocks built on those reads

| Block | Module |
|---|---|
| Were merged changes actually reviewed? | `review_reality`, with `gitlab_notes` on GitLab |
| How work flows from review to merge | `delivery` |
| Committed platform settings against the live ones | `config_drift` |
| GitLab's merged CI configuration | `gitlab_ci_lint` |
| GitHub secret scanning and push protection | `github_secret_scanning` |
| The organisation's fallback security policy | `org_security_policy` |
| Are issues answered, triaged and closed? (backlog-ready level 2) | `issue_outcomes`, with `issue_outcomes_gitlab` on GitLab |

The package list and the shared rules are in [lib/README.md](../README.md).
