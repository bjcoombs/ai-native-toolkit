"""fleet.py over real assess_signals runs: an owner exclusion reaches level.json and fleet-data.json (#944).

The estate is three levels deep (group, subgroup, repository) and built from the
#744 contract files (``test_level_tally``): ``grp/sub/alpha`` and ``grp/sub/ci2`` have
tests, CI running them and an instruction file; ``grp/nci`` has no CI. ``alpha`` also
commits ``[checks] exclude`` for the coverage gate's condition, so it holds Step 3 and
the coverage gate keeps it from Step 4 while the exclusion stands.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lib.scoring.level_signals import DEFAULT_LEVELS
from test_fleet_cli import fleet_cli
from test_level_tally import CIWF, CLMD, FL5, _commit

CONFIG = """\
[checks]
exclude = [
  { id = "layer-6-coverage.1", kind = "measured_elsewhere", reason = "Coverage is enforced in the release pipeline." },
]
"""
REPOS = {"grp/sub/alpha": {**FL5, **CIWF, **CLMD, ".assess/config.toml": CONFIG},
         "grp/sub/ci2": {**FL5, **CIWF, **CLMD}, "grp/nci": FL5}


@pytest.fixture(scope="module")
def estate(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("fleet944")
    for name, files in REPOS.items():
        repo = root / name
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
        for rel, text in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text, encoding="utf-8", newline="\n")
        _commit(repo, "2026-11-01T10:00:00Z", "one")
    (root / "list.txt").write_text("".join(f"{name}\n" for name in REPOS), encoding="utf-8")
    return root


def _run(estate: Path, out: str, flags: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    cwd = Path.cwd()
    os.chdir(estate)
    try:
        assert fleet_cli.main(["--from-list", "list.txt", "--levels", str(DEFAULT_LEVELS), "--out", out,
                               "--no-diagrams", "--no-history", *flags]) == 0
    finally:
        os.chdir(cwd)
    read = lambda name: json.loads((estate / out / name).read_text(encoding="utf-8"))  # noqa: E731
    return read("level.json")["repos"], read("fleet-data.json")


@pytest.mark.parametrize("flags", [["--full"], ["--hash-paths"], ["--hash-salt", "s944"]])
def test_an_owner_exclusion_reaches_both_files_and_holds_the_step(estate, flags):
    tag = "-".join(flags).strip("-")
    levels, data = _run(estate, f"{tag}-1", flags)
    if flags != ["--hash-paths"]:  # --hash-paths alone draws a new salt per run
        _run(estate, f"{tag}-2", flags)
        for name in ("fleet-data.json", "level.json"):
            assert (estate / f"{tag}-1" / name).read_bytes() == (estate / f"{tag}-2" / name).read_bytes()
    assert {r["key"] for r in data["repos"]} == set(levels)  # the files join on the same ids
    for field in ("step", "next_step", "tally"):
        assert all(r[field] == levels[r["key"]][field] for r in data["repos"])
    (repo,) = [r for r in data["repos"] if r["exclusions"]]
    assert levels[repo["key"]]["excluded"] == [{"id": "layer-6-coverage.1", "kind": "measured_elsewhere",
                                               "source": "repo"}]
    assert repo["step"] == 3 and repo["next_step"]["step"] == 4
    (gate,) = [b for b in repo["next_step"]["blockers"] if b["kind"] == "gate"]
    assert (gate["gate"], gate["state"], gate["disposition"]) == (
        "coverage_gate", "not_applicable", "excluded_by_owner")
    reason = "Coverage is enforced in the release pipeline." if flags == ["--full"] else None
    assert repo["exclusions"] == [{"id": "layer-6-coverage.1", "kind": "measured_elsewhere", "source": "repo",
                                   "reason": reason}]
    if flags != ["--full"]:
        text = (estate / f"{tag}-1" / "fleet-data.json").read_text(encoding="utf-8")
        for name in ("release pipeline", "grp", "alpha", "nci", "ci2"):
            assert name not in text, name
    units = {u["key"]: u for v in data["views"] for u in v["units"] if v["view"] == "ownership"}
    (sub,) = [u for u in units.values() if u["repos"] == 2]
    assert (sub["step"]["lowest_step"], sub["step"]["measured"]) == (3, 2)
    (coverage,) = [g for g in sub["step"]["next_step"]["gates"] if g["gate"] == "coverage_gate"]
    assert (coverage["held"], coverage["excluded"]) == (2, 1)
