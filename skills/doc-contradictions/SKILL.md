---
name: doc-contradictions
description: "Find contradictions in a repository's documentation: rules that conflict between two documents, and document claims that contradict the code they describe. Clusters the docs by the doc graph and shared subject code, fans out one read-only Haiku comparison subagent per cluster plus a cross-cluster pass over hub documents, and writes a Markdown report with one entry per contradiction citing both sides as path:line with a confidence. Excludes historical intent records (docs/design/, docs/superpowers/) by default. TRIGGER when the user types /doc-contradictions, asks to find docs that contradict each other or the code, wants a stale-claim or doc-drift check, asks 'do our docs disagree?', 'is the README still true?', or follows an /assess recommendation to check the doc set for contradictions."
argument-hint: "[path] [--no-default-excludes] [--exclude <glob>]... [--changed-from <ref>]"
---

# doc-contradictions: where do the docs disagree with each other or the code?

Run it on any repository. It is **read-only** apart from its report: it never edits a document or a source file.

**Windows:** run the bash blocks below in Git Bash, which ships with Git for Windows. Installs, PATH notes and the make-free commands are in the [Windows setup guide](../../docs/windows.md).

`/assess` measures the doc graph structurally (hubs, orphans, broken links) and states plainly that contradictions are out of its deterministic scope. This skill is the LLM pass that covers them.

## Two roles, two models

| Role | Model | Does |
|------|-------|------|
| Orchestrator (you) | the session model | enumerates docs, builds clusters, spawns comparers, validates citations, merges, writes the report |
| Comparer | **Haiku** | reads one cluster and reports contradictions with both sides cited |

The orchestrator never judges whether two statements contradict. It **only clusters and merges**. A finding that no comparer reported never enters the report, and a comparer finding is never dropped on the orchestrator's opinion. The one exception is the mechanical citation check in Step 5, and every finding it drops is listed in the report. This split is what makes Haiku's quality measurable: the evals in [`evals/doc-contradictions/README.md`](../../evals/doc-contradictions/README.md) grade the comparers, not a larger model cleaning up after them.

## Arguments

| Argument | Default | Effect |
|----------|---------|--------|
| `path` | the current repository root | the repository to scan |
| `--no-default-excludes` | off | also scan `docs/design/` and `docs/superpowers/` |
| `--exclude <glob>` | none | exclude more paths; repeatable |
| `--changed-from <ref>` | off | check only what changed since `ref`: the clusters holding a changed doc or a changed subject-code file, plus the cross-cluster pass when a changed file is a hub |

**Why the default excludes.** `docs/design/` and `docs/superpowers/` hold historical intent records: specs and plans that describe what was intended at the time and are not kept in step with the code. A design doc that proposed one value when the code shipped another is history, not a contradiction. Pass `--no-default-excludes` to audit them anyway.

**Scoping to a change.** With `--changed-from <ref>`, Steps 1 and 2 run as code, not by hand: `python3 "${CLAUDE_SKILL_DIR}/scripts/doc_clusters.py" --repo "$REPO_ROOT" --changed-from <ref>`, passing `--no-default-excludes` and each `--exclude` through. It compares the merge base of `ref` and `HEAD` with the work tree, and prints the clusters, the hubs and their subject code, and whether the cross-cluster pass runs. Use them as printed and go to Step 3; with no cluster and no cross-cluster pass, write the report with `Contradictions: 0` and no comparer runs. A changed file that no longer exists still selects the clusters whose docs name it. The script builds the same clusters without the flag, so a full run may use it too. The `doc-sync` pull request check (`.github/workflows/doc-sync.yml`) runs this mode against each pull request's base; `docs/contributing/ci.md` describes it.

## Step 1: enumerate the documents

Set `REPO_ROOT` to `path`, or to `git rev-parse --show-toplevel`. List the docs with `rg`, which respects `.gitignore` and works without git. `--hidden` keeps docs under dot-directories such as `.github/`; the two `!` globs keep out git internals and this skill's own report:

