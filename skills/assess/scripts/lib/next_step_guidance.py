"""Repository-native wording for a maturity model criterion: the change and its "done when".

A team page (``lib/fleet_next_steps.py``) turns each unmet maturity model criterion into
one next step a team can accept or decline on its own. A signal path such as
``agent_environment.check.named_total`` means nothing to a maintainer without
the plugin, so each step leads with:

- ``change``: the one change that moves the criterion;
- ``done_when``: how a maintainer checks it in the repository's own terms (a
  file state, a command, a CI or platform setting), never a signal path.

The signal expression follows on the page as the optional re-run check.

``guidance(signal, op, value)`` builds the wording from the criterion's own
operator and value, so a caller's maturity model that reuses a signal with another
threshold gets that threshold. It returns ``None`` for a signal it has no
wording for, or an operator/value shape the wording does not fit; the page
then states the signal expression alone. ``measure_hint(signal)`` says why a
signal can be unavailable and how to get it measured, so an ``unknown`` level
reads as a measurement gap, never as a failure.

Every signal in the shipped ``levels/default.toml`` has wording here
(``tests/test_fleet_next_steps.py`` checks it).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypedDict

from lib.levels import Scalar


class Guidance(TypedDict):
    change: str
    done_when: str


Operand = Scalar | list[Scalar]
Wording = Callable[[str, Operand], Guidance | None]
PartsWording = Callable[[str, Operand, Sequence[str]], Guidance | None]

# The setting names ghreport reports, as a maintainer reads them.
SETTING_NAMES = {
    "two_factor": "two-factor authentication requirement",
    "push_rules": "push rules",
    "default_branch_protection": "default branch protection",
    "rulesets": "rulesets",
}

_INSTRUCTION_TEXT = ("the always-loaded Claude Code instructions (root `CLAUDE.md`, the files it "
                     "imports, unscoped `.claude/rules/*.md`)")


def _is_true(op: str, value: Operand) -> bool:
    return (op == "eq" and value is True) or (op == "ne" and value is False)


def _is_false(op: str, value: Operand) -> bool:
    return (op == "eq" and value is False) or (op == "ne" and value is True)


def _number(value: Operand) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _percent(value: int | float) -> str:
    return f"{value * 100:g}%"


def _at_least(op: str, value: Operand) -> int | float | None:
    """The lower bound ``gte N`` / ``gt N`` sets, or None for any other shape."""
    number = _number(value)
    return number if number is not None and op in ("gte", "gt") else None


def _share(op: str, bound: int | float) -> str:
    """``gte N`` as "at least N%", strict ``gt N`` as "more than N%"."""
    return f"{'more than' if op == 'gt' else 'at least'} {_percent(bound)}"


def _flag(change: str, done_when: str) -> Wording:
    def wording(op: str, value: Operand) -> Guidance | None:
        return {"change": change, "done_when": done_when} if _is_true(op, value) else None
    return wording


def _flag_count(change: str, done_when: str) -> Wording:
    """Wording for a ``gt 0`` / ``gte 1`` count criterion."""
    def wording(op: str, value: Operand) -> Guidance | None:
        fits = op == "gt" and value == 0 or op == "gte" and value == 1
        return {"change": change, "done_when": done_when} if fits else None
    return wording


def _none_left(change: str, done_when: str) -> Wording:
    """Wording for an ``eq 0`` / ``lt 1`` / ``lte 0`` count criterion."""
    def wording(op: str, value: Operand) -> Guidance | None:
        fits = op == "eq" and value == 0 or op == "lt" and value == 1 or op == "lte" and value == 0
        return {"change": change, "done_when": done_when} if fits else None
    return wording


def _instructions_present(op: str, value: Operand) -> Guidance | None:
    if not (op == "gt" and value == 0 or op == "gte" and value == 1):
        return None
    return {"change": "Add agent instructions in a root `CLAUDE.md` (a one-line `CLAUDE.md` holding "
                      "`@AGENTS.md` counts).",
            "done_when": "A non-empty `CLAUDE.md` is committed at the repository root."}


def _instructions_lines(op: str, value: Operand) -> Guidance | None:
    number = _number(value)
    if number is None or op not in ("lte", "lt"):
        return None
    bound = f"{number:g} lines or fewer" if op == "lte" else f"fewer than {number:g} lines"
    return {"change": f"Cut {_INSTRUCTION_TEXT} to {bound}.",
            "done_when": f"`wc -l` over those files totals {bound}."}


def _check_named(op: str, value: Operand) -> Guidance | None:
    if not (op == "gt" and value == 0 or op == "gte" and value == 1):
        return None
    return {"change": "Name the command an agent runs to check its work (for example `make check` "
                      "or `npm test`) in the agent instruction file.",
            "done_when": "`CLAUDE.md` or `AGENTS.md` names a check command that exists in the "
                         "repository (a Makefile target, a package script or a committed script)."}


def _claims_hold(op: str, value: Operand) -> Guidance | None:
    if not (op == "eq" and value == 0 or op == "lt" and value == 1 or op == "lte" and value == 0):
        return None
    return {"change": "Correct the instruction-file sentences that no longer match the repository: "
                      "a script said to run in CI that CI does not call, a version said to be "
                      "pinned in a file that does not hold it, a file count that is out of date.",
            "done_when": "Each script the instruction files say CI runs appears in the CI "
                         "configuration, and each pin and count they state matches the files."}


_CHECK_NAMED = "check_named"
_SETUP = "setup_or_lockfile"
_LAYER5_PARTS = {
    _CHECK_NAMED: ("name a check command that resolves (a Makefile target, a package script or a "
                   "committed script) in the agent instruction file",
                   "`CLAUDE.md` or `AGENTS.md` names a check command that exists in the repository"),
    _SETUP: ("commit a lockfile for every dependency manifest, or a setup (`.devcontainer/`, a "
             "setup script, `copilot-setup-steps`)",
             "every dependency manifest has its lockfile committed, or a devcontainer or setup "
             "script is committed"),
}


def _layer5_cap(op: str, value: Operand, parts: Sequence[str] = ()) -> Guidance | None:
    """Worded on the cap's ``unmet`` tokens when known; both halves when not."""
    if not _is_false(op, value):
        return None
    known = [p for p in (_CHECK_NAMED, _SETUP) if p in parts]
    chosen = known or [_CHECK_NAMED, _SETUP]
    change = " and ".join(_LAYER5_PARTS[p][0] for p in chosen)
    done = ", and ".join(_LAYER5_PARTS[p][1] for p in chosen)
    return {"change": change[0].upper() + change[1:] + ".", "done_when": done[0].upper() + done[1:] + "."}


