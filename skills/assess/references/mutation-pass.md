# Phase 3: the bounded mutation pass (Step 2d)

Step 2d of `SKILL.md`: when the mutation pass applies, how it is offered on its own, and how it runs.

**This is its own phase, asked on its own - never batched with the Phase 1 tool installs.** The default core run is read-only - it never mutates or runs code, so `test_pressure` carries the cheap hollow-test heuristics and mutation-config detection but no survivor data. The decisive Layer 1 signal (would a test actually fail if the code were wrong?) needs this bounded pass, which **modifies your source files and runs your test suite over the mutated code** - a different risk class, so it gets a dedicated question with explicit code-modification framing rather than being waved through inside a bundle of install prompts, and honours the non-interactive contract (skip when `run-context.json .interactive` is `false`).

The `test_focus` block is the single source of focus targets - the risky files that most need test work, already cross-joined from hotspot risk, coverage, and the hollow-test heuristics. Read it; don't recompute it:

```bash
jq '.test_focus' "$REPO_ROOT/.assess/run-context.json"
```

<!-- chat-replace:mutation-offer-intro -->
**Only when `test_focus.entries` holds at least one entry with test evidence** (`test_signal` of `covered_but_hollow` or `sibling_test_only`, on a hot file that is not itself a test) is there anything to deepen - mutating a file with no test yields all survivors and measures the missing test, not an existing one, so `unsupported` / `no_covering_test` / `unknown_no_coverage` entries stay in the report table but out of the mutation scope. If no entry qualifies, skip straight to Step 3. When it has entries, follow the same detect-or-offer-install pattern as Steps 2a/2b: first detect a mutation tool, then ask the user whether to run the bounded pass.

<!-- chat-skip:start -->
**Detect a mutation tool for the repo's language.** Mirror the Step 2b heuristic - `mutmut` for Python, `stryker` for TS/JS - and check PATH plus the permanent-decline marker:

```bash
# Scope: entries with test evidence, ranked, minus test files (the core's mutation_scope;
# regex mirrors sibling_tests.IS_TEST_RE; suffixes mirror test_focus.MUTABLE_SUFFIXES).
# Tool by dominant focus-file language
# (Python -> mutmut, TS/JS -> stryker); mutmut when mixed.
FOCUS_FILES=$(jq -r '(.test_focus.entries // [])[] | select(.test_signal == "covered_but_hollow" or .test_signal == "sibling_test_only") | .path | select(split("/") as $p | (($p[-1] | sub("\\.[^.]*$"; "") | test("(^test_|_test$|\\.test$|\\.spec$|_spec$|Tests?$)")) or ($p[-1] | endswith(".bats")) or any($p[:-1][]; . == "__tests__")) | not) | select(test("\\.(py|tsx?|jsx?|go|rs)$"))' "$REPO_ROOT/.assess/run-context.json" | head -5)
case "$FOCUS_FILES" in
  *.ts|*.tsx|*.js|*.jsx) MUT_TOOL=stryker ;;
  *) MUT_TOOL=mutmut ;;
esac
command -v "$MUT_TOOL" >/dev/null 2>&1 && MUT_PRESENT=1 || MUT_PRESENT=0
[ -f "$REPO_ROOT/.assess/.no-$MUT_TOOL" ] && MUT_DECLINED=1 || MUT_DECLINED=0

# Re-offer once when the existing decline was made under an older plugin major
# (the mutation pass may have changed materially since). The core computes this.
REOFFER_MUT=$(jq -r '.reoffer_mutation // false' "$REPO_ROOT/.assess/run-context.json" 2>/dev/null)
```

**Offer with AskUserQuestion** as a **standalone question** (skip it entirely when `run-context.json .interactive` is `false`, or when `MUT_DECLINED=1` **unless** `REOFFER_MUT=true`, in which case ask once more - the prior decline predates a major bump). Frame the code modification explicitly - not "run a deeper test analysis?" but "**this will modify your source files** (mutating up to 5 focus files) and run your test suite over the changes, time-boxed, then revert". When re-offering, say so: _"You previously declined mutation testing under an older version; the pass has since changed - run it now?"_. Three options, same shape as Steps 2a/2b:

