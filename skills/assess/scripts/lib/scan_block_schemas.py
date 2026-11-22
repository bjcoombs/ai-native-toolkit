"""Field descriptions of the scan blocks a fleet reader reads first (issue #779).

``signals_schema`` keeps per-scan blocks open: each scan owns its shape. A few
blocks carry the figures a fleet reader compares across repositories, so the
published schemas (``skills/assess/references/signals.schema.json`` and
``skills/fleet/references/repos.schema.json``) name and explain their fields
here: ``ai_usage`` (#547, #695), ``ci_checks`` (#698), ``coverage_gate`` (#781),
``delivery`` (#715), ``guardrails`` (#767), ``agent_consistency`` (#768) and
``layer_grades`` (#620, #821).
Both generators read :data:`BLOCK_SCHEMAS`, so the two files describe the same
fields in the same words.

The descriptions stay loose on purpose. No field is required (an unavailable
block is ``{"available": false, "reason"}`` and counts-only output drops text
fields), there is no ``additionalProperties`` limit, and a list of strings is
typed as an array or an integer because counts-only output keeps only its
length. ``tests/test_scan_block_schemas.py`` checks every described field is
a field of the block's TypedDict, and that real documents validate.
"""
from __future__ import annotations

from lib.ci_includes import STATUSES
from lib.merged_ci import MERGED_STATUSES
from lib.run_context_types import JsonDict

_NUM_OR_NULL: JsonDict = {"type": ["number", "null"]}
_INT: JsonDict = {"type": "integer"}
_STRINGS: JsonDict = {"type": ["array", "integer"],
                      "description": "A list of strings; counts-only output keeps only its length."}


def _field(base: JsonDict, description: str) -> JsonDict:
    return {**base, "description": description}


def _share(of: str) -> JsonDict:
    return _field(_NUM_OR_NULL, f"Share (0 to 1) of {of}; null when nothing was counted.")


def _hours(what: str) -> JsonDict:
    return _field(_NUM_OR_NULL, f"{what}, in hours (rounded to 0.01, nearest-rank percentile); "
                                "null when no sampled change has both timestamps.")


AI_USAGE: JsonDict = {
    "description": (
        "Agent involvement in the commits of the run window (lib/ai_usage.py), counted from git "
        "history. An agent identity is matched by trailer (Co-Authored-By and similar) or by "
        "author against the accounts confirmed in [identities], the pinned agent rules and any "
        "[[ai_usage.agent_identities]] rules of --fleet-config. Counts are commits; shares are of total_commits. A lower bound: an "
        "agent that leaves no trace is not counted."),
    "properties": {
        "available": {"type": "boolean"},
        "window": _field({"type": "string"}, "The history window read, for example 6mo."),
        "since": _field({"type": ["string", "null"]},
                        "The window as an absolute git --since bound (@<epoch>, counted back "
                        "from HEAD's commit time); dropped in counts-only output."),
        "merge_rule": _field({"type": "string"}, "How merge commits are treated (excluded: not counted)."),
        "total_commits": _field(_INT, "Commits in the window."),
        "agent_trailer_commits": _field(_INT, "Commits carrying an agent trailer."),
        "agent_trailer_share": _share("commits carrying an agent trailer"),
        "agent_authored_commits": _field(_INT, "Commits whose author an agent identity matches."),
        "agent_authored_share": _share("commits whose author an agent identity matches"),
        "agent_either_commits": _field(_INT, "Commits with an agent trailer or an agent author."),
        "agent_either_share": _share("commits with an agent trailer or an agent author"),
        "dependency_bot_commits": _field(_INT, "Commits by a dependency-update bot (Dependabot, "
                                               "Renovate); never counted as agent work."),
        "token_user_commits": _field(_INT, "Commits by a GitLab access-token user "
                                           "(project_<id>_bot_<hex>) not promoted to agent; a "
                                           "config rule or token_users_as_agents promotes one."),
        "agent_identity_rule_commits": _field(
            {"type": "array", "items": _INT},
            "Commits each [[ai_usage.agent_identities]] rule matched, a positional list in rule "
            "order; the rule patterns themselves are never written."),
        "identities_awaiting_confirmation": _field(
            _INT, "Accounts in the window that look automated (a [bot] suffix, a GitLab "
                  "token user, a bot or AI tool name) and are neither classified in the "
                  "[identities] config section nor counted by an agent rule (#765). A next "
                  "step for the human, never a score; the accounts are listed only by "
                  "scripts/identity_candidates.py."),
        "lower_bound": {"type": "boolean"},
        "covered_agents": _STRINGS,
        "covered_author_agents": _STRINGS,
        "limits": _STRINGS,
        "reason": {"type": "string"},
    },
}

