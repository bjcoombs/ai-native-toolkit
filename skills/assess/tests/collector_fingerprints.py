"""Collector fingerprints over the fleet golden: a collector's output may not change under the same revision (#952).

``tests/fixtures/fleet_golden/collector_fingerprints.json`` holds, per core collector whose
blocks appear in the fleet golden (``tests/fixtures/fleet_golden/repos.jsonl``), its
``revision`` from ``skills/assess/checks/core.toml`` and, per golden repository, the sha256 of
its blocks in that repository's row. When a collector's golden output changes, its revision
must be bumped too: ``violations`` names each collector whose hash moved, in a repository
present on both sides, under an unchanged revision, and this script refuses to record it. A
bumped revision, a collector new to the golden, and a repository added to or dropped from the
golden (a fixture change, not a collector change) are recorded as they are.

Regenerate after the fleet golden (``make regen`` runs both):

    cd skills/assess && uv run --locked python tests/collector_fingerprints.py

A missing fingerprints file is refused, so deleting it cannot clear the guard; ``--init``
starts a new one.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from lib.manifest.check_manifest import collectors, core_manifest  # noqa: E402

GOLDEN = HERE / "fixtures" / "fleet_golden" / "repos.jsonl"
FINGERPRINTS = HERE / "fixtures" / "fleet_golden" / "collector_fingerprints.json"


def golden_rows(path: Path = GOLDEN) -> list[dict[str, Any]]:
    return sorted((json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()),
                  key=lambda row: row["repo"])


def output_hashes(rows: list[dict[str, Any]], publishes: Mapping[str, list[str]]) -> dict[str, dict[str, str]]:
    """Collector id -> {repo: sha256 of its blocks in that repo's row}, for the repos holding one of its blocks."""
    out: dict[str, dict[str, str]] = {}
    for cid, blocks in publishes.items():
        per_repo: dict[str, str] = {}
        for row in rows:
            scans = row.get("scans") or {}
            found = {b: scans[b] for b in blocks if b in scans}
            if found:
                text = json.dumps(found, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                per_repo[row["repo"]] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if per_repo:
            out[cid] = per_repo
    return out


def violations(stored: Mapping[str, Mapping[str, Any]], hashes: Mapping[str, Mapping[str, str]],
               revisions: Mapping[str, int]) -> list[str]:
    """Each collector whose output changed, in a golden repository present on both sides, while its
    revision did not. A repository added to or dropped from the golden is no violation."""
    out: list[str] = []
    for cid, now in sorted(hashes.items()):
        then = stored.get(cid)
        if then is None or then["revision"] != revisions[cid]:
            continue
        moved = sorted(repo for repo in now.keys() & then.get("repos", {}).keys() if now[repo] != then["repos"][repo])
        if moved:
            out.append(f"{cid}: its output in the fleet golden changed ({', '.join(moved)}) but its revision "
                       f"is still {revisions[cid]}; bump `revision` in its skills/assess/checks/core/ fragment")
    return out


def fingerprints(hashes: Mapping[str, Mapping[str, str]], revisions: Mapping[str, int]) -> dict[str, dict[str, Any]]:
    return {cid: {"revision": revisions[cid], "repos": dict(sorted(per_repo.items()))}
            for cid, per_repo in sorted(hashes.items())}


def current() -> tuple[dict[str, dict[str, str]], dict[str, int]]:
    """The golden's output hashes and the manifest's revisions."""
    entries = collectors(core_manifest())
    hashes = output_hashes(golden_rows(), {c["id"]: c["publishes"] for c in entries})
    return hashes, {c["id"]: c["revision"] for c in entries}


def read_stored(path: Path = FINGERPRINTS) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    data: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["collectors"]
    return data


def dumps(prints: Mapping[str, Mapping[str, Any]]) -> str:
    return json.dumps({"collectors": prints}, sort_keys=True, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Record the fingerprints; refuse when the file is missing (it would record any output as
    unchanged) unless ``--init`` asks to start it."""
    args = sys.argv[1:] if argv is None else argv
    if not FINGERPRINTS.is_file() and "--init" not in args:
        print(f"{FINGERPRINTS.name} is missing: restore it from git, or pass --init to start a new one",
              file=sys.stderr)
        return 1
    hashes, revisions = current()
    found = violations(read_stored(), hashes, revisions)
    if found:
        print("\n".join(found), file=sys.stderr)
        return 1
    FINGERPRINTS.write_text(dumps(fingerprints(hashes, revisions)), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
