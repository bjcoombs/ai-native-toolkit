"""Per-team next-steps pages in Markdown from one fleet run (issue #571).

Reads a fleet run's output (``repos.jsonl`` and ``groups.jsonl``, through
``lib/fleet/fleet_view.py``) plus the maturity model and settings the run used, and renders
one page per group, with no rescan:

- the group's level on every maturity model row, with the roll-up rule;
- for each row below its top level, the unmet criteria of the next level, one
  numbered step per criterion, so a team can take or leave each on its own.
  Each step leads with the change and a "done when" in the repository's own
  terms (``lib/next_step_guidance.py``); the signal expression follows as the
  optional re-run check. Between the two, a "Why:" line gives the check manifest's why
  for that criterion at its level (``lib.level_tally.criterion_why``, #1128); a criterion
  with no manifest entry (a caller's own model) gets no Why line, never an empty one. A team row names the repositories holding the group
  back, each with its current value; an org row names the settings scope it
  applies to;
- for an ``unknown`` row, the signals that could not be measured and why: a
  measurement gap, not a setback. A row with a confirmed floor (#697) reads
  "at least N", and a signal only a full ``/assess`` measures (its block
  unavailable as ``not_registered`` or ``network`` in every repository missing
  it, ``fleet_groups.full_run_only_signals``) says so instead of a scan hint; a
  ``PLATFORM_SCANS`` signal unmeasured as ``network`` (or in a
  ``PLATFORM_ONLY_SCANS`` block, absent without ``--platform``) says ``fleet
  --platform`` measures it (``PLATFORM_HINT``).

Levels come from re-running ``lib.levels.evaluate`` on the same inputs the
roll-up (``lib/fleet/fleet_groups.py``) evaluated: each repository's ``scans`` and
the group's joined settings fragment. ``build_pages`` checks every row's
status and level against ``groups.jsonl`` and raises ``NextStepsError`` on any
difference (a different maturity model or settings than the run used), so a page never
disagrees with the run.

Counts-only: a page holds repository and group ids, maturity model titles and
operands (caller-authored, printed in clear), counts and the actual values of
criteria. An actual value is a number, a boolean, a short token (``TOKEN_RE``) or ``set``
for any other string, the same reduction ``groups.jsonl`` applies to settings.

Every heading on a page opens with a line starting ``In place:``
(``lib/vocabulary.IN_PLACE``, #743) that says what the group already has before any
next step; at level 0 or unknown it says so plainly and never implies credit.

Output is deterministic: rows in maturity model order, repositories sorted, no date.
``write_pages`` writes ``<out>/teams.md`` (the index) and
``<out>/teams/<group>.md`` (a group id's segments as directories; a hashed id, #909,
is ``teams/h-<hex>.md``), after
removing the ``*.md`` pages a previous run left under ``<out>/teams/``.

The per-team next-steps pages behind `skills/fleet/scripts/fleet_next_steps.py`
(issue #571). Every heading opens with an `In place:` line (`vocabulary.IN_PLACE`, #743). `build_pages` re-runs `levels.evaluate` per group on exactly the inputs
`fleet_groups` evaluated (`repo_signals`, `settings_for`, `settings_signals`), then
checks each row's status and level against `groups.jsonl` and raises
`NextStepsError` on any difference (a different `--levels` or `--settings`, or files
from two runs). Each page: a levels table, then per row the next level's unmet
criteria, collected cumulatively over levels 1..next (under `share` a repository can
sit below the group level), one numbered step per criterion. A team step lists the
repositories where that criterion is definitely unmet, with the current value; a
repository below the next level with no unmet criterion is listed apart as not
measured (a failed scan with its reason token). An org step names the settings scope.
An `unknown` row (keyed on `status`, not on `missing_signals`) names each missing
signal, how many repositories lack it and how to measure it, the rolled-up bounds,
and the criteria measured repositories already fail above the high bound. Values
print counts-only (`scalar`: numbers, booleans, `TOKEN_RE` tokens, else `set`);
maturity model titles are escaped (`text`), ids are code spans (`code`). `write_pages` writes
`teams.md` and `teams/<group>.md` after `clear_pages` removes the previous run's
`*.md` pages, and refuses a path that resolves outside the output directory;
`page_path` refuses an id with an empty, dot or backslash segment. Golden pages:
`tests/fixtures/fleet_next_steps/expected/` (regeneration command in the test
module's docstring).
A row with a floor (#697) reads "at least N of top" in the levels table and "Level at least N" in its section; a full-run-only signal gets `FULL_RUN_HINT` in place of a scan hint.
"""
from __future__ import annotations

