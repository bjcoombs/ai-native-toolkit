#!/usr/bin/env python3
"""Apply refiner plans through the guarded write scripts (issue #1321).

The refiner workflows run this with no model in the loop. It reads the
refinement script's verdict (`refine.py` output) and, on the model paths,
the plan the model returned, validates the plan against its schema, and
makes every change through `refine_write.Writer`, which holds the rules.

    refine_apply.py route  --verdict V --issue N    # prints model=true|false
    refine_apply.py settled --verdict V --issue N   # a settled issue, no model
    refine_apply.py triage --verdict V --issue N --plan P
    refine_apply.py dedupe-route --verdict V --issue N   # prints model=true|false, writes nothing
    refine_apply.py dedupe-exact --verdict V --issue N   # prints model=true|false
    refine_apply.py dedupe --verdict V --issue N --plan P
    refine_apply.py sweep  --verdict V
    refine_apply.py summary                          # actions log as markdown

- **route.** The model runs only for an issue the script left unsettled.
- **settled.** A `ready` verdict gets the ready label; anything else is left
  for a person or a later run.
- **triage.** Size marker, blocked-by edges, labels (the ready label only
  through the plan's `ready` and the rule in `refine_write.ready_blocker`),
  then the plan's questions in one batch. The ready label is withheld when
  the size write an `unsized` issue needs, or any planned blocked-by edge,
  was refused or failed; the refusal is logged in the actions log.
- **dedupe-route / dedupe-exact / dedupe.** An exact, closable pair is closed
  with no model; otherwise the model's candidates are named in one comment,
  never closed. `dedupe-route` makes the same decision with no write, for the
  read-only job that runs the model.
- **sweep.** The scheduled run: ready label on settled `ready` issues, exact
  duplicate closes under the weekly cap, and the ready-buffer report.

A plan that fails validation is refused whole: nothing in it is applied.
Exit codes as in `refine_write.py`, plus 5 for an invalid plan.

Python 3.8 compatible and stdlib only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from refine_write import GhError, Json, Refused, Runner, Settings, Writer, make_writer, verdict_entry

HERE = Path(__file__).resolve().parent
SCHEMAS = {"triage": HERE / "triage-plan.schema.json", "dedupe": HERE / "dedupe-plan.schema.json"}
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


class PlanError(ValueError):
    """The model's plan does not match its schema."""


