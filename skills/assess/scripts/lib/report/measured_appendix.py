"""The "How this was measured" report section: which scans ran, which were skipped and why (#794).

``measured_section(documents)`` takes one ``{block name: block}`` mapping per repository
(a fleet row's ``scans``, or a full ``run-context.json``) and lists each scan name the
headless runner accepts (``signals.known_scans()``):

- **Ran**: the scan produced its block in at least one repository. Where a block reports
  itself unavailable, the line says in how many repositories and gives the reason codes;
  where it was skipped in some, it says in how many and why: the skip codes, or the
  ``absent`` words (#918).
- **Skipped**: the scan did not run in any repository, with the reason: only a full
  ``/assess`` run builds it (``not_registered``), it needs the network
  (``network``, with or without a ``--platform`` remedy), a ``--profile`` or ``--levels``
  run's scan plan left it out ("not run under profile citizen", #1268), it exists only on the platform
  (``signals.PLATFORM_ONLY_SCANS``), or a ``--scans`` selection left it out.

The section opens with an ``In place:`` line (``vocabulary.IN_PLACE``, #743) and names the
catalog, ``docs/what-we-measure.md``, by absolute URL because a report is written outside
the repository. Pure and deterministic; reasons are reduced to
their codes (``counts_only.reason_code``), so counts-only and full rows read alike.

`measured_section(documents, names=None, absent=...)`: the "How this was measured" section. One
`{block: block}` mapping per repository (a fleet row's `scans`, or a run-context); for each
known scan, **Ran** when its block exists without a `not_registered` or `network` reason in at
least one repository (with "not measured in k of n" and the reason codes where the block is
unavailable), else **Skipped** with why (not run under a `--profile` / `--levels` scan plan, #1268;
full `/assess` only, needs the network with or without
a `--platform` remedy, platform only, or `absent`: "not selected by --scans" for a fleet,
`assess_report.py` instead passes `absent_code="no_block"`: a full run runs every scan, so a
missing block, such as `structure_drift` with no ownership map, is listed under Ran as not
measured). Its first line starts `In place:`
(`vocabulary.IN_PLACE`) and says how many scans were measured everywhere, ran but were not
measured everywhere, or were skipped. It links `docs/what-we-measure.md` by absolute URL. The fleet `report.md` ends with it; the deterministic `/assess` report too.
The `agent_ops` line adds "permissions set centrally (managed settings on this machine) in k
of n" where a run read them under `--managed-settings` and they credited the practice (#1153).
The skip codes are `signals.FULL_RUN_ONLY_REASONS` plus the scan plan's not-run code, imported,
never listed (#918). Tests: `tests/test_measured_appendix.py`.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from lib.counts_only.content_free import reason_code
from lib.run.scan_plan import NOT_RUN_CODE, not_run_text
from lib.run.signals import (FULL_RUN_ONLY_REASONS, NETWORK_REASON, PLATFORM_ONLY_SCANS, PLATFORM_SCANS,
                             UNREGISTERED_REASON, known_scans)
from lib.vocabulary import IN_PLACE

HEADING = "## How this was measured"
CATALOG_URL = "https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/what-we-measure.md"
# Reason codes meaning the scan did not run at all: the full-run-only ones (signals.FULL_RUN_ONLY_REASONS,
# #918), and a --profile or --levels run's plan leaving the scan out (lib/run/scan_plan.py, #1268).
NETWORK_CODE = reason_code(NETWORK_REASON)
UNREGISTERED_CODE = reason_code(UNREGISTERED_REASON)
SKIP_CODES = FULL_RUN_ONLY_REASONS | {NOT_RUN_CODE}


def _skip_reason(name: str, codes: set[str], absent: str, not_run: str | None = None) -> str:
    if not_run is not None:
        return not_run
    if NETWORK_CODE in codes:
        if name in PLATFORM_SCANS:
            return "it reads GitHub or GitLab; run with --platform to measure it"
        return "it reads GitHub or GitLab, which only a full /assess run does"
    if UNREGISTERED_CODE in codes:
        return "only a full /assess run builds it"
    if name in PLATFORM_ONLY_SCANS:
        return "it exists only on the platform; run with --platform to measure it"
    return absent


def _skip_word(state: str | None, block: object, absent: str) -> str:
    """Why one repository skipped a scan: the ``absent`` words, a scan plan's own words
    ("not run under profile citizen", as a whole-skip line reads), or the reason code."""
    if state == "absent":
        return absent
    return not_run_text(block) or str(state)


def _scans(n: int) -> str:
    return "scan was" if n == 1 else "scans were"


# The evidence line for a permission rule read from this machine's managed settings (#1153).
CENTRAL = "set centrally (managed settings on this machine)"


def _central(name: str, documents: Sequence[Mapping[str, object]]) -> int:
    """How many documents credit ``agent_ops`` permissions from managed settings."""
    if name != "agent_ops":
        return 0
    return sum(1 for doc in documents
               if isinstance(block := doc.get(name), Mapping)
               and isinstance(summary := block.get("summary"), Mapping)
               and summary.get("permissions_source") == "managed")


def _state(block: object) -> str | None:
    """None when the block was measured, else its reason code."""
    if isinstance(block, Mapping) and block.get("available") is False:
        return reason_code(block.get("reason"))
    return None


def measured_section(documents: Sequence[Mapping[str, object]],
                     names: Sequence[str] | None = None,
                     absent: str = "not selected by --scans",
                     absent_code: str | None = None) -> str:
    """The section's Markdown (heading first, no trailing blank line). ``absent`` is the
    reason for a scan whose block no document holds (a fleet's ``--scans`` selection).
    With ``absent_code`` a missing block means the scan ran and wrote nothing (a full run,
    where every scan runs): it is listed under Ran, not measured, with that code."""
    names = known_scans() if names is None else names
    total = len(documents)
    link = (f"Every check is described in [docs/what-we-measure.md]({CATALOG_URL}). A scan that "
            "was skipped, or could not read its input, earns nothing and costs nothing.")
    if total == 0:
        return "\n".join([HEADING, "", f"{IN_PLACE} the catalog of every check. No repository was "
                                       f"scanned, so no scan ran. {link}"])
    ran: list[str] = []
    skipped: list[str] = []
    unread = 0  # scans that ran but were unavailable or skipped in at least one repository
    for name in names:
        states = [_state(doc[name]) if name in doc else (absent_code or "absent") for doc in documents]
        run = [s for s in states if s not in SKIP_CODES and s != "absent"]
        if not run:
            codes = {s for s in states if s is not None}
            not_run = next((t for doc in documents if (t := not_run_text(doc.get(name)))), None)
            skipped.append(f"- `{name}`: {_skip_reason(name, codes, absent, not_run)}.")
            continue
        unmeasured = sorted({s for s in run if s is not None})
        missing = sum(1 for s in run if s is not None)
        line = f"- `{name}`"
        if missing:
            line += f": not measured in {missing} of {total} ({', '.join(unmeasured)})"
        if len(run) < total:
            why = sorted({_skip_word(s, doc.get(name), absent) for doc, s in zip(documents, states)
                          if s in SKIP_CODES or s == "absent"})
            line += f"{';' if missing else ':'} skipped in {total - len(run)} of {total} ({', '.join(why)})"
        if missing or len(run) < total:
            unread += 1
        if central := _central(name, documents):
            line += f"{';' if ':' in line else ':'} permissions {CENTRAL} in {central} of {total}"
        ran.append(line)
    scope = "1 repository" if total == 1 else f"{total} repositories"
    measured = len(ran) - unread
    headline = (f"{IN_PLACE} over {scope}, {measured} {_scans(measured)} measured everywhere, {unread} "
                f"ran but {'was' if unread == 1 else 'were'} not measured everywhere, and "
                f"{len(skipped)} {'was' if len(skipped) == 1 else 'were'} skipped.")
    lines = [HEADING, "", f"{headline} {link}",
             "", f"### Ran ({len(ran)})", "", *(ran or ["- none"]),
             "", f"### Skipped ({len(skipped)})", "", *(skipped or ["- none"])]
    return "\n".join(lines)
