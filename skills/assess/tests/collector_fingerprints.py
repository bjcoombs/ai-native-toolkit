"""Collector fingerprints over the fleet golden: a collector's output may not change under the same revision (#952).

``tests/fixtures/fleet_golden/collector_fingerprints.json`` holds, per core collector whose
blocks appear in the fleet golden (``tests/fixtures/fleet_golden/repos.jsonl``), its
``revision`` from ``skills/assess/checks/core.toml`` and the sha256 of its blocks across the
golden's repositories. When a collector's golden output changes, its revision must be bumped
too: ``violations`` names each collector whose hash moved under an unchanged revision, and
this script refuses to record it. A bumped revision (or a collector new to the golden) is
recorded with its new hash.

Regenerate after the fleet golden (``make regen`` runs both):

    cd skills/assess && uv run --locked python tests/collector_fingerprints.py
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


def output_hashes(rows: list[dict[str, Any]], publishes: Mapping[str, list[str]]) -> dict[str, str]:
    """Collector id -> sha256 of its blocks in every row, for the collectors with a block in any row."""
    out: dict[str, str] = {}
    for cid, blocks in publishes.items():
        found = [[row["repo"], {b: row["scans"][b] for b in blocks if b in (row.get("scans") or {})}]
                 for row in rows]
        if not any(blocks_of for _repo, blocks_of in found):
            continue
        text = json.dumps(found, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        out[cid] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return out


def violations(stored: Mapping[str, Mapping[str, Any]], hashes: Mapping[str, str],
               revisions: Mapping[str, int]) -> list[str]:
    """Each collector whose output hash changed while its revision did not."""
    return [f"{cid}: its output in the fleet golden changed but its revision is still {revisions[cid]}; "
            f"bump `revision` in its skills/assess/checks/core/ fragment"
            for cid, sha in sorted(hashes.items())
            if cid in stored and stored[cid]["revision"] == revisions[cid] and stored[cid]["sha256"] != sha]


def fingerprints(hashes: Mapping[str, str], revisions: Mapping[str, int]) -> dict[str, dict[str, Any]]:
    return {cid: {"revision": revisions[cid], "sha256": sha} for cid, sha in sorted(hashes.items())}


def current() -> tuple[dict[str, str], dict[str, int]]:
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


def main() -> int:
    hashes, revisions = current()
    found = violations(read_stored(), hashes, revisions)
    if found:
        print("\n".join(found), file=sys.stderr)
        return 1
    FINGERPRINTS.write_text(dumps(fingerprints(hashes, revisions)), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
