"""Check a fleet repos.jsonl from the Windows smoke runs (.github/workflows/windows.yml).

Usage: python check_fleet_rows.py REPOS_JSONL ID [ID ...]

Passes when the file is UTF-8 with LF endings and holds exactly one row per
named repository id, each with status "ok" and a scans object.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    path, expected = Path(argv[0]), sorted(argv[1:])
    data = path.read_bytes()
    assert b"\r" not in data, f"{path} has CR bytes"
    rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line]
    ids = sorted(row["repo"] for row in rows)
    assert ids == expected, f"ids {ids} != {expected}"
    for row in rows:
        assert row["status"] == "ok", f"{row['repo']}: {row['status']} ({row.get('reason')})"
        assert isinstance(row["scans"], dict) and row["scans"], f"{row['repo']}: no scans"
    print(f"{path}: {len(rows)} ok rows: {', '.join(ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
