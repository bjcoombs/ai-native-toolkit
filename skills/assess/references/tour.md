# The guided first run (`/assess --tour`, issue #1102)

Loaded by `/assess` when its arguments contain `--tour`. The tour never assesses the user's repository and writes nothing into it. It builds a small sample repository in a new temporary directory, assesses it, walks the newcomer through the report one section at a time, has them make one fix, and re-runs so they see the Step move.

The sample is `tally`, a tiny Python expense ledger with tests, a CI workflow and an `AGENTS.md`. It has one deliberate gap: its CI runs `pytest || true`, so a failing test never fails the build. It lands on **Step 2** (an agent opens pull requests and a person reviews each one), and the one gate between it and **Step 3** is `ci_runs_tests`. Its files, history and fix live in `tests/fixtures/tour_sample/`; the script is `scripts/assess_tour.py`. The same build gives the same report every time.

<!-- chat-skip:start -->
## 1. Build and assess the sample

```bash
SAMPLE="$(mktemp -d)/tally"
uv run "${CLAUDE_SKILL_DIR}/scripts/assess_tour.py" build "$SAMPLE"
uv run "${CLAUDE_SKILL_DIR}/scripts/assess_tour.py" run "$SAMPLE"
```

`run` prints one JSON line: `step` (2), `next_gates` (`["ci_runs_tests"]`) and `report`, the path of the report (`$SAMPLE/.assess/deterministic-report.md`). The Step ladder and the evidence waffle are beside it, in `$SAMPLE/.assess/`. If `build` says the sample is not installed, the tour needs the full plugin rather than the standalone skill: say so and stop.

Tell the newcomer in two sentences what is about to happen: a sample repository has been assessed, and you will go through its report a section at a time before they fix one thing in it.

## 2. Walk the report, one section per message

Read the report and show one section per message, quoting its text, then wait for the newcomer to reply before the next. Keep your own words to two or three plain sentences per section. Use the report's words, never a condition id or a layer number.

1. **The summary**: the three lines at the top and the **Start here** line. Say what Step 2 means for them: an agent can open pull requests, and a person has to review each one.
2. **The Step ladder**: the "Where you are on the Steps" section. Give the path of `step-ladder.svg` so they can open it. Point at the "You are here" rung and the highlighted gate above it.
3. **The next gate**: the Next line, then the sample's `.github/workflows/ci.yml`. Show the line `pytest || true` and say why it holds the sample back: CI reports success even when a test fails, so nobody can leave an agent's pull request unwatched.
4. **The evidence**: the "Evidence behind the Step" section and the path of `evidence-waffle.svg`. Say that each square is one practice at one level, that the outlined squares are the gates the Steps read, and that a hatched square was not measured and is never read as a zero.

## 3. Make the fix

Show the fix: delete `|| true` from the last line of `$SAMPLE/.github/workflows/ci.yml` (the comment above it can go too). The same change is in `tests/fixtures/tour_sample/fix.patch`. Offer two ways to make it: edit the file themselves, which is the point of the tour, or let you apply the patch. Either way, then run:

```bash
uv run "${CLAUDE_SKILL_DIR}/scripts/assess_tour.py" fix "$SAMPLE"
```

It commits the edit if they made one (`"fix": "edited"`) and applies and commits the patch if not (`"applied"`). `"already_fixed"` means the commit is already there. An error means their edit and the patch disagree: show `git -C "$SAMPLE" diff` and help them finish the edit.

## 4. Re-run and see the Step move

```bash
uv run "${CLAUDE_SKILL_DIR}/scripts/assess_tour.py" run "$SAMPLE"
```

`step` is now 3 and `next_gates` is `["coverage_gate"]`. Show the new summary lines and the "Since Last Run" section, which lists the gate gained. Say what Step 3 means: an agent's pull request can go through without a person watching, because CI now fails when a test fails.

## 5. Close

In two sentences, tell them the next gate on the sample is a coverage gate, and that `/assess` with no arguments runs the same assessment on their own repository. Tell them the sample is in `$SAMPLE` and can be deleted.

A headless run has nobody to walk through the report: run steps 1, 3 (with the patch) and 4 and print both JSON lines.
<!-- chat-skip:end -->

## The sample report in CI

One command builds the sample in a temporary directory and writes its report to a known path; `--fixed` gives the report after the fix:

```bash
cd skills/assess && uv run --locked python scripts/assess_tour.py report --out /tmp/tour-sample-report.md
```

Tests: `tests/test_assess_tour.py` pins the Step, the next gate, the move by exactly one Step and byte-identical reports from two builds.
