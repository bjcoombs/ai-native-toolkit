"""Per-team next-steps pages in Markdown from one fleet run (issue #571).

Reads a fleet run's output (``repos.jsonl`` and ``groups.jsonl``, through
``lib/fleet_view.py``) plus the maturity model and settings the run used, and renders
one page per group, with no rescan:

- the group's level on every maturity model row, with the roll-up rule;
- for each row below its top level, the unmet criteria of the next level, one
  numbered step per criterion, so a team can take or leave each on its own.
  Each step leads with the change and a "done when" in the repository's own
  terms (``lib/next_step_guidance.py``); the signal expression follows as the
  optional re-run check. A team row names the repositories holding the group
  back, each with its current value; an org row names the settings scope it
  applies to;
- for an ``unknown`` row, the signals that could not be measured and why: a
  measurement gap, never a failure.

Levels come from re-running ``lib.levels.evaluate`` on the same inputs the
roll-up (``lib/fleet_groups.py``) evaluated: each repository's ``scans`` and
the group's joined settings fragment. ``build_pages`` checks every row's
status and level against ``groups.jsonl`` and raises ``NextStepsError`` on any
difference (a different maturity model or settings than the run used), so a page never
disagrees with the run.

Counts-only: a page holds repository and group ids, maturity model titles and
operands (caller-authored, printed in clear), counts and the actual values of
criteria. An actual value is a number, a boolean, a short token (``TOKEN_RE``) or ``set``
for any other string, the same reduction ``groups.jsonl`` applies to settings.

Output is deterministic: rows in maturity model order, repositories sorted, no date.
``write_pages`` writes ``<out>/teams.md`` (the index) and
``<out>/teams/<group>.md`` (a group id's segments as directories), after
removing the ``*.md`` pages a previous run left under ``<out>/teams/``.
"""
from __future__ import annotations

import math
import re
from urllib.parse import quote
from collections.abc import Iterable, Mapping, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any

from lib.counts_only_rules import TOKEN_RE
from lib.fleet import STATUS_OK, FleetRow
from lib.fleet_groups import GroupRow, repo_signals, settings_for, settings_signals
from lib.next_step_guidance import guidance, measure_hint
from lib.next_step_guidance import parts as unmet_parts_of
from lib.levels import Criterion, CriterionOutcome, Evaluation, Row, RowResult, MaturityModel, evaluate
from lib.run_context_types import JsonDict

TEAMS_DIR = "teams"
INDEX_FILE = "teams.md"

_OPS = {"eq": "=", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">=",
        "in": "in", "not_in": "not in"}
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|#~])")


class NextStepsError(ValueError):
    """The inputs cannot produce pages that agree with the run; the message says why."""


# --- formatting --------------------------------------------------------------


def text(value: str) -> str:
    """Caller-supplied text (a maturity model title) as inert Markdown on one line."""
    return _MD_SPECIAL.sub(r"\\\1", " ".join(value.split()))


def code(value: str, in_table: bool = False) -> str:
    """An id as a code span; a ``|`` is escaped inside a table cell."""
    value = " ".join(value.split())
    fence = "``" if "`" in value else "`"
    pad = " " if fence == "``" else ""
    out = f"{fence}{pad}{value}{pad}{fence}"
    return out.replace("|", "\\|") if in_table else out


def scalar(value: Any) -> str:
    """A counts-only rendering of one value: number, boolean, token, else ``set``."""
    if value is None:
        return "not measured"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if math.isfinite(value) else "not measured"
    if isinstance(value, str) and TOKEN_RE.match(value):
        return f"`{value}`"
    return "set"


def operand(value: Any) -> str:
    """A maturity model operand: caller-authored, so a string prints in clear as a code span."""
    if isinstance(value, list):
        return "[" + ", ".join(operand(v) for v in value) + "]"
    return code(value) if isinstance(value, str) else scalar(value)


def expression(criterion: Criterion | CriterionOutcome) -> str:
    return f"{code(criterion['signal'])} {_OPS[criterion['op']]} {operand(criterion['value'])}"


# --- evaluation --------------------------------------------------------------


def _key(c: Criterion | CriterionOutcome) -> tuple[str, str, str]:
    return c["signal"], c["op"], repr(c["value"])


def evaluate_group(maturity_model: MaturityModel, group: str, rows: Sequence[FleetRow],
                   by_scope: Mapping[str, JsonDict]) -> list[RowResult]:
    """The maturity model result for one group, built exactly as ``fleet_groups`` builds it."""
    scope, settings = settings_for(group, by_scope)
    org_data = settings_signals(by_scope[scope] if scope else None, settings)
    result = evaluate(maturity_model, {"name": group, "settings": org_data,
                               "repos": {row["repo"]: repo_signals(row) for row in rows}})
    return result["rows"]


