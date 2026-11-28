"""The fixed words for each Step gate of the shipped models: change, noun, task and gap (#1641).

The shipped models' Steps are cumulative foundations (``levels/default.toml``, #1641): Step 2
gates on ``instructions`` (``layer-0-instructions`` up to level 2), Step 3 adds
``ci_runs_tests``, ``review``, ``linters`` and ``secrets_guard``, and Step 4 adds
``coverage_gate``, ``wide_review``, ``agent_permissions`` and ``boundaries``. Each table words
every one of those gate ids, and a gate id a table does not know (a caller's own model) reads
the fallback its reader gives:

- ``GATE_CHANGE``: the change that holds the gate, as the headline's Next line and the Step
  ladder's caption word it (``lib/scoring/headline.py``); fallback ``the <id> gate holds``;
- ``GATE_NOUN``: the gate as the one-line summary names it (the badge, ``log.md``, the
  since-last-run lines); fallback the id itself;
- ``GATE_TASK``: the gate as a task a person can do, for the top card's Start here line, the
  fleet front screen and ``assess_fix.py`` (``lib/top_card.py``); fallback ``make the <id> gate
  hold``;
- ``GATE_GAP``: the gap the top card's Blocks line names (``lib/top_card.py``); fallback ``the
  <id> gate does not hold yet``.

The words follow the plain-English rule (``lib/plain_english.py``: short sentences, no idiom)
and hold no critical word; ``tests/test_plain_english.py`` lints them through
``plain_english.fixed_lines``. A ``GATE_CHANGE`` entry with its longest state fits one line of
the Step ladder (``tests/test_assess_diagrams_ladder.py``). The instructions gate also needs a
claim the scan can check (an enforcement, pin or count claim): ``instruction_claims.failed``
reads not measured on a file that makes none (#827). ``headline`` and ``top_card`` re-export the tables under their
old names. Tests: ``tests/test_cumulative_gates.py``.
"""
from __future__ import annotations

__all__ = ["GATE_CHANGE", "GATE_GAP", "GATE_NOUN", "GATE_TASK"]

GATE_CHANGE = {
    "instructions": "the instruction file names a check, and its claims hold",
    "ci_runs_tests": "CI turns red on a broken check",
    "review": "merged changes show review evidence",
    "linters": "CI runs a linter",
    "secrets_guard": "a secrets guard is configured",
    "coverage_gate": "a coverage gate is enforced",
    "wide_review": "most merged changes are reviewed and approved",
    "agent_permissions": "agent permissions are committed",
    "boundaries": "a boundary or ownership map names module paths",
}

GATE_NOUN = {
    "instructions": "instruction file with a check command",
    "ci_runs_tests": "CI runs tests",
    "review": "review evidence",
    "linters": "linter in CI",
    "secrets_guard": "secrets guard",
    "coverage_gate": "coverage gate",
    "wide_review": "wide review",
    "agent_permissions": "agent permissions",
    "boundaries": "boundary map",
}

GATE_TASK = {
    "instructions": "commit an AGENTS.md or CLAUDE.md that names the command an agent runs and makes one "
                    "claim the scan can check",
    "ci_runs_tests": "make CI turn red when a check breaks",
    "review": "commit an AI review step in CI, or review each change before it is merged",
    "linters": "run a linter in CI",
    "secrets_guard": "configure a secrets guard, such as a secret scan in pre-commit or CI",
    "coverage_gate": "enforce a coverage gate in CI",
    "wide_review": "review most merged pull requests and approve at least half of them",
    "agent_permissions": "commit agent permissions, such as a deny rule in `.claude/settings.json`",
    "boundaries": "commit a CODEOWNERS line or an ARCHITECTURE.md entry for each module path",
}

GATE_GAP = {
    "instructions": "no committed instruction file names the agent's check and makes a claim that holds",
    "ci_runs_tests": "CI does not turn red when a check breaks",
    "review": "merged changes show no review evidence",
    "linters": "CI runs no linter",
    "secrets_guard": "no secrets guard is configured",
    "coverage_gate": "no coverage gate is enforced",
    "wide_review": "too few merged changes are reviewed and approved",
    "agent_permissions": "no agent permissions are committed",
    "boundaries": "no boundary or ownership map names module paths",
}
