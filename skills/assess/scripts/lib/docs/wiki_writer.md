# `lib/wiki_writer.py`

The full reference for `skills/assess/scripts/lib/wiki_writer.py`, whose module docstring holds the summary.

Renders and writes the `.assess/` wiki files (`index.md`, `log.md`,
`hotspots/*.md`) from string templates. No LLM calls. Pure string formatting + file IO.
An optional `run_id`/`schema_version` prepends a non-rendering HTML-comment
provenance stamp to each file (omitted -> byte-identical legacy output).
Both persistent pages compound across runs rather than reflecting only the latest
one. `write_hotspot_page` reads the page's existing history table
(`parse_history_rows`, which reads pre-`Run`-column five-cell rows with `-` as the
run) and `merge_history_rows` appends this run's row, ordered by date, replacing
only a row with the same run date and short run id. Before the pages are
rewritten, `sweep_superseded_history_rows` removes a superseded, never-finalized
run's row from every page on disk, including files this run does not rank, matching
the log entry the core drops (#421). The page labels its `ccn` the file aggregate and
adds a `Worst function` row only when the sidecar's `max_fn_ccn` / `max_fn_name` are
set (#423). `format_ccn` renders integral sidecar floats as integers; the worst
function is shown unclamped, with a note when it exceeds the aggregate (a Dart file's
aggregate is scc's, its worst function the Dart scanner's), and a `|` in its name is
escaped. `write_index` seeds from the prior `index.md` rows (`parse_index_rows`)
and from every hotspot page on disk (`read_hotspot_page_entries`), then
`merge_index_entries` lets this run's entries win per path: a path the run did not
rank keeps its last known row but renders `graduated` (it is not a hotspot this
run, whatever live status its page last carried), a carried row whose page is
retired takes the retired status, and no row is kept for a path excluded before
finalize (its page's status, or the core's `never_assessed` list) or now excluded
as generated (the core's `excluded_as_generated` predicate) (#420). The
`merge_index_entries` docstring lists each reason a path can be absent and the row
it gets.
Also guards wiki integrity: `prune_orphan_hotspots(assess_dir, repo_root)` stamps
any hotspot page whose source file left the tree as `retired - file deleted`
(history preserved, no active page lies about a live file), and
`retire_excluded_hotspots(assess_dir, paths)` stamps `retired - excluded before
finalize` on the pages of files the core found first flagged only by a superseded,
never-finalized run and now excluded by `.assess/config.toml`, returning the paths
it retired and those whose page had no status token to stamp. Every retired status
begins `retired`; `is_retired_status` is the one predicate for that, used by the
pruner (which skips any retired page) and the orphan-invariant tests; `append_log_entry`
chains each `log.md` entry with a `<!-- chain:<hash> -->` marker and
`verify_log_chain(assess_dir)` returns `(valid, broken_at_entry)` so a later edit
of a prior entry is detected and disclosed. The tool's own edits go through
`rewrite_log_entry(assess_dir, index, new_content)`, which re-chains the entry and
every later one (stopping at an entry that was already broken); `find_log_entry`,
`read_log_entries` and `log_entry_is_unfinalized` (placeholder `(LLM fills in)`)
address entries by their `assess:run_id` stamp, and
`supersede_unfinalized_log_entry` drops a same-date, same-commit run's unfilled
last entry before the core appends its own (`last_log_entry_is_unfinalized_run` is
the condition it acts under, which the core also reads before writing the wiki); it and `--drop-entry` act only on an
entry that starts with its own stamp (`log_entry_owns_span`), never on a span that
also holds pre-chain history. Both are additive and back-compat -
a legacy wiki (no markers, live files) is untouched and reads valid.