def _check_agrees(group: GroupRow, results: Sequence[RowResult]) -> None:
    recorded = {(r["id"], r["status"], r["level"]) for r in group["levels"]}
    for result in results:
        if (result["id"], result["status"], result["level"]) not in recorded:
            raise NextStepsError(
                f"group {group['group']!r} row {result['id']!r}: re-evaluation gives "
                f"{result['status']} {result['level']}, which groups.jsonl does not record; pass "
                "the --levels and --settings the fleet run used")
    if len(recorded) != len(results):
        raise NextStepsError(f"group {group['group']!r}: groups.jsonl has a different set of maturity model rows")


# --- one row -----------------------------------------------------------------


def _level_cell(result: RowResult, top: int) -> str:
    if result["status"] != "level" or result["level"] is None:
        return "unknown"
    return f"{result['level']} of {top}" + (" (top)" if result["level"] == top else "")


def _rule_cell(result: RowResult, settings_scope: str | None) -> str:
    aggregation = result.get("aggregation")
    if aggregation is None:
        return f"group settings {code(settings_scope, True)}" if settings_scope else "group settings"
    if aggregation["rule"] == "min":
        return "every repository (min)"
    return f"{aggregation['share'] * 100:g}% of repositories (share)"


def _needed(result: RowResult, total: int) -> int:
    aggregation = result.get("aggregation")
    share = Fraction(str(aggregation["share"])) if aggregation else Fraction(1)
    return max(1, math.ceil(share * total))


def _criteria_upto(row: Row, target: int) -> list[Criterion]:
    seen: set[tuple[str, str, str]] = set()
    out: list[Criterion] = []
    for level in row["levels"][:target]:
        for criterion in level["criteria"]:
            if _key(criterion) not in seen:
                seen.add(_key(criterion))
                out.append(criterion)
    return out


def _outcomes(evaluation: Evaluation | RowResult, target: int) -> dict[tuple[str, str, str], CriterionOutcome]:
    out: dict[tuple[str, str, str], CriterionOutcome] = {}
    for level in evaluation.get("levels", [])[:target]:
        for outcome in level["unmet"]:
            out.setdefault(_key(outcome), outcome)
    return out


def _step(number: int, criterion: Criterion, where: Sequence[str],
          unmet_parts: Sequence[str] = ()) -> list[str]:
    words = guidance(criterion["signal"], criterion["op"], criterion["value"], unmet_parts)
    if words is None:
        return [f"{number}. **Meet {expression(criterion)}.** No repository-native wording exists "
                "for this signal.",
                f"   - Done when: the next fleet run reads {expression(criterion)}.",
                *where]
    return [f"{number}. **{words['change']}**",
            f"   - Done when: {words['done_when']}",
            f"   - Re-run check (optional): the next fleet run reads {expression(criterion)}.",
            *where]


def _team_steps(row: Row, result: RowResult, target: int, failed: Mapping[str, str],
                signals: Mapping[str, JsonDict], report_unmeasured: bool = True) -> list[str]:
    evaluations = result.get("repos", {})
    below = sorted(name for name, ev in evaluations.items() if ev["bounds"][0] < target)
    per_repo = {name: _outcomes(evaluations[name], target) for name in below}
    lines: list[str] = []
    number = 0
    blocking: set[str] = set()
    for criterion in _criteria_upto(row, target):
        hits = [(name, per_repo[name][_key(criterion)]) for name in below
                if _key(criterion) in per_repo[name] and per_repo[name][_key(criterion)]["status"] == "unmet"]
        if not hits:
            continue
        number += 1
        blocking.update(name for name, _ in hits)
        found = {name: unmet_parts_of(criterion["signal"], signals.get(name, {})) for name, _ in hits}
        listed = ", ".join(
            f"{code(name)} (now {scalar(o['actual'])}"
            + (f"; unmet: {', '.join(code(p) for p in found[name])})" if found[name] else ")")
            for name, o in hits)
        union = sorted({p for found_parts in found.values() for p in found_parts})
        lines += _step(number, criterion, [f"   - Holding the group back ({len(hits)}): {listed}."], union)
    unmeasured = [name for name in below if name not in blocking]
    if unmeasured and report_unmeasured:
        parts = []
        for name in unmeasured:
            if name in failed:
                parts.append(f"{code(name)} (scan failed: {code(failed[name])})")
            else:
                missing = sorted({o["signal"] for o in per_repo[name].values() if o["status"] == "unknown"})
                parts.append(f"{code(name)} (not measured: {', '.join(code(s) for s in missing)})")
        lines += ["", f"Below level {target} with no unmet criterion found, because a signal was "
                      f"not measured ({len(unmeasured)}): {', '.join(parts)}."]
    return lines


