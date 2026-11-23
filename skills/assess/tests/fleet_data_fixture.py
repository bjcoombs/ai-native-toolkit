"""Builds the golden ``fleet-data.json`` files under ``tests/fixtures/fleet_data/`` (issue #703).

A three-level GitLab fleet (group, subgroup, project) with a mapping file giving a
system view, evaluated against a two-row maturity model. ``test_fleet_data.py`` fails
when the committed files differ from a fresh build; after a deliberate change,
regenerate with ``uv run --locked python tests/fleet_data_fixture.py`` from
``skills/assess`` and review the diff.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.fleet.runner import FLEET_ROW_VERSION, FleetRow  # noqa: E402
from lib.fleet.fleet_data import FleetData, Privacy, build_fleet_data, dumps_fleet_data  # noqa: E402
from lib.fleet.fleet_stamp import FleetStamp  # noqa: E402
from lib.fleet.fleet_units import build_trees, parse_units, repo_owners  # noqa: E402
from lib.levels import MaturityModel, parse_levels  # noqa: E402
from lib.run_context_types import JsonDict  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fleet_data"
SALT = "fixture-salt"

LEVELS = """\
version = "fleet-data-1"

[[rows]]
id = "org2fa"
title = "Two-factor"
scope = "org"

  [[rows.levels]]
  level = 1
  truth = ["governance"]
  criteria = [{ signal = "two_factor.value", op = "eq", value = "on" }]

[[rows]]
id = "docs"
title = "Instruction docs"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  truth = ["agent_context", "docs"]  # two tags: in each tag's cell, once in the tally (#1073)
  criteria = [{ signal = "docs.lines", op = "gt", value = 0 }]

  [[rows.levels]]
  level = 2
  truth = ["docs"]
  criteria = [{ signal = "docs.grade", op = "in", value = ["A", "B"] }]

[[rows]]
id = "ci"
title = "CI gate"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  truth = ["behaviour"]
  criteria = [{ signal = "ci.required", op = "eq", value = true }]

# #944: fixture signals have no producer class, so their conditions are listed, never
# counted: the tally's met stays 0, and the gates (which read state) decide each Step.
[[steps]]
step = 1
name = "Assisted"
threshold = 0

[[steps]]
step = 2
name = "Docs"
threshold = 0
gates = [{ id = "docs", row = "docs", level = 1 }]

[[steps]]
step = 3
name = "Docs and CI"
threshold = 0
gates = [{ id = "docs", row = "docs", level = 1 }, { id = "ci", row = "ci", level = 1 }]
"""

UNITS = """\
[[unit]]
view = "system"
id = "checkout"
kind = "system"
name = "Checkout"
owners = ["@grp/checkout-leads"]

[[repo]]
repo = "grp/sub/proj1"
system = "checkout"

[[repo]]
repo = "grp/proj4"
system = "checkout"
owners = ["@grp/payments"]
"""


def _row(repo: str, scans: JsonDict | None, reason: str = "scan_timeout") -> FleetRow:
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": repo.rsplit("/", 1)[0],
            "status": "ok" if scans is not None else "failed", "reason": None if scans is not None else reason,
            "signals_schema_version": 1 if scans is not None else None, "scans": scans,
            "active": True if scans is not None else None}


ROWS: list[FleetRow] = [
    # level 2 docs, level 1 ci; the owner excluded ci.1, so Step 3 stays unreached (#944)
    _row("grp/sub/proj1", {"docs": {"available": True, "lines": 40, "grade": "A"},
                           "ci": {"available": True, "required": True},
                           "owner_exclusions": {"exclusions": [
                               {"id": "ci.1", "kind": "measured_elsewhere", "source": "repo",
                                "reason": "CI runs in the release pipeline repository.",
                                "excluded_by": "maintainer", "excluded_at": "2026-11-22", "version": "2.2.0"}],
                               "skipped": [], "disclosures": []}}),
    # level 1 docs (grade C holds it back), level 0 ci; scanned by unit (#866): apps/api reads
    # its own docs at grade A, apps/web its own empty docs, and both inherit the root ci
    _row("grp/sub/proj2", {"docs": {"available": True, "lines": 12, "grade": "C"},
                           "ci": {"available": True, "required": False},
                           "unit_readings": {"available": True, "unit_count": 2, "unit_count_reason": None,
                                             "unit_blocks": ["docs"], "units": [
                               {"path": "apps/api", "blocks": {"docs": {"available": True, "lines": 30, "grade": "A"}},
                                "conditions": [{"id": "docs.1", "origin": "unit"}, {"id": "docs.2", "origin": "unit"},
                                               {"id": "ci.1", "origin": "inherited"}]},
                               {"path": "apps/web", "blocks": {"docs": {"available": True, "lines": 0, "grade": "F"}},
                                "conditions": [{"id": "docs.1", "origin": "unit"}, {"id": "docs.2", "origin": "unit"},
                                               {"id": "ci.1", "origin": "inherited"}]}]}}),
    # docs unknown (block unavailable), ci level 1
    _row("grp/other/proj3", {"docs": {"available": False, "reason": "unavailable"},
                             "ci": {"available": True, "required": True}}),
    _row("grp/proj4", None),
]

STAMP: FleetStamp = {
    "fleet_stamp_version": 1, "toolkit_commit": None, "plugin_version": "0.0.0-fixture",
    "scan_date": "2026-11-20", "window": "6mo", "levels_version": "fleet-data-1", "counts_only": True,
    "paths_hashed": False, "scans": None, "fleet_config_sha256": None, "fleet_config_entries": None,
    "platform": False,
    "repositories": {"selected": 4, "scanned_ok": 3, "failed": 1, "qualifying": 3},
}
WEB_URLS = {"grp/sub/proj1": "https://gitlab.example.com/grp/sub/proj1",
            "grp/sub/proj2": "https://gitlab.example.com/grp/sub/proj2",
            "grp/other/proj3": None, "grp/proj4": "https://gitlab.example.com/grp/proj4"}
CODEOWNERS = {"grp/sub/proj2": ["@grp/sub/maintainers", "bob@example.com"]}  # nested: owners only


def model() -> MaturityModel:
    return parse_levels(tomllib.loads(LEVELS))


def build(privacy: Privacy) -> FleetData:
    mapping = parse_units(tomllib.loads(UNITS))
    repos = [row["repo"] for row in ROWS]
    stamp: FleetStamp = {**STAMP, "counts_only": not privacy.full, "paths_hashed": privacy.salt is not None}
    return build_fleet_data(stamp, ROWS, model(), build_trees(repos, mapping, {}, CODEOWNERS),
                            owners=repo_owners(mapping, CODEOWNERS, repos), web_urls=WEB_URLS, privacy=privacy)


VARIANTS = {"full.json": Privacy(full=True), "counts-only.json": Privacy(), "hashed.json": Privacy(salt=SALT)}


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, privacy in VARIANTS.items():
        (FIXTURES / name).write_text(dumps_fleet_data(build(privacy)), encoding="utf-8")
        print(f"wrote {FIXTURES / name}")


if __name__ == "__main__":
    main()
