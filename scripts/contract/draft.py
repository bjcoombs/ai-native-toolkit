#!/usr/bin/env python3
"""Draft a run's acceptance contract from the ready queue (issue #1328).

The refiner's sweep turns the ready queue into a contract draft so a marathon
run no longer waits for a hand-written contract. Freezing stays a human step:
this module never writes freeze evidence.

Input is a queue file (`refine_queue.py` in `skills/refine/scripts/` writes
it): `{"repository": str, "issues": [{"number", "title", "url", "criteria":
[str]}]}`, where `criteria` are the issue's Success Criteria items verbatim.

`write` produces two files in the contract dir (`contract_location`):

- `<run-id>.contract.md`: the draft, in the contract-file format of
  `tests/canaries/README.md`. Class `cli`; one tier-1 criterion per Success
  Criteria item, id `I<issue>-<item>`. The action keeps the item's text
  verbatim, so every command the item names in a code span sits next to it.
  The first line is `DRAFT_MARKER`, which `draft_approval.py` keys on.
- `<run-id>.kill-test.json`: the draft's kill test. Each entry records a
  criterion's issue, item, text and commands, with `passed_against_null`
  left `null`. `check` is the generated kill test: it passes only when the
  draft holds every recorded criterion with all of its commands and nothing
  else. The same file is the input `freeze.py --kill-test-results` takes: a
  person or agent runs each criterion against the default branch before the
  run and fills in the booleans. `freeze.py` refuses a `null`, so the draft is
  never frozen automatically.

Output is deterministic: an unchanged queue rewrites both files byte for byte.
`change_summary` compares the previous kill test with the new one and names,
by issue, the criteria added, removed and reworded; the sweep puts it in the
draft pull request so the maintainer sees what a refresh changed.

An item with no code span names no command. Its issue is left out of the
draft whole, never in part, and listed under "Not drafted" in the draft and
the change summary, so one prose criterion never blocks the rest of the queue.
A leading task-list box (`[ ]`, `[x]`) is dropped from each item, so ticking
it on the issue changes nothing. Whitespace runs outside code spans collapse to
one space; inside a code span every space and tab is kept, and only a line
break becomes a space, as in a rendered code span, so a command keeps its
exact spacing (#1565).

Exit codes: 0 written or check passed; 1 refused or check failed; 2 usage or
unreadable input; 3 nothing to draft (an empty queue, or no issue whose items
all name a command).

Stdlib only (`tests/test_plugin_contract.py`, `test_gate_code_is_stdlib_only`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import freeze  # noqa: E402
from contract_location import HELP_DEFAULT, resolve_contract_dir  # noqa: E402

DRAFT_MARKER = "<!-- contract-draft -->"
DRAFT_VERSION = 1
_CODE_SPAN = re.compile(r"`([^`]+)`")
_TASK_BOX = re.compile(r"^\[[ xX]\]\s*")
OBSERVATION = (
    "Each command or test the action names gives the result the criterion states "
    "(exit code, printed value or passing test). A missing command, a missing test "
    "or a different result fails."
)


class DraftError(ValueError):
    """The queue cannot be drafted; the message names why."""


class NothingToDraft(DraftError):
    """The queue holds no issue the draft can include (exit 3, not a failure)."""


def _validate_run_id(run_id: str) -> None:
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        raise DraftError("invalid run id %r: path separators are not allowed" % run_id)


def contract_path(contract_dir: Path, run_id: str) -> Path:
    return contract_dir / ("%s.contract.md" % run_id)


def kill_test_path(contract_dir: Path, run_id: str) -> Path:
    return contract_dir / ("%s.kill-test.json" % run_id)


def is_draft(text: str) -> bool:
    """True when the contract text was drafted by this module."""
    return text.splitlines()[:1] == [DRAFT_MARKER]


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _item_text(raw: str) -> str:
    """One line: whitespace collapsed outside code spans, kept inside them."""
    parts: List[str] = []
    pos = 0
    for match in _CODE_SPAN.finditer(raw):
        parts.append(re.sub(r"\s+", " ", raw[pos:match.start()]))
        parts.append(re.sub(r"\r\n|\r|\n", " ", match.group(0)))
        pos = match.end()
    parts.append(re.sub(r"\s+", " ", raw[pos:]))
    return "".join(parts).strip()


def _queue_issues(queue: Dict[str, Any]) -> List[Dict[str, Any]]:
    issues = queue.get("issues")
    if not isinstance(issues, list):
        raise DraftError("queue.issues must be a list")
    out: List[Dict[str, Any]] = []
    for raw in issues:
        if not isinstance(raw, dict) or not isinstance(raw.get("number"), int):
            raise DraftError("each queued issue needs an integer 'number'")
        criteria = raw.get("criteria")
        if not isinstance(criteria, list) or not all(isinstance(c, str) for c in criteria):
            raise DraftError("issue #%d: 'criteria' must be a list of strings" % raw["number"])
        out.append(raw)
    return sorted(out, key=lambda i: int(i["number"]))


def _issue_entries(issue: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """(entries, reason not drafted) for one queued issue: all of its items, or none."""
    number = int(issue["number"])
    if not issue["criteria"]:
        return [], "no Success Criteria items"
    entries: List[Dict[str, Any]] = []
    for index, raw in enumerate(issue["criteria"], start=1):
        text = _TASK_BOX.sub("", _item_text(raw), count=1)
        commands = _CODE_SPAN.findall(text)
        if not commands:
            return [], "item %d names no command (no code span): %s" % (index, text)
        entries.append({
            "id": "I%d-%d" % (number, index),
            "issue": number,
            "item": index,
            "criterion": text,
            "commands": commands,
            "passed_against_null": None,
        })
    return entries, None


def build_entries(queue: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(entries, not drafted): one kill-test entry per Success Criteria item.

    An issue whose items are not all checkable by a named command is left out
    whole, never in part, and listed with the reason; one such issue never
    blocks the draft for the rest of the queue.
    """
    entries: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for issue in _queue_issues(queue):
        found, reason = _issue_entries(issue)
        if reason is None:
            entries += found
        else:
            skipped.append({"number": int(issue["number"]), "title": _one_line(issue.get("title", "")),
                            "reason": reason})
    return entries, skipped