CI_CHECKS: JsonDict = {
    "description": (
        "Does CI run a test or lint check (lib/ci_checks.py), read from the git-tracked CI "
        "files and the GitLab includes resolved offline (lib/ci_includes.py: local files, and "
        "project or component files of clones in the --ghsync-root). test_or_lint_configured is true "
        "when an own or included CI line runs one, false when none does and every include "
        "resolved, and null (unknown) when a CI file pulls jobs from a file this run could not "
        "read (an unresolved GitLab include, a reusable workflow in another repository); basis "
        "says which. Under --platform, a repository with an unresolved GitLab include also gets "
        "GitLab's merged configuration from the CI lint API (lib/gitlab_ci_lint.py), counted "
        "apart in merged_check_commands; a template resolved offline wins. enforced_check_runs (issue #775) is the same test with "
        "masked checks left out (|| true, continue-on-error: true, allow_failure: true, set +e "
        "without a re-raise; lib/ci_masking.py) and checks off the ordinary triggers left out "
        "(lib/ci_triggers.py); masked_check_commands and off_trigger_check_commands count the "
        "lines left out. lint_runs (issue #782) is the enforced test for lint lines only (Layer 3 "
        "level 1); a check target or script that may run a linter inside it reads null. "
        "CI files of other systems are not parsed (issues #787, #928): unread_ci_files counts "
        "them, and with no readable check one makes basis unread_ci_system and "
        "enforced_check_runs null (and lint_runs null with no readable lint line). The "
        "unread-CI paths, all relative to the repository root: Jenkinsfile, "
        ".circleci/config.yml, bitbucket-pipelines.yml, .travis.yml, azure-pipelines.yml, "
        "azure-pipelines.yaml, .azure-pipelines.yml, .azure-pipelines.yaml, "
        ".buildkite/pipeline.yml, .buildkite/pipeline.yaml, buildkite.yml, buildkite.yaml, "
        ".ci.yaml (Flutter Cocoon / LUCI), .prow.yaml, .zuul.yaml, .drone.yml, .woodpecker.yml, "
        ".cirrus.yml, appveyor.yml, .semaphore/semaphore.yml, cloudbuild.yaml, bitrise.yml and "
        "codemagic.yaml; any *.star or *.cfg file under infra/config/ (LUCI project "
        "configuration); any *.yml or *.yaml file under .azure/pipelines/, eng/pipelines/, "
        ".azure-pipelines/, azure-pipelines/ (Azure Pipelines) or .woodpecker/; and any file "
        "under zuul.d/ (lib/unread_ci.py). Off-repository CI (issue #928): under --platform, "
        "when more than half of the sampled merged pull requests carry a check run or commit "
        "status of a known external CI system (lib/external_ci.py, at most two GET calls per "
        "sampled pull request), the false cases read null with basis external_ci; offline, a "
        "root Prow OWNERS_ALIASES file with no CI file of any kind reads null with basis "
        "external_ci_marker. Neither earns credit."),
    "properties": {
        "available": {"type": "boolean"},
        "reason": _field({"type": "string"}, "Why the block is unavailable."),
        "ci_files": _field(_INT, "CI configuration files read."),
        "check_commands": _field(_INT, "CI lines that run a test or lint check."),
        "included_files": _field(_INT, "CI files that pull jobs from elsewhere."),
        "included_check_commands": _field(_INT, "Lines of resolved included files that run a "
                                                "test or lint check."),
        "resolved_includes": _field(_INT, "Include items whose every file was read."),
        "unresolved_includes": _field(_INT, "Include items not read (status other than resolved)."),
        "includes": _field(
            {"type": ["array", "integer"], "items": {"type": "object", "properties": {
                "kind": {"enum": ["local", "project", "component", "template", "remote"]},
                "id": _field({"type": ["string", "null"]},
                             "gitlab_project:<path> or gitlab_component:<path>; null for local, "
                             "template and remote."),
                "status": _field({"enum": list(STATUSES)},
                                 "resolved, or why not: no_root, not_cloned, file_missing, "
                                 "not_offline (template, remote), glob, bad_path, depth_limit, "
                                 "file_limit, unparsed, ref_missing (a pin the clone does not "
                                 "hold)."),
                "ref_basis": _field({"enum": ["ref", "head", "worktree", None]},
                                    "How the file was read: at the pinned ref, at the clone's "
                                    "HEAD (no pin), or from the work tree; null when not "
                                    "resolved."),
                "depth": _field(_INT, "Include level: 1 for the repository's own include."),
            }}},
            "One record per include item, nested includes after their parent; no file name "
            "or text."),
        "merged_config": _field(
            {"enum": list(MERGED_STATUSES)},
            "The CI lint API read: not_read (offline), not_needed (every include resolved "
            "offline), read, invalid (GitLab marked the configuration invalid) or unavailable."),
        "merged_config_reason": _field(
            {"type": ["string", "null"]},
            "The reason code when merged_config is unavailable (no_access, not_found, "
            "not_gitlab, ...); null otherwise."),
        "merged_check_commands": _field(_INT, "Lines of the merged configuration that run a test "
                                              "or lint check; the text is never published."),
        "test_or_lint_configured": _field(
            {"type": ["boolean", "null"]},
            "The CI test-or-lint signal: true, false, or null when included jobs may hold "
            "the check."),
        "basis": _field(
            {"enum": ["check_command", "merged_config", "included_jobs", "merged_config_partial",
                      "unread_ci_system", "external_ci", "external_ci_marker",
                      "no_check_command", "no_ci"]},
            "The reason for test_or_lint_configured: check_command or merged_config (true; the second "
            "when only the merged configuration ran a check), included_jobs or "
            "merged_config_partial (null; the second when the merged configuration was read "
            "with no check but may lack jobs: an include under rules:, or a trigger: child "
            "pipeline), unread_ci_system (false: no readable check, but a CI file of a system "
            "the scan does not parse is tracked), external_ci or external_ci_marker (false: no "
            "readable check, but CI runs off the repository; as enforced_basis), "
            "no_check_command or no_ci (false)."),
        "enforced_check_runs": _field(
            {"type": ["boolean", "null"]},
            "Layer 5 level 1: true when a CI line runs a check that can fail its job on ordinary "
            "changes, null when none does but included jobs, unsettled triggers, a committed "
            "script the tree does not hold, a target whose recipe is not read, a CI system the "
            "scan does not parse or an external CI system may hold one, else false."),
        "enforced_basis": _field(
            {"enum": ["enforced_check", "included_jobs", "unknown_trigger", "script_not_found",
                      "check_target", "unread_ci_system", "external_ci", "external_ci_marker",
                      "not_enforced", "no_check_command", "no_ci"]},
            "The reason for enforced_check_runs: enforced_check (true), included_jobs, "
            "unknown_trigger, script_not_found, check_target or unread_ci_system (null), "
            "external_ci (null: an "
            "external check context on merged changes under --platform; more than half of the "
            "sampled merged pull requests carry a check run or commit status of a known "
            "external CI system such as Buildkite, LUCI, Prow tide or Azure Pipelines, read with "
            "at most two GET calls per sampled pull request; it never gives credit), "
            "external_ci_marker (null: offline, a root Prow OWNERS_ALIASES file with no CI file "
            "present, neither a workflow, a .gitlab-ci.yml nor an unread CI file; an OWNERS file "
            "alone is not a marker), not_enforced, no_check_command or no_ci (false)."),
        "masked_check_commands": _field(
            _INT, "CI lines that run a check whose failure cannot fail the job."),
        "off_trigger_check_commands": _field(
            _INT, "Unmasked CI check lines whose workflow or job does not run on ordinary changes "
            "(workflow_dispatch or schedule only, a manual GitLab job)."),
        "lint_runs": _field(
            {"type": ["boolean", "null"]},
            "Layer 3 level 1 (issue #782): true when a CI line runs a linter that can fail its job "
            "on ordinary changes (not --version or --help), or a step uses a known lint action "
            "(golangci-lint-action, ruff-action, super-linter, reviewdog), "
            "null when none does but included "
            "jobs, unsettled triggers, a lint script the tree does not hold or a check target or "
            "script that may lint inside it could hold one, else false."),
        "lint_basis": _field(
            {"enum": ["lint_command", "included_jobs", "merged_config_partial", "unknown_trigger",
                      "script_not_found", "check_target", "unread_ci_system", "external_ci",
                      "external_ci_marker", "not_enforced", "no_lint_command", "no_ci"]},
            "The reason for lint_runs: lint_command (true), included_jobs, merged_config_partial, "
            "unknown_trigger, script_not_found, check_target, unread_ci_system, external_ci or "
            "external_ci_marker (null; as enforced_basis), not_enforced, no_lint_command or "
            "no_ci (false)."),
        "unread_ci_files": _field(
            _INT, "Tracked CI files of systems the scan does not parse, at the unread-CI paths "
            "the block description lists; a count, never the paths."),
    },
}

