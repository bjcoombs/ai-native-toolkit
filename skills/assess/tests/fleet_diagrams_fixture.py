"""Builds the fixture fleet run under ``tests/fixtures/fleet_diagrams/`` (issue #674).

``repos.jsonl`` there is synthetic repository rows written by ``lib/fleet.write_rows``;
``test_fleet_diagrams.py`` fails when the committed file differs from a fresh build.
Regenerate it and the golden renders with ``uv run --locked python tests/fleet_diagrams_fixture.py``
from ``skills/assess``, then review the diff.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.fleet import FLEET_ROW_VERSION, FleetRow, write_rows  # noqa: E402
from lib.fleet_diagrams import build_diagrams, report_markdown  # noqa: E402
from lib.fleet_view import read_repos  # noqa: E402
from lib.rubric import load_rubric  # noqa: E402
from lib.run_context_types import JsonDict  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fleet_diagrams"


def _include(source: str, ref: str | None, tracked: bool = True) -> JsonDict:
    return {"kind": "gitlab_project", "id": f"gitlab_project:{source}", "ref": ref,
            "path": ".gitlab-ci.yml", "line": 2, "tracked": tracked}


def _scans(lines: int | None, commits: int | None, agent: int = 0,
           ci: tuple[tuple[str, str | None], ...] = (), markets: tuple[str, ...] = (),
           tooling: bool = True) -> JsonDict:
    scans: JsonDict = {}
    if lines is not None:
        scans["nested_instructions"] = {"budget": {"claude_code": {"lines": lines}}}
    if commits is not None:
        scans["ai_usage"] = {"available": True, "window": "6mo", "total_commits": commits,
                             "agent_trailer_commits": agent}
    else:
        scans["ai_usage"] = {"available": False}
    if tooling:
        includes = [_include(source, ref) for source, ref in ci]
        scans["shared_tooling"] = {
            "available": True, "ci_includes": includes, "marketplaces": [],
            "ci_source_ids": sorted({e["id"] for e in includes}),
            "marketplace_ids": sorted(f"marketplace:{m}" for m in markets),
        }
    return scans


def _row(repo: str, scans: JsonDict | None) -> FleetRow:
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": repo.rsplit("/", 1)[0],
            "status": "ok" if scans is not None else "failed",
            "reason": None if scans is not None else "scan_timeout",
            "signals_schema_version": 1 if scans is not None else None, "scans": scans,
            "active": None if scans is None else True}


ROWS: tuple[FleetRow, ...] = (
    _row("payments/api", _scans(10, 212, 3, (("ci/python", "v4"),), ("platform",))),
    _row("payments/ledger", _scans(5, 168, 2, (("ci/python", "v3"),), ("platform",))),
    _row("payments/fraud", _scans(80, 61, 30, (("ci/python", "v4"),))),
    _row("platform/gateway", _scans(90, 140, 45, (("ci/go", "v2"),), ("platform",))),
    _row("platform/auth", _scans(70, 138, 41, (("ci/go", "v2"),), ("platform",))),
    _row("platform/infra", _scans(60, 38, 5, (("ci/go", "v2"),))),
    _row("data/pipeline", _scans(20, 176, 12, (("ci/python", "v4"),), ("data",))),
    _row("data/warehouse", _scans(60, 24, 0, (("ci/python", "v3"),), ("data",))),
    _row("data/notebooks", _scans(None, 12)),
    _row("web/storefront", _scans(40, 254, 50, (("ci/node", "v5"),), ("platform",))),
    _row("web/admin", _scans(0, 83, 0, (("ci/node", "v5"),))),
    _row("web/design<&>\"sys", _scans(90, 47, 20, (("ci/node", "v4"),), ("platform",))),
    _row("mobile/ios", _scans(0, 121, 1, (("ci/mobile", None),))),
    _row("mobile/android", None),
    _row("mobile/watch", _scans(30, None, tooling=False)),
)


WINDOW = "6mo"


def build(out: Path) -> None:
    """Write ``out/repos.jsonl``."""
    write_rows(out, list(ROWS))


def goldens(out: Path) -> dict[str, str]:
    """The golden renders of the run in ``out``: ``expected-<file>`` -> text."""
    diagrams = build_diagrams(load_rubric(out / "levels.toml"), read_repos(out), WINDOW)
    files = {f"expected-{d['file']}": d["svg"] for d in diagrams if d["svg"] is not None}
    return {**files, "expected-report.md": report_markdown(diagrams)}


if __name__ == "__main__":
    build(FIXTURES)
    for name, content in goldens(FIXTURES).items():
        (FIXTURES / name).write_text(content, encoding="utf-8", newline="\n")