def _action(entry: Dict[str, Any]) -> str:
    return "Run the checks this criterion of #%d names: %s" % (entry["issue"], entry["criterion"])


def render_contract(run_id: str, repository: str, issues: List[Dict[str, Any]],
                    entries: List[Dict[str, Any]], skipped: List[Dict[str, Any]]) -> str:
    """The draft contract text. Deterministic: no clock, no environment."""
    left_out = {int(i["number"]) for i in skipped}
    titles = {int(i["number"]): _one_line(i.get("title", "")) for i in issues
              if int(i["number"]) not in left_out}
    lines = [
        DRAFT_MARKER,
        "# Acceptance contract draft: %s" % run_id,
        "",
        "Drafted by `scripts/contract/draft.py` from the Success Criteria of the ready issues "
        "queued for run `%s`%s. Each criterion is one Success Criteria item, its text and "
        "commands kept verbatim. To change a criterion, edit the issue: the next refiner sweep "
        "regenerates this file." % (run_id, " in %s" % repository if repository else ""),
        "",
        "- **Approve:** merge the pull request that adds this file to the default branch. "
        "`start_gate.py` refuses a draft that is not on the default branch.",
        "- **Freeze:** run each criterion against the default branch before the run, record "
        "`passed_against_null` in `%s.kill-test.json`, then run `freeze.py`. A criterion that "
        "already passes is vacuous and freeze refuses it." % run_id,
        "",
        "| Issue | Title | Criteria |",
        "|---|---|---|",
    ]
    for number in sorted(titles):
        ids = ", ".join(e["id"] for e in entries if e["issue"] == number)
        lines.append("| #%d | %s | %s |" % (number, titles[number].replace("|", "/"), ids))
    if skipped:
        lines += ["", "Not drafted: each issue below has a Success Criteria item with no command, so none "
                  "of its items is in this draft. Name the command in the issue to include it."]
        lines += ["- #%d %s: %s" % (i["number"], i["title"], i["reason"]) for i in skipped]
    lines += ["", "```yaml", "class: cli", "criteria:"]
    for entry in entries:
        lines += [
            "  - id: %s" % entry["id"],
            "    tier: 1",
            '    action: "%s"' % _action(entry),
            '    observation: "%s"' % OBSERVATION,
        ]
    lines += ["```", ""]
    return "\n".join(lines)