import math
import re
from urllib.parse import quote
from collections.abc import Iterable, Mapping, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any

from lib.counts_only.content_free import hash_value, is_hashed
from lib.counts_only.counts_only_rules import TOKEN_RE
from lib.fleet.runner import STATUS_OK, FleetRow
from lib.fleet.fleet_groups import (GroupRow, full_run_only_signals, platform_signals, repo_signals, settings_for,
                              settings_signals)
from lib.level_tally import criterion_why
from lib.next_step_guidance import guidance, measure_hint
from lib.next_step_guidance import parts as unmet_parts_of
from lib.levels import Criterion, CriterionOutcome, Evaluation, Row, RowResult, MaturityModel, evaluate, floor_of
from lib.run_context_types import JsonDict
from lib.vocabulary import IN_PLACE

TEAMS_DIR = "teams"
INDEX_FILE = "teams.md"

_OPS = {"eq": "=", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">=",
        "in": "in", "not_in": "not in"}
FULL_RUN_HINT = ("Only a full `/assess` run measures it: the headless fleet run reports its block "
                 "unavailable (`not_registered`, or `network` outside the `--platform` scans), so "
                 "this is not a gap a scan option closes.")
PLATFORM_HINT = ("Measured with `fleet --platform`: the run made no network calls, so its block "
                 "reads `network` (or, for `delivery`, is absent); this is a gap a scan option "
                 "does close, with no full `/assess` needed.")
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
        floor = floor_of(result)
        return "unknown" if floor is None else f"at least {floor} of {top} (higher unknown)"
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


def _criteria_upto(row: Row, target: int) -> list[tuple[int, Criterion]]:
    """Each criterion of levels 1..target once, with the level it first appears at."""
    seen: set[tuple[str, str, str]] = set()
    out: list[tuple[int, Criterion]] = []
    for level in row["levels"][:target]:
        for criterion in level["criteria"]:
            if _key(criterion) not in seen:
                seen.add(_key(criterion))
                out.append((level["level"], criterion))
    return out


def _outcomes(evaluation: Evaluation | RowResult, target: int) -> dict[tuple[str, str, str], CriterionOutcome]:
    out: dict[tuple[str, str, str], CriterionOutcome] = {}
    for level in evaluation.get("levels", [])[:target]:
        for outcome in level["unmet"]:
            out.setdefault(_key(outcome), outcome)
    return out


