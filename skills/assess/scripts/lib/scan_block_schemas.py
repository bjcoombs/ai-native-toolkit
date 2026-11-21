"""Field descriptions of the scan blocks a fleet reader reads first (issue #779).

``signals_schema`` keeps per-scan blocks open: each scan owns its shape. A few
blocks carry the figures a fleet reader compares across repositories, so the
published schemas (``skills/assess/references/signals.schema.json`` and
``skills/fleet/references/repos.schema.json``) name and explain their fields
here: ``ai_usage`` (#547, #695), ``ci_checks`` (#698) and ``delivery`` (#715).
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

from typing import Any

from lib.ci_includes import STATUSES
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
        "author against the pinned agent rules plus any [[ai_usage.agent_identities]] rules of "
        "--fleet-config. Counts are commits; shares are of total_commits. A lower bound: an "
        "agent that leaves no trace is not counted."),
    "properties": {
        "available": {"type": "boolean"},
        "window": _field({"type": "string"}, "The history window read, for example 6mo."),
        "since": _field({"type": ["string", "null"]}, "The window as git --since text; dropped "
                                                      "in counts-only output."),
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
        "project or component files of clones in the --ghsync-root). test_or_lint_runs is true "
        "when an own or included CI line runs one, false when none does and every include "
        "resolved, and null (unknown) when a CI file pulls jobs from a file this run could not "
        "read (an unresolved GitLab include, a reusable workflow in another repository); basis "
        "says which."),
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
                                 "file_limit, unparsed."),
                "ref_basis": _field({"enum": ["ref", "head", "worktree", None]},
                                    "How the file was read: at the pinned ref, at the clone's "
                                    "HEAD (no pin, or a pin the clone does not hold), or from the "
                                    "work tree; null when not resolved."),
                "depth": _field(_INT, "Include level: 1 for the repository's own include."),
            }}},
            "One record per include item, nested includes after their parent; no file name "
            "or text."),
        "test_or_lint_runs": _field(
            {"type": ["boolean", "null"]},
            "The CI test-or-lint signal: true, false, or null when included jobs may hold "
            "the check."),
        "basis": _field(
            {"enum": ["check_command", "included_jobs", "no_check_command", "no_ci"]},
            "The reason for test_or_lint_runs: check_command (true), included_jobs (null), "
            "no_check_command or no_ci (false)."),
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
BLOCK_SCHEMAS: dict[str, Any] = {
    "ai_usage": AI_USAGE,
    "ci_checks": CI_CHECKS,
    "delivery": DELIVERY,
}
