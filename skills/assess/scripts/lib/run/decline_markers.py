"""Read decline markers (``.assess/.no-<tool>``) with provenance.

A decline marker records that a user permanently declined an optional tool for
this repo - ``scc`` (coverage-extension for the treemap), a per-language
dead-code linter (``vulture`` / ``ts-prune`` / ``staticcheck``), or the bounded
mutation pass (``mutmut`` / ``stryker``). Historically each marker was an empty
``touch``-ed file: present-or-absent was the entire signal. That made the
decline a silent, provenance-free fact - the report could not disclose *who*
declined *what* and *when*, and a decline made against an old major version
outlived the tool changes that might have made it worth re-offering.

Markers are now JSON with provenance::

    {"declined_by": "ben", "declined_at": "2026-07-07",
     "plugin_version": "1.54.4", "reason": "pure-docs repo"}

``reason`` is optional. Legacy empty / non-JSON markers are still honoured as a
decline - they just carry no provenance (``declined_by`` / ``declined_at`` /
``version`` are ``None``), so they read as "declined by an unknown user on an
unknown date" rather than crashing the scan.

**Re-offer on a major bump.** A marker written under an older *major* plugin
version is stale enough that the tool's behaviour may have changed materially
since the user declined. Such a marker carries ``reoffer: True`` so the
orchestrator can re-ask once. When the user declines again permanently, the new
marker is stamped with the *current* version - its major now matches, so
``reoffer`` drops back to ``False`` and the re-offer does not repeat every run
within the same major (re-offer once per major). Legacy markers carry no
version, so they are never auto-re-offered (there is no major to compare).

The ``reoffer`` field is a pure staleness signal, set for *any* tool declined
under an older major - but only mutation tools are actually re-offered (SKILL.md
Step 2d); Step 2b never re-asks a linter decline. So the report's
"re-offer eligible" disclosure suffix is gated on ``MUTATION_TOOLS`` (see
:func:`_disclosure`), and ``reoffer_mutation`` keys off the same set - a stale
linter marker keeps ``reoffer: True`` for its own record without claiming a
re-offer that never comes.

Pure stdlib. Never raises out of :func:`read_decline_markers`.

Reads the `.assess/.no-<tool>` markers that record a permanent decline of an optional
tool (`scc`, a dead-code linter, or the `mutmut` / `stryker` mutation pass). Markers
are JSON carrying `declined_by` / `declined_at` / `plugin_version` / optional `reason`;
a legacy empty `touch`-ed marker is still honoured as a decline, with no provenance.
A marker written under an older plugin *major* carries `reoffer: True`, and
`build_decline_block` returns the run-context `decline_markers` block: the per-marker
provenance, `reoffer_mutation` (true only for a stale mutation-tool decline, the
SKILL.md Step 2d re-offer flag), and one-line `disclosures` so an active decline is
never invisible in the report. Legacy markers have no version and are never
re-offered. Pure stdlib; never raises. Called by `assess_core.py`.

**Declined backlog proposals (#813).** A reconcile proposal (close, reopen, demote or
supersede, `lib/report/backlog_reconcile.py`) the user declines is recorded in
`.assess/backlog-declines.json` (`{"declines": [...]}`) with the same provenance
(`declined_by`, `declined_at`, optional `reason`) plus what was turned down: the proposal's
subject (`kind`, `number`, `condition` or `finding`, `superseded_by`), its `evidence` line,
and the evidence it rested on, the condition's `state` and the model (`level.levels_version`).
`split_declined` holds back a proposal a live record covers. `live_proposal_declines` drops a
record once this run reads another model or another state for its condition, so the proposal
comes back once and a second decline records it again under the new evidence; a run that
cannot say (no level result, a condition its model does not list) keeps the record.
`proposal_disclosure` is the one line that keeps a held-back proposal visible in the summary.
Called by `assess_actions_render.py reconcile`.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict, TypeVar

MARKER_PREFIX = ".no-"

# Tools whose decline gates the consent-heavy bounded mutation pass. A stale
# decline for one of these is what ``reoffer_mutation`` keys off.
MUTATION_TOOLS = frozenset({"mutmut", "stryker"})


@dataclass
class DeclineMarker:
    """One parsed ``.no-<tool>`` marker."""

    path: str  # repo-root-relative, e.g. ".assess/.no-mutmut"
    tool: str  # e.g. "mutmut"
    declined_by: str | None
    declined_at: str | None
    version: str | None  # plugin_version recorded in the marker
    reason: str | None
    reoffer: bool  # marker major < current major (never True for legacy markers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "tool": self.tool,
            "declined_by": self.declined_by,
            "declined_at": self.declined_at,
            "version": self.version,
            "reason": self.reason,
            "reoffer": self.reoffer,
        }


def _major(version: str | None) -> int | None:
    """Extract the integer major component of a semver string, else None."""
    if not version:
        return None
    head = str(version).strip().lstrip("vV").split(".", 1)[0]
    try:
        return int(head)
    except ValueError:
        return None


def _parse_marker(path: Path, current_version: str) -> DeclineMarker:
    tool = path.name[len(MARKER_PREFIX):]
    declined_by: str | None = None
    declined_at: str | None = None
    version: str | None = None
    reason: str | None = None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        raw = ""
    if raw:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            declined_by = data.get("declined_by") or None
            declined_at = data.get("declined_at") or None
            version = data.get("plugin_version") or None
            reason = data.get("reason") or None

    marker_major = _major(version)
    current_major = _major(current_version)
    # Re-offer only when both majors are known and the marker's is older. A
    # legacy marker (no version) has no major, so it is never auto-re-offered.
    reoffer = (
        marker_major is not None
        and current_major is not None
        and marker_major < current_major
    )
    return DeclineMarker(
        path=path.name,
        tool=tool,
        declined_by=declined_by,
        declined_at=declined_at,
        version=version,
        reason=reason,
        reoffer=reoffer,
    )


def read_decline_markers(
    assess_dir: Path, current_version: str
) -> list[DeclineMarker]:
    """Scan ``assess_dir`` for ``.no-<tool>`` markers and parse each.

    Returns markers sorted by tool for a stable, byte-reproducible run-context.
    A missing or unreadable ``.assess`` directory yields an empty list; never
    raises.
    """
    try:
        if not assess_dir.is_dir():
            return []
        candidates = sorted(
            p
            for p in assess_dir.iterdir()
            if p.is_file()
            and p.name.startswith(MARKER_PREFIX)
            and len(p.name) > len(MARKER_PREFIX)
        )
    except OSError:
        return []
    markers = [_parse_marker(p, current_version) for p in candidates]
    markers.sort(key=lambda m: m.tool)
    return markers


def build_decline_block(
    assess_dir: Path, current_version: str
) -> dict[str, Any]:
    """Build the run-context ``decline_markers`` block plus derived flags.

    Returns a dict with:

    - ``markers``: list of per-marker provenance dicts.
    - ``reoffer_mutation``: True when a mutation-tool decline (``mutmut`` /
      ``stryker``) was written under an older major - the SKILL.md Step 2d
      re-offer flag.
    - ``disclosures``: human-readable one-liners the report surfaces so an
      active permanent decline is never invisible.
    """
    markers = read_decline_markers(assess_dir, current_version)
    reoffer_mutation = any(
        m.tool in MUTATION_TOOLS and m.reoffer for m in markers
    )
    disclosures = [_disclosure(m) for m in markers]
    return {
        "markers": [m.to_dict() for m in markers],
        "reoffer_mutation": reoffer_mutation,
        "disclosures": disclosures,
    }


def _disclosure(m: DeclineMarker) -> str:
    """One-line report disclosure of an active decline marker."""
    who = m.declined_by or "an unknown user"
    when = m.declined_at or "an unknown date"
    label = _tool_label(m.tool)
    line = f"{label} permanently declined by {who} on {when}"
    if m.version:
        line += f" (plugin v{m.version})"
    # Only mutation tools are actually re-offered (SKILL.md Step 2d). A linter
    # decline (scc, vulture, ...) carries reoffer=True too when it predates a
    # major bump, but Step 2b never consults it - so gate the suffix on
    # MUTATION_TOOLS, else a stale linter marker claims a re-offer that never
    # comes.
    if m.reoffer and m.tool in MUTATION_TOOLS:
        line += " - re-offer eligible (declined under an older major)"
    return line


def _tool_label(tool: str) -> str:
    if tool in MUTATION_TOOLS:
        return "Mutation testing"
    if tool == "scc":
        return "scc coverage extension"
    return f"Dead-code analysis (`{tool}`)"


# ── Declined backlog proposals (#813) ───────────────────────────────────────
# The reconcile pass (``lib/report/backlog_reconcile.py``, #811) proposes a
# close, reopen, demote or supersede per filed item. A proposal the user
# declines is recorded here with the same provenance as a tool marker, so the
# next run does not propose it again until its evidence changes.

PROPOSAL_DECLINES = "backlog-declines.json"
_SUBJECT = ("kind", "number", "condition", "finding", "superseded_by")


class ProposalDecline(TypedDict):
    """One declined reconcile proposal, keyed by its subject, the condition state and the model."""

    kind: str  # close, reopen, demote or supersede
    number: int  # the tracker item
    condition: str | None  # the condition id the proposal read
    finding: str | None  # the finding id (a finding demote)
    superseded_by: int | None  # the item a supersede pointed at
    state: str | None  # the condition's state when declined
    model: str | None  # level.levels_version when declined
    evidence: str  # the proposal's reason line: what the user turned down
    declined_by: str | None
    declined_at: str | None
    reason: str | None  # the user's own reason, when given


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _opt_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _state_of(condition: str | None, conditions: Mapping[str, Mapping[str, object]] | None) -> str | None:
    if condition is None or conditions is None or condition not in conditions:
        return None
    return _opt_str(conditions[condition].get("state"))


def decline_record(proposal: Mapping[str, object], conditions: Mapping[str, Mapping[str, object]] | None,
                   model: str | None, *, declined_by: str | None, declined_at: str | None,
                   reason: str | None = None) -> ProposalDecline | None:
    """The record for a declined ``proposal`` under this run's ``conditions`` and ``model``; None when malformed."""
    kind, number = _opt_str(proposal.get("kind")), _opt_int(proposal.get("number"))
    if kind is None or number is None:
        return None
    condition = _opt_str(proposal.get("condition"))
    return ProposalDecline(
        kind=kind, number=number, condition=condition, finding=_opt_str(proposal.get("finding")),
        superseded_by=_opt_int(proposal.get("superseded_by")), state=_state_of(condition, conditions),
        model=model, evidence=str(proposal.get("reason") or ""), declined_by=_opt_str(declined_by),
        declined_at=_opt_str(declined_at), reason=_opt_str(reason))


