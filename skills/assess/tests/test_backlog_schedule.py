"""The scheduled reconcile's status comment and write plan (#814): ``lib/report/backlog_schedule.py``."""
from __future__ import annotations

from collections.abc import Mapping

import pytest

from lib.report import backlog_items as bi
from lib.report import backlog_reconcile as br
from lib.report import backlog_schedule as bs

MODEL = "default-2026.11.24"
LABELS = [{"name": bi.DEFAULT_LABEL}]


def _facts(*conditions: Mapping[str, object], level: bool = True) -> br.RunFacts:
    return br.RunFacts(interactive=False, model=MODEL, previous_model=MODEL, head_sha="c" * 40,
                       previous_commit="d" * 40, dirty=False,
                       conditions={str(c["id"]): c for c in conditions} if level else None)


def _cond(cid: str, state: str, **extra: object) -> dict[str, object]:
    return {"id": cid, "state": state, "grade": "measured", **extra}


def _item(number: int, cid: str, state: str = "OPEN", comments: list[object] | None = None) -> dict[str, object]:
    return {"number": number, "title": f"Item {number}", "state": state, "labels": LABELS,
            "body": f"x\n\n{bi.condition_marker(cid, MODEL)}\n", "comments": comments or []}


def _comment(cid: int, body: str, association: str = "MEMBER", login: str = "dev") -> dict[str, object]:
    return {"url": f"https://github.com/o/r/issues/7#issuecomment-{cid}", "body": body,
            "authorAssociation": association, "author": {"login": login}}


def _empty() -> br.ReconcileResult:
    return br.ReconcileResult(proposals=[], rules_changed=[], notes=[], summary="")


STATUS = bs.new_status_comment(bs.status_line("old", "2026-11-01", None))


def test_scheduled_facts_turn_on_proposals_and_keep_the_rest() -> None:
    facts = bs.scheduled_facts(_facts())
    assert facts["interactive"] is True and facts["model"] == MODEL


def test_comment_id_reads_rest_ids_and_gh_urls() -> None:
    assert bs.comment_id({"id": 42}) == 42
    assert bs.comment_id({"id": "IC_kw", "url": "https://x/issues/1#issuecomment-77"}) == 77
    assert bs.comment_id({"id": True}) is None and bs.comment_id({}) is None


def test_status_comment_needs_the_marker_and_a_trusted_author() -> None:
    item = {"comments": [_comment(1, STATUS, "NONE", "outsider"), _comment(2, "no marker"),
                         {"id": 3, "body": STATUS, "author_association": "CONTRIBUTOR",
                          "user": {"login": "github-actions[bot]"}}, "junk"]}
    assert bs.status_comment(item) == {"id": 3, "body": STATUS}
    assert bs.status_comment({"comments": [_comment(4, STATUS, "OWNER")]}) == {"id": 4, "body": STATUS}
    assert bs.status_comment({"comments": "x"}) is None
    assert bs.status_comment({"comments": [{"body": STATUS, "authorAssociation": "OWNER"}]}) is None


def test_with_status_replaces_one_line_keeps_crlf_and_adds_a_missing_line() -> None:
    body = "<!-- assess:status -->\r\n**Status:** old\r\nkeep\r\n**Status:** second\r\n"
    assert bs.with_status(body, "**Status:** new") == body.replace("**Status:** old", "**Status:** new", 1)
    assert bs.with_status("a\n<!-- assess:status -->\nb", "**Status:** x") == "a\n<!-- assess:status -->\n**Status:** x\nb"
    assert bs.with_status("nothing", "**Status:** x") == "nothing"
    assert bs.status_line("t.", "2026-11-27", "f" * 40) == "**Status:** t. Last run: 2026-11-27, commit `ffffffffffff`."


