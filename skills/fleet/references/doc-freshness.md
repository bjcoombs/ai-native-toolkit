## Output: `doc_freshness.jsonl` (opt-in)

Written only when a scanned repository's `.assess/config.toml` declares
`[[describes]]` mappings (a central docs repository naming the service surface
each doc describes; see the assess skill's config section). One row per mapping,
method `cross-repo-explicit`: the service commits to the mapping's `scope`
authored after the doc's last content change, skipping merges, Dependabot,
Renovate and other `[bot]` accounts, lockfile or manifest-only commits, and bulk
mechanical commits (the content clock's rule). `status` is `behind` at 3 or more
such commits (a starting threshold awaiting tuning on a real fleet),
`minor_drift` at 1-2, `current` at 0; only `behind` is a finding. A doc or scope
path that no longer exists is `broken`; a repository absent from the run, a
shallow checkout whose history ends after the doc's last edit, or a git failure
is `unknown` with a `reason` token. Rejected entries (a missing `scope`, say) are
`invalid` rows and the message is printed to stderr. Counts-only rows carry the
count and the doc's last-change date only; `--full` adds up to five commit shas and subjects so a
human can check the verdict. `--hash-paths` hashes the doc and scope paths and both repository ids. The
join runs before any scan (so `--delete-after` cannot remove a checkout it
needs), costs one `git log` per mapping plus one content-clock pass per docs
repository, makes no model call, and is kept in the run history beside the
other files. Schema: [`references/doc_freshness.schema.json`](doc_freshness.schema.json).
Logic: `skills/assess/scripts/lib/fleet_doc_freshness.py`.
