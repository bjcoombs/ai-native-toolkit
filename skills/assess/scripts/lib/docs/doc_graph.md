# `lib/docs/doc_graph.py`

The full reference for `skills/assess/scripts/lib/docs/doc_graph.py`, whose module docstring holds the summary.

(co-change hotspot)
Doc link-graph for Layer 0 navigability. Parses `[[wikilinks]]` and
`[text](relative/path)` links, resolves them to real files, and builds a directed
graph. Derives PageRank centrality, orphan rate, connectivity, MOC validation, and
doc->code association edges. The doc->code edges are reused by `doc_staleness.py` and
`understanding_analysis.py`, making this module a shared dependency for the document
analysis layer. Folds in vault-native navigation edges from `vault_queries.py` (`.base`
hubs become entry nodes; `dataview` query blocks emit edges from their declaring note),
so a vault navigated by dynamic queries doesn't score as orphaned. `doc-graph-svg.py`
renders this exact graph, so the two artifacts always agree. Obsidian-vault detection
(`_vault_detected`) walks the repo subtree, pruning `EXCLUDE_DIRS`, to find a `.obsidian/`
directory anywhere under `repo_root`, not just at the root - a vault kept as a subdirectory
(`repo/notes/.obsidian/`) sits below the `git rev-parse --show-toplevel` scan target and was
previously reported as no vault, silently disabling downstream vault accommodations (#179).
Pruning `EXCLUDE_DIRS` keeps a vendored or build-artifact `.obsidian/` from tripping a false
positive. Every doc-to-doc edge carries a `kind`: `link` for markdown links, wikilinks and
vault query edges, `reference` for a backticked token outside a fence that resolves to an
existing doc (path tokens via `ownership_parser._extract_path_refs`; exact paths before
guesses: doc-relative, then a path via `ownership_parser._resolve_ref` or a bare basename
that names exactly one walked doc). Fences are recognised behind blockquote and list-item
markers too. References settle in a first pass, before the link
pass: a `.claude/` doc a reference names joins the graph and is read in turn, so links
reach it from any doc; an uncited one stays excluded. The headline `orphan_rate` and
`reachability_pct` count both kinds; `link_only_orphan_rate` / `link_only_reachability_pct`
report links alone over the same node set, so a doc only a reference brought in counts as
an orphan there. A reference edge also clears the pair from `missing_xrefs` (#353).
`directory_breakdown` lists `{path, doc_count, unreachable_count, broken_link_count}`
per top-level directory (`path` is the first segment; root-level docs key as `.`), over
the same curated layer as the headline: raw-source and working-notes trees are left out.
A broken link counts toward the directory of the doc it is written in. Rows are ordered by
unreachable count, then broken links, then doc count, and capped at
`MAX_DIRECTORY_BREAKDOWN`; `directory_count` carries the uncapped total. Only an uncut
list (`len(directory_breakdown) == directory_count`) sums to `doc_count`,
`len(unreachable)` and `dangling_links`; a cut one sums to less (#365).
`link_parents` carries one `{path, link_parent, link_entry}` record per node,
sorted by path, over link edges alone - the same edge set as
`link_only_reachability_pct`, so a doc only a reference edge brought in has no
link path. An entry document carries a null `link_parent` and its own path as
`link_entry`; a doc the walk reached carries the doc that first reached it and
the entry that walk started from; a doc with no link path carries null for both,
which is why `link_entry` is on the record at all. `link_parent` is the next hop
toward an entry, not "who links to me": a doc linked only from an unreachable doc
has an in-edge but no link path, so it records null for both. Walking
`link_parent` up from a doc reconstructs the whole strip back to its entry. The
walk seeds from `entry_points` in their exported byte order, runs each seed to
exhaustion before the next, takes the frontier and each node's successors in
byte order, writes once and never re-expands a recorded doc - so a rebuild is
byte-identical and the earlier entry claims a doc a later one reaches in fewer
hops. There is no cap and no total: `len(link_parents) == doc_count` is the
bound, and capping would drop exactly the doc a doc-heavy run flags. That makes
it the one per-document list on the block, unlike `pagerank`, which is kept off
`as_dict()` to keep `run-context.json` lean: a record is three paths, about 140
bytes as written (137 per record over this repository's 113 docs), so a
5,000-doc vault adds roughly 700 KB. The trade was made because a path strip
needs the exact doc a run flags, and a rounded float can be dropped where a path
cannot. The cost lands on readers, not the file: the layer scorer and the report
writer read `.doc_graph` into model context, so both drop the key with
`del(.link_parents)`. A new LLM reader of the block does the same;
`tests/test_plugin_contract.py` fails a shipped single-line `jq` read of the whole
block that does not. One reader sits outside that guard: `skills/assess/SKILL.md`
tells the orchestrator to read `run-context.json` before writing the report,
which takes the whole file, `link_parents` included. Link syntax and the link pass
live in `doc_links.py`; the SVG's render-side helpers live in `doc_graph_layout.py`.