_RUNGS = {
    1: ("Emit runtime telemetry (OpenTelemetry, Prometheus, an APM agent or structured logging).",
        "The service's code or configuration initialises a telemetry library or structured logger."),
    2: ("Tell the agent where runtime truth lives in an `OBSERVABILITY.md` or runbook.",
        "An `OBSERVABILITY.md` or runbook is committed and names the dashboards, logs and traces."),
    3: ("Give the agent an invokable path to runtime state: an MCP server or repository skill "
        "over logs and metrics, or a documented, runnable query command.",
        "The repository commits an MCP server entry, a skill or a documented command that "
        "returns live logs or metrics."),
}


def _observability(op: str, value: Operand) -> Guidance | None:
    bound = _at_least(op, value)
    if bound is None:
        return None
    rung = int(bound) + (1 if op == "gt" else 0)
    if rung not in _RUNGS:
        return None
    change, done_when = _RUNGS[rung]
    return {"change": change, "done_when": done_when}


def _reviewed(op: str, value: Operand) -> Guidance | None:
    bound = _at_least(op, value)
    if bound is None:
        return None
    share = "some" if op == "gt" and bound == 0 else f"{_share(op, bound)} of the"
    return {"change": f"Have {share} merged pull requests reviewed by someone other than the "
                      "author before merge.",
            "done_when": "Branch protection or a ruleset on the default branch requires a pull "
                         "request review, and the merged pull requests show a review by a "
                         "second account."}


def _approved(op: str, value: Operand) -> Guidance | None:
    bound = _at_least(op, value)
    if bound is None:
        return None
    return {"change": f"Require an approving review before merge ({_share(op, bound)} of "
                      "merged pull requests approved by someone other than the author).",
            "done_when": "Branch protection or a ruleset on the default branch requires at least "
                         "one approving review."}