def _type_ok(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    expected = TYPES.get(name)
    if expected is None:
        raise PlanError(f"schema uses unsupported type {name}")
    return isinstance(value, expected) and not (name != "boolean" and isinstance(value, bool))


def _check_object(value: Json, schema: Json, where: str) -> None:
    props: Json = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in value:
            raise PlanError(f"{where}: missing {key}")
    if schema.get("additionalProperties") is False:
        extra = sorted(set(value) - set(props))
        if extra:
            raise PlanError(f"{where}: unknown field(s) {extra}")
    for key, sub in props.items():
        if key in value:
            check(value[key], sub, f"{where}.{key}")


def _check_scalar(value: Any, schema: Json, where: str) -> None:
    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
        raise PlanError(f"{where}: longer than {schema['maxLength']} characters")
    if isinstance(value, int) and not isinstance(value, bool) and "minimum" in schema and value < schema["minimum"]:
        raise PlanError(f"{where}: below {schema['minimum']}")


def check(value: Any, schema: Json, where: str = "plan") -> None:
    """Validate against the JSON Schema subset the plan schemas use."""
    types = schema.get("type")
    if types is not None:
        names: List[str] = [str(n) for n in types] if isinstance(types, list) else [str(types)]
        if not any(_type_ok(value, n) for n in names):
            raise PlanError(f"{where}: expected {types}")
    if isinstance(value, dict):
        _check_object(value, schema, where)
    elif isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise PlanError(f"{where}: more than {schema['maxItems']} items")
        for i, item in enumerate(value):
            check(item, schema.get("items", {}), f"{where}[{i}]")
    else:
        _check_scalar(value, schema, where)


def load_plan(kind: str, text: str, issue: int) -> Json:
    """Parse and validate a plan; it must be about `issue`."""
    try:
        plan = json.loads(text)
    except ValueError as exc:
        raise PlanError(f"plan is not JSON: {exc}") from exc
    check(plan, json.loads(SCHEMAS[kind].read_text(encoding="utf-8")))
    if plan["issue"] != issue:
        raise PlanError(f"plan is about #{plan['issue']}, not #{issue}")
    return dict(plan)


def route(verdict: Json, issue: int) -> bool:
    """Does this issue need the model? Only when the script left it unsettled."""
    entry = verdict_entry(verdict, issue)
    return entry is not None and entry.get("settled") is False


def apply_settled(w: Writer, verdict: Json, issue: int) -> None:
    entry = verdict_entry(verdict, issue)
    if entry is None or entry.get("verdict") != "ready":
        return
    if w.s.ready_label in entry.get("labels", []):
        return
    w.edit_labels(issue, [w.s.ready_label], [], verdict, None)


def _attempt(w: Writer, fn: Any, *args: Any) -> bool:
    """Run one write; a refusal or a failed `gh` call is logged and the run goes on.

    Returns True when the call went through, including a no-op such as a
    marker or edge already in place. A `gh` failure is counted in
    `w.failures`, so the command still exits 3 once every other write has had
    its turn.
    """
    try:
        fn(*args)
        return True
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
    except GhError as exc:
        w.failures += 1
        print(f"gh failed: {exc}", file=sys.stderr)
    return False


def apply_triage(w: Writer, verdict: Json, issue: int, plan: Json) -> None:
    withheld: List[str] = []
    if plan["size"] is not None:
        sized = _attempt(w, w.write_size_marker, issue, plan["size"],
                         plan["size_reason"] or "sized by the refiner", verdict)
        if not sized and (verdict_entry(verdict, issue) or {}).get("unsized") is True:
            withheld.append(f"the size write for #{issue} did not go through")
    for blocker in plan["blocked_by"]:
        if not _attempt(w, w.add_blocked_by, issue, blocker):
            withheld.append(f"the blocked-by edge to #{blocker} did not go through")
    add = [a for a in plan["labels_add"] if a != w.s.ready_label]
    if plan["ready"] and not plan["questions"]:
        if withheld:
            for reason in withheld:
                w.refuse("edit-issue-labels.sh", issue, f"{w.s.ready_label}: {reason}")
                print(f"refused: {w.s.ready_label}: {reason}", file=sys.stderr)
        else:
            add.append(w.s.ready_label)
    if add or plan["labels_remove"]:
        _attempt(w, w.edit_labels, issue, add, plan["labels_remove"], verdict, plan)
    _attempt(w, w.post_questions, [{"issue": issue, "question": q} for q in plan["questions"]])


def exact_pair(verdict: Json, issue: int) -> Optional[Json]:
    for pair in verdict.get("duplicates", []):
        if pair.get("duplicate") == issue and pair.get("kind") == "exact" and pair.get("closable") is True:
            return dict(pair)
    return None


def apply_dedupe_exact(w: Writer, verdict: Json, issue: int) -> bool:
    """Close an exact duplicate with no model. True when the model should still run."""
    pair = exact_pair(verdict, issue)
    if pair is None:
        return True
    _attempt(w, w.close_duplicate, issue, int(pair["kept"]), verdict)
    return False


def apply_dedupe(w: Writer, issue: int, plan: Json) -> None:
    if plan["candidates"]:
        _attempt(w, w.comment_on_duplicates, issue, plan["candidates"])


def report_body(verdict: Json) -> str:
    """The ready-buffer report, from refine.py's buffer and critical path."""
    buf = verdict.get("buffer", {})
    path = verdict.get("critical_path", {})
    chain = " -> ".join(f"#{n}" for n in path.get("issues", [])) or "none"
    lines = [
        "### Ready buffer",
        "",
        f"- **Depth:** {buf.get('depth', 0)} ready issue(s) with no open blocker, against a target of "
        f"{buf.get('target', 0)} ({buf.get('builder_slots', 0)} builder slots x 2): "
        f"{'met' if buf.get('meets_target') else 'below target'}.",
        f"- **Critical path:** {chain}, {path.get('length', 0)} issue(s), {path.get('points', 0)} point(s)"
        f"{', ' + str(path.get('unsized')) + ' unsized' if path.get('unsized') else ''}.",
    ]
    if path.get("cycle_members"):
        lines.append("- **Dependency cycle:** " + ", ".join(f"#{n}" for n in path["cycle_members"]) + ".")
    lines += ["", f"Generated {verdict.get('generated_at', '')} for {verdict.get('repository', '')}."]
    return "\n".join(lines)


def apply_sweep(w: Writer, verdict: Json) -> None:
    for entry in verdict.get("issues", []):
        if entry.get("settled") and entry.get("verdict") == "ready" and w.s.ready_label not in entry.get("labels", []):
            _attempt(w, w.edit_labels, int(entry["number"]), [w.s.ready_label], [], verdict, None)
    for pair in verdict.get("duplicates", []):
        if pair.get("kind") == "exact" and pair.get("closable") is True:
            _attempt(w, w.close_duplicate, int(pair["duplicate"]), int(pair["kept"]), verdict)
    _attempt(w, w.post_report, report_body(verdict))


def summary(settings: Settings) -> str:
    """The actions log as a markdown table for the job summary."""
    rows: List[Dict[str, Any]] = []
    if settings.actions_log.is_file():
        for line in settings.actions_log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    mode = "live" if settings.live else "dry-run (nothing was written)"
    out = ["## Refiner actions", "", f"Mode: {mode}", ""]
    if not rows:
        return "\n".join(out + ["No actions."])
    out += ["| Script | Action | Issue | Detail |", "|---|---|---|---|"]
    for r in rows:
        detail = str(r.get("detail", "")).replace("|", "\\|")
        out.append(f"| {r.get('script')} | {r.get('action')} | #{r.get('issue')} | {detail} |")
    return "\n".join(out)


def _read(path: str) -> Json:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="refine_apply.py", description="Apply refiner plans (issue #1321).")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("route", "settled", "triage", "dedupe-route", "dedupe-exact", "dedupe"):
        s = sub.add_parser(name)
        s.add_argument("--verdict", required=True)
        s.add_argument("--issue", type=int, required=True)
        if name in ("triage", "dedupe"):
            s.add_argument("--plan", required=True)
    sub.add_parser("sweep").add_argument("--verdict", required=True)
    sub.add_parser("summary")
    return p