def _org_steps(row: Row, result: RowResult, target: int, scope: str | None, repos: int) -> list[str]:
    outcomes = _outcomes(result, target)
    where = f"   - Applies to: group settings {code(scope or '')}, covering {_repos(repos)}."
    lines: list[str] = []
    number = 0
    for criterion in _criteria_upto(row, target):
        outcome = outcomes.get(_key(criterion))
        if outcome is None or outcome["status"] != "unmet":
            continue
        number += 1
        lines += _step(number, criterion, [f"   - Now: {scalar(outcome['actual'])}.", where])
    return lines


def _repos(count: int) -> str:
    return f"{count} repository" if count == 1 else f"{count} repositories"


def _rolled(result: RowResult, top: int, bound: int) -> int:
    """The team roll-up of the repositories' low (0) or high (1) bounds, as lib.levels rolls it."""
    evaluations = result.get("repos", {})
    levels = [ev["bounds"][bound] for ev in evaluations.values()]
    best = 0
    for candidate in range(1, top + 1):
        if levels and sum(1 for lv in levels if lv >= candidate) >= _needed(result, len(levels)):
            best = candidate
    return best


def _unknown_section(row: Row, result: RowResult, settings_scope: str | None,
                     failed: Mapping[str, str], signals: Mapping[str, JsonDict]) -> list[str]:
    gap = "This is a measurement gap, not a failure: the run could not measure every signal the level depends on."
    if result["scope"] == "org":
        if settings_scope is None:
            return [f"Level unknown. {gap}", "",
                    "- No ghreport settings matched this group: pass ghreport's settings to the fleet "
                    "run with `--settings`."]
        return [f"Level unknown. {gap}", ""] + [
            f"- {code(signal)}: not measured. {measure_hint(signal)}" for signal in result["missing_signals"]]
    top = len(row["levels"])
    evaluations = result.get("repos", {})
    low, high = _rolled(result, top, 0), _rolled(result, top, 1)
    span = f" (between {low} and {high} of {top})" if evaluations else ""
    lines = [f"Level unknown{span}. {gap}", ""]
    for signal in result["missing_signals"]:
        names = sorted(n for n, ev in evaluations.items() if signal in ev["missing_signals"])
        hint = ("The repository scan failed in each of them; the reason token is listed below."
                if all(n in failed for n in names) else measure_hint(signal))
        lines.append(f"- {code(signal)}: not measured in {len(names)} of {_repos(len(evaluations))}. {hint}")
    undetermined = result.get("unknown_repos", [])
    if undetermined:
        parts = [f"{code(n)} (scan failed: {code(failed[n])})" if n in failed else code(n)
                 for n in undetermined]
        lines.append(f"- Repositories whose own level is undetermined ({len(parts)}): {', '.join(parts)}.")
    if evaluations and high < top:
        steps = _team_steps(row, result, high + 1, failed, signals, report_unmeasured=False)
        if steps:
            lines += ["", f"Measured unmet criteria already cap the group at level {high}, whatever the "
                          f"missing signals show. Toward level {high + 1}:", ""] + steps
    return lines


def _row_section(row: Row, result: RowResult, group: GroupRow, failed: Mapping[str, str],
                 signals: Mapping[str, JsonDict]) -> list[str]:
    top = len(row["levels"])
    lines = ["", f"## {text(row['title'])}", ""]
    level = result["level"]
    if result["status"] != "level" or level is None:
        return lines + _unknown_section(row, result, group["settings_scope"], failed, signals)
    if level >= top:
        return lines + [f"Level {top} of {top}: the top level. No next step."]
    target = level + 1
    if result["scope"] == "team":
        evaluations = result.get("repos", {})
        reached = sum(1 for ev in evaluations.values() if ev["bounds"][0] >= target)
        lines.append(f"Level {level} of {top}. To reach level {target}: {reached} of "
                     f"{_repos(len(evaluations))} already there; the rule needs "
                     f"{_needed(result, len(evaluations))}.")
        lines.append("")
        return lines + _team_steps(row, result, target, failed, signals)
    lines += [f"Level {level} of {top}. To reach level {target}:", ""]
    return lines + _org_steps(row, result, target, group["settings_scope"], group["repos"])


# --- pages -------------------------------------------------------------------


