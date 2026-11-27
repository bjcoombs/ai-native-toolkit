"""Propose-only reconcile of filed backlog items against this run (#811).

``assess-pr`` Step 6 files each Top 3 action as a tracker item, and an item whose action names
a condition carries the condition marker (``lib/report/backlog_items.py``, #810). On an
interactive run this pass reads the tracker's items again (``gh issue list --state all --json
number,title,body,state,labels,stateReason,closedAt``) beside this run's ``run-context.json`` and proposes, per
item, at most one change. It never writes: ``reconcile`` returns proposals, the skill shows
them as one batched summary, and ``confirmed_writes`` gives back only the writes for the item
numbers the user confirmed. A declined proposal is recorded and held back outside this pass
(``lib/run/decline_markers.py``, #813).

Per item, in this order (the first rule that applies wins; an item with the
``assess-informational`` label, already demoted, is passed over):

1. **Demote** (open items): the item cannot move any more, so it is proposed as
   informational (``assess-informational`` label added, state kept). That holds for an item
   whose body names a finding keyed on commit origin (``**Finding:** `self_referential_tests```,
   ``reviewed_findings.REVIEWABLE_FINDINGS``: no code change clears it; never an item number in
   ``filed``, which this run's Top 3 just asked for), and for a marked item
   whose condition reads ``not_applicable``. A condition this run's model does not list is
   left as it is, with one note: a ``--profile`` run cuts rows to fewer levels and a pack's rows
   exist only on a ``--pack`` run, so a missing condition need not have left the model.
2. **Close** (open, marked): the condition now holds (``met``; ``unmet`` for a hazard, whose
   ``met`` means the harm is present). The proposal cites the evidence line (condition id,
   state, grade and its why) and the commit this run measured.
3. **Reopen** (closed, marked): the condition lapsed (``unmet``; ``met`` for a hazard). The
   proposal names the cause: the reading at the measured commit, its reason code, and the
   model change when the rules moved as well.
4. **Rules changed** (instead of 2 or 3): when the item's marker names another model than
   this run's, the previous run's model (``prior_level``) differs from this run's too, and
   the repository did not change since the previous run (``since_last_run.previous_commit``
   equals ``measured_commit.head_sha`` on a clean tree), the move is the rules', not the
   team's. It reads ``rules changed: your repo did not`` in the summary, with no proposal and
   no write, so it is never shown as progress or regression (#771 Recount). A move under a
   changed model on a changed repository cannot be split between the two, so it is proposed
   as 2 or 3 with the model change named.
5. **Superseded** (open, marked with ``part``): an open item marked for the same condition
   and ``sub`` without ``part`` exists, because the action is no longer split
   (``plan_filing`` gave it one of the earlier parts). The other parts are proposed closed as
   superseded by it. An item number in ``filed`` (the items this run filed or reused) is
   never superseded, so a run that splits the action again keeps its parts. A closed part
   beside the whole item, open or closed, is never proposed reopened: the whole item carries
   the action, so a lapse reopens only it (#1530).

No close, reopen or demote is proposed for a condition ``not_measured`` or ``declined``
(unknown never counts); a split part is still checked for supersession. Nothing is proposed for an item the label check excludes (``label``, as ``plan_filing`` applies it).
With an empty ``label`` (``--label ""``, filing upstream without triage access) the
informational label cannot be written, so no demote is proposed; one note counts the skipped
demotes, and nothing is written, so the next run reads the same (#1530).
With ``wip`` (``[backlog] wip``, #1542) a reopen is proposed only while the limit leaves a
slot: ``within_wip`` takes the free slots (``wip`` minus ``backlog_wip.open_backlog``, the
count filing uses) next-unlock first, then by item number, and lists each other reopen under
``next_up`` and the summary's "Next up" block. A close in the same batch frees its slot only
once written. Without ``wip`` no reopen is held, so a caller that holds proposals back after
this pass (``assess_actions_render.py`` holds declined ones) applies ``within_wip`` itself
after that, and a declined reopen keeps no slot.

Close and reopen are held, with one note, when the working tree is dirty or the measured
commit is unknown: no commit carries the reading. A run whose ``interactive`` is false
proposes nothing (v1 runs on interactive ``/assess`` only), and a run with no level result
proposes only the finding demotes.

``summary`` is the batched confirm-before-write block as Markdown. Each proposal carries its
``write``: ``close`` or ``reopen`` with a ``comment``, or ``label`` with ``add_label`` and a
``comment``. Pure stdlib apart from the run-context read in ``run_facts``; never touches the
tracker. Tests: ``tests/test_backlog_reconcile.py``.
"""
from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from typing import NotRequired, TypedDict

