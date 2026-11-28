"""Per-practice not-measured counts, split by reason, for the fleet views (issue #1350).

``opportunities.in_place`` (``lib/fleet/fleet_opportunities.py``) counts per ``team`` row the
measured repositories and those holding the row's top level. A practice no measured repository
holds is not "in place nowhere" while other repositories read it unknown: the scan could not see
them. ``not_measured`` counts the scanned repositories whose level on the row is unknown, and
``reasons`` splits them by the reason code of the row's lowest not-measured condition in each
repository's level result (``included_jobs`` or ``ci_includes_unresolved`` for CI in an include the
scan did not read, ``needs_platform``, ``no_value`` when no result names one), sorted by count
then code, so a split such as 21 of 27 reads as such.

``absence_line`` is the report sentence for such a practice: "not found at its top level in N
measured repositories; M not measured (code count, ...)" when any repository reads unknown, and
"not in place at its top level in any of the N measured repositories" only when none does. A
repository may hold the practice at a lower level, so the line names the top level. ``report.md``'s "What's working
across the fleet" lists these lines (``lib/fleet/fleet_opportunities_section.py``), and the fleet
report writer follows the same rule (``skills/fleet/references/report-and-diagrams.md``).

Every value is a count or a reason code, so counts-only output keeps it. Imports
``lib.scoring.level_result_types`` only. Tests: ``tests/test_fleet_not_measured.py``.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import TypedDict

from lib.scoring.level_result_types import LevelResult

NO_VALUE = "no_value"  # a repository whose level result names no not-measured condition on the row


class ReasonCount(TypedDict):
    reason: str  # the condition's reason code
    count: int  # repositories not measured on the row for that reason


class InPlace(TypedDict):
    """One ``opportunities.in_place`` entry (re-exported by ``lib.fleet.fleet_opportunities``)."""

    row: str
    at_top: int  # measured repositories whose level result holds the row at its top level (#1470)
    measured: int  # repositories with a known level on the row
    not_measured: int  # scanned repositories whose level on the row is unknown (#1350)
    not_measured_reasons: list[ReasonCount]  # those, by reason code (#1350)


def row_reason(row: str, result: LevelResult | None) -> str:
    """The reason code of ``row``'s lowest not-measured condition in ``result``; ``NO_VALUE`` without one."""
    found = sorted((c["level"], c.get("reason", NO_VALUE)) for c in (result["conditions"] if result else [])
                   if c["row"] == row and c["state"] == "not_measured")
    return found[0][1] if found else NO_VALUE


def reasons(row: str, unknown: Iterable[str], results: Mapping[str, LevelResult | None]) -> list[ReasonCount]:
    """The not-measured repositories ``unknown`` on ``row``, counted by reason (most first, then code)."""
    tally = Counter(row_reason(row, results.get(repo)) for repo in unknown)
    return [{"reason": code, "count": n} for code, n in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))]


def _repos(n: int) -> str:
    return f"{n} measured {'repository' if n == 1 else 'repositories'}"


def absence_line(p: InPlace, title: str) -> str:
    """The report line for a practice no measured repository holds at its top level."""
    if not p["not_measured"]:
        where = "the 1 measured repository" if p["measured"] == 1 else f"any of the {_repos(p['measured'])}"
        return f"- {title}: not in place at its top level in {where}."
    split = ", ".join(f"`{r['reason']}` {r['count']}" for r in p["not_measured_reasons"])
    return (f"- {title}: not found at its top level in {_repos(p['measured'])}; {p['not_measured']} not measured "
            f"({split}).")