COVERAGE_GATE: JsonDict = {
    "description": (
        "Committed line-coverage thresholds (lib/coverage_gate.py), read from config and CI "
        "files at HEAD, never from measured coverage. Each gate keeps its threshold as written "
        "beside threshold_percent, the same value as a percentage (a JaCoCo ratio 0.80 is 80)."),
    "properties": {
        "available": {"type": "boolean"},
        "reason": _field({"type": "string"}, "Why the block is unavailable."),
        "configured": _field({"type": "boolean"},
                             "True when any gate gates something (a non-zero threshold, or a "
                             "null one from a tool default or a templated flag). Read from configuration text at HEAD; was enforced before issue #783."),
        "gates": _field(
            {"type": "array", "items": {"type": "object", "properties": {
                "file": {"type": "string"},
                "line": _INT,
                "tool": {"type": "string"},
                "form": {"type": "string"},
                "threshold": _field(_NUM_OR_NULL, "The threshold as written; null when not "
                                                  "read (a template variable, a tool default)."),
                "unit": {"enum": ["percent", "ratio", "uncovered_count", "missed_ratio"]},
                "threshold_percent": _field(
                    _NUM_OR_NULL, "The threshold as a percentage: as written for percent, "
                                      "times 100 for ratio; null for uncovered_count, "
                                      "missed_ratio (a JaCoCo MISSEDRATIO maximum) or a "
                                      "threshold not read."),
                "metric": {"type": ["string", "null"]},
            }}},
            "Each gate found, with its file and 1-based line."),
        "max_threshold_percent": _field(
            _NUM_OR_NULL, "The highest non-null threshold_percent across the gates; null "
                              "when no gate has one."),
        "threshold_meets_minimum": _field(
            {"type": ["boolean", "null"]},
            "The Layer 6 level 1 signal: true when a counted gate's threshold_percent is at "
            "least 50; null (unknown) when none is but a counted gate's percentage was not "
            "read, or when a repository holding a go.mod or Cargo.toml has no gate of that "
            "language and none of at least 50 for another (see unmeasured_reason); false "
            "otherwise. Gates counted are those enforced "
            "counts."),
        "unmeasured_reason": _field(
            {"type": ["string", "null"]},
            "Why threshold_meets_minimum is null when no gate of the repository's Go or Rust "
            "module is found: go_rust_gate_form_not_read when the repository holds a go.mod "
            "or Cargo.toml with no gate read for it and none of at least 50 percent for "
            "another language (issue #937), so its gate may be a script this scan does not "
            "read; null otherwise."),
        "enforced_meets_minimum": _field(
            {"type": ["boolean", "null"]},
            "The Layer 6 level 1 signal (issue #921): threshold_meets_minimum with a CI run "
            "required. True when it is true and an enforced CI line (unmasked, on ordinary "
            "triggers; lib/ci_masking.py, lib/ci_triggers.py) passes a counted threshold of at "
            "least 50 itself or runs the tool that reads it; null when threshold_meets_minimum "
            "is null or the run cannot be told (see enforced_basis); false otherwise."),
        "enforced_basis": _field(
            {"enum": ["ci_run", "no_gate", "threshold_below_minimum", "threshold_not_read",
                      "unknown_trigger", "indirect_run", "included_jobs", "unread_ci_system",
                      "not_run_in_ci", "no_ci"]},
            "The reason for enforced_meets_minimum: ci_run (true); no_gate, "
            "threshold_below_minimum, not_run_in_ci and no_ci (false); threshold_not_read, "
            "unknown_trigger, indirect_run (a check target or script such as make test may run "
            "it), included_jobs and unread_ci_system (null)."),
        "not_detected": _STRINGS,
    },
}