def _modularity(op: str, value: Operand) -> Guidance | None:
    bound = _at_least(op, value)
    if bound is None:
        return None
    return {"change": "Group the code into modules that import mostly within themselves, so the "
                      "import graph splits into clear communities.",
            "done_when": f"The import graph's modularity (Newman Q) is {'above' if op == 'gt' else 'at least'} "
                         f"{bound:g} on the next run."}


def _front_doors(op: str, value: Operand) -> Guidance | None:
    bound = _at_least(op, value)
    if bound is None:
        return None
    return {"change": "Import other packages through their public entry point (the package "
                      "itself), not their internal submodules.",
            "done_when": f"{_share(op, bound)[0].upper()}{_share(op, bound)[1:]} of imports that "
                         "cross a package boundary name the package, not one of its submodules."}


def _suppressions_present(op: str, value: Operand) -> Guidance | None:
    if not (op == "gt" and value == 0 or op == "gte" and value == 1):
        return None
    return {"change": "Run a linter over the code in CI and the agent's check command.",
            "done_when": "A linter (ruff, ESLint, golangci-lint or similar) is configured and run "
                         "by CI; its suppressions (`noqa`, `eslint-disable`, `nolint`) appear "
                         "where a finding is accepted."}


def _suppressions_stale(op: str, value: Operand) -> Guidance | None:
    number = _number(value)
    if number is None or op not in ("lte", "lt"):
        return None
    bound = f"{number:g} or fewer" if op == "lte" else f"fewer than {number:g}"
    return {"change": "Fix the findings behind long-lived bare lint suppressions, or give each "
                      "kept one an inline reason.",
            "done_when": f"{bound[0].upper()}{bound[1:]} suppressions without a reason have "
                         "survived repeated edits to their file."}


def _ownership_map(op: str, value: Operand) -> Guidance | None:
    if not (op == "gt" and value == 0 or op == "gte" and value == 1):
        return None
    return {"change": "Declare module ownership and boundaries in a `CODEOWNERS` file or an "
                      "`ARCHITECTURE.md`.",
            "done_when": "A committed `CODEOWNERS` or `ARCHITECTURE.md` names paths that exist in "
                         "the repository."}


def _boundary_map(op: str, value: Operand) -> Guidance | None:
    if not (op == "gt" and value == 0 or op == "gte" and value == 1):
        return None
    return {"change": "Declare module boundaries: a `CODEOWNERS` line per module path, or an "
                      "`ARCHITECTURE.md` that names module paths.",
            "done_when": "A committed `CODEOWNERS` line or `ARCHITECTURE.md` reference names "
                         "a module path that matches a tracked, non-excluded source file (not "
                         "a catch-all such as `*` or `/**`, an extension glob such as `*.py`, "
                         "`.github/`, or a docs-only path)."}


def _file_size(op: str, value: Operand) -> Guidance | None:
    number = _number(value)
    if number is None or op not in ("lte", "lt"):
        return None
    bound = f"{number:g} lines or fewer" if op == "lte" else f"fewer than {number:g} lines"
    return {"change": f"Split large source files and enforce a file-size limit in CI, so 95% of "
                      f"files stay at {bound}.",
            "done_when": f"A CI check fails a file over the limit, and 95% of source files are "
                         f"{bound}."}


def _stale_docs(op: str, value: Operand) -> Guidance | None:
    number = _number(value)
    if number is None or op not in ("lte", "lt"):
        return None
    bound = f"{number:g}% or fewer" if op == "lte" else f"fewer than {number:g}%"
    return {"change": "Update the docs whose code has moved on since they last changed, "
                      "regenerate generated docs whose source has moved on, and keep docs "
                      "current in the same pull request as the code they describe.",
            "done_when": f"{bound[0].upper()}{bound[1:]} of the docs that describe a part of "
                         "the code have had more than two changes to their code since the doc "
                         "last changed (the `/assess` staleness ratio above 2), or whose "
                         "declared source changed after the doc was generated."}


def _agents_agree(op: str, value: Operand) -> Guidance | None:
    if not (op == "eq" and value == 0):
        return None
    return {"change": "Make every agent system's instruction file name the same test and lint "
                      "commands and the same coverage, complexity and line-length numbers, "
                      "ideally by keeping them in `AGENTS.md` only.",
            "done_when": "No check command or threshold is named in one tool's instruction file "
                         "and missing from, or different in, another's."}


