"""Repository-native wording for a maturity model criterion: the change and its "done when".

A team page (``lib/fleet/fleet_next_steps.py``) turns each unmet maturity model criterion into
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
reads as a measurement gap, not a setback.

The wording is keyed by signal alone. Why a step matters is keyed by the criterion's row
and level in the check manifest, so the page reads it from ``lib.fleet.fleet_why.criterion_why``
(#1128), not from here.

Every signal in the shipped ``levels/default.toml`` has wording here
(``tests/test_fleet_next_steps.py`` checks it).

Repository-native wording for a maturity model criterion. `guidance(signal, op, value)` gives
the one change and a "done when" a maintainer checks without the plugin (a file
state, a command, a platform setting), templated on the criterion's own operator and
value. A compound check (`agent_environment.layer5_cap.applies`, no longer read by the
default model since #786 but kept for models that still use it) is worded on the
parts actually unmet: `parts` reads a repository's `layer5_cap.unmet` tokens, the page
passes their union across the repositories a step lists and annotates each repository
with its own. `guidance` returns `None` for a signal or shape it has no wording for, and the page
then states the signal expression alone. `measure_hint(signal)` says why a signal can
be unavailable (a full `/assess` run only, a network read, an opt-in pass, a ghreport
permission; for the `structure`, `stats_summary` and `promissory_markers` blocks a missing
grimp, networkx, lizard or `rg`, and for `structure_drift` no ownership map). Every signal of
`levels/default.toml` has wording (tested).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypedDict

from lib.scoring.levels import Scalar
from lib.scoring.next_step_guidance_backlog import BACKLOG_FLAGS, BACKLOG_HINT
from lib.scoring.next_step_guidance_contracts import API_COMPAT_HINT, CONTRACT_FLAGS
from lib.scoring.next_step_guidance_guards import GUARD_FLAGS, GUARD_HINT

# The guardrail deny rule the permissions wording names (#1153); ``/assess --fix`` writes it,
# with the repository's default branch in place of ``main`` (``lib/starter_templates.py``, #1154).
DENY_RULE_EXAMPLE = "Bash(git push origin main)"


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
                      "`ARCHITECTURE.md` that names two or more module paths.",
            "done_when": "A committed `CODEOWNERS` line, or two or more `ARCHITECTURE.md` "
                         "references, name a module path that matches a tracked, non-excluded "
                         "source file (not "
                         "a catch-all such as `*` or `/**`, an extension glob such as `*.py`, "
                         "`.github/`, or a docs-only path)."}


def _file_size(op: str, value: Operand) -> Guidance | None:
    number = _number(value)
    if number is None or op not in ("lte", "lt"):
        return None
    bound = f"{number:g} lines or fewer" if op == "lte" else f"fewer than {number:g} lines"
    return {"change": f"Split large source files and enforce a file-size limit in CI, so 95% of "
                      f"production source files stay at {bound}.",
            "done_when": f"A CI check turns the job red for a file over the limit, and 95% of source files are "
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


def _guardrails_stated(op: str, value: Operand) -> Guidance | None:
    number = _at_least(op, value)
    if number is None:
        return None
    share = "every gate" if number >= 100 else f"at least {number:g}% of the gates"
    return {"change": "Add the one-line snippet `/assess` gives for each configured gate an "
                      "instruction file does not state yet (complexity limit, coverage floor, "
                      "pre-commit hooks, mypy strict) to `CLAUDE.md` or `AGENTS.md`, so the next "
                      "session knows the rule before it runs into it.",
            "done_when": f"A graded instruction file states {share} the repository configures: "
                         "the rule and its threshold on one line (for example \"Functions stay "
                         "under complexity 15 (ruff C901): run `ruff check .`\")."}


def _agents_agree(op: str, value: Operand) -> Guidance | None:
    if not (op == "eq" and value == 0):
        return None
    return {"change": "Make every agent system's instruction file name the same test and lint "
                      "commands and the same coverage, complexity and line-length numbers, "
                      "ideally by keeping them in `AGENTS.md` only.",
            "done_when": "No check command or threshold is named in one tool's instruction file "
                         "and absent from, or different in, another's."}


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
        "Commit an agent instruction file (`CLAUDE.md` or `AGENTS.md` at the repository root) "
        "that says how to build, test and find the code; if one holds only headings, fill it in.",
        "`git ls-files` lists a `CLAUDE.md`, `AGENTS.md` or `.github/copilot-instructions.md` "
        "with more than headings in it."),
    "nested_instructions.budget.claude_code.lines": _instructions_lines,
    "agent_environment.check.named_total": _check_named,
    "agent_environment.pinning.unlocked_total": _none_left(
        "Commit a lockfile beside every dependency manifest (`uv.lock`, `package-lock.json`, "
        "`go.sum`, `Cargo.lock` or similar), or pin every requirement with `==`.",
        "Every dependency manifest has its lockfile committed in its directory or a parent."),
    "ci_checks.test_or_lint_configured": _flag(
        "Run the test suite or linter in CI: a GitHub Actions workflow or `.gitlab-ci.yml` job "
        "whose script runs it (`make test`, `pytest`, `npm test`, `uv run ruff check`).",
        "A workflow in `.github/workflows/` or `.gitlab-ci.yml` has a step that runs a test or "
        "lint command."),
    "ci_checks.enforced_check_runs": _flag(
        "Run the test suite or linter in CI so a test or lint error stops the job (`make test`, `pytest`, "
        "`npm test`, `uv run ruff check`) on pull requests or pushes. Remove `|| true` / "
        "`continue-on-error` / `allow_failure` from the step that runs the tests, or re-raise "
        "the status after `set +e`.",
        "A workflow in `.github/workflows/` or `.gitlab-ci.yml` has a step that runs a test or "
        "lint command, is not masked and runs on ordinary changes "
        "(`ci_checks.masked_check_commands` and `off_trigger_check_commands` count the others)."),
    "ci_checks.lint_runs": _flag(
        "Run a linter or type checker in CI so a finding stops the job: a GitHub Actions or "
        "`.gitlab-ci.yml` step that calls it (`ruff check .`, `npx eslint .`, `mypy src`, "
        "`golangci-lint run`, `make lint`) on pull requests or pushes, without `|| true`, "
        "`continue-on-error` or `allow_failure`.",
        "A workflow in `.github/workflows/` or `.gitlab-ci.yml` has a step that runs a linter "
        "command (`ruff check .`, `eslint`, `mypy`, `make lint`), not only `--version`."),
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
    "stats_summary.loc_by_class.source.p95": _file_size,
    "doc_staleness.summary.stale_pct": _stale_docs,
    "guardrails.stated_pct": _guardrails_stated,
    "agent_consistency.conflicts": _agents_agree,
    "agent_consistency.unlinked": _agents_linked,
    "contributor_files.guide.present": _flag(
        "Commit a `CONTRIBUTING.md` that says how to build, test and shape a change, at the root, "
        "in `.github/` or in `docs/`.",
        "`git ls-files` lists a `CONTRIBUTING` file at the root, in `.github/` or in `docs/`."),
    "skill_library.present": _flag(
        "Commit a `skills/<name>/SKILL.md` (or `.claude/skills/<name>/SKILL.md`) for a procedure the "
        "team repeats, with frontmatter giving a `name` and a `description` of when to use it.",
        "`git ls-tree -r HEAD --name-only` lists a committed `SKILL.md` with a `name` and a `description` under `skills/`, a harness "
        "folder's `skills/` or `plugins/<name>/skills/`, outside `node_modules`, vendored and fixture folders."),
    "contributor_files.policy.present": _flag(
        "Commit a `SECURITY.md` that says how to report a vulnerability privately (or link GitHub "
        "private vulnerability reporting), at the root, in `.github/` or in `docs/`.",
        "`git ls-files` lists a `SECURITY` file at the root, in `.github/` or in `docs/`, or the "
        "owner's public `.github` repository holds one."),
    "ci_model_jobs.ungated": _none_left(
        "Gate each CI job the report names that calls a model: run it only when prompts, skills or "
        "instruction files change (a `paths:` filter), or nightly, or on a label. Cancel superseded "
        "runs, sample the eval cases or cache results by content hash, use the cheapest model that "
        "still tells a good result from a bad one, and move rule-checkable assertions into ordinary tests.",
        "No CI job that calls a model API runs on every push to the default branch or every pull "
        "request update without a `paths:` filter, a schedule or manual trigger, a label, or GitLab "
        "`rules:changes:`."),
    "ci_model_jobs.agent_evals.present": _flag(
        "Keep the agent's eval cases in an `evals/` folder, commit a baseline beside them (a "
        "snapshot, recorded model responses or a tool-budget file), and run the folder in a CI job.",
        "A CI command names an eval folder that calls a model, and that folder holds a committed "
        "baseline the run compares against."),
    "agent_ops.summary.agent_jobs_least_privilege": _flag(
        "Give each workflow job that runs an agent `permissions:` with no write scope "
        "(`contents: read`, or `read-all`), and move writes into a separate job that `needs:` the "
        "agent job, or into GitHub Agentic Workflows `safe-outputs:`.",
        "Every job that runs an agent has its own or its workflow's `permissions:` with no `write` "
        "value (`id-token: write` aside) and no job-level override back to `write`."),
    # A hazard (polarity hazard): its criterion reads the harm, so the wording says how to clear it.
    "agent_ops.summary.agent_job_write_access": _flag_count(
        "Drop the write scopes from each agent job's `permissions:` and do the writes in a job "
        "that `needs:` it, or in GitHub Agentic Workflows `safe-outputs:`. A `max-turns` cap "
        "limits cost, not what the token can do.",
        "No job that runs an agent has a `write` scope unless another job that `needs:` it, or "
        "`safe-outputs:`, does the writing."),
    "agent_ops.summary.agent_instruction_rewrite": _flag_count(
        "Add a CODEOWNERS line with an owner for `AGENTS.md`, `CLAUDE.md`, the other instruction "
        "files, each agent workflow, its gh-aw `.lock.yml`, the local workflows and actions it "
        "runs, and the CODEOWNERS file itself. Also give an owner to each committed file a gh-aw "
        "source's frontmatter `imports:` names, and to each file those import. "
        "Require code-owner review on the default branch. Make each gh-aw file in "
        "`agent_instruction_rewrite_unread_files` readable: a closing `---`, `imports:` a YAML list.",
        "No agent job that can write files runs while an instruction file, its own workflow, its "
        "`.lock.yml`, a file it imports or the CODEOWNERS file has no owner or an unreadable import list."),
    "ci_hazards.skip_labels": _flag_count(
        "Drop the label condition from the jobs' `if:` lines, or keep it only on jobs that check "
        "nothing; scope a skip with a `paths` filter so the checks still run on code changes.",
        "No pull request label skips every check job of the workflows that run checks on pull requests."),
    "ci_hazards.test_failures_ignored": _flag_count(
        "Remove `-Dmaven.test.failure.ignore=true` and `testFailureIgnore` from the CI command, the "
        "Jenkinsfile and the poms; quarantine a flaky test by name instead.",
        "No CI file, Jenkinsfile, `.mvn/maven.config`, reactor pom or followed CI step sets "
        "`maven.test.failure.ignore` or `testFailureIgnore` to true."),
    "ci_hazards.instructions_unchecked": _flag_count(
        "Remove the instruction files from `paths-ignore`, or add them to the `paths` filter, in at "
        "least one workflow that runs checks on pull requests.",
        "Every instruction file is on the paths of a workflow that runs checks on pull requests."),
    "ai_governance.enforced": _flag(
        "Add a workflow on `pull_request` that reads the pull request body or the commit trailers "
        "for the AI disclosure your policy asks for, and turns the check red or adds a label on it.",
        "A workflow on pull requests reads the pull request body or the commit trailers for an AI "
        "disclosure or attribution marker and turns the check red or labels on it."),
    "ai_governance.policy_declared": _flag(
        "Commit an `AI_POLICY.md` at the root, in `.github/` or in `docs/`, or add a pull request "
        "template checkbox that asks the author to disclose AI assistance.",
        "An `AI_POLICY.md` is committed at a fixed path, or a pull request template checkbox asks for AI disclosure."),
    "secrets_guard.configured": _flag(
        "Add a secrets hook to `.pre-commit-config.yaml` (gitleaks or detect-secrets), or a secrets "
        "scan step to CI.",
        "`.pre-commit-config.yaml` names a secrets hook, or a CI file runs a secrets scan."),
    "secrets_guard.enforcement.present": _flag(
        "Add a secrets scan step to CI that runs on each push or pull request (`gitleaks/gitleaks-action`, "
        "`trufflesecurity/trufflehog`, or GitLab's `Secret-Detection` template); a pre-commit hook "
        "alone can be skipped with `--no-verify`.",
        "A workflow in `.github/workflows/` that runs on a push or pull request, or a plain job in "
        "`.gitlab-ci.yml`, runs a secrets scan that can stop the run when it finds a secret."),
    "skill_personal_data.files_with_personal_data": _none_left(
        "In each skill or agent file the report names, remove the kind of data it lists (an email, "
        "phone number, home path, IP address, SSH login or key), or replace it with a placeholder "
        "such as `user@example.com` or `/home/user/`; rotate any key that was committed.",
        "No `SKILL.md`, agent definition or skill `references/` file holds an email, phone number, "
        "personal home path, IP address, SSH login or key (personal names are not checked; "
        "credential-shaped assignments are counted for information only)."),
    "skill_script_tests.skills_untested": _none_left(
        "Add tests for each named skill's scripts in that skill's own folder (`tests/` or "
        "`test_*.py` beside them). Where the report finds them elsewhere, move or copy them in: "
        "tests outside the folder do not ship with the skill. This checks that tests exist, not "
        "how much they cover.",
        "Every skill folder that holds a script also holds a test file (`tests/`, `test_*.py`, "
        "`*_test.py`, `*.test.ts` or similar)."),
    "skill_disclosure.files_breaking": _none_left(
        "In each `SKILL.md` or agent definition the report names, fix the rule it breaks: give "
        "each a `name` and a `description` of at most 1024 characters, move detail only some "
        "tasks need into `references/` files linked from `SKILL.md`, and link each reference "
        "from `SKILL.md` to a file that exists.",
        "Every `SKILL.md` and agent definition has a body of 500 lines or fewer, about 5000 tokens "
        "(20,000 characters) or fewer, a `name` and a `description` of 1024 characters or fewer, "
        "and every `references/` link resolves straight from it."),
    "coverage_gate.configured": _flag(
        "Make the test run exit non-zero when line coverage drops below a floor.",
        "The test command CI runs enforces a coverage threshold (`fail_under` in `pyproject.toml`, "
        "`--cov-fail-under`, Jest `coverageThreshold` or similar), and a run below it exits non-zero."),
    "coverage_gate.threshold_meets_minimum": _flag(
        "Make the test run exit non-zero when line coverage drops below a threshold of at least 50 "
        "percent (a JaCoCo ratio of at least `0.50`).",
        "A committed coverage threshold of at least 50 percent (`fail_under = 50` in "
        "`pyproject.toml`, `--cov-fail-under=50`, Jest `coverageThreshold`, JaCoCo `<minimum>0.50"
        "</minimum>`, go-test-coverage `threshold: total: 50`, `cargo tarpaulin --fail-under 50`, "
        "or similar) is in the repository as a number, not a variable. A threshold "
        "below 50 does not count, even when measured coverage is higher."),
    "coverage_gate.enforced_meets_minimum": _flag(
        "Run the coverage check in CI on pull requests or pushes, with a threshold of at least 50 "
        "percent, so a drop below it stops the job (`pytest --cov --cov-fail-under=80`, or `pytest "
        "--cov` with `fail_under = 80` in `pyproject.toml`).",
        "A committed coverage threshold of at least 50 percent, and a CI step that runs the tool "
        "that reads it (`pytest --cov`, `coverage report`, `jest --coverage`, `mvn verify`) or "
        "passes the threshold itself, is not masked and runs on ordinary changes. A threshold no "
        "CI step runs does not count (`coverage_gate.enforced_basis` names the reason)."),
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
        "Commit a guardrail as a deny rule, such as `\"permissions\": {\"deny\": "
        f"[\"{DENY_RULE_EXAMPLE}\"]}}` in `.claude/settings.json`. Deny rules hold in every "
        "permission mode, auto mode included. If your organisation sets this centrally, run with "
        "`--managed-settings`.",
        "`.claude/settings.json` is tracked in git and its `permissions` lists at least one entry, "
        "or, with `--managed-settings`, a managed settings source on the machine running the "
        "check does."),
    "agent_ops.summary.hooks_present": _flag(
        "Commit an agent hook: a `hooks` entry in `.claude/settings.json` or a script in "
        "`.claude/hooks/`.",
        "`git ls-files .claude` lists a settings file with a `hooks` entry or a script under "
        "`.claude/hooks/`."),
    "agent_ops.summary.agent_work_present": _flag(
        "Commit the agent work the repository runs on a schedule or trigger: a workflow with a "
        "`schedule`, `issues`, `issue_comment` or `pull_request` trigger whose steps run an agent "
        "(`anthropics/claude-code-action`, `claude -p`, `codex exec` or a named model), a GitHub "
        "Agentic Workflows source in `.github/workflows/*.md`, or a file under `.claude/routines/`.",
        "A tracked routine file, or a tracked scheduled or issue or pull request workflow that runs "
        "an agent, directly or through a local reusable workflow."),
    **{signal: _flag(change, done) for signal, (change, done) in BACKLOG_FLAGS.items()},  # #1324
    **{signal: _flag(change, done) for signal, (change, done) in GUARD_FLAGS.items()},  # #854
    **{signal: _flag(change, done) for signal, (change, done) in CONTRACT_FLAGS.items()},  # #852, #853
}
_WORDING.update({f"{name}.value": _setting(name) for name in SETTING_NAMES})

_NEEDS = "A headless scan reports the `{}` block unavailable when {}; install it and rerun."

# Why a signal can be unavailable, by its first path segment (the block name).
_MEASURE_HINTS = {
    "guardrails": "Not applicable when the `guardrails` scan finds no configured gate (a ruff "
                  "or flake8 complexity limit, a coverage floor, pre-commit hooks, mypy "
                  "strict): with nothing to state there is no share to measure "
                  "(`stated_pct_reason: no_configured_gates`).",
    "agent_consistency": "Not applicable when fewer than two agent systems have a root "
                         "instruction file: one system has nothing to agree with, so "
                         "`conflicts` and `unlinked` are null (`applicable: false`, "
                         "reason `fewer_than_two_systems`).",
    "structure": _NEEDS.format("structure", "grimp or networkx is not installed (`no_grimp`, "
                               "`no_networkx`)") + " With no import graph the reason names "
                 "the dominant source language: `no_builder:<language>` when no "
                 "builder covers that language (the reason names a tool that would, "
                 "such as `jdeps`), `no_import_graph_<language>` when its builder "
                 "found nothing to graph, `no_python_packages` for a Python "
                 "repository with no importable package, `no_source_files` with no "
                 "source at all. An import graph with no edges leaves "
                 "`modularity_q` null (`modularity_reason: no_import_edges`): modules "
                 "that never import each other have no community structure to measure.",
    "stats_summary": _NEEDS.format("stats_summary", "lizard is not installed (`no_lizard`)")
                     + " With no production source file scored (a docs-only repository, or "
                     "only tests, fixtures or examples) the file size is not measured "
                     "(`no_source_files`): a percentile of no files says nothing. A stats "
                     "sidecar from an older toolkit has no class split (`no_class_split`); "
                     "rerun `/assess`. A full run with no `complexity-stats.json` "
                     "reads `no_complexity_stats`: the treemap step did not run or did not "
                     "finish, or scored nothing without `scc` on `PATH` (with it, an empty "
                     "set is written as `files_scored` 0). Install `scc`, then check the "
                     "treemap step's output in the next `/assess` run.",
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
                     "every file has about one commit (rescan a full-history clone), when no "
                     "doc describes a part of the code of its own, or below 20 measured docs "
                     "(`too_few_measured_docs`). Docs kept true by CI are not measured (`ci_not_read`) "
                     "when none is found and the tree holds a CI file of a system this reader does not parse.",
    "review_reality": "Unavailable (`unsupported_host`) when the remote's host is not GitHub or a "
                      "GitLab host glab reaches (Bitbucket, Azure DevOps, GitHub Enterprise): "
                      "neither `--platform` nor `/assess` reads merged changes there; sign glab "
                      "in to the host if it is a GitLab. Otherwise it "
                      "needs GitHub or GitLab API access; a headless fleet scan makes no network "
                      "calls, so this is not measured there.",
    "review_evidence": "Unknown when no AI review step is committed to CI and review on merged "
                       "changes is not read: a headless fleet scan reads it with `--platform`.",
    "contributor_files": "Not measured (`knowledge_base`) for a knowledge base, which takes no "
                         "code patches. The policy is not measured (`org_policy_not_read`) when the "
                         "repository holds none and the owner's shared `.github` repository was not "
                         "read: run with `--platform`, or `/assess` with `gh` signed in.",
    "agent_ops": "Permissions are not measured (`managed_settings_unreadable`) when the repository "
                 "lists none and a managed settings source could not be read. Agent work and the "
                 "agent job scope are not measured (`agent_workflows_unread`) when only another "
                 "repository's reusable workflow may run the agent. The agent job scope is not "
                 "measured (`no_agent_jobs`) when no committed workflow job runs an agent, and "
                 "(`agent_job_scope_unread`) when an agent job's `permissions:` is an expression "
                 "or another value the scan cannot read.",
    "skill_library": "Not measured (`not_a_git_repository`) outside a git repository, where the "
                     "committed files cannot be listed.",
    "ai_governance": "Not measured (`no_pr_workflows`) when no workflow runs on pull requests, and "
                     "(`ruby_unread`) when none enforces but a step runs Ruby naming an AI marker.",
    "ci_hazards": "Not measured (`no_pr_checks`) when no GitHub Actions workflow runs a check on pull "
                  "requests, and (`no_ci_files`) when no CI file, Jenkinsfile or root `pom.xml` is tracked.",
    "ci_model_jobs": "Not measured (`no_model_jobs`) when no model-calling CI job is detected. "
                     "Not measured (`trigger_unresolved`, `ci_jobs_unread`) when none is ungated but a "
                     "trigger, a condition or a job this reader cannot read could still run one on every change. "
                     "Agent evals are not measured (`eval_ci_unread`) when an eval suite holds a baseline, no "
                     "CI file read runs it, and a CI file or include was not read.",
    "instruction_claims": "Not measured (`no_instruction_claims`) when the instruction files make no "
                          "claim this check reads (a script run in CI, a pinned version, a file count): "
                          "no false claim among none is not a claim that holds.",
    "nested_instructions": "The always-loaded budget is not measured (`no_loaded_instructions`) when "
                           "Claude Code loads no instruction file at launch: there is no budget to keep.",
    "secrets_guard": "Level 2 is not measured (`secret_scanning_not_read`) on a GitHub repository with "
                     "no secrets scan in CI when its push protection setting was not read: run with "
                     "`--platform`, or `/assess` with `gh` signed in, as a repository admin, organization owner or security "
                     "manager, or add a CI "
                     "scan, to settle it. It is also not measured "
                     "(`ci_includes_unresolved`) when a GitLab include that could hold the scan was not read, or "
                     "(`gitlab_rules_unevaluated`) when a GitLab scan job runs under rules, only/except or a "
                     "when that is not evaluated.",
    "skill_personal_data": "Not measured (`no_skill_files`) when the repository, or the skill unit, "
                           "holds no `SKILL.md` or agent definition: there is nothing to scan.",
    "skill_script_tests": "Not measured (`no_skill_scripts`) when no skill in the repository, or in "
                          "the skill unit, holds a script: there is nothing to test.",
    "skill_disclosure": "Not measured (`no_skill_files`) when the repository, or the skill unit, "
                        "holds no `SKILL.md` or agent definition: there is nothing to disclose.",
    "agent_environment": "The `agent_environment` scan reported it unavailable or did not run: "
                         "check the fleet run's `--scans` and its `logs/` directory. The lockfile "
                         "check (`unlocked_total`) is also not measured with no dependency manifest "
                         "read (`manifests_total` 0, `no_manifests`): there is nothing to lock. "
                         "A library whose ecosystem commits no lock (a Composer library, a published "
                         "Pub package, a SwiftPM library) is listed with the note `skipped_library` "
                         "and not counted.",
    "ci_checks": "Unknown when no GitHub Actions or GitLab CI line runs a test or lint command "
                 "that can stop its job but a CI file pulls jobs from a file the run could not read (`basis: "
                 "included_jobs`: a GitLab `include:` whose template is not cloned in the "
                 "fleet root, or a `template:` or `remote:` include, a reusable workflow from "
                 "another repository): the check may live there. Cloning the template into "
                 "the same ghsync root lets the fleet read it. `enforced_check_runs` is also "
                 "unknown when the check's workflow or job has triggers the scan cannot settle "
                 "(`enforced_basis: unknown_trigger`: no `on:` key, a `workflow_call` nobody "
                 "calls, a GitLab `rules: if:` on a custom variable): say when the workflow "
                 "runs, by adding `push` or `pull_request` triggers (or a merge request rule). "
                 "It is unknown when the only CI line runs a target whose recipe the scan "
                 "does not read (`enforced_basis: check_target`: `make foo`, `npm run foo`, "
                 "`./scripts/foo.sh`, `docker run img`): call the test command in its "
                 "own CI step (`make test`, `pytest`) to measure it. "
                 "It is unknown when the only check's exit status may not reach its step "
                 "(`enforced_basis: check_may_pass`: `pytest || echo failed`, `if pytest; then "
                 "...; fi`, `until pytest; do break; done`, `pytest && echo ok` with more of "
                 "the step after it): run the check on its own line, or end the fallback with "
                 "`exit 1` (`pytest || { echo failed; exit 1; }`). "
                 "It is unknown when the only check runs a committed script the tree does "
                 "not hold (`enforced_basis: script_not_found`, `run: ./ci.sh` with no "
                 "`ci.sh`): commit the script, or call the test command in its own CI step. "
                 "It is unknown too when no GitHub Actions or GitLab CI line runs a check but "
                 "the repository commits a CI file of another system (Jenkins, CircleCI, "
                 "Azure Pipelines, Buildkite, LUCI, Prow, Zuul and others; `basis: "
                 "unread_ci_system`, `unread_ci_files` counts them): the scanner cannot read "
                 "those systems, so their checks are not measured; nothing in the repository "
                 "needs to change. "
                 "It is unknown when CI runs off the repository: under `--platform`, most "
                 "sampled merged pull requests carry a check from a known external CI "
                 "system (`enforced_basis: external_ci`), or, offline, the root holds a Prow "
                 "`OWNERS_ALIASES` file and no CI file (`enforced_basis: "
                 "external_ci_marker`): for `external_ci_marker`, a run with `--platform` "
                 "reads the checks on merged pull requests instead. "
                 "A scan stored before `enforced_check_runs` existed has no such key: re-run "
                 "the scan to measure it. `lint_runs` (the linters practice) is unknown for the same "
                 "reasons, and when no CI line calls a linter but one runs a check target "
                 "or script that may (`lint_basis: check_target`: `make check`, `npm test`, "
                 "`tox`, `pre-commit run`): the scan does not read recipes, so call the "
                 "linter in its own CI step (`ruff check .`, `make lint`) to measure it.",
    "coverage_gate": "The `coverage_gate` scan reads the threshold unknown when none of at "
                     "least 50 percent is committed but a gate's threshold is not a readable "
                     "percentage: a variable (`--cov-fail-under=${{ env.COV_MIN }}`), an nyc/c8 "
                     "default, or a Jest uncovered-line count. Write the threshold as a number "
                     "to measure it. A Go or Rust repository with no Go or Rust gate found, "
                     "and none of at least 50 percent for another language, also reads "
                     "unknown (`unmeasured_reason` `go_rust_gate_form_not_read`): a script "
                     "comparing `go tool cover` to a number is not read, while go-test-coverage's "
                     "`.testcoverage.yml`, `cargo tarpaulin --fail-under` and `cargo llvm-cov "
                     "--fail-under-lines` are. A scoped run of a Python package with no coverage.py "
                     "config of its own reads unknown (`root_coverage_config_unresolved`) when "
                     "the root config's floor would decide it but where CI runs coverage is not "
                     "resolved: run coverage from the root in CI, or pass `--cov-config`. "
                     "A Codecov project status with `target: auto` (or no target) reads unknown "
                     "(`codecov_target_not_read`), as does a patch status alone "
                     "(`codecov_patch_only`), and a numeric Codecov target reads CI unknown "
                     "(`enforced_basis: status_check_unconfirmed`): whether branch protection "
                     "requires the status is not read. To measure the level, run a threshold of "
                     "at least 50 percent in a CI step (`pytest --cov --cov-fail-under=80`). "
                     "The block is unavailable when the scan did not run: check "
                     "the fleet run's `--scans` and its `logs/` directory.",
    "test_pressure": "Only a full `/assess` run with its opt-in mutation pass measures this; a "
                     "headless fleet scan does not.",
    "backlog_ready": BACKLOG_HINT,
    "repo_guards": GUARD_HINT,
    "api_compat": API_COMPAT_HINT,
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
