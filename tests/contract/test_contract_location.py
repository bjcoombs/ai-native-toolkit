"""Tests for the shared contract-folder resolver (`scripts/contract/contract_location.py`).

Covers issue #438:

- the resolution order (explicit value, then `ACCEPTANCE_CONTRACT_DIR`, then the
  default `.claude/contracts`), evaluated at call time against the cwd;
- the fallback for a repo that has only the legacy Task Master folder, and the
  new default winning when both folders exist;
- every contract script resolving through it, so a run driven end to end with
  only the environment variable set writes nothing outside the custom folder.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "contract"))
sys.path.insert(0, str(REPO / "scripts" / "canaries"))

import complete_gate as cg  # noqa: E402
import contract_location as cl  # noqa: E402
import freeze as fz  # noqa: E402
import record_readiness as rr  # noqa: E402
import run_canaries as rc  # noqa: E402
import spawn_verifier as sv  # noqa: E402
import start_gate as sg  # noqa: E402
import tiers  # noqa: E402
import validate_completion as vc  # noqa: E402
import verifier as vr  # noqa: E402

NEW = Path(".claude/contracts")
LEGACY = Path(".taskmaster/contract")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """An empty repo as the cwd, with no override in the environment."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(cl.ENV_CONTRACT_DIR, raising=False)
    return tmp_path


# --------------------------------------------------------------------------- #
# Resolution order and the legacy fallback.
# --------------------------------------------------------------------------- #


def test_default_is_claude_contracts_when_neither_folder_exists(repo):
    assert cl.resolve_contract_dir() == NEW


def test_legacy_folder_used_when_only_it_exists(repo):
    (repo / LEGACY).mkdir(parents=True)
    assert cl.resolve_contract_dir() == LEGACY


def test_new_default_wins_when_both_folders_exist(repo):
    (repo / LEGACY).mkdir(parents=True)
    (repo / NEW).mkdir(parents=True)
    assert cl.resolve_contract_dir() == NEW


def test_env_var_beats_default_and_legacy(repo, monkeypatch):
    (repo / LEGACY).mkdir(parents=True)
    monkeypatch.setenv(cl.ENV_CONTRACT_DIR, "custom")
    assert cl.resolve_contract_dir() == Path("custom")


def test_explicit_value_beats_env_var(repo, monkeypatch):
    monkeypatch.setenv(cl.ENV_CONTRACT_DIR, "custom")
    assert cl.resolve_contract_dir("flag") == Path("flag")
    assert cl.resolve_contract_dir(Path("flag")) == Path("flag")


def test_empty_explicit_and_env_fall_through_to_default(repo, monkeypatch):
    monkeypatch.setenv(cl.ENV_CONTRACT_DIR, "")
    assert cl.resolve_contract_dir("") == NEW


def test_resolution_happens_at_call_time_not_import(repo):
    """The fallback depends on the cwd when the function runs: creating the
    legacy folder after import changes the answer."""
    assert tiers.tier3_artifact_path("run", "c1") == NEW / "run" / "tier3-c1.artifact"
    (repo / LEGACY).mkdir(parents=True)
    assert tiers.tier3_artifact_path("run", "c1") == LEGACY / "run" / "tier3-c1.artifact"


def test_every_script_resolves_through_the_shared_module():
    """No contract script keeps a private default or resolver."""
    for mod in (sg, cg, fz, rr, sv, vc, tiers):
        assert not hasattr(mod, "DEFAULT_CONTRACT_DIR"), mod.__name__
        assert not hasattr(mod, "DEFAULT_PROVENANCE_DIR"), mod.__name__
        assert getattr(mod, "resolve_contract_dir", cl.resolve_contract_dir) is (
            cl.resolve_contract_dir
        ), mod.__name__


def test_spawn_verifier_cli_accepts_contract_dir(tmp_path, monkeypatch):
    monkeypatch.delenv(cl.ENV_CONTRACT_DIR, raising=False)
    ns = sv.build_parser().parse_args(["r.contract.md", "build/", "--contract-dir", "x"])
    assert ns.contract_dir == "x"


