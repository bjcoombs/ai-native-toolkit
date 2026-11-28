"""Merge and review rules kept as code, read offline: the ``review_rules`` block (issue #856).

The review condition reads branch protection, CODEOWNERS and AI review, so rules a repository
keeps as code elsewhere went unread. ``scan_review_rules(repo_root)`` reads five arms from
git-tracked files, each parsed, never matched as text:

- ``merge_queue``: a workflow under ``.github/workflows`` whose ``on:``
  (``ci_triggers.workflow_events``) names ``merge_group``, or a Mergify config
  (``.mergify.yml``, ``.mergify/config.yml``, ``.github/mergify.yml``) with a non-empty
  ``queue_rules`` list;
- ``policy_bot``: a root ``.policy.yml`` whose ``policy.approval`` expression always needs an
  approval: each rule it names resolves in ``approval_rules``, a rule needs one only with a
  ``requires.count`` of 1 or more and no ``if`` (a conditional rule skips the changes it does not
  match), an ``and`` needs one when any member does, an ``or`` only when every branch does (an unconditional auto-approve, ``count`` 0
  and no ``if``, clears it; a conditional one is left out). An unknown rule name is unsettled;
- ``prow_owners``: Prow ``OWNERS`` files naming ``approvers``, at the top level or under
  ``filters``. Mirroring the #785 catch-all rule, OWNERS files that between them name one owner
  for everything (after expanding a root ``OWNERS_ALIASES``), or nobody at all, do not count: with Prow's implicit
  self-approval that owner reviews nothing. ``catch_all_owners`` is then true;
- ``ruleset_json``: a committed ruleset or classic branch-protection export
  (``lib.hosting.config_snapshots.find_snapshots``, the shapes the drift scan reads) that requires
  an approving review or a merge queue. A ruleset counts only with ``enforcement: active`` and
  a branch target; ``evaluate`` and ``disabled`` rulesets do not, and one with no
  ``enforcement`` is unsettled. The ref scope is not read (a known limit);
- ``review_guide``: a tracked directory whose name marks review guidance (``review-guidelines``,
  ``review_checklist``, ``code-review-criteria``, or a folder of grading sheets such as
  ``rubrics``) that a workflow running on proposed changes
  (an ``on:`` naming a ``review_automation.REVIEW_EVENTS`` event) names as a path, outside its
  ``on:`` block (a ``paths:`` filter only says when the workflow runs). Comment text never counts;
  a workflow whose ``on:`` cannot be read is unsettled, as for the merge queue.

Committed config is evidence, not proof the app or rule is live: the block moves no tally or
Step. ``lib.scoring.condition_evidence`` attaches ``arms`` to the review condition while it is
met, and ``lib.hosting.review_reality`` reads the committed merge queue when the platform rules
could not be read. Files under ``vendor``, ``third_party``, ``testdata``, ``fixtures`` or
``node_modules`` are never read (a vendored Kubernetes ``OWNERS`` is someone else's rule).

``present`` is true when an arm is credited; null when none is and a file could not be settled
(YAML outside ``lib.ci.yaml_tree``'s subset, JSON that does not parse, a workflow ``on:`` the
reader cannot list, a ruleset with no ``enforcement``), with the paths in ``unsettled``; else
false. Outside a git checkout the block is ``available: false``. Counts-only keeps the booleans,
the arm tokens and the paths (hashed under ``--hash-paths``). Tests: ``tests/test_review_rules.py``.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from lib.ci.review_automation import REVIEW_EVENTS
from lib.ci.shared_tooling import workflow_events
from lib.ci.yaml_tree import Value, parse
from lib.history.git_churn import tracked_files
from lib.hosting.config_snapshots import find_snapshots
from lib.pack_yaml import YamlError, strip_comment
from lib.run_context_types_review_rules import ReviewRule, ReviewRulesBlock

ARMS = ("merge_queue", "policy_bot", "prow_owners", "ruleset_json", "review_guide")
MERGIFY_FILES = (".mergify.yml", ".mergify/config.yml", ".github/mergify.yml")
POLICY_FILE = ".policy.yml"
OWNERS = "OWNERS"
OWNERS_ALIASES = "OWNERS_ALIASES"
MAX_BYTES = 1_000_000

_WORKFLOWS = ".github/workflows/"
_RULESETS = ".github/rulesets/"
_EXCLUDED_DIRS = frozenset({"vendor", "third_party", "third-party", "testdata", "fixtures", "node_modules"})
_GUIDE_DIR = re.compile(
    r"(?:[a-z0-9]+[-_.])*(?:code[-_]?)?review[-_]?"
    r"(?:rubrics?|guidelines?|rules|criteria|checklists?|standards?)"
    r"|(?:[a-z0-9]+[-_.])*rubrics?")


class _Unsettled(Exception):
    """A file the reader cannot settle."""


def _read(root: Path, rel: str) -> str:
    path = root / rel
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise _Unsettled(rel)
        path.resolve().relative_to(root)
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise _Unsettled(rel) from exc


def _yaml(root: Path, rel: str) -> Value:
    try:
        return parse(_read(root, rel))
    except YamlError as exc:
        raise _Unsettled(rel) from exc


def _names(value: Value) -> list[str]:
    """The owner names of an ``approvers`` / ``reviewers`` value; anything but a list of strings
    is unsettled."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise YamlError("unsupported_yaml")
    return [v for v in value if isinstance(v, str)]