def _agents_linked(op: str, value: Operand) -> Guidance | None:
    if not (op == "eq" and value == 0):
        return None
    return {"change": "Keep `AGENTS.md` as the one canonical source and replace each other "
                      "tool's file with the pointer line `/assess` gives for it (`@AGENTS.md` "
                      "for Claude Code and Gemini CLI, `#[[file:AGENTS.md]]` for Kiro, `read: "
                      "AGENTS.md` in `.aider.conf.yml`), or make it a symlink to `AGENTS.md`.",
            "done_when": "Every agent system's root instruction file is `AGENTS.md`, a symlink to "
                         "it, an `@AGENTS.md` import or a line pointing to it."}


def _setting(name: str) -> Wording:
    label = SETTING_NAMES.get(name, f"`{name}` setting")

    def wording(op: str, value: Operand) -> Guidance | None:
        if op == "eq" and isinstance(value, str):
            return {"change": f"Set the group's {label} to `{value}`.",
                    "done_when": f"The group settings page shows the {label} as `{value}`."}
        if op == "in" and isinstance(value, list):
            options = ", ".join(f"`{v}`" for v in value)
            return {"change": f"Set the group's {label} to one of {options}.",
                    "done_when": f"The group settings page shows the {label} as one of {options}."}
        if op == "ne" and isinstance(value, str):
            return {"change": f"Change the group's {label} from `{value}`.",
                    "done_when": f"The group settings page no longer shows the {label} as `{value}`."}
        return None
    return wording


_WORDING: dict[str, Wording] = {
    "nested_instructions.budget.claude_code.bytes": _instructions_present,
    "layer_grades.instruction_file_present": _flag(
        "Commit an agent instruction file (`CLAUDE.md` or `AGENTS.md` at the repository root).",
        "`git ls-files` lists a `CLAUDE.md`, `AGENTS.md` or `.github/copilot-instructions.md`."),
    "nested_instructions.budget.claude_code.lines": _instructions_lines,
    "agent_environment.check.named_total": _check_named,
    "agent_environment.pinning.unlocked_total": _none_left(
        "Commit a lockfile beside every dependency manifest (`uv.lock`, `package-lock.json`, "
        "`go.sum`, `Cargo.lock` or similar), or pin every requirement with `==`.",
        "Every dependency manifest has its lockfile committed in its directory or a parent."),
    "ci_checks.test_or_lint_runs": _flag(
        "Run the test suite or linter in CI: a GitHub Actions workflow or `.gitlab-ci.yml` job "
        "whose script runs it (`make test`, `pytest`, `npm test`, `uv run ruff check`).",
        "A workflow in `.github/workflows/` or `.gitlab-ci.yml` has a step that runs a test or "
        "lint command."),
    "ci_checks.enforced_check_runs": _flag(
        "Run the test suite or linter in CI so a failure fails the job (`make test`, `pytest`, "
        "`npm test`, `uv run ruff check`) on pull requests or pushes. Remove `|| true` / "
        "`continue-on-error` / `allow_failure` from the step that runs the tests, or re-raise "
        "the status after `set +e`.",
        "A workflow in `.github/workflows/` or `.gitlab-ci.yml` has a step that runs a test or "
        "lint command, is not masked and runs on ordinary changes "
        "(`ci_checks.masked_check_commands` and `off_trigger_check_commands` count the others)."),
    "instruction_claims.failed": _claims_hold,
    "observability.rung": _observability,
    "structure.modularity_q": _modularity,
    "structure.front_door_ratio": _front_doors,
    "structure.cross_package_edges": _flag_count(
        "Split the code into more than one top-level package, with imports between them.",
        "At least one module imports from a different top-level package of the repository."),
    "promissory_markers.families.suppression.total": _suppressions_present,
    "promissory_markers.families.suppression.stale": _suppressions_stale,
    "structure_drift.tier_0.boundary_patterns": _boundary_map,
    "structure_drift.tier_0.matched_patterns": _ownership_map,
    "promissory_markers.aging_reliable": _flag(
        "Assess a full-history clone, not a shallow or squashed one, so lint suppressions can "
        "be aged.",
        "`git rev-parse --is-shallow-repository` prints `false` and most changed files have "
        "more than one commit in the history."),
    "stats_summary.loc.p95": _file_size,
    "doc_staleness.summary.stale_pct": _stale_docs,
    "agent_consistency.conflicts": _agents_agree,
    "agent_consistency.unlinked": _agents_linked,
    "coverage_gate.enforced": _flag(
        "Make the test run fail when line coverage drops below a floor.",
        "The test command CI runs enforces a coverage threshold (`fail_under` in `pyproject.toml`, "
        "`--cov-fail-under`, Jest `coverageThreshold` or similar), and a run below it fails."),
    "coverage_gate.threshold_meets_minimum": _flag(
        "Make the test run fail when line coverage drops below a threshold of at least 50 "
        "percent (a JaCoCo ratio of at least `0.50`).",
        "A committed coverage threshold of at least 50 percent (`fail_under = 50` in "
        "`pyproject.toml`, `--cov-fail-under=50`, Jest `coverageThreshold`, JaCoCo `<minimum>0.50"
        "</minimum>`, go-test-coverage `threshold: total: 50`, `cargo tarpaulin --fail-under 50`, "
        "or similar) is in the repository as a number, not a variable. A threshold "
        "below 50 does not count, even when measured coverage is higher."),
    "test_pressure.mutation_run": _flag(
        "Run mutation testing against the test suite.",
        "A mutation tool (mutmut, Stryker, PIT or similar) is configured in the repository and a "
        "run of it completes."),
    "review_evidence.present": _flag(
        "Commit an AI review step to CI (a workflow step running `anthropics/claude-code-action`, "
        "a GitLab AI review component, or an AI review app's config file such as "
        "`.coderabbit.yaml`), or have merged changes reviewed by someone other than the author.",
        "A tracked CI file or review config runs an AI review on each change, or the merged "
        "changes show a review by a second account."),
    "review_reality.reviewed_share": _reviewed,
    "review_reality.approved_share": _approved,
    "agent_ops.summary.permissions_encoded": _flag(
        "Commit the agent's permissions: an `allow`, `deny` or `ask` list in `.claude/settings.json`.",
        "`.claude/settings.json` is tracked in git and its `permissions` lists at least one entry."),
    "agent_ops.summary.hooks_present": _flag(
        "Commit an agent hook: a `hooks` entry in `.claude/settings.json` or a script in "
        "`.claude/hooks/`.",
        "`git ls-files .claude` lists a settings file with a `hooks` entry or a script under "
        "`.claude/hooks/`."),
    "agent_ops.summary.routines_present": _flag(
        "Commit an agent routine definition under `.claude/workflows/` or `.claude/routines/`.",
        "`git ls-files .claude/workflows .claude/routines` lists at least one file."),
}
_WORDING.update({f"{name}.value": _setting(name) for name in SETTING_NAMES})

