# lib/docs

This folder holds two kinds of file.

- **The doc scans (`.py`).** They read a repository's Markdown and reStructuredText and measure how well an
  agent can find its way through it: which docs link to which, which docs nobody reaches,
  which docs describe which code, and which docs have fallen behind the code they describe.
- **The long module references (`.md`).** A module from any part of `lib/` whose full
  description does not fit in its docstring keeps a summary there and puts the rest in one
  file here. The list of references, with a link to each, is in the Module reference
  section of [lib/README.md](../README.md#module-reference).

## Where to start

`doc_graph.py` builds the link graph every other doc scan leans on: orphans, reachability,
entry points and the edges from docs to code. Its long reference is
[doc_graph.md](doc_graph.md). After it, read `doc_staleness.py`, which asks whether a doc
has kept up with its code.

| Question | Modules |
|---|---|
| How do docs link to each other? | `doc_graph`, `doc_links`, `doc_rst` (reST and Sphinx), `vault_queries` (Obsidian queries as links), `doc_graph_rows` (link parents, directory counts), `doc_graph_layout` (drawing only) |
| Which code does a doc describe? | `doc_ownership` (the one ownership rule), `ownership_parser`, `parallel_docs` |
| Has a doc fallen behind its code? | `doc_staleness`, `doc_provenance` (generated docs), `doc_directives` (pages that hold only generator directives) |
| Are a doc's claims true? | `doc_claims` |
| Where do stale docs and complex code meet? | `doc_complexity_join` |

`doc_graph` imports `ownership_parser` inside a function, because `ownership_parser`
imports `doc_graph`. Keep that import where it is.