def _owners(doc: Value) -> tuple[list[str], list[str]]:
    """``(approvers, every owner)`` of one OWNERS document, filters included."""
    if doc is None:
        return [], []
    if not isinstance(doc, dict):
        raise YamlError("unsupported_yaml")
    sections: list[Mapping[str, Value]] = [doc]
    filters = doc.get("filters")
    if filters is not None:
        if not isinstance(filters, dict) or not all(isinstance(v, dict) for v in filters.values()):
            raise YamlError("unsupported_yaml")
        sections += [v for v in filters.values() if isinstance(v, dict)]
    approvers = [n for s in sections for n in _names(s.get("approvers"))]
    everyone = approvers + [n for s in sections for n in _names(s.get("reviewers"))]
    return approvers, everyone


def _aliases(root: Path, tracked: set[str]) -> dict[str, list[str]]:
    if OWNERS_ALIASES not in tracked:
        return {}
    doc = _yaml(root, OWNERS_ALIASES)
    table = doc.get("aliases") if isinstance(doc, dict) else None
    if not isinstance(table, dict):
        raise _Unsettled(OWNERS_ALIASES)
    try:
        return {k.lower(): [n.lower() for n in _names(v)] for k, v in table.items()}
    except YamlError as exc:
        raise _Unsettled(OWNERS_ALIASES) from exc