from lib.prior_level import record_for
from lib.report.backlog_items import DEFAULT_LABEL, item_labels, marker_key, parse_marker
from lib.report.backlog_wip import DEFAULT_WIP, next_unlock_conditions, open_backlog
from lib.report.reviewed_findings import REVIEWABLE_FINDINGS

INFORMATIONAL_LABEL = "assess-informational"
RULES_CHANGED = "rules changed: your repo did not"
KINDS = ("close", "reopen", "demote", "supersede")
_FINDING = re.compile(r"\*\*Finding:\*\*\s*`(?P<finding>[a-z0-9_]+)`")
_OPEN = ("open", "opened")


class ReconcileWrite(TypedDict):
    """One tracker write: ``close``, ``reopen`` or ``label`` (``add_label``), each with a comment."""

    op: str
    number: int
    comment: str
    add_label: NotRequired[str]


class Proposal(TypedDict):
    """One proposed change to one filed item; ``reason`` is the evidence line or the cause."""

    kind: str  # one of KINDS
    number: int
    title: str
    reason: str
    write: ReconcileWrite
    condition: NotRequired[str]
    finding: NotRequired[str]
    commit: NotRequired[str]
    superseded_by: NotRequired[int]


class RulesNote(TypedDict):
    """A move the rules caused: shown in the summary, never proposed or written."""

    number: int
    title: str
    condition: str
    line: str


class NextUp(TypedDict):
    """A lapsed item held back by the open-item limit: listed under "Next up", never proposed (#1542)."""

    number: int
    title: str
    condition: str
    reason: str


class ReconcileResult(TypedDict):
    proposals: list[Proposal]
    rules_changed: list[RulesNote]
    notes: list[str]
    summary: str
    next_up: NotRequired[list[NextUp]]  # set whenever ``reconcile`` ran; empty without a ``wip``


