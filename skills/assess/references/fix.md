# Writing the basics for the next gate (`--fix` and `--starter`, issue #1154)

Loaded by `/assess` when the arguments contain `--fix` or `--starter`. Only these two arguments let `/assess` write starter files into the target; every other run writes nothing outside `.assess/`, and `/fleet` has no fix path.

- **`--fix`:** run the assessment as usual, then run the fix script on `$REPO_ROOT`. It writes a file for each gate blocking the next Step.
- **`--starter`:** for an empty or new repository, such as one a scaffolding flow just created. Skip the assessment steps and run the script with `--starter`; it writes a file for every gate up to the top Step. Add `--language <name>` and `--check-command "<command>"` when the repository has no source or build file yet, and `--ci github|gitlab` when it has no remote.

<!-- chat-skip:start -->
```bash
uv run "${CLAUDE_SKILL_DIR}/scripts/assess_fix.py" "$REPO_ROOT" --fix
uv run "${CLAUDE_SKILL_DIR}/scripts/assess_fix.py" "$REPO_ROOT" --starter --language python --check-command "pytest"
```
<!-- chat-skip:end -->

The script never pushes on its own: it commits to a local branch and prints the exact command that pushes it and opens the pull request. `--pr` (opt-in) also pushes and opens the pull (merge) request. In an interactive run, offer to open one after the files are written and rerun with `--pr` only after the user says yes; a non-interactive run adds `--pr` only when its caller asked for a pull request. `--json` gives a machine-readable report.

## What it writes

Each file is built from what the repository already has, read by the existing detectors: the dominant language, the check command its build exposes (a test target first, then a check target, then the build's own test command), and its CI system (a tracked GitHub Actions or GitLab CI file, else the `origin` remote's host, else GitHub).

| Gate | File |
|------|------|
| `instructions` | `CLAUDE.md` naming the check command |
| `ci_runs_tests` | `.github/workflows/tests.yml`, or a `tests` job in `.gitlab-ci.yml` |
| `coverage_gate` | `.github/workflows/coverage.yml`, or a `coverage` job, at 50 percent (pytest-cov, cargo-llvm-cov, c8) |
| guardrail | `.claude/settings.json` with the deny rule, when no permission rule is committed |

It never overwrites a file. An existing one is reported as a conflict, with the text to add by hand. An existing `.gitlab-ci.yml` is left as it is: the job goes in `.gitlab/ci/<name>.yml` and the manual step is the `include:` line.

## Proof and delivery

The script stages the files, reads the Step again and reports, per gate, whether it now holds or the one manual step left: an `include:` line, a check command to name, or a coverage threshold for a language with no coverage form it knows. Exit 0 means every targeted gate holds; exit 1 means a manual step is left.

When the tree was clean before the run (the assessment's own `.assess/` output aside; it is never committed), it commits the files to a new local `assess/fix-<gate>` branch, leaves the checkout on that branch, and ends its report with the command to push it and open the pull request. On a dirty tree it commits nothing and the files stay staged. With `--pr` it also pushes and opens the pull (merge) request, but only when `gh` or `glab` is authenticated for `origin` and the run started on the default branch's tip. Off that tip, with or without `--pr`, the report says "run from <default> to open a fix PR" and prints no push command, so no other branch's commits reach a fix PR. Relay the report to the user as written.
