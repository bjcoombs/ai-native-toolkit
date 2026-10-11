# /assess architecture

How the `/assess` skill splits work between a deterministic core and the LLM. The per-module reference is [`skills/assess/scripts/lib/README.md`](../../skills/assess/scripts/lib/README.md). Back to the [Map of Content](../index.md).

## Layering

The deterministic core in `skills/assess/scripts/lib/` does all data work; the LLM only writes prose. The layering is **inward-only**: a `lib/` module may import other `lib/` modules and third-party libraries, but never an orchestrator script (`assess_core.py`, `assess_finalize.py`, `assess_report.py`, `assess_gate.py`, ...). This keeps the core independently testable and reusable. `skills/assess/tests/test_self_architecture.py` enforces it: an `ast` scan fails the build if any `lib/` module imports an orchestrator (the forbidden set is derived from disk, so a new orchestrator is covered automatically).

Key modules:

- `lib/agent_instructions_grader.py` - heuristic scoring of CLAUDE.md / AGENTS.md / GEMINI.md / .cursorrules / .github/copilot-instructions.md (regex + arithmetic, no AI).
- `lib/stats_diff.py` - cross-run comparison (graduated/regressed/new/persistent hotspots).
- `lib/wiki_writer.py` - renders `index.md`, `log.md`, `hotspots/*.md` from templates.
- `lib/anomaly_detector.py` - flags suspicious run results for self-feedback.
- `scripts/assess_core.py` - orchestrator; writes `run-context.json` for the LLM to read.
- `scripts/assess_finalize.py` - LLM write-back; reads `.assess/finalize-input.json` and replaces deterministic-core placeholders in `log.md` and `hotspots/*.md` with the LLM-derived score and per-file actions.

Tests live in `skills/assess/tests/` and run via `uv run --with pytest pytest`. Add a test alongside any change to a deterministic module: that is the contract that lets the output be trusted regardless of which LLM is driving.

`skills/assess/scripts/lib/README.md` is also the home of the **co-change seam map**: it names the directories that move together by design (the core <-> lib seam, each lib module <-> its test, and the standalone build <-> the skills it packages), so the historical coupling `/assess` flags on this repo reads as owned cohesion, not entanglement. Update it alongside any change to a `lib/` module so it never becomes a lying map of its own.

## The `.assess/` wiki

The `.assess/` directory in a target repo is a compounding wiki:

- `assess-report.md` - latest prose-heavy summary (LLM-written)
- `complexity-stats.json` + `.prior.json` - current and previous run sidecars
- `complexity-heatmap.svg` - current treemap
- `run-context.json` - structured data the LLM uses for prose
- `index.md` - catalog of every hotspot ever flagged (deterministic)
- `log.md` - append-only run history (deterministic)
- `hotspots/<slug>.md` - per-file persistent page (deterministic)

Each `/assess` run reads the prior state from this directory and adds to it. Hotspots that leave the top list graduate. The wiki is the value, not any single snapshot.

## The LLM write-back pattern

The deterministic core writes log and hotspot files with placeholders. After the LLM derives the score, top action, and per-hotspot suggestions, it writes `.assess/finalize-input.json` and invokes `assess_finalize.py` to replace the placeholders in place. The deterministic core stays ignorant of LLM-derived content while the wiki still has every value filled.

## Schema convention

`instructions_grade` is `Optional[str]`: `null` means no instruction file was found at any known location, which needs different remediation from an F. The Layer 0 scoring rule in `agents/assess-layer-scorer.md` distinguishes these cases.
