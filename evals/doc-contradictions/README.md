# doc-contradictions evals

Eval cases for [`/doc-contradictions`](../../skills/doc-contradictions/SKILL.md). Their job is to show that Haiku is good enough as the comparison model. The orchestrator runs on the session model and only clusters and merges, so a pass here is a pass by the [Haiku comparer](../../agents/doc-contradictions-comparer.md), not by a larger model cleaning up after it. Back to the [Map of Content](../../docs/index.md).

## Layout

Each case uses the repository's eval format (`docs/design/2026-09-modernization/evals/spec.md`), plus two files that `claude plugin eval` needs to stage a fixture repository:

| File | Read by | Holds |
|------|---------|-------|
| `prompt.md` | `claude plugin eval`, `ab-equivalence` | the user prompt; its frontmatter sets turns, timeout and tools |
| `graders/criteria.md` | `claude plugin eval` (LLM judge), `skill-forge` | **Response checks**, which the judge grades from the final reply, and **Trace checks**, which the operator grades from the run trace |
| `README.md` | people | what the case holds the skill to, and its input |
| `case.yaml` | `claude plugin eval` | `context.scaffold_script` only |
| `scaffold.sh` | `claude plugin eval --scaffold` | copies the case's fixture from `skills/doc-contradictions/tests/fixtures/` into the run workspace and commits it |

The cases live here rather than under `skills/doc-contradictions/evals/`, because `claude plugin eval` refuses an eval directory inside `skills/` (a loaded component directory). The fixtures stay under the skill's `tests/fixtures/`, which `/assess`'s doc graph skips.

| Case | Holds the skill to |
|------|--------------------|
| [`happy-doc-vs-doc-setting`](./happy-doc-vs-doc-setting/README.md) | two pages pin different values for the same setting; both lines cited |
| [`happy-doc-vs-code-default`](./happy-doc-vs-code-default/README.md) | a doc claims a default the code does not use; the code line cited |
| [`edge-clean-set-no-findings`](./edge-clean-set-no-findings/README.md) | consistent docs in different words and units; no contradiction reported |
| [`edge-excluded-historical-record`](./edge-excluded-historical-record/README.md) | a conflict only between `docs/design/` and the code; hidden by default, reported with `--no-default-excludes` |

## Running them

From the repository root, with git 2.31 or newer first on `PATH`:

```bash
claude plugin eval . --eval-dir evals/doc-contradictions \
  --trust-plugin --scaffold --allow-tools Bash Write \
  --runs 3 --ablation none --judge-model sonnet \
  --no-publish --keep-temp --max-cost-usd 15
```

- `--scaffold` runs each case's `scaffold.sh`, which stages the fixture.
- `--allow-tools Bash Write` lets the skill run `rg` and write its report.
- `--judge-model sonnet` sets the grader only. The comparers still run on Haiku.
- `--keep-temp` keeps each run's `out/trace.jsonl` for the trace checks.

**Trace checks.** Every comparer must have run on a Haiku model ID, and each reported finding must be in a comparer's raw JSON. The first is one `jq` over a kept trace:

```bash
jq -r 'select(.type=="assistant" and .parent_tool_use_id != null) | .message.model' out/trace.jsonl | sort -u
```

The second compares the `Agent` tool results in the same trace with the report.

## Recorded results

Recorded on 2026-11-20 with Claude Code 2.1.329. The first three rows come from one full-suite run. The last row is a `--case edge-excluded-historical-record` rerun after fix 4 below. The orchestrator ran on `claude-opus-5-5` (the session default). **Every comparer ran on `claude-haiku-5-5`**, and no case needed a larger comparison model.

| Case | `claude plugin eval` (3 runs) | Trace checks (3 runs) |
|------|-------------------------------|-----------------------|
| happy-doc-vs-doc-setting | 3/3 pass, score 1.00 | 3/3: Haiku cited `docs/configuration.md:13` and `docs/troubleshooting.md:9` |
| happy-doc-vs-code-default | 3/3 pass, score 1.00 | 3/3: Haiku cited `docs/caching.md:9` and `src/cache.py:4` |
| edge-clean-set-no-findings | 3/3 pass, score 1.00 | 3/3: the comparer returned `[]` |
| edge-excluded-historical-record | 3/3 pass, score 1.00 | 3/3: the default-run brief omitted the design doc and the comparer returned `[]`; the override comparer cited `docs/design/2024-03-batching.md:7` and `src/consumer.py:3` |

Getting there took four fixes. None of them changed the comparison model.

1. **The `excluded-historical` fixture.** It defined `FLUSH_AFTER_SECONDS` and never used it, so "a partial batch is flushed after 2 seconds" really was unbacked by the code. Haiku reported this in 2 of the first 3 runs. The fixture now implements the flush.
2. **The clean-set fixture.** It equated "10 MB" with `10 * 1024 * 1024`. Haiku returned `[]`, but the orchestrator added an aside of its own about MB versus MiB, and the judge failed it. The fixture now says MiB. The skill now forbids orchestrator observations about the documents.
3. **The citation check.** The orchestrator once "corrected" Haiku's correct `docs/configuration.md:13` to line 12. The citation check now moves a line number only on an `rg -nF` hit.
4. **The historical-record prompt.** The case prompt now asks for both reports in full. One run had summarised the default report, so the judge could not grade it.