class _Scan:
    def __init__(self, root: Path, tracked: set[str]) -> None:
        self.root, self.tracked = root, tracked
        self.rules: list[ReviewRule] = []
        self.unsettled: set[str] = set()
        self.catch_all = False

    def credit(self, arm: str, rel: str) -> None:
        self.rules.append({"arm": arm, "path": rel})

    def workflows(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for rel in sorted(r for r in self.tracked if r.startswith(_WORKFLOWS)
                          and r.count("/") == 2 and r.endswith((".yml", ".yaml"))):
            try:
                out.append((rel, _read(self.root, rel)))
            except _Unsettled:
                self.unsettled.add(rel)
        return out

    def merge_queue(self, workflows: list[tuple[str, str]]) -> None:
        for rel, text in workflows:
            events = workflow_events(text)
            if events is None:
                self.unsettled.add(rel)
            elif "merge_group" in events:
                self.credit("merge_queue", rel)
        for rel in MERGIFY_FILES:
            if rel in self.tracked:
                self.guard(rel, lambda r: self._mergify(r))

    def _mergify(self, rel: str) -> None:
        doc = _yaml(self.root, rel)
        if not isinstance(doc, dict):
            raise _Unsettled(rel)
        rules = doc.get("queue_rules")
        if isinstance(rules, list) and rules:
            self.credit("merge_queue", rel)

    def guard(self, rel: str, read: Callable[[str], object]) -> None:
        try:
            read(rel)
        except (_Unsettled, ValueError):  # YamlError and JSONDecodeError are ValueErrors
            self.unsettled.add(rel)

    def policy_bot(self) -> None:
        if POLICY_FILE in self.tracked:
            self.guard(POLICY_FILE, self._policy)

    def _policy(self, rel: str) -> None:
        doc = _yaml(self.root, rel)
        if not isinstance(doc, dict):
            raise _Unsettled(rel)
        policy, rules = doc.get("policy"), doc.get("approval_rules")
        approval = policy.get("approval") if isinstance(policy, dict) else None
        if not isinstance(approval, list) or not approval:
            return
        if not isinstance(rules, list):
            raise _Unsettled(rel)  # the policy names rules the file does not define
        by_name: dict[str, Mapping[str, Any]] = {
            name: r for r in rules if isinstance(r, dict) and isinstance(name := r.get("name"), str)}
        if _needs_approval(approval, by_name, conjunction=True):
            self.credit("policy_bot", rel)

    def prow_owners(self) -> None:
        files = sorted(r for r in self.tracked if PurePosixPath(r).name == OWNERS)
        if not files:
            return
        try:
            aliases = _aliases(self.root, self.tracked)
        except _Unsettled:
            self.unsettled.add(OWNERS_ALIASES)
            return
        with_approvers: list[str] = []
        everyone: set[str] = set()
        unread = False
        for rel in files:
            try:
                approvers, names = _owners(_yaml(self.root, rel))
            except (_Unsettled, YamlError):
                self.unsettled.add(rel)
                unread = True
                continue
            for name in names:
                everyone.update(aliases.get(name.lower(), [name.lower()]))
            if approvers:
                with_approvers.append(rel)
        if with_approvers and len(everyone) <= 1:
            # One owner for everything (#785), or none once aliases resolve: no second reader. An unread OWNERS file may
            # name another, so the catch-all is then not called either way.
            self.catch_all = not unread
            return
        for rel in with_approvers:
            self.credit("prow_owners", rel)

    def rulesets(self) -> None:
        found = {s["file"]: s for s in find_snapshots(self.root) if s["file"] in self.tracked}
        for rel in sorted(r for r in self.tracked if r.startswith(_RULESETS) and r.endswith(".json")):
            if rel not in found:
                self.guard(rel, lambda r: json.loads(_read(self.root, r)))
        for rel, snap in sorted(found.items()):
            doc = snap["doc"]
            if snap["kind"] == "ruleset":
                enforcement = doc.get("enforcement")
                if enforcement is None:
                    self.unsettled.add(rel)
                elif enforcement == "active" and doc.get("target", "branch") == "branch" and any(
                        _rule_counts(r) for r in doc["rules"]):
                    self.credit("ruleset_json", rel)
            elif snap["kind"] == "branch_protection" and _approvals(
                    doc.get("required_pull_request_reviews")) >= 1:
                self.credit("ruleset_json", rel)

    def guides(self, workflows: list[tuple[str, str]]) -> None:
        dirs = sorted({p for r in self.tracked for p in _parents(r)
                       if _GUIDE_DIR.fullmatch(PurePosixPath(p).name.lower())})
        if not dirs:
            return
        texts = [_without_on(text) for _, text in workflows
                 if (events := workflow_events(text)) is not None and set(events) & REVIEW_EVENTS]
        for guide in dirs:
            named = re.compile(r"(?<![\w./-])(?:\./)?" + re.escape(guide) + r"(?![\w.-])")
            if any(named.search(text) for text in texts):
                self.credit("review_guide", guide)


def _required_count(rule: Mapping[str, Any]) -> int:
    requires = rule.get("requires")
    count = requires.get("count") if isinstance(requires, dict) else None
    return count if isinstance(count, int) and not isinstance(count, bool) else 0


def _needs_approval(expr: Any, rules: Mapping[str, Mapping[str, Any]], conjunction: bool) -> bool:
    """Whether a policy-bot ``approval`` expression always needs an approval.

    A list is an ``and`` (``conjunction``) or the members of an ``or``. A rule name needs one when
    its ``requires.count`` is 1 or more and it has no ``if``: policy-bot skips a conditional rule
    for the changes it does not match. An ``or`` needs one only when every branch does, so an
    unconditional auto-approve (``count`` 0, no ``if``) clears it; a conditional auto-approve
    (with ``if``) clears only the changes it matches and is left out. A name the file does not
    define, or any other shape, is unsettled."""
    if isinstance(expr, str):
        if expr not in rules:
            raise _Unsettled(expr)
        rule = rules[expr]
        return "if" not in rule and _required_count(rule) >= 1  # a conditional rule skips other changes
    if isinstance(expr, dict) and len(expr) == 1 and next(iter(expr)) in ("and", "or"):
        key, members = next(iter(expr.items()))
        return _needs_approval(members, rules, conjunction=key == "and")
    if not isinstance(expr, list) or not expr:
        raise _Unsettled("approval")
    if conjunction:
        return any(_needs_approval(e, rules, conjunction=True) for e in expr)
    branches = [e for e in expr if not (isinstance(e, str) and e in rules and "if" in rules[e]
                                        and _required_count(rules[e]) < 1)]
    return bool(branches) and all(_needs_approval(e, rules, conjunction=True) for e in branches)


def _without_on(text: str) -> str:
    """A workflow's text without its top-level ``on:`` block, so a ``paths:`` filter that names a
    folder is not read as the workflow using it."""
    out: list[str] = []
    skipping = False
    for line in text.splitlines():
        if line[:1] not in ("", " ", "\t", "#", "-"):
            skipping = line.split(":", 1)[0].strip().strip("'\"") in ("on", "true")
        if not skipping:
            out.append(strip_comment(line))
    return "\n".join(out)


def _approvals(params: Any) -> int:
    n = params.get("required_approving_review_count") if isinstance(params, dict) else None
    return n if isinstance(n, int) and not isinstance(n, bool) else 0


def _rule_counts(rule: Any) -> bool:
    kind = rule.get("type") if isinstance(rule, dict) else None
    return kind == "merge_queue" or (kind == "pull_request" and _approvals(rule.get("parameters")) >= 1)


def _parents(rel: str) -> list[str]:
    parts = PurePosixPath(rel).parts[:-1]
    return ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]