def test_each_status_reads_the_proposal_decline_rules_note_or_reading() -> None:
    items = [_item(n, f"layer-{n}-x.1", comments=[_comment(n, STATUS)]) for n in range(1, 7)]
    write = br.ReconcileWrite(op="close", number=1, comment="")
    result = br.ReconcileResult(
        proposals=[br.Proposal(kind="close", number=1, title="", reason="it holds", write=write)],
        rules_changed=[br.RulesNote(number=3, title="", condition="layer-3-x.1", line="rules changed")],
        notes=[], summary="")
    facts = _facts(_cond("layer-4-x.1", "unmet"), _cond("layer-6-x.1", "met", grade=None))
    plan = bs.plan_scheduled(items, facts, result, mode="propose", today="2026-11-27", declined={2: "close #2 declined"})
    lines = {w["number"]: w["body"].split("\n")[1] for w in plan["writes"]}
    assert "Proposed close: it holds. Confirm it" in lines[1]
    assert "Declined before: close #2 declined." in lines[2]
    assert "No change: rules changed." in lines[3]
    assert "`layer-4-x.1` reads unmet (measured)." in lines[4]
    assert "does not list `layer-5-x.1`" in lines[5]
    assert "`layer-6-x.1` reads met." in lines[6]
    assert all(w["op"] == "edit_comment" for w in plan["writes"])
    no_level = bs.plan_scheduled(items[:1], _facts(level=False), _empty(), mode="propose", today="2026-11-27")
    assert "no level result" in no_level["writes"][0]["body"]


def test_an_unchanged_status_writes_nothing_and_propose_never_adds_a_comment() -> None:
    facts = _facts(_cond("layer-1-x.1", "unmet"))
    line = bs.status_line("`layer-1-x.1` reads unmet (measured).", "2026-11-27", facts["head_sha"])
    items = [_item(1, "layer-1-x.1", comments=[_comment(1, bs.new_status_comment(line))]), _item(2, "layer-1-x.1")]
    plan = bs.plan_scheduled(items, facts, _empty(), mode="propose", today="2026-11-27")
    assert plan["writes"] == []
    assert plan["notes"] == ["1 marked item(s) have no status comment; `propose` mode edits only an existing one"]
    filed = bs.plan_scheduled(items, facts, _empty(), mode="file", today="2026-11-27")
    assert [w["op"] for w in filed["writes"]] == ["add_comment"] and bs.STATUS_MARKER in filed["writes"][0]["body"]


def test_file_mode_files_unmarked_next_step_gates_under_the_limit() -> None:
    level = {"next_step": {"step": 5, "blockers": [{"kind": "gate", "condition": c} for c in
                                                     ("layer-9-a.1", "layer-1-x.1", "layer-8-b.2", "bad")]}}
    facts = _facts(_cond("layer-9-a.1", "unmet", why="Agents need it. "), _cond("layer-8-b.2", "unmet"))
    existing = [_item(1, "layer-1-x.1", state="CLOSED")]
    plan = bs.plan_scheduled(existing, facts, _empty(), mode="file", today="2026-11-27", wip=1, level=level)
    creates = [w for w in plan["writes"] if w["op"] == "create"]
    assert [w["title"] for w in creates] == ["Meet layer-8-b.2 to reach Step 5"]
    assert creates[0]["labels"] == [bi.DEFAULT_LABEL] and bi.marker_key(creates[0]["body"]) == ("layer-8-b", 2, "", 0)
    assert bs.STATUS_MARKER in creates[0]["status"] and "reads unmet" in creates[0]["status"]
    assert "1 next-Step gate(s) not filed" in plan["summary"]
    two = bs.plan_scheduled(existing, facts, _empty(), mode="file", today="2026-11-27", wip=3, level={})
    assert [w for w in two["writes"] if w["op"] == "create"] == []
    none = bs.plan_scheduled(existing, _facts(level=False), _empty(), mode="file", today="d", level=level)
    assert [w for w in none["writes"] if w["op"] == "create"] == []
    nameless = bs.plan_scheduled([], facts, _empty(), mode="file", today="d", level={"next_step": {"blockers": [
        {"condition": "layer-8-b.2"}]}})
    assert nameless["writes"][0]["title"] == "Meet layer-8-b.2 to reach the next Step"
    custom = bs.plan_scheduled([], facts, _empty(), mode="file", today="d", label="backlog", level=level)
    assert {tuple(w["labels"]) for w in custom["writes"]} == {("backlog",)}
    bare = bs.plan_scheduled([], facts, _empty(), mode="file", today="d", label="", level=level)
    assert {tuple(w["labels"]) for w in bare["writes"]} == {()}