```bash
cd "$REPO_ROOT"
rg --files --hidden -g '!.git/**' -g '!.doc-contradictions/**' \
   -g '*.md' -g '*.mdx' -g '*.rst' -g '*.adoc' \
   -g '!docs/design/**' -g '!docs/superpowers/**' \
   -g '!**/node_modules/**' -g '!**/vendor/**' -g '!**/tests/fixtures/**' \
   -g '!CHANGELOG*' | sort
```

- With `--no-default-excludes`, drop the two `docs/design` and `docs/superpowers` globs. Keep the others.
- Add one `-g '!<glob>'` per `--exclude`.
- Count what the default excludes removed (run the same listing restricted to those two trees) for the report header.

If no document is left, write the report with `Documents scanned: 0` and stop.

## Step 2: build the clusters

A cluster is a set of docs that talk about the same thing, plus the code they describe. Build it from structure, not from your own reading of the prose.

1. **Doc edges.** For each doc, collect the repo paths it points at: the target of every markdown link and every backticked path that exist on disk. Resolve relative links against the doc's directory.
2. **Subject code.** A doc's subject code is every non-doc file it points at, plus, for a doc whose directory holds code (a module `README.md`), the code files in that directory.
3. **Components.** Two docs join the same cluster when one links to the other, or when they share a subject code file. Take the connected components.
4. **Leftovers.** A doc with no edges joins the cluster of the other docs in its top-level directory. Root-level loose docs form one cluster.
5. **Size cap.** Split any cluster over 8 docs or about 2,000 doc lines by subdirectory, keeping each doc beside the docs it links to most.
6. **Hubs.** Rank docs by inbound doc links. The top 5 with at least two inbound links are the hubs (fewer on a small repo, none if no doc has two).

A shell sketch for the edges (refine per repo; the goal is the edge list, not this exact pipeline):

```bash
rg -o --no-heading -n '\]\(([^)#]+)' -r '${1}' <doc-list>   # link targets
rg -o --no-heading -n '`([^` ]+/[^` ]+\.[a-z]+)`' -r '${1}' <doc-list>   # backticked paths
```

Record each cluster as `{id, docs: [...], subject_code: [...]}`.

## Step 3: fan out the comparers

Spawn **one comparer per cluster**, all in one message so they run in parallel:

```
Agent(
  subagent_type: "doc-contradictions-comparer",
  model: "haiku",
  description: "Compare doc cluster <id>",
  prompt: "<brief>"
)
```

Pass `model: "haiku"` on every call even though the agent's frontmatter already says `haiku`. When the harness has not loaded the agent definition it runs the spawn on the lead's model, and the explicit `model` keeps it on Haiku. If `doc-contradictions-comparer` is not an available agent type, spawn `subagent_type: "general-purpose"` with `model: "haiku"`, read `${CLAUDE_SKILL_DIR}/../../agents/doc-contradictions-comparer.md` and put its body (below the frontmatter) at the top of the brief, and add "Read-only: use Read, Grep and Glob only." That subagent still holds Write, Edit and Bash, so this fallback is read-only by instruction only, not by its tool set.

The brief names:

- `REPO_ROOT`;
- the cluster's docs and subject code, as repo-relative paths;
- the excluded globs, so the comparer does not cite an excluded file it finds by searching.
- with `--no-default-excludes`: "Docs under `docs/design/` and `docs/superpowers/` are included on purpose. Report where they disagree with the current code or current docs, even though they are written as plans or decisions."

The comparer returns a JSON array of findings (schema in the agent file), `[]` when it finds none.

## Step 4: the cross-cluster pass

When there are two or more clusters and at least one hub, spawn **one more** comparer the same way over the hub docs together, with the union of their subject code. Its brief adds: "These are the most-linked documents. Compare them against each other and against their subject code; a contradiction inside one cluster has already been checked." On a repo that is one cluster, skip this pass and say so in the report header.