def _subject(entry: Mapping[str, object]) -> tuple[object, ...]:
    return tuple(entry.get(k) for k in _SUBJECT)


def read_proposal_declines(assess_dir: Path) -> list[ProposalDecline]:
    """The records in ``<assess_dir>/backlog-declines.json``; a missing or malformed file reads empty. Never raises."""
    try:
        data = json.loads((assess_dir / PROPOSAL_DECLINES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("declines") if isinstance(data, dict) else None
    out: list[ProposalDecline] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        record = decline_record(row, None, _opt_str(row.get("model")), declined_by=row.get("declined_by"),
                                declined_at=row.get("declined_at"), reason=row.get("reason"))
        if record is not None:
            record["state"], record["evidence"] = _opt_str(row.get("state")), str(row.get("evidence") or "")
            out.append(record)
    return out


def write_proposal_declines(assess_dir: Path, records: Sequence[ProposalDecline]) -> None:
    """Write ``records`` to ``<assess_dir>/backlog-declines.json``, sorted by item number and kind."""
    assess_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda r: (r["number"], r["kind"], str(r["condition"]), str(r["finding"])))
    payload = json.dumps({"declines": ordered}, indent=2, ensure_ascii=False) + "\n"
    (assess_dir / PROPOSAL_DECLINES).write_text(payload, encoding="utf-8")


def merge_proposal_declines(records: Sequence[ProposalDecline],
                            new: Sequence[ProposalDecline]) -> list[ProposalDecline]:
    """``records`` with each of ``new`` in place of the record for the same proposal subject."""
    fresh = {_subject(r) for r in new}
    return [r for r in records if _subject(r) not in fresh] + list(new)


def live_proposal_declines(records: Sequence[ProposalDecline],
                           conditions: Mapping[str, Mapping[str, object]] | None,
                           model: str | None) -> list[ProposalDecline]:
    """The records whose evidence still holds: drops one whose model or condition state this run reads differently.

    A record is kept when this run cannot say: no model, no level result, or a condition the
    run's model does not list (a profile or pack run). A dropped record stays dropped, so a
    state that changes and changes back proposes again.
    """
    live: list[ProposalDecline] = []
    for r in records:
        if model is not None and r["model"] != model:
            continue
        state = _state_of(r["condition"], conditions)
        if state is not None and state != r["state"]:
            continue
        live.append(r)
    return live


P = TypeVar("P", bound=Mapping[str, object])


def split_declined(proposals: Sequence[P], records: Sequence[ProposalDecline],
                   conditions: Mapping[str, Mapping[str, object]] | None,
                   model: str | None) -> tuple[list[P], list[ProposalDecline]]:
    """``(kept, declined)``: the proposals no live record covers, and the records that held one back."""
    live = {_subject(r): r for r in live_proposal_declines(records, conditions, model)}
    kept: list[P] = []
    held: list[ProposalDecline] = []
    for p in proposals:
        record = live.get(_subject(p))
        if record is not None and record["state"] == _state_of(_opt_str(p.get("condition")), conditions):
            held.append(record)
        else:
            kept.append(p)
    return kept, held


def proposal_disclosure(r: ProposalDecline) -> str:
    """One line naming a held-back proposal, who declined it, when, under which model, and why."""
    who, when = r["declined_by"] or "an unknown user", r["declined_at"] or "an unknown date"
    subject = r["condition"] or r["finding"]
    line = f"{r['kind']} #{r['number']}" + (f" (`{subject}`)" if subject else "")
    line += f" declined by {who} on {when}"
    if r["model"]:
        line += f" under model {r['model']}"
    if r["evidence"]:
        line += f": {r['evidence']}"
    if r["reason"]:
        line += f" (reason: {r['reason']})"
    return line
