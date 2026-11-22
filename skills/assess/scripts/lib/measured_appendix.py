"""The "How this was measured" report section: which scans ran, which were skipped and why (#794).

``measured_section(documents)`` takes one ``{block name: block}`` mapping per repository
(a fleet row's ``scans``, or a full ``run-context.json``) and lists each scan name the
headless runner accepts (``signals.known_scans()``):

- **Ran**: the scan produced its block in at least one repository. Where a block reports
  itself unavailable, the line says in how many repositories and gives the reason codes.
- **Skipped**: the scan did not run in any repository, with the reason: only a full
  ``/assess`` run builds it (``not_registered``), it needs the network
  (``network``, with or without a ``--platform`` remedy), it exists only on the platform
  (``signals.PLATFORM_ONLY_SCANS``), or a ``--scans`` selection left it out.

The section opens with an ``In place:`` line (``vocabulary.IN_PLACE``, #743) and names the
catalog, ``docs/what-we-measure.md``, by absolute URL because a report is written outside
the repository. Pure and deterministic; reasons are reduced to
their codes (``counts_only.reason_code``), so counts-only and full rows read alike.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from lib.counts_only import reason_code
from lib.signals import PLATFORM_ONLY_SCANS, PLATFORM_SCANS, known_scans
from lib.vocabulary import IN_PLACE

HEADING = "## How this was measured"
CATALOG_URL = "https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/what-we-measure.md"
# Reason codes meaning the scan did not run at all (signals.UNREGISTERED_REASON, NETWORK_REASON).
SKIP_CODES = frozenset({"not_registered", "network"})


def _skip_reason(name: str, codes: set[str], absent: str) -> str:
    if "network" in codes:
        if name in PLATFORM_SCANS:
            return "it reads GitHub or GitLab; run with --platform to measure it"
        return "it reads GitHub or GitLab, which only a full /assess run does"
    if "not_registered" in codes:
        return "only a full /assess run builds it"
    if name in PLATFORM_ONLY_SCANS:
        return "it exists only on the platform; run with --platform to measure it"
    return absent


def _scans(n: int) -> str:
    return "scan was" if n == 1 else "scans were"


def _state(block: object) -> str | None:
    """None when the block was measured, else its reason code."""
    if isinstance(block, Mapping) and block.get("available") is False:
        return reason_code(block.get("reason"))
    return None


def measured_section(documents: Sequence[Mapping[str, object]],
                     names: Sequence[str] | None = None,
                     absent: str = "not selected by --scans") -> str:
    """The section's Markdown (heading first, no trailing blank line). ``absent`` is the
    reason for a scan whose block no document holds (a fleet's ``--scans`` selection)."""
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
        states = [_state(doc[name]) if name in doc else "absent" for doc in documents]
        run = [s for s in states if s not in SKIP_CODES and s != "absent"]
        if not run:
            codes = {s for s in states if s is not None}
            skipped.append(f"- `{name}`: {_skip_reason(name, codes, absent)}.")
            continue
        unmeasured = sorted({s for s in run if s is not None})
        missing = sum(1 for s in run if s is not None)
        line = f"- `{name}`"
        if missing:
            line += f": not measured in {missing} of {total} ({', '.join(unmeasured)})"
        if len(run) < total:
            line += f"{';' if missing else ':'} skipped in {total - len(run)} of {total}"
        if missing or len(run) < total:
            unread += 1
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