def group_page(maturity_model: MaturityModel, group: GroupRow, rows: Sequence[FleetRow],
               results: Sequence[RowResult]) -> str:
    """One group's page."""
    failed = {row["repo"]: row["reason"] or "failed" for row in rows if row["status"] != STATUS_OK}
    signals = {row["repo"]: repo_signals(row) for row in rows}
    lines = [f"# Next steps: {code(group['group'])}", "",
             f"Maturity model {code(maturity_model['version'])}. {_repos(group['repos'])}: "
             f"{group['scanned']} scanned, {group['failed']} failed.", "",
             "Each numbered step is one change: take or leave it on its own. \"Done when\" is "
             "checkable in the repository or its platform settings; the re-run check is optional.",
             "", "## Levels", "", "| Row | Level | Roll-up |", "|-----|-------|---------|"]
    for row, result in zip(maturity_model["rows"], results):
        lines.append(f"| {text(row['title'])} | {_level_cell(result, len(row['levels']))} | "
                     f"{_rule_cell(result, group['settings_scope'])} |")
    for row, result in zip(maturity_model["rows"], results):
        lines += _row_section(row, result, group, failed, signals)
    return "\n".join(lines) + "\n"


def page_path(group: str) -> Path:
    """``teams/<group>.md`` with the id's segments as directories; refuses an escaping id."""
    parts = group.split("/")
    if any(p in ("", ".", "..") or "\\" in p or p.startswith(".") for p in parts):
        raise NextStepsError(f"group id {group!r} cannot name a page path")
    return Path(TEAMS_DIR, *parts[:-1], f"{parts[-1]}.md")


def index_page(maturity_model: MaturityModel, groups: Sequence[GroupRow]) -> str:
    lines = ["# Team next steps", "",
             f"Maturity model {code(maturity_model['version'])}: {len(maturity_model['rows'])} rows. One page per group.", "",
             "| Group | Repositories | Rows at top level | Rows with next steps | Rows unknown |",
             "|-------|--------------|-------------------|----------------------|--------------|"]
    tops = {row["id"]: len(row["levels"]) for row in maturity_model["rows"]}
    for group in groups:
        known = [r for r in group["levels"] if r["status"] == "level" and r["level"] is not None]
        top = sum(1 for r in known if r["level"] == tops.get(r["id"]))
        unknown = len(group["levels"]) - len(known)
        link = quote(page_path(group["group"]).as_posix())
        lines.append(f"| [{code(group['group'], True)}]({link}) | {group['repos']} | {top} | "
                     f"{len(known) - top} | {unknown} |")
    return "\n".join(lines) + "\n"


def build_pages(maturity_model: MaturityModel, repos: Iterable[FleetRow], groups: Sequence[GroupRow],
                by_scope: Mapping[str, JsonDict]) -> dict[str, str]:
    """Every page as ``{relative path: Markdown}``, checked against ``groups.jsonl``."""
    members: dict[str, list[FleetRow]] = {}
    for row in repos:
        members.setdefault(row["group"], []).append(row)
    if sorted(members) != sorted(g["group"] for g in groups):
        raise NextStepsError("repos.jsonl and groups.jsonl name different groups; they are not one run")
    pages: dict[str, str] = {INDEX_FILE: index_page(maturity_model, groups)}
    for group in groups:
        if group["levels_version"] is None:
            raise NextStepsError("the fleet run had no --levels, so groups.jsonl holds no levels; "
                                 "rerun fleet.py with --levels")
        if group["levels_version"] != maturity_model["version"]:
            raise NextStepsError(
                f"groups.jsonl was rolled up with maturity model {group['levels_version']!r}, not "
                f"{maturity_model['version']!r}; pass the --levels the fleet run used")
        rows = sorted(members[group["group"]], key=lambda r: r["repo"])
        results = evaluate_group(maturity_model, group["group"], rows, by_scope)
        _check_agrees(group, results)
        pages[page_path(group["group"]).as_posix()] = group_page(maturity_model, group, rows, results)
    return pages


def clear_pages(out_dir: Path) -> None:
    """Remove the ``*.md`` pages under ``<out>/teams/`` and the directories that empties."""
    teams = out_dir / TEAMS_DIR
    if not teams.is_dir() or teams.is_symlink():
        return
    for path in teams.rglob("*.md"):
        if path.is_file() and not path.is_symlink():
            path.unlink()
    for directory in sorted((d for d in teams.rglob("*") if d.is_dir() and not d.is_symlink()),
                            key=lambda d: len(d.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass


def write_pages(out_dir: Path, pages: Mapping[str, str]) -> list[Path]:
    """Write every page under ``out_dir`` (after ``clear_pages``); returns the paths written."""
    clear_pages(out_dir)
    root = out_dir.resolve()
    written: list[Path] = []
    for relative in sorted(pages):
        target = out_dir / relative
        if not target.resolve().is_relative_to(root):
            raise NextStepsError(f"page {relative} resolves outside {out_dir}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(pages[relative], encoding="utf-8", newline="\n")
        written.append(target)
    return written