## Step 5: validate and merge

For each finding in every comparer's array:

1. **Citation check (mechanical).** First, drop the finding if either side's `quote` is empty, only whitespace, or contains a newline: `rg -nF -f` with an empty pattern matches every line, so such a quote would pass on any line. List it under "Dropped by citation check" with the reason. Otherwise, for each side, write the quote verbatim to a file under `$REPO_ROOT/.doc-contradictions/quotes/` (excluded from the doc scan) with your file-writing tool, not through a shell string (code quotes often hold `'` or `"`), then run `rg -nF -f <quote-file> -- '<path>'`. The `rg` hit numbers are the only evidence; never move a line number by reasoning about the file or counting lines yourself.
   - The cited `line` is among the hits: keep the finding as cited.
   - The cited `line` is not a hit, but exactly one hit lies within two lines of it: replace `line` with that hit.
   - Otherwise (no hit, the file is missing, or several nearby hits): drop the finding and list it under "Dropped by citation check" with the `rg` result.

   Do not re-judge the contradiction itself. When every finding is checked, delete `$REPO_ROOT/.doc-contradictions/quotes/`, so the run leaves nothing outside `report.md`.
2. **Excluded paths.** Drop a finding that cites an excluded path, and count it in the header's `Dropped (cited an excluded path)` line.
3. **Dedupe.** Two findings that cite the same pair of locations (in either order) are one finding: keep the higher confidence and both explanations.

Keep the raw comparer arrays in your working context until the report is written: the report's appendix records how many findings each comparer returned.

## Step 6: write the report

Write `$REPO_ROOT/.doc-contradictions/report.md` (create the directory) and print the same content. Order findings high, then medium, then low confidence, and within a confidence doc-vs-code first. Cite every path repo-relative; strip a `REPO_ROOT` prefix a comparer included.

```markdown
# Documentation contradictions

- Repository: <REPO_ROOT basename>
- Documents scanned: <n> in <k> clusters; cross-cluster pass over <h> hub docs (or: skipped, one cluster)
- Scope: changed from `<ref>`, <k> of <K> clusters (only with `--changed-from`)
- Excluded: docs/design/ (<n> docs), docs/superpowers/ (<n> docs)<, user globs>
- Dropped (cited an excluded path): <n>
- Comparison model: haiku (<k + 1> comparer runs)
- Contradictions: <total> (<doc-vs-doc> doc-vs-doc, <doc-vs-code> doc-vs-code)

## 1. <subject, a few words>

- Kind: doc-vs-code
- Confidence: high
- `docs/api.md:40`: "Clients may send 100 requests per minute."
- `src/limits.go:7`: "const DefaultRatePerMinute = 60"
- Why: <the comparer's one or two sentence explanation>

## Dropped by citation check

- <subject>: `<path>:<line>` quote not found at that line

## Comparer runs

| Run | Docs | Findings returned |
|-----|------|-------------------|
| cluster 1 | `README.md`, `docs/a.md` | 1 |
| hubs | `README.md` | 0 |
```

- With no contradictions, the body after the header is the single line `No contradictions found.`, followed by the Comparer runs table.
- Omit "Dropped by citation check" when nothing was dropped.
- Every finding cites both sides as `path:line` with the quoted text. A finding with one side is not a contradiction; the citation check drops it.

Close with one line to the user: the report path and the count by confidence. Add no observations of your own about the documents, not even as an aside marked as yours: a contradiction the comparers did not report is out of this skill's output by design, and an orchestrator remark is exactly the larger-model rescue the Haiku evals must not contain.

## What counts as a contradiction

The comparer's agent file carries the full rule. In short: two statements that **cannot both be true** of the current system - different values for the same setting, a documented default the code does not use, an instruction one doc requires and another forbids. Different wording for the same fact, one doc being more detailed than another, and a doc describing a planned change it labels as planned are not contradictions.