def _kept(rel: str) -> bool:
    return not any(part in _EXCLUDED_DIRS for part in PurePosixPath(rel).parts[:-1])


def scan_review_rules(repo_root: Path) -> ReviewRulesBlock:
    """Build the ``review_rules`` block for one repository (see the module docstring)."""
    root = repo_root.resolve()
    tracked_paths = tracked_files(root)
    if tracked_paths is None:
        return {"available": False, "reason": "git_tracking_unknown: not a git checkout, or git failed",
                "present": None, "arms": [], "rules": [], "catch_all_owners": False, "unsettled": []}
    tracked: set[str] = set()
    for path in tracked_paths:
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if _kept(rel):
            tracked.add(rel)
    scan = _Scan(root, tracked)
    workflows = scan.workflows()
    scan.merge_queue(workflows)
    scan.policy_bot()
    scan.prow_owners()
    scan.rulesets()
    scan.guides(workflows)
    rules = sorted(scan.rules, key=lambda r: (ARMS.index(r["arm"]), r["path"]))
    arms = [arm for arm in ARMS if any(r["arm"] == arm for r in rules)]
    unsettled = sorted(scan.unsettled)
    return {"available": True, "present": True if arms else (None if unsettled else False),
            "arms": arms, "rules": rules, "catch_all_owners": scan.catch_all, "unsettled": unsettled}


def rules_or_unknown(repo_root: Path) -> ReviewRulesBlock:
    """``scan_review_rules``, or an unavailable block (``scan_failed``) when the reader raises."""
    try:
        return scan_review_rules(repo_root)
    except Exception:  # noqa: BLE001 - one broken reader never stops the run
        return {"available": False, "reason": "scan_failed", "present": None, "arms": [], "rules": [],
                "catch_all_owners": False, "unsettled": []}


def committed_merge_queue(block: Mapping[str, Any] | None) -> bool | None:
    """Whether a ``review_rules`` block credits a committed merge queue: None when the block is
    missing or unavailable, or credits none while a file was unsettled."""
    if not isinstance(block, Mapping) or block.get("available") is not True:
        return None
    if "merge_queue" in (block.get("arms") or ()):
        return True
    return None if block.get("unsettled") else False
