"""Tests for lib/scan_block_schemas.py: the described scan blocks (issue #779)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, get_type_hints

import jsonschema
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.ai_usage import AiUsageBlock  # noqa: E402
from lib.ci_checks import CiChecksBlock  # noqa: E402
from lib.counts_only import counts_only_scans  # noqa: E402
from lib.coverage_gate import CoverageGateBlock  # noqa: E402
from lib.delivery import DeliveryBlock  # noqa: E402
from lib.guardrails import GuardrailsBlock  # noqa: E402
from lib.fleet_schema import build_schema as build_row_schema  # noqa: E402
from lib.scan_block_schemas import BLOCK_SCHEMAS  # noqa: E402
from lib.signals import collect  # noqa: E402
from lib.signals_schema import REASON_KEPT, build_schema, described_block  # noqa: E402

TYPED = {"ai_usage": AiUsageBlock, "ci_checks": CiChecksBlock, "coverage_gate": CoverageGateBlock,
         "delivery": DeliveryBlock, "guardrails": GuardrailsBlock}


@pytest.mark.parametrize("name", sorted(BLOCK_SCHEMAS))
def test_every_described_field_is_a_field_of_the_block(name: str) -> None:
    described = set(BLOCK_SCHEMAS[name]["properties"])
    assert described - {"reason"} <= set(get_type_hints(TYPED[name]))
    assert set(get_type_hints(TYPED[name])) <= described  # and none is left out


def test_both_published_schemas_describe_the_blocks() -> None:
    signals_scans = build_schema()["properties"]["scans"]["properties"]
    row_scans = next(b for b in build_row_schema()["properties"]["scans"]["anyOf"]
                     if b.get("type") == "object")["properties"]
    for name in BLOCK_SCHEMAS:
        assert signals_scans[name] == row_scans[name] == described_block(name)
    for field in ("agent_authored_commits", "agent_authored_share", "agent_either_commits",
                  "agent_either_share", "dependency_bot_commits", "token_user_commits",
                  "agent_identity_rule_commits"):
        assert field in signals_scans["ai_usage"]["properties"]
    assert {"test_or_lint_configured", "basis"} <= set(signals_scans["ci_checks"]["properties"])
    assert "hours" in signals_scans["delivery"]["properties"]["time_to_merge_hours_p50"]["description"]
    assert "review_automation" in row_scans


def _reason_texts(block: dict[str, Any]) -> list[str]:
    """Every ``reason`` description of a block schema: its field and its unavailable rule."""
    return [r["description"] for r in (block.get("properties", {}).get("reason", {}),
                                       block["then"]["properties"]["reason"]) if "description" in r]


def test_each_block_describes_only_its_own_reason_codes() -> None:
    """The structure codes are described under structure only (#795, from #790's review)."""
    for scans in (build_schema()["properties"]["scans"],
                  next(b for b in build_row_schema()["properties"]["scans"]["anyOf"]
                       if b.get("type") == "object")):
        for name in BLOCK_SCHEMAS:
            texts = _reason_texts(scans["properties"][name])
            assert texts and not [t for t in texts for code in ("no_grimp", "no_networkx",
                                                                "grimp_failed") if code in t], name
        structure = " ".join(_reason_texts(scans["properties"]["structure"]))
        for code in ("no_grimp", "no_networkx", "grimp_failed", "no_python_packages",
                     "no_import_graph_<language>", "invalid_import_graph", "no_source_files"):
            assert code in structure
        assert structure.endswith(REASON_KEPT)
        assert "no_grimp" not in " ".join(_reason_texts(scans["additionalProperties"]))
    assert _reason_texts(described_block("ai_usage"))[-1].endswith(REASON_KEPT)


@pytest.mark.parametrize(("name", "reason", "code"), [
    ("ai_usage", "git is not installed", "unavailable"),
    ("ci_checks", "no_ci: no CI file", "no_ci"),
    ("delivery", "no_access: HTTP 403", "no_access"),
    ("structure", "no_grimp: grimp not installed; static structure not assessed", "no_grimp"),
])
def test_the_reason_text_matches_the_counts_only_reduction(name: str, reason: str, code: str) -> None:
    """Every block's reason survives counts-only as its leading code, as REASON_KEPT says."""
    kept = counts_only_scans({name: {"available": False, "reason": reason}})[name]
    assert kept == {"available": False, "reason": code}
    jsonschema.validate(kept, described_block(name))


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env,
                   encoding="utf-8", errors="replace")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: pytest\n", encoding="utf-8")
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(root.parent, "init", "-q", str(root))
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Ann", "-c", "user.email=ann@example.com", "commit", "-qm",
         "init\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    return root


def _validate(scans: dict[str, Any]) -> None:
    for name, block in scans.items():
        jsonschema.validate(block, described_block(name))


def test_real_blocks_validate_full_and_counts_only(repo: Path) -> None:
    scans = collect(repo, ["ai_usage", "ci_checks"])
    assert scans["ai_usage"]["available"] is True
    assert scans["ci_checks"]["test_or_lint_configured"] is True
    _validate(scans)
    _validate(counts_only_scans(scans))


def test_delivery_blocks_validate_and_unavailable_needs_a_reason() -> None:
    full = {"available": True, "merged_count": 5, "time_to_merge_hours_p50": 6.0,
            "time_to_merge_hours_p90": 6.0, "first_human_comment_hours_p50": 1.0,
            "first_human_comment_hours_p90": 1.0, "human_commented_share": 1.0,
            "ai_review_share": 0.4, "approved_share": 0.6, "self_merged_share": 0.4,
            "agent_authored_share": None}
    refused = {"available": False, "reason": "no_access"}
    _validate({"delivery": full})
    _validate({"delivery": refused, "ai_usage": refused, "ci_checks": refused})
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"available": False}, described_block("delivery"))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**full, "merged_count": "five"}, described_block("delivery"))


def test_coverage_gate_blocks_validate_full_and_counts_only(repo: Path) -> None:
    (repo / "pyproject.toml").write_text(
        "[tool.coverage.report]\nfail_under = 80\n", encoding="utf-8")
    (repo / "pom.xml").write_text(
        "<project><!-- jacoco -->\n<minimum>0.55</minimum>\n</project>\n", encoding="utf-8")
    scans = collect(repo, ["coverage_gate"])
    assert [g["threshold_percent"] for g in scans["coverage_gate"]["gates"]] == [55.0, 80.0]
    assert scans["coverage_gate"]["threshold_meets_minimum"] is True
    _validate(scans)
    _validate(counts_only_scans(scans))
    _validate({"coverage_gate": {"available": False, "reason": "scan_failed"}})