class RunFacts(TypedDict):
    """What the pass reads from ``run-context.json`` (``run_facts``)."""

    interactive: bool
    model: str | None  # level.levels_version
    previous_model: str | None  # the previous run's levels_version, from prior_level
    head_sha: str | None  # measured_commit.head_sha
    previous_commit: str | None  # since_last_run.previous_commit
    dirty: bool | None  # measured_commit.dirty
    conditions: dict[str, Mapping[str, object]] | None  # level.conditions by id; None without a level
    next_unlock: NotRequired[list[str]]  # level.next_step.blockers[].condition: the reopens ranked first


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _block(ctx: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = ctx.get(key)
    return value if isinstance(value, Mapping) else {}


def run_facts(ctx: Mapping[str, object]) -> RunFacts:
    """The facts ``reconcile`` reads, from a ``run-context.json`` mapping; missing parts read None."""
    level, commit = _block(ctx, "level"), _block(ctx, "measured_commit")
    raw = level.get("conditions")
    conditions = None
    if isinstance(raw, list):
        conditions = {str(c["id"]): c for c in raw if isinstance(c, Mapping) and isinstance(c.get("id"), str)}
    record = record_for(ctx.get("prior_level"), level) if level else None
    dirty = commit.get("dirty")
    return RunFacts(
        interactive=ctx.get("interactive") is not False,
        model=_str(level.get("levels_version")),
        previous_model=_str(record.get("levels_version")) if record else None,
        head_sha=_str(commit.get("head_sha")),
        previous_commit=_str(_block(ctx, "since_last_run").get("previous_commit")),
        dirty=dirty if isinstance(dirty, bool) else None,
        conditions=conditions,
        next_unlock=sorted(next_unlock_conditions(level)),
    )


def _is_open(item: Mapping[str, object]) -> bool:
    return str(item.get("state", "")).lower() in _OPEN


def _number(item: Mapping[str, object]) -> int | None:
    number = item.get("number")
    return number if isinstance(number, int) and not isinstance(number, bool) else None


def _body(item: Mapping[str, object]) -> str:
    return str(item.get("body") or "")


def _short(sha: str) -> str:
    return sha[:12]


def _done(condition: Mapping[str, object]) -> bool | None:
    """True when the condition holds, False when it does not, None when the reading cannot say."""
    state = condition.get("state")
    if state not in ("met", "unmet"):
        return None
    return (state == "met") != (condition.get("polarity") == "hazard")


def _reading(cid: str, condition: Mapping[str, object], sha: str) -> str:
    grade = condition.get("grade")
    line = f"`{cid}` reads {condition.get('state')}" + (f" ({grade})" if isinstance(grade, str) else "")
    return f"{line} at {_short(sha)}"


class _Pass:
    def __init__(self, existing: Sequence[Mapping[str, object]], facts: RunFacts, label: str,
                 filed: Collection[int]) -> None:
        self.existing, self.facts, self.label, self.filed = existing, facts, label, filed
        self.proposals: list[Proposal] = []
        self.rules: list[RulesNote] = []
        self.held = 0
        self.unlisted = 0
        self.skipped_demotes = 0

    def propose(self, kind: str, item: Mapping[str, object], reason: str, write: ReconcileWrite, *,
                condition: str = "", finding: str = "", commit: str = "", superseded_by: int = 0) -> None:
        proposal = Proposal(kind=kind, number=write["number"], title=str(item.get("title") or ""),
                            reason=reason, write=write)
        if condition:
            proposal["condition"] = condition
        if finding:
            proposal["finding"] = finding
        if commit:
            proposal["commit"] = commit
        if superseded_by:
            proposal["superseded_by"] = superseded_by
        self.proposals.append(proposal)

    def demote(self, item: Mapping[str, object], number: int, reason: str, *,
               condition: str = "", finding: str = "") -> None:
        if not self.label:
            self.skipped_demotes += 1  # no triage access: the label write would fail on every run
            return
        write = ReconcileWrite(op="label", number=number, add_label=INFORMATIONAL_LABEL,
                               comment=f"Demoted to informational by /assess on confirmation: {reason}.")
        self.propose("demote", item, reason, write, condition=condition, finding=finding)

    def rules_only(self, fields: Mapping[str, str]) -> bool:
        f = self.facts
        return (f["model"] is not None and fields.get("model") != f["model"]
                and f["previous_model"] is not None and f["previous_model"] != f["model"]
                and f["head_sha"] is not None and f["previous_commit"] == f["head_sha"] and f["dirty"] is False)

    def model_moved(self, fields: Mapping[str, str]) -> str:
        marked, now = fields.get("model"), self.facts["model"]
        return f"; the model also changed ({marked} -> {now})" if marked and now and marked != now else ""

    def item(self, item: Mapping[str, object]) -> None:
        number = _number(item)
        labels = item_labels(item)
        if number is None or INFORMATIONAL_LABEL in labels or (self.label and self.label not in labels):
            return
        found = _FINDING.search(_body(item))
        if _is_open(item) and found and found.group("finding") in REVIEWABLE_FINDINGS and number not in self.filed:
            finding = found.group("finding")
            self.demote(item, number, f"`{finding}` keys on commit origin, so no code change clears it",
                        finding=finding)
            return
        key, fields = marker_key(_body(item)), parse_marker(_body(item))
        conditions = self.facts["conditions"]
        if key is None or fields is None or conditions is None:
            return
        cid = f"{key[0]}.{key[1]}"
        whole = None
        if key[3] > 0:
            wholes = [i for i in self.existing if (n := _number(i)) is not None
                      and n != number and (k := marker_key(_body(i))) is not None
                      and k[:3] == key[:3] and k[3] == 0]
            if wholes and not _is_open(item):
                return  # a closed part beside the whole item, open or closed: only the whole reopens
            whole = next((_number(i) for i in wholes if _is_open(i)), None)
        if self.condition(item, number, cid, conditions.get(cid), fields):
            return
        if _is_open(item) and number not in self.filed:
            if whole is not None:
                reason = f"the action is no longer split, and #{whole} carries it"
                write = ReconcileWrite(op="close", number=number,
                                       comment=f"Closed by /assess on confirmation: superseded by #{whole}; {reason}.")
                self.propose("supersede", item, reason, write, condition=cid, superseded_by=whole)

    def condition(self, item: Mapping[str, object], number: int, cid: str,
                  condition: Mapping[str, object] | None, fields: Mapping[str, str]) -> bool:
        """Propose for one marked item's condition; True when it got a proposal or a rules note."""
        is_open = _is_open(item)
        if condition is None:
            self.unlisted += 1
            return True
        if condition.get("state") == "not_applicable":
            if not is_open:
                return False
            self.demote(item, number, f"`{cid}` reads not applicable, so no change to the repository can move it",
                        condition=cid)
            return True
        done = _done(condition)
        if done is None or done is not is_open:
            return False
        if self.rules_only(fields):
            line = f"{RULES_CHANGED} ({fields.get('model')} -> {self.facts['model']})"
            self.rules.append(RulesNote(number=number, title=str(item.get("title") or ""), condition=cid, line=line))
            return True
        sha = self.facts["head_sha"]
        if sha is None or self.facts["dirty"] is not False:
            self.held += 1
            return True
        reading = _reading(cid, condition, sha)
        if is_open:
            note = condition.get("why")
            note = note.strip().rstrip(".") if isinstance(note, str) else ""
            evidence = reading + (f": {note}" if note else "")
            write = ReconcileWrite(op="close", number=number,
                                   comment=f"Closed by /assess on confirmation: the condition holds. {evidence}. Commit: {sha}.")
            self.propose("close", item, evidence + self.model_moved(fields), write, condition=cid, commit=sha)
        else:
            reason = condition.get("reason")
            cause = reading + (f" ({reason})" if isinstance(reason, str) and reason else "") + self.model_moved(fields)
            write = ReconcileWrite(op="reopen", number=number,
                                   comment=f"Reopened by /assess on confirmation: the condition lapsed. {cause}. Commit: {sha}.")
            self.propose("reopen", item, cause, write, condition=cid, commit=sha)
        return True


def within_wip(proposals: Sequence[Proposal], existing: Sequence[Mapping[str, object]], facts: RunFacts,
               wip: int, *, label: str = DEFAULT_LABEL) -> tuple[list[Proposal], list[NextUp]]:
    """``proposals`` with the reopens the open-item limit has no slot for taken out, and those as "Next up".

    Free slots are ``wip`` minus the open backlog (``backlog_wip.open_backlog``, the count
    filing uses), never below 0. Reopens take them next-unlock first (``facts["next_unlock"]``),
    then by item number. A close proposed in the same batch frees its slot only once written,
    so the reopen it would make room for comes on the next run.
    """
    free = max(0, wip - len(open_backlog(existing, label)))
    unlocks = set(facts.get("next_unlock", ()))
    reopens = sorted((p for p in proposals if p["kind"] == "reopen"),
                     key=lambda p: (p.get("condition") not in unlocks, p["number"]))
    held = reopens[free:]
    numbers = {p["number"] for p in held}
    kept = [p for p in proposals if p["number"] not in numbers]
    return kept, [NextUp(number=p["number"], title=p["title"], condition=p.get("condition", ""), reason=p["reason"])
                  for p in held]


def reconcile(existing: Sequence[Mapping[str, object]], facts: RunFacts, *, label: str = DEFAULT_LABEL,
              filed: Collection[int] = (), wip: int | None = None) -> ReconcileResult:
    """The proposals for the tracker's ``existing`` items against this run; writes nothing.

    With ``wip`` (``[backlog] wip``, #1542) a reopen the limit has no slot for is listed under
    ``next_up`` instead of proposed (``within_wip``); without it no reopen is held. A caller
    that holds proposals back after this pass (declines) passes no ``wip`` and applies
    ``within_wip`` after that, so a held-back reopen does not keep a slot.
    """
    notes: list[str] = []
    if not facts["interactive"]:
        notes.append("non-interactive run: reconcile proposes nothing")
        return ReconcileResult(proposals=[], rules_changed=[], notes=notes, summary=summary([], [], notes),
                               next_up=[])
    run = _Pass(existing, facts, label, filed)
    for item in existing:
        run.item(item)
    if facts["conditions"] is None:
        notes.append("this run has no level result, so marked items were not read against their conditions")
    if run.unlisted:
        notes.append(f"{run.unlisted} item(s) left as they are: this run's model does not list their condition "
                     "(a profile or pack run may not)")
    if run.held:
        notes.append(f"{run.held} close or reopen proposal(s) held: the working tree has uncommitted changes "
                     "or the measured commit is not known, so no commit carries the reading")
    if run.skipped_demotes:
        notes.append(f"{run.skipped_demotes} demote proposal(s) skipped: with no label (`--label \"\"`) "
                     "the informational label cannot be written")
    proposals = sorted(run.proposals, key=lambda p: (KINDS.index(p["kind"]), p["number"]))
    next_up: list[NextUp] = []
    if wip is not None:
        proposals, next_up = within_wip(proposals, existing, facts, wip, label=label)
    return ReconcileResult(proposals=proposals, rules_changed=run.rules, notes=notes,
                           summary=summary(proposals, run.rules, notes, next_up, DEFAULT_WIP if wip is None else wip),
                           next_up=next_up)


_VERB = {"close": "Close", "reopen": "Reopen", "demote": "Demote to informational", "supersede": "Close as superseded"}


def summary(proposals: Sequence[Proposal], rules: Sequence[RulesNote], notes: Sequence[str],
            next_up: Sequence[NextUp] = (), limit: int = DEFAULT_WIP) -> str:
    """The batched confirm-before-write block: every proposal, the reopens held as next up, then the notes.

    ``limit`` is the ``[backlog] wip`` the "Next up" header names; it is read only with ``next_up``.
    """
    head = (f"**Reconcile: {len(proposals)} proposal(s) for filed items.** Nothing is written until you confirm."
            if proposals else "**Reconcile: no proposals for filed items.**")
    lines = [head, ""]
    for p in proposals:
        extra = f" Commit: `{p['commit']}`." if "commit" in p else ""
        lines.append(f"- {_VERB[p['kind']]} #{p['number']} ({p['title']}): {p['reason']}.{extra}")
    if next_up:
        noun = "item" if limit == 1 else "items"
        lines += ["", f"**Next up** (not reopened: the backlog holds at most {limit} open {noun}, "
                      "the `[backlog] wip` limit):"]
        lines += [f"- #{n['number']} ({n['title']}): {n['reason']}." for n in next_up]
    if rules:
        lines += ["", "No change proposed:"]
        lines += [f"- #{r['number']} `{r['condition']}`: {r['line']}" for r in rules]
    if notes:
        lines += [""] + [f"_{n}._" for n in notes]
    return "\n".join(lines).rstrip() + "\n"


def confirmed_writes(proposals: Sequence[Proposal], confirmed: Collection[int]) -> list[ReconcileWrite]:
    """The writes of the proposals whose item number the user confirmed, in proposal order."""
    return [p["write"] for p in proposals if p["number"] in confirmed]