def run(args: argparse.Namespace, runner: Optional[Runner]) -> int:
    settings = Settings()
    if args.command == "summary":
        print(summary(settings))
        return 0
    verdict = _read(args.verdict)
    if args.command == "route":
        print(f"model={'true' if route(verdict, args.issue) else 'false'}")
        return 0
    if args.command == "dedupe-route":
        print(f"model={'false' if exact_pair(verdict, args.issue) else 'true'}")
        return 0
    w = make_writer(settings, runner)
    if args.command == "settled":
        apply_settled(w, verdict, args.issue)
    elif args.command == "dedupe-exact":
        print(f"model={'true' if apply_dedupe_exact(w, verdict, args.issue) else 'false'}")
    elif args.command in ("triage", "dedupe"):
        plan = load_plan(args.command, Path(args.plan).read_text(encoding="utf-8"), args.issue)
        if args.command == "triage":
            apply_triage(w, verdict, args.issue, plan)
        else:
            apply_dedupe(w, args.issue, plan)
    elif args.command == "sweep":
        apply_sweep(w, verdict)
    return 3 if w.failures else 0


def main(argv: Optional[Sequence[str]] = None, runner: Optional[Runner] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run(args, runner)
    except PlanError as exc:
        print(f"invalid plan: {exc}", file=sys.stderr)
        return 5
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 4
    except GhError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (ValueError, OSError) as exc:
        print(f"usage: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