_NEEDS = "A headless scan reports the `{}` block unavailable when {}; install it and rerun."

# Why a signal can be unavailable, by its first path segment (the block name).
_MEASURE_HINTS = {
    "agent_consistency": "Not measured when fewer than two agent systems have a root "
                         "instruction file: one system has nothing to agree with, so "
                         "`conflicts` and `unlinked` are null (`applicable: false`).",
    "structure": _NEEDS.format("structure", "grimp or networkx is missing (`no_grimp`, "
                               "`no_networkx`)") + " With no import graph the reason names "
                 "the dominant source language: `no_import_graph_<language>` when no "
                 "builder graphs that language, `no_python_packages` for a Python "
                 "repository with no importable package, `no_source_files` with no "
                 "source at all. An import graph with no edges leaves "
                 "`modularity_q` null (`modularity_reason: no_import_edges`): modules "
                 "that never import each other have no community structure to measure.",
    "stats_summary": _NEEDS.format("stats_summary", "lizard is missing (`no_lizard`)"),
    "promissory_markers": _NEEDS.format("promissory_markers", "`rg` is not on `PATH` "
                                        "(`no_rg`)") + " A repository with no lint "
                          "suppressions has no `suppression` family, so it reads unknown there too.",
    "structure_drift": "Unavailable (`no_ownership_map`) when the repository commits no "
                       "supported ownership map (`CODEOWNERS`, `ARCHITECTURE.md`, `DESIGN.md`, "
                       "or a `README.md` that declares module boundaries).",
    "observability": "Read from committed files by a full `/assess` run and a headless scan "
                     "alike; unavailable when the scan raised (`scan_failed`) or the scan came "
                     "from an older toolkit, which measured it in a full run only.",
    "doc_staleness": "Measured from git history: unavailable (`not_git_root`) outside a git work "
                     "tree, and the stale share is null on a shallow clone or a history where "
                     "every file has about one commit (rescan a full-history clone), or when no "
                     "doc describes a part of the code of its own.",
    "review_reality": "Needs GitHub or GitLab API access; a headless fleet scan makes no network "
                      "calls, so this is not measured there.",
    "review_evidence": "Unknown when no AI review step is committed to CI and review on merged "
                       "changes is not read: a headless fleet scan reads it with `--platform`.",
    "ci_checks": "Unknown when no GitHub Actions or GitLab CI line runs a test or lint command "
                 "that can fail its job but a CI file pulls jobs from a file the run could not read (`basis: "
                 "included_jobs`: a GitLab `include:` whose template is not cloned in the "
                 "fleet root, or a `template:` or `remote:` include, a reusable workflow from "
                 "another repository): the check may live there. Cloning the template into "
                 "the same ghsync root lets the fleet read it. `enforced_check_runs` is also "
                 "unknown when the check's workflow or job has triggers the scan cannot settle "
                 "(`enforced_basis: unknown_trigger`: no `on:` key, a `workflow_call` nobody "
                 "calls, a GitLab `rules: if:` on a custom variable): say when the workflow "
                 "runs, by adding `push` or `pull_request` triggers (or a merge request rule). "
                 "A scan stored before `enforced_check_runs` existed has no such key: re-run "
                 "the scan to measure it.",
    "coverage_gate": "The `coverage_gate` scan reads the threshold unknown when none of at "
                     "least 50 percent is committed but a gate's threshold is not a readable "
                     "percentage: a variable (`--cov-fail-under=${{ env.COV_MIN }}`), an nyc/c8 "
                     "default, or a Jest uncovered-line count. Write the threshold as a number "
                     "to measure it. A Go or Rust repository with no gate found also reads "
                     "unknown (`unmeasured_reason` `go_rust_gate_form_not_read`): a script "
                     "comparing `go tool cover` to a number is not read, while go-test-coverage's "
                     "`.testcoverage.yml`, `cargo tarpaulin --fail-under` and `cargo llvm-cov "
                     "--fail-under-lines` are. The block is unavailable when the scan did not run: check "
                     "the fleet run's `--scans` and its `logs/` directory.",
    "test_pressure": "Only a full `/assess` run with its opt-in mutation pass measures this; a "
                     "headless fleet scan does not.",
}
_SETTING_HINT = ("ghreport could not read this setting (token scope or provider tier); rerun "
                 "ghreport with access to group settings and pass them to the fleet run with "
                 "`--settings`.")