- **Run mutation analysis** - run the bounded pass on the focus files (installing `$MUT_TOOL` first if `MUT_PRESENT=0`, exactly as Step 2b installs a dead-code tool: run the platform-appropriate install, surface any failure as a chat message, and fall back to no-mutation on failure - never block).
- **Skip for now** - continue with the cheap read intact. Don't write a marker; ask again next run.
- **Skip permanently for this repo** - `write_decline_marker "$MUT_TOOL"` so future runs don't ask. Re-declining restamps the marker at the current version, so the re-offer won't repeat within this major.

**On accept (tool available):** run the opt-in mutation pass, then regenerate the heatmap with the survivor overlay. The core re-run reads the `test_focus` targets with test evidence itself, runs `scan_test_pressure(..., opt_in=True)` scoped to them, and rewrites the `test_pressure` block in `run-context.json` in place, together with what derives from it: `mutation_not_run_cap`, the `level` tally with the `headline`, the `log.md` readiness line and `badge.json` worded from it, the `untrusted_hotspot` finding in `derived_findings`, `attention`, `findings_markdown`, `keyhole_summary` and `prescribed_actions`. Read those blocks after this step, not before:

<!-- chat-skip:end -->
```bash
# 1. Re-run the test-pressure scan with the bounded mutation pass enabled
<!-- chat-replace:uv-core-mutation -->
uv run "${CLAUDE_SKILL_DIR}/scripts/assess_core.py" "$REPO_ROOT" --opt-in-mutation  # plus the run's --profile NAME or --levels FILE and every --pack FILE, if it had them

# 2. Regenerate the heatmap with the survivor overlay so covered-but-unpinned
#    files get hatched and stop reading as safe green
<!-- chat-replace:uv-treemap-overlay -->
uv run "${CLAUDE_SKILL_DIR}/scripts/complexity-treemap.py" "$REPO_ROOT" -o "$REPO_ROOT/.assess/complexity-heatmap.svg" --stats "$REPO_ROOT/.assess/complexity-stats.json" --test-pressure "$REPO_ROOT/.assess/run-context.json"
```

The pass runs once per package root among the focus files (the nearest directory with a `pyproject.toml`, `setup.cfg` or `setup.py`), from that directory, under the package's own mutmut config when it has one and a generated one otherwise. mutmut runs the package's suite, so it runs where the suite's dependencies import: a `.venv` / `venv` in the package that already holds mutmut 3 (`runner: venv`, read only: nothing installed into it, no bytecode written), else, for a `pyproject.toml` with a `[project]` table and `uv` on PATH, a scratch environment `uv run --project` builds in the temp copy from the package's dependencies and default dependency groups (not `[project.optional-dependencies]` extras, so test dependencies kept in an extra still fail the baseline run, and the reason says which import) plus `mutmut==3.8.0` and pytest (`runner: uv`; first use may download them, and the time counts against the pass's budget), else the `mutmut` on PATH (`runner: path`), which serves only a suite with no third-party test dependencies. `test_pressure.mutation_groups` lists each run with its `root`, `config` (`repo` or `generated`), `runner` (absent on a group that stopped before a runner was chosen: budget, config gap or copy error), its `reason` when it could not run or stopped early (a failed baseline run names the first import error or failing test), `partial: true` when it hit the time box and kept only the mutants it had tested, and any `unmeasured` focus files (outside the package config's scope, or not reached before a partial stop) when it ran without them. The pass counts as run when any group produced mutant records; in the report, name each group that did not run or is `partial` and quote its `reason`, and name the `unmeasured` files, so a partial pass reads as partial. A `partial` group's figures are the fastest mutants only, a biased sample, so when every group with records is `partial` the Layer 6 cap holds at Partial (`mutation_not_run_cap.partial`, Step 3).

<!-- chat-skip:start -->
With no mutation data the `--test-pressure` flag is a silent no-op, so the overlay regeneration is harmless even if the pass produced nothing.

**On decline or no tool available:** continue with the cheap read intact - the assessment is complete without it. State in the report that the deep mutation pass was **not** run and why (declined, or no mutation tool for the language), so the Layer 1 read is honest about its depth rather than implying the focus files were proven well-tested.
<!-- chat-skip:end -->