def _step(number: int, criterion: Criterion, where: Sequence[str],
          unmet_parts: Sequence[str] = (), why: str | None = None) -> list[str]:
    # an eq_signal criterion's value is a second path, not an operand to set (lib/levels_fields.py)
    words = None if "eq_signal" in criterion else guidance(
        criterion["signal"], criterion["op"], criterion["value"], unmet_parts)
    because = [f"   - Why: {text(why)}"] if why else []  # no manifest why: no line (#1128)
    if words is None:
        return [f"{number}. **Meet {expression(criterion)}.** No repository-native wording exists "
                "for this signal.",
                *because,
                f"   - Done when: the next fleet run reads {expression(criterion)}.",
                *where]
    return [f"{number}. **{words['change']}**",
            *because,
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
    for level, criterion in _criteria_upto(row, target):
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
        lines += _step(number, criterion, [f"   - Holding the group back ({len(hits)}): {listed}."], union,
                       criterion_why(row["id"], level, criterion["signal"]))
    unmeasured = [name for name in below if name not in blocking]
    if unmeasured and report_unmeasured:
        parts = []
        for name in unmeasured:
            if name in failed:
                parts.append(f"{code(name)} (scan did not complete: {code(failed[name])})")
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
    for level, criterion in _criteria_upto(row, target):
        outcome = outcomes.get(_key(criterion))
        if outcome is None or outcome["status"] != "unmet":
            continue
        number += 1
        lines += _step(number, criterion, [f"   - Now: {scalar(outcome['actual'])}.", where],
                       why=criterion_why(row["id"], level, criterion["signal"]))
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
    gap = "This is a measurement gap: the run could not measure every signal the level depends on."
    floor = floor_of(result)
    if result["scope"] == "org":
        head = (f"{IN_PLACE} nothing confirmed on this row yet; the level is not measured" if floor is None
                else f"{IN_PLACE} at least level {floor}, confirmed; higher levels are not measured yet")
        if settings_scope is None:
            return [f"{head}. {gap}", "",
                    "- No ghreport settings matched this group: pass ghreport's settings to the fleet "
                    "run with `--settings`."]
        return [f"{head}. {gap}", ""] + [
            f"- {code(signal)}: not measured. {measure_hint(signal)}" for signal in result["missing_signals"]]
    top = len(row["levels"])
    evaluations = result.get("repos", {})
    low, high = _rolled(result, top, 0), _rolled(result, top, 1)
    span = f" (between {low} and {high} of {top} once measured)" if evaluations else ""
    head = (f"{IN_PLACE} nothing confirmed on this row yet; the level is not measured{span}" if floor is None
            else f"{IN_PLACE} at least level {floor} of {top}, confirmed; higher levels are not measured "
                 f"yet (up to {high})")
    lines = [f"{head}. {gap}", ""]
    only = set(full_run_only_signals(evaluations, signals))
    platform = set(platform_signals(evaluations, signals))
    for signal in result["missing_signals"]:
        names = sorted(n for n, ev in evaluations.items() if signal in ev["missing_signals"])
        hint = ("The repository scan did not complete in each of them; the reason token is listed below."
                if all(n in failed for n in names) else FULL_RUN_HINT if signal in only
                else PLATFORM_HINT if signal in platform else measure_hint(signal))
        lines.append(f"- {code(signal)}: not measured in {len(names)} of {_repos(len(evaluations))}. {hint}")
    undetermined = result.get("unknown_repos", [])
    if undetermined:
        parts = [f"{code(n)} (scan did not complete: {code(failed[n])})" if n in failed else code(n)
                 for n in undetermined]
        lines.append(f"- Repositories whose own level is undetermined ({len(parts)}): {', '.join(parts)}.")
    if evaluations and high < top:
        steps = _team_steps(row, result, high + 1, failed, signals, report_unmeasured=False)
        if steps:
            lines += ["", f"Measured unmet criteria already cap the group at level {high}, whatever the "
                          f"signals not measured yet show. Toward level {high + 1}:", ""] + steps
    return lines


def _held(level: int, top: int) -> str:
    """The opening line of a known row below its top: level 0 confirms nothing, so it says so."""
    if level == 0:
        return f"{IN_PLACE} nothing confirmed on this row yet (level 0 of {top})"
    return f"{IN_PLACE} level {level} of {top}"


def _row_section(row: Row, result: RowResult, group: GroupRow, failed: Mapping[str, str],
                 signals: Mapping[str, JsonDict]) -> list[str]:
    top = len(row["levels"])
    lines = ["", f"## {text(row['title'])}", ""]
    level = result["level"]
    if result["status"] != "level" or level is None:
        return lines + _unknown_section(row, result, group["settings_scope"], failed, signals)
    if level >= top:
        return lines + [f"{IN_PLACE} level {top} of {top}, the top level. No next step."]
    target = level + 1
    if result["scope"] == "team":
        evaluations = result.get("repos", {})
        reached = sum(1 for ev in evaluations.values() if ev["bounds"][0] >= target)
        lines.append(f"{_held(level, top)}; {reached} of {_repos(len(evaluations))} "
                     f"already at level {target}.")
        lines.append("")
        lines.append(f"To reach level {target}: the rule needs {_needed(result, len(evaluations))}.")
        lines.append("")
        return lines + _team_steps(row, result, target, failed, signals)
    lines += [f"{_held(level, top)}.", "", f"To reach level {target}:", ""]
    return lines + _org_steps(row, result, target, group["settings_scope"], group["repos"])


# --- pages -------------------------------------------------------------------


def group_page(maturity_model: MaturityModel, group: GroupRow, rows: Sequence[FleetRow],
               results: Sequence[RowResult]) -> str:
    """One group's page."""
    failed = {row["repo"]: row["reason"] or "failed" for row in rows if row["status"] != STATUS_OK}
    signals = {row["repo"]: repo_signals(row) for row in rows}
    tops = [len(row["levels"]) for row in maturity_model["rows"]]
    known = [(r["level"], top) for r, top in zip(results, tops) if r["status"] == "level" and r["level"] is not None]
    at_top = sum(1 for level, top in known if level == top)
    started = sum(1 for level, _ in known if level >= 1)
    rows_n = len(maturity_model["rows"])
    lines = [f"# Next steps: {code(group['group'])}", "",
             f"{IN_PLACE} {at_top} of {rows_n} rows at the top level and {started} at level 1 or above, "
             f"across {_repos(group['repos'])}.", "",
             f"Maturity model {code(maturity_model['version'])}. {_repos(group['repos'])}: "
             f"{group['scanned']} scanned, {group['failed']} could not be scanned.", "",
             "Each numbered step is one change: take or leave it on its own. \"Done when\" is "
             "checkable in the repository or its platform settings; the re-run check is optional.",
             "", "## Levels", "",
             f"{IN_PLACE} {len(known)} of {rows_n} rows measured for this group, each with the roll-up "
             "rule it uses.", "",
             "| Row | Level | Roll-up |", "|-----|-------|---------|"]
    for row, result in zip(maturity_model["rows"], results):
        lines.append(f"| {text(row['title'])} | {_level_cell(result, len(row['levels']))} | "
                     f"{_rule_cell(result, group['settings_scope'])} |")
    for row, result in zip(maturity_model["rows"], results):
        lines += _row_section(row, result, group, failed, signals)
    return "\n".join(lines) + "\n"


def page_path(group: str) -> Path:
    """``teams/<group>.md`` with the id's segments as directories; refuses an escaping id.
    A hashed group id (#909) is ``teams/h-<hex>.md``: no colon, which Windows refuses."""
    if is_hashed(group):
        return Path(TEAMS_DIR, f"{group.replace(':', '-')}.md")
    parts = group.split("/")
    if any(p in ("", ".", "..") or "\\" in p or p.startswith(".") for p in parts):
        raise NextStepsError(f"group id {group!r} cannot name a page path")
    return Path(TEAMS_DIR, *parts[:-1], f"{parts[-1]}.md")


def index_page(maturity_model: MaturityModel, groups: Sequence[GroupRow]) -> str:
    tops = {row["id"]: len(row["levels"]) for row in maturity_model["rows"]}
    reached = sum(1 for g in groups for r in g["levels"]
                  if r["status"] == "level" and r["level"] is not None and r["level"] == tops.get(r["id"]))
    lines = ["# Team next steps", "",
             f"{IN_PLACE} {len(groups)} {'group' if len(groups) == 1 else 'groups'} with a page, holding "
             f"{reached} {'row' if reached == 1 else 'rows'} at the top level between them.", "",
             f"Maturity model {code(maturity_model['version'])}: {len(maturity_model['rows'])} rows. One page per group.", "",
             "| Group | Repositories | Rows at top level | Rows with next steps | Rows unknown |",
             "|-------|--------------|-------------------|----------------------|--------------|"]
    for group in groups:
        known = [r for r in group["levels"] if r["status"] == "level" and r["level"] is not None]
        top = sum(1 for r in known if r["level"] == tops.get(r["id"]))
        unknown = len(group["levels"]) - len(known)
        link = quote(page_path(group["group"]).as_posix())
        lines.append(f"| [{code(group['group'], True)}]({link}) | {group['repos']} | {top} | "
                     f"{len(known) - top} | {unknown} |")
    return "\n".join(lines) + "\n"


def group_scopes(group: GroupRow, by_scope: Mapping[str, JsonDict],
                 salt: str | None) -> Mapping[str, JsonDict]:
    """The settings fragments ``group`` joins. A group hashed at the output boundary (#909)
    joins the fragment its recorded ``settings_scope`` names, through the run's salt."""
    if not is_hashed(group["group"]) or not by_scope:
        return by_scope
    if salt is None:
        raise NextStepsError("groups.jsonl names hashed groups; pass the --hash-salt the fleet run "
                             "used to join --settings (a --hash-paths run with no salt cannot)")
    hashed = {hash_value(scope, salt): fragment for scope, fragment in by_scope.items()}
    scope = group["settings_scope"]
    return {group["group"]: hashed[scope]} if scope is not None and scope in hashed else {}


def build_pages(maturity_model: MaturityModel, repos: Iterable[FleetRow], groups: Sequence[GroupRow],
                by_scope: Mapping[str, JsonDict], salt: str | None = None) -> dict[str, str]:
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
        results = evaluate_group(maturity_model, group["group"], rows, group_scopes(group, by_scope, salt))
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