DELIVERY: JsonDict = {
    "description": (
        "How work flows through review to merge (lib/delivery.py), from one shared sample of "
        "up to 30 changes merged inside the run window, newest merge first (the sample "
        "review_reality reads). A platform block: present only under --platform (absent "
        "otherwise); a refused read is available false with a reason (no_access on HTTP 403), "
        "never zeros. Durations are hours; shares are of merged_count."),
    "properties": {
        "available": {"type": "boolean"},
        "reason": _field({"type": "string"}, "Why the block is unavailable."),
        "merged_count": _field(_INT, "Merged changes in the sample (at most 30)."),
        "time_to_merge_hours_p50": _hours("Median time from opened to merged"),
        "time_to_merge_hours_p90": _hours("90th percentile time from opened to merged"),
        "first_human_comment_hours_p50": _hours(
            "Median time from opened to the first comment or review by a person other than "
            "the author (bots and agent identities excluded)"),
        "first_human_comment_hours_p90": _hours(
            "90th percentile time from opened to the first human comment or review"),
        "human_commented_share": _share("changes with any human comment or review"),
        "ai_review_share": _share("changes with a review or comment by an AI reviewer other "
                                  "than the author"),
        "approved_share": _share("changes approved by someone other than the author (copied "
                                 "from review_reality)"),
        "self_merged_share": _share("changes merged by their author (copied from "
                                    "review_reality)"),
        "agent_authored_share": _share("changes an agent identity authored"),
    },
}

