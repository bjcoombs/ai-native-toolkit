"""Write the basics for the next blocking Step gate as one reviewable change (issue #1154).

``/assess --fix`` and ``/assess --starter`` run this; a plain ``/assess`` never does, and
``/fleet`` has no fix path at all. It reads the repository with the headless scans the Step gates
need (``lib/signals.collect``), takes the gates blocking the next Step from the level result
(``lib/level_tally.level_result``, the default maturity model), and writes a starter file for
each one, plus the deny-rule guardrail when no permission rule is committed
(``lib/starter_files.py``). Each file is built from what the repository already has: its
language, the check command its build exposes and its CI system.

- ``--fix``: the gates blocking the next Step.
- ``--starter``: every gate up to the top Step, for an empty or new repository (a scaffolding
  flow). ``--language`` and ``--check-command`` stand in for what an empty repository cannot show.

It never overwrites a file: an existing one is a conflict, reported with the text to add by
hand. The written files are staged; the scans then run again and the report says, per gate,
whether it now holds or the one manual step left. When the tree was clean before the run
(``.assess/``, the assessment's own output, aside) and ``gh`` or ``glab`` is authenticated for
the ``origin`` remote, the change becomes a branch and a pull (merge) request (``lib/starter_delivery.py``); ``--no-pr`` keeps it staged in the working
tree, as does any failed precondition, with the reason in the report.

Run:
    uv run assess_fix.py <repo> --fix [--no-pr] [--json]
    uv run assess_fix.py <repo> --starter [--language python] [--check-command "pytest"]
        [--ci github|gitlab] [--no-pr] [--json]

Exit codes:
    0  ran: files written, or nothing to do (every targeted gate already held)
    1  ran, and a targeted gate still does not hold (the report names the manual step)
    2  usage error: no mode, both modes, or a target that is not a directory
"""
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
from __future__ import annotations

import argparse
import functools
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.exec_summary import GATE_TASK  # noqa: E402
from lib.level_tally import default_model, level_result  # noqa: E402
from lib.levels import MaturityModel, condition_id  # noqa: E402
from lib.run_context_types_fix import CiSystem, Delivery, FileOutcome, FixReport, GateProof  # noqa: E402
from lib.signals import collect  # noqa: E402
from lib.starter_delivery import Runner, is_git_repo, open_change, run, stage, tree_clean  # noqa: E402
from lib.starter_files import GUARDRAIL, detect_facts, plan_files, write_files  # noqa: E402

# The blocks the Step gates, the guardrail and the fact detectors read.
SCANS = ("layer_grades", "ci_checks", "coverage_gate", "agent_environment", "agent_ops")
GUARDRAIL_TASK = "commit a deny rule in `.claude/settings.json`"
_LRU = type(functools.lru_cache(maxsize=1)(lambda: None))


def clear_scan_caches() -> None:
    """Drop every ``functools`` cache in the loaded ``lib`` modules (tracked files, CI includes),
    so the re-read after writing sees the new files as a fresh ``/assess`` run would."""
    for name, module in list(sys.modules.items()):
        if name == "lib" or name.startswith("lib."):
            for value in list(vars(module).values()):
                if isinstance(value, _LRU) and getattr(value, "__module__", None) == name:
                    value.cache_clear()


def read_scans(root: Path) -> dict[str, Any]:
    clear_scan_caches()
    return dict(collect(root, list(SCANS)))


def gate_held(model: MaturityModel, result: Mapping[str, Any], gate: str) -> bool:
    """Every condition the gate reads (its row's levels 1 up to the gate's level) is met."""
    states = {c["id"]: c["state"] for c in result.get("conditions", [])}
    for step in model.get("steps", []):
        for g in step["gates"]:
            if g["id"] == gate:
                return all(states.get(condition_id(g["row"], n)) == "met" for n in range(1, g["level"] + 1))
    return False


def guardrail_held(scans: Mapping[str, Any]) -> bool:
    ops = scans.get("agent_ops")
    summary = ops.get("summary") if isinstance(ops, dict) else None
    return isinstance(summary, dict) and summary.get("permissions_encoded") is True


def target_gates(model: MaturityModel, result: Mapping[str, Any], starter: bool) -> list[str]:
    """``--fix``: the next Step's blocking gates. ``--starter``: every gate not held, in Step order."""
    if not starter:
        nxt = result.get("next_step")
        return [b["gate"] for b in (nxt or {}).get("blockers", []) if b.get("kind") == "gate" and b.get("gate")]
    gates = [g["id"] for step in model.get("steps", []) for g in step["gates"]]
    return [g for g in dict.fromkeys(gates) if not gate_held(model, result, g)]


def _proof(gate: str, before: bool, after: bool, files: Sequence[FileOutcome]) -> GateProof:
    manual = next((f["manual"] for f in files if f["gate"] == gate and f["manual"]), None)
    if not after and manual is None:
        manual = "the file is written but the gate does not hold yet; rerun `/assess` to see the blocker"
    return {"gate": gate, "task": GATE_TASK.get(gate, GUARDRAIL_TASK if gate == GUARDRAIL else gate),
            "held_before": before, "held_after": after, "manual": None if after else manual}


def _pr_body(report_files: Sequence[FileOutcome], proofs: Sequence[GateProof]) -> str:
    lines = ["## Summary", "", "Starter files for the next /assess Step gate, built from what the repository "
             "already has. Review and edit them before merging.", "", "## Changes Made", ""]
    lines += [f"- `{f['path']}` ({f['gate']})" for f in report_files if f["state"] == "written"]
    left = [p for p in proofs if p["manual"]]
    if left:
        lines += ["", "## Manual steps", ""] + [f"- {p['task']}: {p['manual']}" for p in left]
    return "\n".join(lines) + "\n"