# --------------------------------------------------------------------------- #
# End to end: only ACCEPTANCE_CONTRACT_DIR set, nothing lands elsewhere.
# --------------------------------------------------------------------------- #


def _stage_contract(cdir: Path, run_id: str) -> Path:
    cdir.mkdir(parents=True, exist_ok=True)
    contract = cdir / ("%s.contract.md" % run_id)
    contract.write_text(
        (rc.KNOWN_GOOD / "contract.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return contract


def _run_pipeline_by_resolution(contract: Path, run_id: str, product: Path) -> None:
    """Drive freeze, readiness, start gate, spawn_verifier and complete gate with
    no folder argument anywhere, so every script must resolve it itself."""
    kill = contract.parent / ("%s.kill.json" % run_id)
    rc._write_kill_test(kill, fz.contract_sha256(contract), rc.porcelain_kill_test_results())

    assert fz.main([str(contract), "--run-id", run_id, "--kill-test-results", str(kill)]) == 0
    assert rr.main([run_id, "--verdict", "ready", "--source", "human"]) == 0
    assert sg.main([run_id]) == 0
    assert sv.main([str(contract), str(product)]) == 0

    spawn = sv.spawn_verifier(contract, product)
    driven = [
        vr.DrivenResult(criterion_id=cid, outcome=out, observation="driven: %s" % out)
        for cid, out in rc.drive_porcelain(product).items()
    ]
    sv.ingest_verifier_results(spawn, vr.run_verifier(spawn, driven))
    assert cg.main([run_id]) == 0


def test_end_to_end_with_env_var_writes_only_to_custom_folder(repo, monkeypatch):
    custom = repo / "elsewhere" / "contracts"
    monkeypatch.setenv(cl.ENV_CONTRACT_DIR, str(custom))
    run_id = "env-run"
    contract = _stage_contract(custom, run_id)
    product = rc.KNOWN_GOOD / "reference_implementation" / "porcelain.py"

    _run_pipeline_by_resolution(contract, run_id, product)

    assert (custom / ("%s.completion.json" % run_id)).is_file()
    assert (custom / ("%s.provenance.json" % run_id)).is_file()
    assert not (repo / ".taskmaster").exists()
    assert not (repo / ".claude").exists()
    assert sorted(p.name for p in repo.iterdir()) == ["elsewhere"]


def test_end_to_end_legacy_only_repo_keeps_working(repo):
    """A repo set up before the default moved keeps its artifacts where they are."""
    legacy = repo / LEGACY
    run_id = "legacy-run"
    contract = _stage_contract(legacy, run_id)
    product = rc.KNOWN_GOOD / "reference_implementation" / "porcelain.py"

    _run_pipeline_by_resolution(contract, run_id, product)

    record = json.loads((legacy / ("%s.completion.json" % run_id)).read_text(encoding="utf-8"))
    assert record["run_id"] == run_id
    assert (legacy / ("%s.provenance.json" % run_id)).is_file()
    assert not (repo / ".claude").exists()


def test_end_to_end_fresh_repo_creates_claude_contracts(repo):
    run_id = "fresh-run"
    contract = repo / ("%s.contract.md" % run_id)
    contract.write_text(
        (rc.KNOWN_GOOD / "contract.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    kill = repo / "kill.json"
    rc._write_kill_test(kill, fz.contract_sha256(contract), rc.porcelain_kill_test_results())

    assert fz.main([str(contract), "--run-id", run_id, "--kill-test-results", str(kill)]) == 0
    assert (repo / NEW / ("%s.completion.json" % run_id)).is_file()
    assert not (repo / ".taskmaster").exists()


def test_help_text_names_env_var_default_and_legacy_fallback():
    for part in (cl.ENV_CONTRACT_DIR, str(NEW), str(LEGACY)):
        assert part in cl.HELP_DEFAULT
    help_text = sv.build_parser().format_help()
    assert cl.ENV_CONTRACT_DIR in help_text
