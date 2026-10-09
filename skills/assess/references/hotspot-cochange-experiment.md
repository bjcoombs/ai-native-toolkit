# Co-change breadth in the hotspot score: experiment and decision

Date: 2026-10-09. Issue: https://github.com/bjcoombs/ai-native-toolkit/issues/515. Paths are relative to the repository root.

## Question

The hotspot score in `skills/assess/scripts/complexity-treemap.py` is `sqrt(effective_ccn) x sqrt(1 + commits) x sqrt(est_tokens)`. Agent benchmarks report that how many files a change touches predicts agent failure better than anything else measured: on SWE-bench-Live (https://arxiv.org/html/2505.23419), single-file patches under 5 lines are solved about 48% of the time and patches touching 3 or more files under 10%. The score has no term for that. Would adding `sqrt(1 + breadth)` change which files the report sends a reader to first?

## Decision

**The formula stays as it is.** The rule set in the issue before measuring was: change it only if the top 3 changes on at least two of the three repositories and the files that enter are defensible on inspection.

In the 12-month churn window, which `pick_churn_window` chose for all three repositories, the rule was met, but only narrowly. The top 3 changed on this repository and on vite and not on flask, and both entrants were defensible. Both changes, though, were swaps of ranks 3 and 4 whose scores were 3.7% and 2.7% apart, and no file from outside the top 4 reached the top 3.

The result did not survive a change of window. With a 24-month window, vite's top 3 does not change (`build.ts` is already third without the term). With a 6-month window it changes, but a different file enters (`plugins/html.ts`). The vite result therefore depends on the window, so the rule holds on only one repository, and this repository's history does not vary with the window at all (see Window sensitivity). Over every window and repository, Spearman's rho between the two scores is 0.97 or higher. The term reorders near-ties; it does not redirect readers.

## Method

- **Baseline score.** The production code at commit `5c9befd` of this repository: `collect` (lizard 1.23.0, with scc where installed), `est_tokens_by_path` and `_enrich_row` from `complexity-treemap.py`, run over every scored file rather than only the ten the stats sidecar keeps.
- **Breadth.** For each file, the median number of *other* files changed in the commits that touched it inside the churn window. Commits come from `parse_commit_file_sets` in `skills/assess/scripts/lib/change_coupling.py`, the same parse the `hidden_coupling` finding uses. Commits of more than 50 files (`MAX_COMMIT_FILES_FOR_COUPLING`, the coupling analysis's bulk cut) are left out of the median. A file with no qualifying commit has breadth 0.
- **New score.** The baseline score times `sqrt(1 + breadth)`. Nothing else differs between the two rankings.
- **Rank agreement.** Spearman's rho and Kendall's tau-b between the two scores, over all scored files and over the baseline top 50.
- **Window sensitivity.** The 12-month run is the one `/assess` would make. The same comparison was rerun with the churn count and the breadth both taken over 6 and 24 months.
- **Robustness variant.** In the 12-month window, breadth was recomputed counting only other *scored* files, leaving out lockfiles, Markdown and `plugin.json`. This repository bumps `.claude-plugin/plugin.json` in 238 of the window's 465 commits, which adds about one to every file's breadth. The variant gave the same top 3 in every repository and correlations within 0.04.
- The analysis script ran outside the repository and is not committed. The two public repositories were cloned with `git clone --filter=blob:none`, and only `git log` and the static complexity scan read them.

## Repositories

| Repository | Shape | Commit measured | Files scored | Commits in 12-month window | Bulk commits excluded | Median breadth (p90) |
|---|---|---|---|---|---|---|
| bjcoombs/ai-native-toolkit | Python core plus Markdown skills, squash-merged PRs | `5c9befd37705ed76bdfc467479c9f7eaa21de1b1` | 224 | 465 | 0 | 8.5 (16) |
| pallets/flask | Mid-size Python library | `d086db856be187255b8ec61ef409357393020f32` | 218 | 95 | 0 | 4 (11) |
| vitejs/vite | TypeScript pnpm monorepo | `3b72997d42e7dd2b31898d0e68461e3025e836b6` | 2592 | 1246 | 5 | 6.5 (28) |

Breadth medians are over files with at least one commit in the window.

## Window sensitivity

"Gap" is the baseline score difference between ranks 3 and 4, as a share of rank 3's score.

| Repository | Window | Commits | Bulk excluded | Spearman, all files | Kendall tau-b, top 50 | Top-10 overlap | Top 3 without breadth | Top 3 with breadth | Top 3 changed | Gap |
|---|---|---|---|---|---|---|---|---|---|---|
| ai-native-toolkit | 6 months | 363 | 0 | 0.972 | 0.722 | 9 of 10 | `complexity-treemap.py`, `keyhole_signals.py`, `assess_core.py` | `complexity-treemap.py`, `keyhole_signals.py`, `doc_graph.py` | yes | 3.7% |
| ai-native-toolkit | 12 months | 465 | 0 | 0.972 | 0.722 | 9 of 10 | same | same | yes | 3.7% |
| ai-native-toolkit | 24 months | 504 | 0 | 0.972 | 0.722 | 9 of 10 | same | same | yes | 3.7% |
| flask | 6 months | 31 | 0 | 0.999 | 0.873 | 8 of 10 | `app.py`, `cli.py`, `test_basic.py` | `cli.py`, `app.py`, `test_basic.py` | no (order only) | 14.0% |
| flask | 12 months | 95 | 0 | 0.999 | 0.878 | 9 of 10 | `app.py`, `test_basic.py`, `cli.py` | same | no | 8.5% |
| flask | 24 months | 235 | 0 | 0.998 | 0.816 | 9 of 10 | `app.py`, `cli.py`, `sansio/app.py` | same | no | 2.9% |
| vite | 6 months | 612 | 2 | 0.991 | 0.747 | 9 of 10 | `plugins/css.ts`, `config.ts`, `utils.ts` | `plugins/css.ts`, `config.ts`, `plugins/html.ts` | yes | 1.1% |
| vite | 12 months | 1246 | 5 | 0.990 | 0.721 | 8 of 10 | `config.ts`, `plugins/css.ts`, `utils.ts` | `plugins/css.ts`, `config.ts`, `build.ts` | yes | 2.7% |
| vite | 24 months | 2712 | 8 | 0.985 | 0.727 | 9 of 10 | `config.ts`, `plugins/css.ts`, `build.ts` | `plugins/css.ts`, `config.ts`, `build.ts` | no (order only) | 5.6% |

This repository's rows are identical because every scored file's recorded history starts inside the last six months. The `/assess` code moved to its current paths on 2026-05-21, and neither the churn count nor the breadth follows renames. So its one swap is a single observation, not three.

In vite's 6-month window, `packages/create-vite/src/index.ts` rises from 13 to 4 on a breadth of 19, taken from the weekly "update all non-major dependencies" PRs. That entrant is not defensible: dependency bumps touch many files without making the file harder to change.

## 12-month rankings

### ai-native-toolkit

| Rank before | File | ccn (worst fn) | Commits | Est. tokens | Breadth | Rank after |
|---|---|---|---|---|---|---|
| 1 | `skills/assess/scripts/complexity-treemap.py` | 214 (14) | 33 | 17610 | 8 | 1 |
| 2 | `skills/assess/scripts/lib/keyhole_signals.py` | 247 (11) | 26 | 17957 | 8.5 | 2 |
| 3 | `skills/assess/scripts/assess_core.py` | 26 (13) | 69 | 9247 | 8 | 4 |
| 4 | `skills/assess/scripts/lib/doc_graph.py` | 209 (15) | 23 | 12110 | 10 | 3 |
| 5 | `skills/assess/tests/test_complexity_treemap.py` | 189 (6) | 25 | 19417 | 7 | 5 |
| 6 | `skills/assess/scripts/lib/wiki_writer.py` | 170 (13) | 17 | 11001 | 8 | 6 |
| 7 | `skills/assess/tests/test_assess_core.py` | 62 (6) | 45 | 7553 | 9 | 8 |
| 8 | `skills/assess/scripts/assess_finalize.py` | 144 (11) | 16 | 9001 | 9.5 | 9 |
| 9 | `skills/assess/scripts/lib/doc_staleness.py` | 119 (14) | 15 | 8402 | 11 | 7 |
| 10 | `scripts/floor_anchor.py` | 188 (15) | 7 | 12580 | 4 | 21 |

Enters the top 10 at 10: `skills/assess/scripts/lib/git_churn.py` (was 14; breadth 12 over 10 commits).

### flask

| Rank before | File | ccn (worst fn) | Commits | Est. tokens | Breadth | Rank after |
|---|---|---|---|---|---|---|
| 1 | `src/flask/app.py` | 170 (16) | 9 | 16325 | 2 | 1 |
| 2 | `tests/test_basic.py` | 276 (5) | 7 | 13557 | 5 | 2 |
| 3 | `src/flask/cli.py` | 161 (18) | 4 | 9189 | 5 | 3 |
| 4 | `src/flask/sansio/app.py` | 81 (13) | 5 | 9884 | 2 | 4 |
| 5 | `src/flask/helpers.py` | 48 (9) | 5 | 6128 | 3 | 6 |
| 6 | `src/flask/ctx.py` | 54 (7) | 5 | 4566 | 3 | 7 |
| 7 | `tests/test_blueprints.py` | 167 (3) | 3 | 7822 | 6 | 5 |
| 8 | `src/flask/sansio/scaffold.py` | 62 (10) | 1 | 7659 | 2 | 9 |
| 9 | `src/flask/sansio/blueprints.py` | 83 (23) | 0 | 6755 | 0 | 14 |
| 10 | `src/flask/sessions.py` | 35 (5) | 4 | 3739 | 5 | 8 |

Enters the top 10 at 10: `src/flask/json/provider.py` (was 13; breadth 8 from a single commit).

### vite

| Rank before | File | ccn (worst fn) | Commits | Est. tokens | Breadth | Rank after |
|---|---|---|---|---|---|---|
| 1 | `packages/vite/src/node/config.ts` | 250 (49) | 70 | 22828 | 3 | 2 |
| 2 | `packages/vite/src/node/plugins/css.ts` | 472 (30) | 55 | 29284 | 4 | 1 |
| 3 | `packages/vite/src/node/utils.ts` | 225 (43) | 44 | 14775 | 3 | 4 |
| 4 | `packages/vite/src/node/build.ts` | 186 (31) | 54 | 15247 | 5 | 3 |
| 5 | `packages/vite/src/node/plugins/html.ts` | 290 (45) | 25 | 14219 | 2 | 6 |
| 6 | `packages/vite/src/node/server/index.ts` | 163 (29) | 40 | 11484 | 2 | 7 |
| 7 | `packages/vite/src/node/plugins/importAnalysis.ts` | 106 (75) | 18 | 9908 | 6 | 5 |
| 8 | `packages/vite/src/node/plugins/resolve.ts` | 226 (45) | 19 | 9360 | 3.5 | 8 |
| 9 | `packages/vite/src/node/optimizer/index.ts` | 182 (13) | 27 | 11608 | 2 | 18 |
| 10 | `packages/create-vite/src/index.ts` | 66 (8) | 65 | 8246 | 2.5 | 17 |

Enter the top 10: `packages/vite/src/node/plugins/importAnalysisBuild.ts` at 9 (was 11; breadth 6) and `packages/vite/src/node/plugins/worker.ts` at 10 (was 17; breadth 9).

## Inspection of the 12-month entrants

- **vite, `build.ts` replaces `utils.ts` in the top 3.** `build.ts` changed alongside `config.ts` in 24 of its 52 qualifying commits and alongside `plugins/css.ts` in 12. `utils.ts` moves with `pnpm-lock.yaml` and `config.ts` (12 commits each) and with its own test (11), a median of 3 other files per change against `build.ts`'s 5. Defensible on its own, but the 24-month run already ranks `build.ts` third without the term.
- **ai-native-toolkit, `doc_graph.py` replaces `assess_core.py` in the top 3.** `doc_graph.py` moves with `doc-graph-svg.py` (9 of 23 commits), `skills/assess/SKILL.md` and `lib/README.md` (12 each), and `doc_staleness.py` (7). Part of that breadth comes from this repository's rule that a `lib/` change updates `lib/README.md` in the same PR. `assess_core.py` is the orchestrator: low complexity (aggregate 26) but touched in 69 commits.
- **Noisy tail.** `src/flask/json/provider.py` enters flask's top 10 on breadth taken from one commit. 818 of vite's 1123 active files take their breadth from two commits or fewer, so the term is least reliable on the low-churn files it moves most.

## Limits

- Three repositories and one commit each. The data show how much the term reorders the list. They cannot show whether either order better predicts agent failure: no dataset links these files to agent outcomes.
- Breadth counts every file in the commit, including documentation, lockfiles and version bumps. In a repository that squash-merges PRs, it measures PR size, which follows house rules as well as code coupling.
- Renames are not followed, the same as the churn count, which is why this repository gives one observation instead of three windows.

## Reopening the question

Rerun this comparison if a dataset appears that links files or patches to agent success, or if the hotspot score gains a per-file measure that changes the gap between near-tied files. A term that only reorders files within a few percent of each other is not worth a stats schema bump. That bump marks every repository's next cross-run diff as not comparable.