def fix(root: Path, *, starter: bool, language: str | None = None, check_command: str | None = None,
        ci: CiSystem | None = None, open_pr: bool = True, runner: Runner = run) -> FixReport:
    """Plan, write, stage, prove and deliver; the report of what happened."""
    model = default_model()
    git = is_git_repo(root, runner)
    was_clean = git and tree_clean(root, runner)
    scans = read_scans(root)
    before = level_result(model, scans)
    gates = target_gates(model, before, starter)
    guard = not guardrail_held(scans)
    facts = detect_facts(root, scans, language=language, check_command=check_command, ci=ci)
    files = write_files(root, plan_files(root, gates, facts, guard))
    written = [f["path"] for f in files if f["state"] == "written"]
    if git:
        stage(root, written, runner)
    after_scans = read_scans(root) if written else scans
    after = level_result(model, after_scans) if written else before
    proofs = [_proof(g, gate_held(model, before, g), gate_held(model, after, g), files) for g in gates]
    if guard:
        proofs.append(_proof(GUARDRAIL, False, guardrail_held(after_scans), files))
    delivery: Delivery = {"mode": "none", "branch": None, "url": None, "reason": "nothing_written"}
    if written and not git:
        delivery = {"mode": "working_tree", "branch": None, "url": None, "reason": "not_a_git_repo"}
    elif written and not open_pr:
        delivery = {"mode": "working_tree", "branch": None, "url": None, "reason": "no_pr_requested"}
    elif written:
        label = "-".join(g for g in gates) or GUARDRAIL
        title = f"chore: Add the /assess starter files for the {label.replace('-', ' and ')} gate"
        delivery = open_change(root, f"assess/fix-{label}", title, _pr_body(files, proofs), written,
                               was_clean=was_clean, runner=runner)
    return {"mode": "starter" if starter else "fix", "step_before": before["step"], "step_after": after["step"],
            "facts": facts, "files": files, "gates": proofs, "delivery": delivery,
            "nothing_to_do": not gates and not guard}


_DELIVERY_WORDS = {
    "dirty_tree": "the tree had other changes before the run",
    "no_supported_remote": "no GitHub or GitLab `origin` remote",
    "gh_not_authenticated": "`gh` is not authenticated",
    "glab_not_authenticated": "`glab` is not authenticated",
    "not_a_git_repo": "the target is not a git checkout",
    "no_pr_requested": "`--no-pr` was given",
}


# A failure after ``git switch -c``: what state the files are in on the new branch.
_COMMITTED = {
    "commit_failed": "staged, not committed,",
    "push_failed": "committed, not pushed,",
    "pull_request_failed": "committed and pushed, with no pull request opened,",
}


def render(report: FixReport) -> str:
    """The terminal report: Step before and after, each file, each gate's proof, the delivery."""
    out = [f"/assess --{report['mode']}: Step {report['step_before']} -> Step {report['step_after']}"]
    if report["nothing_to_do"]:
        return out[0] + "\nNothing to do: every targeted gate holds and a deny rule is committed.\n"
    facts = report["facts"]
    out.append(f"Read from the repository: language {facts['language'] or 'unknown'}, check command "
               f"{facts['check_command'] or 'none found'}, CI {facts['ci_system']} ({facts['ci_basis']}).")
    for f in report["files"]:
        if f["state"] == "written":
            out.append(f"- wrote {f['path']} ({f['gate']})")
        elif f["state"] == "conflict":
            out.append(f"- conflict: {f['path']} exists and was not changed. Add by hand:\n{f['suggested']}")
    for p in report["gates"]:
        out.append(f"- {p['task']}: " + ("holds" if p["held_after"] else f"not yet; manual step: {p['manual']}"))
    d = report["delivery"]
    if d["mode"] == "pull_request":
        out.append(f"Opened {d['url'] or 'a pull request'} from branch {d['branch']}; "
                   f"the checkout is now on {d['branch']}.")
    elif d["mode"] == "working_tree" and d["branch"]:
        state = _COMMITTED.get(d["reason"], "committed")
        out.append(f"The files are {state} on branch {d['branch']}, the current checkout "
                   f"({d['reason'].replace('_', ' ')}).")
    elif d["mode"] == "working_tree":
        why = _DELIVERY_WORDS.get(d["reason"], d["reason"].replace("_", " "))
        out.append(f"The files are staged in the working tree for review ({why}).")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("repo", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fix", action="store_true", help="write the basics for the next blocking Step gate")
    mode.add_argument("--starter", action="store_true", help="write every gate's basics, for a new repository")
    parser.add_argument("--language", help="the language, when the repository has no source yet")
    parser.add_argument("--check-command", help="the command that runs the tests")
    parser.add_argument("--ci", choices=("github", "gitlab"), help="the CI system, when none is detected")
    parser.add_argument("--no-pr", action="store_true", help="leave the files staged; open no pull request")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    if not args.repo.is_dir():
        parser.error(f"{args.repo} is not a directory")
    report = fix(args.repo.resolve(), starter=args.starter, language=args.language,
                 check_command=args.check_command, ci=args.ci, open_pr=not args.no_pr)
    sys.stdout.write(json.dumps(report, indent=2) + "\n" if args.json else render(report))
    return 0 if all(p["held_after"] for p in report["gates"]) else 1


if __name__ == "__main__":
    sys.exit(main())