# Signals whose wording depends on which part of a compound check is unmet,
# read from the repository's own block by ``parts``.
_PARTS_WORDING: dict[str, PartsWording] = {"agent_environment.layer5_cap.applies": _layer5_cap}
_PARTS_PATH = {"agent_environment.layer5_cap.applies": ("agent_environment", "layer5_cap", "unmet")}


def parts(signal: str, scans: Mapping[str, Any]) -> list[str]:
    """The unmet-part tokens a repository's block reports for ``signal`` (empty when none)."""
    node: Any = scans
    for key in _PARTS_PATH.get(signal, ()):
        node = node.get(key) if isinstance(node, dict) else None
    if isinstance(node, str):
        return [node]
    return sorted(str(p) for p in node) if isinstance(node, list) and signal in _PARTS_PATH else []


def guidance(signal: str, op: str, value: Operand, unmet_parts: Sequence[str] = ()) -> Guidance | None:
    """The repository-native change and done-when for one criterion, or None.

    ``unmet_parts`` (from ``parts``, across the repositories the step lists)
    narrows the wording of a compound check to the parts actually unmet."""
    if signal in _PARTS_WORDING:
        return _PARTS_WORDING[signal](op, value, unmet_parts)
    wording = _WORDING.get(signal)
    return wording(op, value) if wording else None


def measure_hint(signal: str) -> str:
    """Why ``signal`` can be unavailable and how to get it measured."""
    block = signal.split(".", 1)[0]
    if block in SETTING_NAMES or block in ("scope", "provider"):
        return _SETTING_HINT
    return _MEASURE_HINTS.get(
        block,
        f"The `{block}` scan reported it unavailable or did not run: check the fleet run's "
        "`--scans` and its `logs/` directory.")
