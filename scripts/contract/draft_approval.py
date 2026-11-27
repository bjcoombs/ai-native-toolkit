#!/usr/bin/env python3
"""Whether a drafted contract is approved: the start gate's draft check (issue #1328).

A contract drafted by `draft.py` from the ready queue is approved in one step:
merging the pull request that adds it to the default branch. `start_gate.py`
asks this module before it accepts freeze evidence. A contract without the
draft marker (`draft.DRAFT_MARKER` on its first line) was written by hand and
needs no approval here; its author froze it.

A draft is approved when all three hold:

1. the draft file is present on the approval ref (the default branch, read
   from `origin/HEAD`, or the ref the caller names);
2. the working copy is byte-identical to that committed copy, so the run
   starts on what was merged;
3. the freeze evidence's contract hash is the hash of that committed copy, so
   what was frozen is what was approved.

Anything else, including a ref git cannot resolve, refuses with the reason
named. The check reads git; it never fetches, so a clone whose `origin/HEAD`
predates the merge refuses until it is fetched.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import draft


def _git(cwd: Path, args: List[str]) -> Optional[bytes]:
    try:
        proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=False)
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def _text(raw: Optional[bytes]) -> Optional[str]:
    if raw is None:
        return None
    out = raw.decode("utf-8", errors="replace").strip()
    return out or None


def default_ref(cwd: Path) -> Optional[str]:
    """The default branch as `origin/<name>`, from `origin/HEAD`; None when unset."""
    return _text(_git(cwd, ["rev-parse", "--abbrev-ref", "origin/HEAD"]))


def _frozen_hash(record: Dict[str, Any]) -> str:
    evidence = record.get("freeze_evidence")
    value = evidence.get("contract_hash") if isinstance(evidence, dict) else None
    text = str(value or "")
    return text[len("sha256:"):] if text.startswith("sha256:") else text


def refusal(contract_dir: Path, run_id: str, record: Dict[str, Any],
            approved_ref: Optional[str] = None) -> Optional[str]:
    """The reason the run's drafted contract is not approved, or None.

    None also when the run's contract is not a draft, or there is no contract
    file: those paths are the freeze evidence's concern alone.
    """
    path = draft.contract_path(contract_dir, run_id)
    try:
        working = path.read_bytes()
    except OSError:
        return None
    if not draft.is_draft(working.decode("utf-8", errors="replace")):
        return None
    folder = path.parent if path.parent.is_dir() else Path(".")
    ref = approved_ref or default_ref(folder)
    if not ref:
        return ("cannot resolve the default branch (origin/HEAD) to check the draft's approval; "
                "pass --approved-ref <ref>")
    top = _text(_git(folder, ["rev-parse", "--show-toplevel"]))
    if top is None:
        return "the draft %s is not in a git repository, so it cannot be approved by a merge" % path
    try:
        rel = path.resolve().relative_to(Path(top).resolve()).as_posix()
    except ValueError:
        return "the draft %s is outside its git repository" % path
    committed = _git(Path(top), ["show", "%s:%s" % (ref, rel)])
    if committed is None:
        return ("the drafted contract %s is not on %s: merge its pull request to approve it"
                % (rel, ref))
    approved = hashlib.sha256(committed).hexdigest()
    if hashlib.sha256(working).hexdigest() != approved:
        return "the working copy of %s differs from the approved draft on %s" % (rel, ref)
    if _frozen_hash(record) != approved:
        return ("the freeze evidence is for a different contract than the approved draft on %s: "
                "freeze the approved draft" % ref)
    return None