def test_file_and_close_writes_the_close_and_frees_its_slot() -> None:
    level = {"next_step": {"step": 2, "blockers": [{"condition": "layer-2-y.1"}]}}
    facts = _facts(_cond("layer-1-x.1", "met"), _cond("layer-2-y.1", "unmet"))
    write = br.ReconcileWrite(op="close", number=1, comment="")
    result = br.ReconcileResult(proposals=[br.Proposal(kind="close", number=1, title="", reason="holds",
                                                       write=write)], rules_changed=[], notes=[], summary="")
    items = [_item(1, "layer-1-x.1", comments=[_comment(9, STATUS)])]
    plan = bs.plan_scheduled(items, facts, result, mode="file-and-close", today="d", wip=1, level=level)
    assert [w["op"] for w in plan["writes"]] == ["edit_comment", "close", "create"]
    assert "Closed by the scheduled reconcile: holds." in plan["writes"][0]["body"]
    assert set(bs.WRITE_OPS) == {"propose", "file", "file-and-close"}


def test_items_without_a_marker_number_or_label_are_skipped_and_an_unknown_mode_raises() -> None:
    items = [{"number": 1, "labels": LABELS, "body": "none", "comments": [_comment(1, STATUS)]},
             {**_item(2, "layer-1-x.1", comments=[_comment(2, STATUS)]), "labels": []},
             {**_item(3, "layer-1-x.1", comments=[_comment(3, STATUS)]), "number": True}]
    assert bs.plan_scheduled(items, _facts(), _empty(), mode="file-and-close", today="d")["writes"] == []
    assert len(bs.plan_scheduled(items, _facts(), _empty(), mode="propose", today="d", label="")["writes"]) == 1
    with pytest.raises(ValueError, match="unknown backlog mode"):
        bs.plan_scheduled([], _facts(), _empty(), mode="close", today="d")


def test_a_run_closes_at_most_the_writer_cap_and_the_rest_wait() -> None:
    """Twelve closes: ten are written, two wait for a later run, so the writer never refuses the plan."""
    items = [_item(n, f"layer-{n}-x.1", comments=[_comment(n, STATUS)]) for n in range(1, 13)]
    proposals = [br.Proposal(kind="close", number=n, title="", reason="holds",
                             write=br.ReconcileWrite(op="close", number=n, comment="")) for n in range(1, 13)]
    result = br.ReconcileResult(proposals=proposals, rules_changed=[], notes=[], summary="")
    plan = bs.plan_scheduled(items, _facts(), result, mode="file-and-close", today="d")
    closes = [w["number"] for w in plan["writes"] if w["op"] == "close"]
    assert closes == list(range(1, 11)) and len(closes) == bs.MAX_CLOSES
    edits = {w["number"]: w["body"] for w in plan["writes"] if w["op"] == "edit_comment"}
    assert all("Closed by the scheduled reconcile" in edits[n] for n in range(1, 11))
    assert all("Closed on a later run" in edits[n] for n in (11, 12))
    assert "2 close(s) wait for a later run" in plan["summary"]


def test_a_run_files_at_most_the_writer_cap() -> None:
    gates = [f"layer-{n}-g.1" for n in range(10, 22)]
    level = {"next_step": {"step": 3, "blockers": [{"condition": c} for c in gates]}}
    facts = _facts(*(_cond(c, "unmet") for c in gates))
    plan = bs.plan_scheduled([], facts, _empty(), mode="file", today="d", wip=50, level=level)
    assert sum(1 for w in plan["writes"] if w["op"] == "create") == bs.MAX_CREATES
    assert "2 next-Step gate(s) wait for a later run" in plan["summary"]


def test_a_multi_line_reason_stays_on_one_status_line() -> None:
    items = [_item(1, "layer-1-x.1", comments=[_comment(1, STATUS)])]
    plan = bs.plan_scheduled(items, _facts(), _empty(), mode="propose", today="d",
                             declined={1: "close #1 declined (reason: not now,\n  after the\r\nrelease)"})
    body = plan["writes"][0]["body"]
    line = body.split("\n")[1]
    assert "not now, after the release)" in line and body.count("\n") == STATUS.count("\n")
