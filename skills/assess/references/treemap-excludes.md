# Treemap excludes and doc provenance

Detail for Step 2c of the assess skill: custom excludes, provenance for generated docs, cross-repo mappings, the automatic and raw-source exclusions, and a failed script.

**Custom excludes for vetted-context / reference data.** When the repo intentionally tracks large non-source files, two mechanisms extend the built-in defaults (the built-ins always apply; both layers are additive). **The same excludes apply across every scan** - the heatmap, the doc-navigability graph, the doc-staleness pass, and the liveness scan all honour the same list, so "this is reference data, not source" is a single statement, not a per-layer toggle:

1. **CLI flag** `--exclude PATTERN` (repeatable, ad-hoc). A plain string is matched as a directory name; a glob is matched against the basename. The flag exists on the treemap script for one-off runs:

   ```bash
   <!-- chat-replace:treemap-exclude-example -->
   uv run "${CLAUDE_SKILL_DIR}/scripts/complexity-treemap.py" "$REPO_ROOT" --exclude regulatory-raw --exclude vetted-context --exclude '*.csv'
   ```

2. **Per-repo config** `.assess/config.toml` (durable, version-controllable, applies to **every** scan via the orchestrator). Recommended for any exclude the user will want to apply every run:

   ```toml
   exclude_dirs = ["regulatory-raw", "vetted-context", "seed-data"]
   exclude_patterns = ["*.csv", "*.parquet"]
   ```

   No section header is needed - the file is already namespaced by living under `.assess/`. Missing or malformed files degrade silently to no extra excludes; the assessment never blocks on a broken config.

**Provenance for generated docs (staleness measured against the source).** A *generated* doc - a Jira note dump, an API reference, codegen output - is not stale because the file is old; it is stale when the **source it was derived from has moved on**. The mtime/age model gets this backwards: a freshly regenerated dump of 1,200 notes shares one recent mtime (looks fresh) even when its source changed afterwards, and an old-but-accurate generated doc reads as a lying map when it is not. Declare provenance and doc-staleness is computed as "is the source newer than the doc?" instead - a generated doc whose source is quiet is never flagged as a `lying_map`, regardless of how busy the surrounding code is. Two ways to declare it (frontmatter wins when both name a source for the same doc):

1. **Frontmatter** on the generated doc - a `source:` key (a string or a list), resolved relative to the repo root first, then to the doc's own directory. An optional `generated_by:` records the generator for humans (it does not affect staleness):

   ```markdown
   ---
   source: data/jira.tsv
   generated_by: scripts/dump-jira-notes.py
   ---
   ```

2. **Per-repo config** `.assess/config.toml` `[[generated]]` array-of-tables, for bulk-generated trees that cannot each carry frontmatter. `path` is a folder relative to the repo root; every doc under it inherits the mapping. `source` is a string or list of strings relative to the repo root:

   ```toml
   [[generated]]
   path = "notes"
   source = "data/jira.tsv"
   ```

   When a generated doc's source is newer than the doc, the staleness verdict is a direct, high-confidence source-vs-doc comparison (git commit time, falling back to mtime) - so a stale generated doc over complex code still surfaces as a `lying_map`, while a fresh one never does.

**Cross-repo mappings (opt-in).** A central docs repository whose docs describe other services can declare each mapping in `.assess/config.toml`; `scope` (the documented surface inside the service) is required, and an entry without it is rejected with a message saying so:

```toml
[[describes]]
path = "services/billing.md"   # a doc or folder in this repository
repo = "grp/billing"           # a repository id in the fleet
scope = ["api/"]               # or ["openapi.yaml"], ["proto/"]; ["."] = whole repo, low confidence
```

A standalone `/assess` lists them under `doc_staleness.declared_cross_repo` as declared, not measured outside fleet mode; only `/fleet`, which holds the service checkouts, measures them.

The script's own output directory `.assess/` is excluded automatically - prior runs' `run-context.json` and SVGs never feed the next run's heatmap, the doc graph, or the dead-code scan. Test fixtures under `**/tests/fixtures/**` are likewise excluded automatically - they are inputs that exercise the scanners (sample `CLAUDE.md` / monolithic-instruction files), not navigational docs or live code, so counting them would inflate the orphan rate and depress the Layer 0 navigability read.

**Raw-source-tree exclusion.** The read-side metrics (orphan rate, reachability, broken links) describe the **curated wiki** - the navigable layer an agent traverses. A repo can also track trees of raw, machine-extracted source documents (a disclosure / SAR export of hundreds of `.msg`/`.pdf`/`.docx` files converted to markdown). Those are immutable raw sources: they legitimately have no inbound wiki links and carry machine-extracted, non-navigational links (`mailto:`/`tel:`/footer URLs), so counting them as orphans / broken links inflates the figures and masks the curated signal. The doc graph auto-detects such subtrees - threshold-based: a large subtree that is almost entirely link-isolated *and* carries the machine-extraction fingerprint (`lib/complexity/raw_source.py`) - and **excludes** them from the headline metrics, reporting each excluded tree + its file count (`doc_graph.excluded_raw_trees`) and the raw layer's own figures separately (`raw_source_doc_count` / `raw_source_orphan_rate` / `raw_source_broken_links`). A second fingerprint excludes working-notes trees the same way: pattern-named notes (plans, session logs, tickets) mostly linked once from one or two index files, reported as `excluded_working_notes_trees` / `working_notes_doc_count`; in `.assess/config.toml`, directories relative to the repo root (a prefix, not a name matched anywhere like `exclude_dirs`): `working_notes_dirs = ["journal"]` excludes one from the headline and `working_notes_ignore = ["docs/chapters"]` keeps one counted. A repo with neither tree is unaffected. The detection reuses the link graph already built, so there is no second parse.

**If the script fails** (no `uv`, no scoreable files, etc.), record the error in the report under "Hotspot snapshot" as "could not be generated - <reason>" and continue with the layered assessment. The treemap is additive; assessment still runs without it.
