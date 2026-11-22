"""Builds the fixture fleet runs under ``tests/fixtures/fleet_heatmap/`` (issue #570).

The ``groups.jsonl`` files there are the real ``lib/fleet/fleet_groups.roll_up`` output
for the synthetic repository rows below. ``test_fleet_heatmap.py`` fails when the
committed files differ from a fresh build; regenerate them (then the goldens) with
``uv run --locked python tests/fleet_heatmap_fixture.py`` from ``skills/assess``.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import lib.fleet.fleet_groups as fg  # noqa: E402
from lib.fleet.runner import FLEET_ROW_VERSION, FleetRow  # noqa: E402
from lib.levels import load_levels  # noqa: E402
from lib.run_context_types import JsonDict  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fleet_heatmap"
LONG_GROUP = "platform/payments/settlement-and-reconciliation/batch-services"


def _row(repo: str, scans: JsonDict | None) -> FleetRow:
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": repo.rsplit("/", 1)[0],
            "status": "ok" if scans is not None else "failed",
            "reason": None if scans is not None else "not_a_git_work_tree",
            "signals_schema_version": 1 if scans is not None else None, "scans": scans}


def _scans(lines: int, tooling: bool = True) -> JsonDict:
    scans: JsonDict = {"nested_instructions": {"budget": {"claude_code": {"lines": lines}}}}
    if tooling:
        scans["shared_tooling"] = {"available": True, "marketplace_ids": [], "ci_source_ids": []}
    return scans


def _fragment(scope: str, two_factor: str) -> JsonDict:
    reason = "denied (HTTP 403)" if two_factor == "unknown" else None
    return {"scope": scope, "provider": "gitlab", "two_factor": {"value": two_factor, "reason": reason}}


def build(out: Path) -> None:
    """Write ``out/groups.jsonl`` (this run) and ``out/earlier/groups.jsonl``."""
    maturity_model = load_levels(FIXTURES / "levels.toml")
    settings = {"grpa": _fragment("grpa", "on"), "grpb": _fragment("grpb", "off"),
                "grpc": _fragment("grpc", "unknown")}
    now = [_row("grpa/svc1", _scans(20)), _row("grpa/svc2", _scans(5)),
           _row("grpb/lib1", _scans(0)),
           _row("grpc/svc1", _scans(9)), _row("grpc/broken", None),
           _row(f"{LONG_GROUP}/api", _scans(3, tooling=False))]
    then = [_row("grpa/svc1", _scans(20)), _row("grpa/svc2", _scans(0)),
            _row("grpb/lib1", _scans(4)), _row("grpc/svc1", _scans(9)),
            _row("grpd/old", _scans(1))]
    fg.write_groups(out, fg.roll_up(now, maturity_model, settings))
    fg.write_groups(out / "earlier", fg.roll_up(then, maturity_model, settings))


if __name__ == "__main__":
    build(FIXTURES)
