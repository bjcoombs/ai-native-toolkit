"""Tests for the fleet agent-building roll-up and clusters (issue #689)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from lib.agent_building import scan_agent_building
from lib.counts_only import counts_only_scans
from lib.fleet import FLEET_ROW_VERSION, FleetRow
from lib.fleet_agent_building import (
    CLUSTERS_FILE,
    clusters,
    group_counts,
    jaccard,
    write_clusters,
)
from lib.fleet_groups import roll_up


def _row(repo: str, block: dict[str, object] | None, *, failed: bool = False) -> FleetRow:
    scans: dict[str, object] | None = None if failed else ({"agent_building": block} if block else {})
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": repo.rsplit("/", 1)[0],
            "status": "failed" if failed else "ok", "reason": "scan_timeout" if failed else None,
            "signals_schema_version": None if failed else 1, "scans": scans,  # type: ignore[typeddict-item]
            "active": None if failed else True}


def _block(cls: str, sdks: list[str], pairing: bool = False) -> dict[str, object]:
    return {"available": True, "agent_class": cls, "sdks": sdks, "pairing": pairing}


def test_group_counts_per_class() -> None:
    rows = [
        _row("g/a", _block("builds-agent", ["anthropic"])),
        _row("g/b", _block("uses-agent-tooling", [])),
        _row("g/c", _block("none", [])),
        _row("g/d", {"available": False, "reason": "x"}),
        _row("g/e", None, failed=True),
    ]
    assert group_counts(rows) == {"repos": 3, "builds_agent": 1, "builds_mcp_server": 0,
                                  "uses_agent_tooling": 1, "none": 1}


def test_group_counts_unknown_without_evidence_is_null_not_zero() -> None:
    assert group_counts([_row("g/a", None), _row("g/b", None, failed=True)]) is None


def test_group_rows_carry_agent_building_counts() -> None:
    rows = [_row("g1/a", _block("builds-mcp-server", ["mcp"])), _row("g2/b", None)]
    by_group = {row["group"]: row for row in roll_up(rows, None, {})}
    assert by_group["g1"]["agent_building"] == {"repos": 1, "builds_agent": 0, "builds_mcp_server": 1,
                                                "uses_agent_tooling": 0, "none": 0}
    assert by_group["g2"]["agent_building"] is None


def test_jaccard() -> None:
    assert jaccard(frozenset({"a", "b"}), frozenset({"a"})) == 0.5
    assert jaccard(frozenset(), frozenset()) == 0.0


def test_similar_builders_in_different_groups_form_one_cluster() -> None:
    rows = [
        _row("payments/ticket-bot", _block("builds-agent", ["anthropic", "mcp"], pairing=True)),
        _row("platform/mr-agent", _block("builds-agent", ["anthropic"], pairing=True)),
        # same SDKs, different pairing marker: not linked
        _row("data/chat", _block("builds-agent", ["anthropic"], pairing=False)),
        # Jaccard 1/3 with ticket-bot: not linked
        _row("web/lc", _block("builds-agent", ["langchain", "openai", "anthropic"], pairing=True)),
        # not builds-agent: never clustered
        _row("web/srv", _block("builds-mcp-server", ["anthropic", "mcp"], pairing=True)),
    ]
    result = clusters(rows)
    assert result["builds_agent_repos"] == 4
    assert result["evidence_repos"] == 5
    assert result["clustered_repos"] == 2
    assert result["clusters"] == [{
        "id": "c1", "agent_class": "builds-agent", "size": 2, "groups": 2,
        "sdks": ["anthropic", "mcp"], "shared_sdks": ["anthropic"], "pairing": True,
    }]


def test_clusters_are_connected_components() -> None:
    # a~b (2/3) and b~c (2/4) link a and c through b although a~c is only 1/4;
    # d~e is a second, smaller component and f a singleton, which is not a cluster.
    rows = [
        _row("g1/a", _block("builds-agent", ["x", "y"])),
        _row("g2/b", _block("builds-agent", ["x", "y", "z"])),
        _row("g3/c", _block("builds-agent", ["y", "z", "w"])),
        _row("g4/d", _block("builds-agent", ["q"])),
        _row("g4/e", _block("builds-agent", ["q"])),
        _row("g5/f", _block("builds-agent", ["solo"])),
    ]
    result = clusters(rows, name=True)
    assert [(c["id"], c["size"], c["groups"]) for c in result["clusters"]] == [("c1", 3, 3), ("c2", 2, 1)]
    assert result["clusters"][0]["repo_ids"] == ["g1/a", "g2/b", "g3/c"]
    assert result["clusters"][1]["group_ids"] == ["g4"]


def test_empty_sdk_sets_never_cluster() -> None:
    rows = [_row("g/a", _block("builds-agent", [])), _row("h/b", _block("builds-agent", []))]
    assert clusters(rows)["clusters"] == []


def test_counts_only_without_naming_carries_no_repository_path(tmp_path: Path) -> None:
    # Two fixture repositories that build similar agents, scanned for real and
    # reduced by the counts-only filter, as a default fleet run would.
    loop = 'if response.stop_reason == "tool_use":\n    pass\n'
    rows: list[FleetRow] = []
    for repo_id in ("team-alpha/ticket-agent", "team-beta/mr-agent"):
        repo = tmp_path / repo_id
        repo.mkdir(parents=True)
        (repo / "requirements.txt").write_text("anthropic\npython-gitlab\njira\n", encoding="utf-8")
        (repo / "agent.py").write_text(loop, encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        scans = counts_only_scans({"agent_building": scan_agent_building(repo)}, hash_paths=True, salt="s")
        rows.append({"fleet_row_version": FLEET_ROW_VERSION, "repo": repo_id, "group": repo_id.split("/")[0],
                     "status": "ok", "reason": None, "signals_schema_version": 1, "scans": scans,
                     "active": True})
    result = clusters(rows)
    assert len(result["clusters"]) == 1
    assert result["clusters"][0]["groups"] == 2
    path = write_clusters(tmp_path / "out", result)
    text = path.read_text(encoding="utf-8")
    for name in ("team-alpha", "team-beta", "ticket-agent", "mr-agent"):
        assert name not in text
    named = write_clusters(tmp_path / "out", clusters(rows, name=True)).read_text(encoding="utf-8")
    assert "team-alpha/ticket-agent" in named
    # A rerun without the naming flag replaces the named file.
    write_clusters(tmp_path / "out", clusters(rows))
    assert "ticket-agent" not in (tmp_path / "out" / CLUSTERS_FILE).read_text(encoding="utf-8")
    assert json.loads(text)["named"] is False