# Block name -> the block's described fields (no ``type``; each generator adds its own rules).
GUARDRAILS: JsonDict = {
    "description": (
        "Guardrails stated (lib/guardrails.py): the gates the repository configures at HEAD "
        "(a ruff or flake8 complexity limit, the coverage floor from coverage_gate, pre-commit "
        "hooks, mypy strict) and whether a graded instruction file states each one's rule and "
        "threshold on one line."),
    "properties": {
        "available": {"type": "boolean"},
        "reason": _field({"type": "string"}, "Why the block is unavailable."),
        "gates": _field(
            {"type": "array", "items": {"type": "object", "properties": {
                "kind": {"enum": ["complexity_limit", "coverage_floor", "pre_commit",
                                  "type_strictness"]},
                "tool": {"type": "string"},
                "rule": {"type": "string"},
                "threshold": _field(_NUM_OR_NULL, "The limit or floor; null for a gate with "
                                                  "none (pre-commit, mypy strict)."),
                "file": {"type": "string"},
                "line": {"type": ["integer", "null"]},
                "stated": {"type": "boolean"},
                "stated_in": _field({"type": ["string", "null"]},
                                    "The first instruction file that states the gate."),
                "snippet": _field({"type": ["string", "null"]},
                                  "A ready one-line sentence for an unstated gate; null when "
                                  "stated. Dropped from counts-only output."),
            }}},
            "Each gate found, one per configuring file."),
        "enforced": _field(_INT, "Gates found."),
        "stated": _field(_INT, "Gates a graded instruction file states."),
        "unstated": _field(_INT, "Gates no graded instruction file states."),
        "stated_pct": _field(
            _NUM_OR_NULL, "The guardrails-stated row's signal: stated as a percentage of "
                          "enforced; null when no gate is found."),
        "instruction_files_read": _field(_INT, "Graded instruction files read."),
        "not_read": _STRINGS,
    },
}

_SYSTEMS: JsonDict = {"type": ["array", "integer"],
                      "description": "Agent system ids (lib/agent_systems.py SYSTEMS)."}