def render_kill_test(run_id: str, repository: str, issues: List[Dict[str, Any]],
                     entries: List[Dict[str, Any]], skipped: List[Dict[str, Any]], contract_text: str) -> str:
    doc = {
        "contract_sha256": hashlib.sha256(contract_text.encode("utf-8")).hexdigest(),
        "draft": {
            "version": DRAFT_VERSION,
            "run_id": run_id,
            "repository": repository,
            "issues": [{"number": int(i["number"]), "title": _one_line(i.get("title", ""))} for i in issues],
            "not_drafted": skipped,
        },
        "results": entries,
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def draft(queue: Dict[str, Any], run_id: str) -> Tuple[str, str]:
    """(contract text, kill-test text) for the queue. Raises `DraftError`."""
    _validate_run_id(run_id)
    issues = _queue_issues(queue)
    if not issues:
        raise NothingToDraft("the ready queue is empty: nothing to draft")
    entries, skipped = build_entries(queue)
    if not entries:
        raise NothingToDraft("no queued issue has Success Criteria items that each name a command: %s"
                             % "; ".join("#%d %s" % (i["number"], i["reason"]) for i in skipped))
    repository = str(queue.get("repository") or "")
    contract = render_contract(run_id, repository, issues, entries, skipped)
    freeze.parse_contract_text(contract)  # the draft must parse as a contract
    return contract, render_kill_test(run_id, repository, issues, entries, skipped, contract)


# --------------------------------------------------------------------------- #
# The generated kill test.
# --------------------------------------------------------------------------- #


def check(contract_text: str, kill_test: Dict[str, Any]) -> List[str]:
    """Reasons the draft fails its kill test; empty when it passes.

    Passes only when the draft parses, holds exactly the recorded criteria, and
    each criterion's action still names every command recorded for it.
    """
    try:
        contract = freeze.parse_contract_text(contract_text)
    except freeze.ContractParseError as exc:
        return ["the draft does not parse as a contract: %s" % exc]
    results = kill_test.get("results")
    if not isinstance(results, list):
        return ["the kill test has no results list"]
    actions = {c.id: c.action for c in contract.criteria}
    reasons: List[str] = []
    recorded = set()
    for entry in results:
        cid = str(entry.get("id")) if isinstance(entry, dict) else ""
        recorded.add(cid)
        if cid not in actions:
            reasons.append("criterion %s is missing from the draft" % cid)
            continue
        commands = entry.get("commands")
        if not isinstance(commands, list) or not commands:
            reasons.append("criterion %s records no command" % cid)
            continue
        missing = [c for c in commands if "`%s`" % c not in actions[cid]]
        if missing:
            reasons.append("criterion %s lost its command(s): %s" % (cid, ", ".join(missing)))
    extra = sorted(set(actions) - recorded)
    if extra:
        reasons.append("criteria not in the kill test: %s" % ", ".join(extra))
    return reasons


# --------------------------------------------------------------------------- #
# Refresh: what changed since the previous draft.
# --------------------------------------------------------------------------- #


def _by_issue(kill_test: Optional[Dict[str, Any]]) -> Dict[int, Dict[str, str]]:
    out: Dict[int, Dict[str, str]] = {}
    for entry in (kill_test or {}).get("results") or []:
        if isinstance(entry, dict) and isinstance(entry.get("issue"), int):
            out.setdefault(entry["issue"], {})[str(entry.get("id"))] = str(entry.get("criterion", ""))
    return out


def _titles(kill_test: Optional[Dict[str, Any]]) -> Dict[int, str]:
    issues = ((kill_test or {}).get("draft") or {}).get("issues") or []
    return {int(i["number"]): str(i.get("title", "")) for i in issues
            if isinstance(i, dict) and isinstance(i.get("number"), int)}


def change_summary(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> str:
    """Markdown naming, by issue, the criteria a refresh added, removed or reworded."""
    before, after = _by_issue(old), _by_issue(new)
    titles = {**_titles(old), **_titles(new)}
    not_drafted = ["- Not drafted #%d %s: %s" % (i["number"], i["title"], i["reason"])
                   for i in (new.get("draft") or {}).get("not_drafted") or []]
    if old is None:
        head = "New contract draft: %d criteria from %d issues." % (
            sum(len(v) for v in after.values()), len(after))
    elif before == after:
        return "\n".join(["No change: the draft already matches the ready queue."] + not_drafted) + "\n"
    else:
        head = "The ready queue changed since the last draft."
    lines = [head, ""]
    for number in sorted(set(before) | set(after)):
        was, now = before.get(number, {}), after.get(number, {})
        label = ("#%d %s" % (number, titles.get(number, ""))).rstrip()
        if not was:
            lines.append("- Added %s: %s" % (label, ", ".join(sorted(now))))
        elif not now:
            lines.append("- Removed %s: %s" % (label, ", ".join(sorted(was))))
        else:
            parts = []
            for verb, ids in (("added", sorted(set(now) - set(was))),
                              ("removed", sorted(set(was) - set(now))),
                              ("reworded", sorted(i for i in set(now) & set(was) if now[i] != was[i]))):
                if ids:
                    parts.append("%s %s" % (verb, ", ".join(ids)))
            if parts:
                lines.append("- Changed %s: %s" % (label, "; ".join(parts)))
    return "\n".join(lines + not_drafted) + "\n"


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write(queue: Dict[str, Any], run_id: str, contract_dir: Path) -> str:
    """Write the draft and its kill test; return the change summary."""
    contract_text, kill_text = draft(queue, run_id)
    ktp = kill_test_path(contract_dir, run_id)
    old = _read_json(ktp) if ktp.exists() else None
    summary = change_summary(old, json.loads(kill_text))
    contract_dir.mkdir(parents=True, exist_ok=True)
    contract_path(contract_dir, run_id).write_bytes(contract_text.encode("utf-8"))
    ktp.write_bytes(kill_text.encode("utf-8"))
    return summary


def _cmd_write(args: argparse.Namespace, contract_dir: Path) -> int:
    queue = _read_json(Path(args.queue))
    if queue is None:
        print("draft: the queue file is missing or not a JSON object: %s" % args.queue, file=sys.stderr)
        return 2
    try:
        summary = write(queue, args.run_id, contract_dir)
    except NothingToDraft as exc:
        print("draft: %s" % exc, file=sys.stderr)
        return 3
    except DraftError as exc:
        print("draft: REFUSED - %s" % exc, file=sys.stderr)
        return 1
    if args.summary:
        Path(args.summary).write_text(summary, encoding="utf-8")
    print(summary, end="")
    return 0


def _cmd_check(args: argparse.Namespace, contract_dir: Path) -> int:
    try:
        _validate_run_id(args.run_id)
        text = contract_path(contract_dir, args.run_id).read_text(encoding="utf-8")
    except (DraftError, OSError) as exc:
        print("draft check: cannot read the draft: %s" % exc, file=sys.stderr)
        return 2
    kill_test = _read_json(kill_test_path(contract_dir, args.run_id))
    if kill_test is None:
        print("draft check: FAILED - the kill test is missing or unreadable", file=sys.stderr)
        return 1
    reasons = check(text, kill_test)
    if reasons:
        print("draft check: FAILED - %s" % "; ".join(reasons), file=sys.stderr)
        return 1
    print("draft check: OK - every criterion keeps its command.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Draft a run contract from the ready queue.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("write", "check"):
        p = sub.add_parser(name)
        p.add_argument("--run-id", required=True, help="run identifier, e.g. issues-agent-ready")
        p.add_argument("--contract-dir", default=None, help="where the draft lives (%s)" % HELP_DEFAULT)
        if name == "write":
            p.add_argument("--queue", required=True, help="queue JSON from refine_queue.py")
            p.add_argument("--summary", default=None, help="also write the change summary here")
    args = parser.parse_args(argv)
    contract_dir = resolve_contract_dir(args.contract_dir)
    return _cmd_write(args, contract_dir) if args.cmd == "write" else _cmd_check(args, contract_dir)


if __name__ == "__main__":
    sys.exit(main())