AGENT_CONSISTENCY: JsonDict = {
    "description": (
        "Cross-agent instruction consistency (lib/agent_consistency.py): the root instruction "
        "files of every agent system present at HEAD (Claude Code, AGENTS.md, Copilot, Cursor, "
        "Windsurf, Cline, Aider, Junie, Amazon Q, Kiro, Continue, Zed), the check commands and "
        "thresholds they disagree on, and how each links to one canonical file."),
    "properties": {
        "available": {"type": "boolean"},
        "reason": _field({"type": "string"}, "Why the block is unavailable."),
        "systems": _SYSTEMS,
        "systems_total": _field(_INT, "Agent systems with a root instruction file."),
        "applicable": _field({"type": "boolean"},
                             "False with fewer than two systems: nothing to agree with."),
        "canonical": _field({"type": ["string", "null"]},
                            "The canonical file: AGENTS.md when present, else the first root "
                            "file in system order; null with no instruction file."),
        "files": _field(
            {"type": "array", "items": {"type": "object", "properties": {
                "path": {"type": "string"},
                "system": {"type": "string"},
                "link": {"enum": ["canonical", "symlink", "import", "pointer", "config_read",
                                  "mirror", "independent"]},
            }}},
            "Each root-context instruction file, its system and how it links to the canonical."),
        "divergences": _field(
            {"type": "array", "items": {"type": "object", "properties": {
                "kind": {"enum": ["command", "threshold"]},
                "value": _field({"type": "string"}, "The command, or the threshold keyword; "
                                                    "dropped from counts-only output."),
                "named_in": _SYSTEMS,
                "missing_from": _SYSTEMS,
                "stated": {"type": "array"},
            }}},
            "A command one system names and another does not, or a threshold two state "
            "differently."),
        "conflicts": _field({"type": ["integer", "null"]},
                            "The agent-consistency row's level 1 signal: divergences; null "
                            "when not applicable."),
        "unlinked": _field({"type": ["integer", "null"]},
                           "The level 2 signal: systems not linked to the canonical file; null "
                           "when not applicable."),
        "consistent": _field({"type": ["boolean", "null"]},
                             "No divergence and every system linked; null when not applicable."),
        "next_steps": _field(
            {"type": "array", "items": {"type": "object", "properties": {
                "system": {"type": "string"},
                "path": {"type": "string"},
                "pointer": _field({"type": "string"}, "The exact pointer line for the tool; "
                                                      "dropped from counts-only output."),
                "symlink": _field({"type": ["string", "null"]},
                                  "A symlink command; null for Aider (config-only)."),
            }}},
            "One per unlinked system."),
        "not_read": _STRINGS,
    },
}

# The deterministic Layer 0 grade and where the instruction files are (#620, #821).
LAYER_GRADES: JsonDict = {
    "description": (
        "Deterministic layer grades (lib/layer_grades.py): the best instruction-file grade and "
        "where the agent instruction files are. Tokens and a boolean, kept whole by counts-only "
        "output; no path is carried."),
    "properties": {
        "instruction_presence": _field(
            {"enum": ["root", "nested_only", "none", None]},
            "root: a file at a root location (CLAUDE.md, AGENTS.md, GEMINI.md, .cursorrules, "
            ".github/copilot-instructions.md and the others instruction_files grades) or one the "
            "tool loads at session start, always loaded. nested_only: no root file, but a nested or "
            "path-scoped file (pkg/AGENTS.md, a .claude/rules file with paths), loaded on demand "
            "where the agent works. none: no instruction file at any known location. Null when "
            "there is no root file and the nested_instructions scan did not run."),
        "instruction_file_present": _field(
            {"type": ["boolean", "null"]},
            "True for root and nested_only, false for none, null with instruction_presence. "
            "Layer 0 level 1 of the default maturity model reads it."),
        "instructions_grade": _field(
            {"type": ["string", "null"]},
            "Best letter grade (A to F) of the files instruction_presence counts: the root files, "
            "else the always-loaded or nested ones. Null only for none."),
    },
}

BLOCK_SCHEMAS: dict[str, JsonDict] = {
    "agent_consistency": AGENT_CONSISTENCY,
    "ai_usage": AI_USAGE,
    "ci_checks": CI_CHECKS,
    "coverage_gate": COVERAGE_GATE,
    "delivery": DELIVERY,
    "guardrails": GUARDRAILS,
    "layer_grades": LAYER_GRADES,
}
