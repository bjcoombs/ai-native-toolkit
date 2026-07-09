"""Tests for the acceptance-contract freeze gate (`scripts/contract/freeze.py`).

Covers the freeze rule's three refusal axes and its success path (PRD B1/B3, A4,
success criteria 3, 13, 14):

- interactive contract with zero tier-3 -> refused with reason; +tier-3 permits.
- a criterion that passes against the class null artifact -> refused as vacuous,
  naming it (B1 / criterion 3).
- a valid contract -> freeze evidence recorded with hash + kill-test outcome, in
  the exact shape validate_completion.py accepts.
- a downgrade below the class default without written justification -> refused.
- fail-closed when kill-test results are absent.
- the committed sound fixtures freeze; the committed vacuous fixture is rejected
  by REAL logic (kill-test outcome), never by fixture name - the same code path
  handles synthetic blind contracts the harness generates per run.

Deterministic, model-free: the agent-driven null-artifact kill test enters as a
validated results file; here the tests supply those outcomes directly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONTRACT_DIR = REPO / "scripts" / "contract"
CANARIES = REPO / "tests" / "canaries"
sys.path.insert(0, str(CONTRACT_DIR))

import freeze as fz  # noqa: E402
import tiers as ti  # noqa: E402
import validate_completion as vc  # noqa: E402


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #

RUN_ID = "test-run"


def write_contract(tmp_path: Path, body: str, name: str = "x.contract.md") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def kill_test_all_fail(contract_path: Path, extra: str = "", **top) -> dict:
    """Kill-test results bound to `contract_path`, every criterion FAILING
    against the null artifact (the sound outcome). Override per-criterion or
    top-level keys via `extra`/`top`."""
    contract = fz.parse_contract_file(contract_path)
    results = [{"id": c.id, "passed_against_null": False} for c in contract.criteria]
    payload = {
        "contract_sha256": fz.contract_sha256(contract_path),
        "results": results,
    }
    payload.update(top)
    return payload


def write_kill_test(tmp_path: Path, payload: dict, name: str = "kt.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


CLI_CONTRACT = """# t

```yaml
class: cli
criteria:
  - id: A1
    tier: 1
    action: "Run the tool on real input; capture exit code."
    observation: "Exit code is 0."
  - id: A2
    tier: 1
    action: "Capture stdout and compare to the golden file."
    observation: "stdout is byte-identical to the golden output."
```
"""

INTERACTIVE_NO_TIER3 = """# t

```yaml
class: interactive
criteria:
  - id: I1
    tier: 1
    action: "Launch the build and read getState()."
    observation: "getState() returns a readable state object."
  - id: I2
    tier: 1
    action: "Dispatch an ArrowUp keydown and re-read state."
    observation: "The player position changes in response to input."
```
"""

INTERACTIVE_WITH_TIER3 = INTERACTIVE_NO_TIER3.replace(
    '    observation: "The player position changes in response to input."\n',
    '    observation: "The player position changes in response to input."\n'
    "  - id: I3\n"
    "    tier: 3\n"
    '    action: "Operator launches the build and plays it."\n'
    '    observation: "The motion reads and feels like the intended toy."\n',
)

REFACTOR_DOWNGRADE = """# t

```yaml
class: refactor
criteria:
  - id: R1
    tier: 1
    action: "Run the transfer set through both versions and diff outputs."
    observation: "Outputs are identical across the transfer set."
```
"""

REFACTOR_DOWNGRADE_JUSTIFIED = REFACTOR_DOWNGRADE.replace(
    '    observation: "Outputs are identical across the transfer set."\n',
    '    observation: "Outputs are identical across the transfer set."\n'
    '    justification: "The transfer set is exhaustive and outputs are exact '
    'byte matches, so this is a genuine binary check, not a proxy."\n',
)


# --------------------------------------------------------------------------- #
# Structural class floor (criterion 13)
# --------------------------------------------------------------------------- #


def test_interactive_zero_tier3_refused_with_reason(tmp_path):
    contract = write_contract(tmp_path, INTERACTIVE_NO_TIER3)
    kt = write_kill_test(tmp_path, kill_test_all_fail(contract))
    result = fz.freeze(contract, RUN_ID, kt, record_path=tmp_path / "r.json")
    assert not result.frozen
    assert any("tier-3" in r for r in result.reasons)
    assert not (tmp_path / "r.json").exists()


def test_interactive_with_tier3_permits_freeze(tmp_path):
    contract = write_contract(tmp_path, INTERACTIVE_WITH_TIER3)
    kt = write_kill_test(tmp_path, kill_test_all_fail(contract))
    record = tmp_path / "r.json"
    result = fz.freeze(contract, RUN_ID, kt, record_path=record)
    assert result.frozen, result.reasons
    assert record.exists()


# --------------------------------------------------------------------------- #
# B1 red-contract-first / vacuity (criterion 3)
# --------------------------------------------------------------------------- #


def test_criterion_passing_against_null_refused_as_vacuous(tmp_path):
    contract = write_contract(tmp_path, CLI_CONTRACT)
    payload = kill_test_all_fail(contract)
    payload["results"][1]["passed_against_null"] = True  # A2 is vacuous
    kt = write_kill_test(tmp_path, payload)
    result = fz.freeze(contract, RUN_ID, kt, record_path=tmp_path / "r.json")
    assert not result.frozen
    assert any("A2" in r and "vacuous" in r for r in result.reasons)
    assert not (tmp_path / "r.json").exists()


def test_missing_kill_test_result_fails_closed(tmp_path):
    contract = write_contract(tmp_path, CLI_CONTRACT)
    payload = kill_test_all_fail(contract)
    payload["results"] = payload["results"][:1]  # drop A2's result
    kt = write_kill_test(tmp_path, payload)
    result = fz.freeze(contract, RUN_ID, kt, record_path=tmp_path / "r.json")
    assert not result.frozen
    assert any("A2" in r for r in result.reasons)


def test_absent_kill_test_fails_closed(tmp_path):
    contract = write_contract(tmp_path, CLI_CONTRACT)
    result = fz.freeze(contract, RUN_ID, None, record_path=tmp_path / "r.json")
    assert not result.frozen
    assert any("red-contract-first" in r for r in result.reasons)
    assert not (tmp_path / "r.json").exists()


def test_kill_test_not_bound_to_contract_refused(tmp_path):
    contract = write_contract(tmp_path, CLI_CONTRACT)
    payload = kill_test_all_fail(contract)
    payload["contract_sha256"] = "deadbeef"  # wrong hash: stale/mismatched
    kt = write_kill_test(tmp_path, payload)
    result = fz.freeze(contract, RUN_ID, kt, record_path=tmp_path / "r.json")
    assert not result.frozen
    assert any("bound to this contract" in r for r in result.reasons)


def test_non_boolean_outcome_refused(tmp_path):
    """An outcome that is not an explicit boolean is not a self-attested pass."""
    contract = write_contract(tmp_path, CLI_CONTRACT)
    payload = kill_test_all_fail(contract)
    payload["results"][0]["passed_against_null"] = "false"  # string, not bool
    kt = write_kill_test(tmp_path, payload)
    result = fz.freeze(contract, RUN_ID, kt, record_path=tmp_path / "r.json")
    assert not result.frozen
    assert any("boolean passed_against_null" in r for r in result.reasons)


# --------------------------------------------------------------------------- #
# Downgrade justification (A4)
# --------------------------------------------------------------------------- #


def test_downgrade_without_justification_refused(tmp_path):
    contract = write_contract(tmp_path, REFACTOR_DOWNGRADE)
    kt = write_kill_test(tmp_path, kill_test_all_fail(contract))
    result = fz.freeze(contract, RUN_ID, kt, record_path=tmp_path / "r.json")
    assert not result.frozen
    assert any("R1" in r and "justification" in r for r in result.reasons)


def test_downgrade_with_justification_permits_freeze(tmp_path):
    contract = write_contract(tmp_path, REFACTOR_DOWNGRADE_JUSTIFIED)
    kt = write_kill_test(tmp_path, kill_test_all_fail(contract))
    record = tmp_path / "r.json"
    result = fz.freeze(contract, RUN_ID, kt, record_path=record)
    assert result.frozen, result.reasons
    assert record.exists()


REPORT_TIER1 = """# t

```yaml
class: report
criteria:
  - id: RP1
    tier: 1
    action: "Resolve each citation anchor (file path + line range)."
    observation: "Every anchor resolves to the cited location."
```
"""


def test_downgrade_defaults_derive_from_the_a4_table():
    """The freeze gate's machine-enforced downgrade defaults are a SUBSET of the
    authoritative A4 table (`tiers.CLASS_TIER_DEFAULTS`), keeping one source of
    truth. interactive/report are excluded (structurally backstopped / prose-
    guarded), so they never appear in the machine-enforced downgrade table."""
    assert fz.DOWNGRADE_DEFAULT_TIER == {
        "cli": ti.CLASS_TIER_DEFAULTS["cli"],
        "refactor": ti.CLASS_TIER_DEFAULTS["refactor"],
    }
    assert "interactive" not in fz.DOWNGRADE_DEFAULT_TIER
    assert "report" not in fz.DOWNGRADE_DEFAULT_TIER


def test_report_tier1_citation_resolution_freezes_without_justification(tmp_path):
    """A4 report split: citation-resolution is legitimately tier-1 with a
    machine-resolvable anchor, and report downgrades are prose-guarded (not
    machine-enforced), so a report tier-1 criterion freezes with no justification
    - the honest-narrowness the PRD states rather than overclaiming."""
    contract = write_contract(tmp_path, REPORT_TIER1)
    kt = write_kill_test(tmp_path, kill_test_all_fail(contract))
    result = fz.freeze(contract, RUN_ID, kt, record_path=tmp_path / "r.json")
    assert result.frozen, result.reasons


def test_upgrade_is_free(tmp_path):
    """A cli criterion at tier-2 (above the tier-1 default) needs no justification."""
    body = CLI_CONTRACT.replace("    tier: 1\n    action: \"Capture stdout", "    tier: 2\n    action: \"Capture stdout")
    contract = write_contract(tmp_path, body)
    kt = write_kill_test(tmp_path, kill_test_all_fail(contract))
    result = fz.freeze(contract, RUN_ID, kt, record_path=tmp_path / "r.json")
    assert result.frozen, result.reasons


# --------------------------------------------------------------------------- #
# Success path: freeze evidence recorded in the validate_completion shape
# --------------------------------------------------------------------------- #


def test_freeze_evidence_recorded_in_validator_shape(tmp_path):
    contract = write_contract(tmp_path, CLI_CONTRACT)
    payload = kill_test_all_fail(contract, sabotage={"authored": True, "rejected": True})
    kt = write_kill_test(tmp_path, payload)
    record = tmp_path / "r.json"
    result = fz.freeze(contract, RUN_ID, kt, record_path=record)
    assert result.frozen, result.reasons

    data = json.loads(record.read_text(encoding="utf-8"))
    assert data["run_id"] == RUN_ID
    assert data["contract_hash"] == fz.contract_sha256(contract)
    evidence = data["freeze_evidence"]
    assert evidence["contract_hash"] == fz.contract_sha256(contract)
    assert evidence["frozen_before_decomposition"] is True
    assert "frozen_at" in evidence
    assert evidence["kill_test"] == {"null_artifact_all_fail": True, "sabotage_rejected": True}

    # The recorded evidence must be exactly what validate_completion accepts.
    valid, _ = vc._freeze_valid(evidence)
    assert valid


def test_sabotage_not_rejected_refuses(tmp_path):
    contract = write_contract(tmp_path, CLI_CONTRACT)
    payload = kill_test_all_fail(contract, sabotage={"authored": True, "rejected": False})
    kt = write_kill_test(tmp_path, payload)
    result = fz.freeze(contract, RUN_ID, kt, record_path=tmp_path / "r.json")
    assert not result.frozen
    assert any("sabotage" in r for r in result.reasons)


def test_no_sabotage_authored_records_null(tmp_path):
    contract = write_contract(tmp_path, CLI_CONTRACT)
    kt = write_kill_test(tmp_path, kill_test_all_fail(contract))
    record = tmp_path / "r.json"
    assert fz.freeze(contract, RUN_ID, kt, record_path=record).frozen
    data = json.loads(record.read_text(encoding="utf-8"))
    assert data["freeze_evidence"]["kill_test"]["sabotage_rejected"] is None


def test_freeze_preserves_existing_record_fields(tmp_path):
    contract = write_contract(tmp_path, CLI_CONTRACT)
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"run_id": RUN_ID, "operator_signoff": {"operator": "ben"}}), encoding="utf-8")
    kt = write_kill_test(tmp_path, kill_test_all_fail(contract))
    assert fz.freeze(contract, RUN_ID, kt, record_path=record).frozen
    data = json.loads(record.read_text(encoding="utf-8"))
    assert data["operator_signoff"] == {"operator": "ben"}  # not clobbered
    assert "freeze_evidence" in data


# --------------------------------------------------------------------------- #
# Real logic, not fixture identity: the committed fixtures via the same path
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fixture", ["known-good", "jet-fighters", "known-good-interactive"])
def test_committed_sound_fixtures_freeze(fixture, tmp_path):
    """The sound committed fixtures freeze when their null-artifact kill test
    shows every criterion failing - discrimination is by outcome, not name."""
    contract = CANARIES / fixture / "contract.md"
    kt = write_kill_test(tmp_path, kill_test_all_fail(contract))
    record = tmp_path / "r.json"
    result = fz.freeze(contract, "%s-run" % fixture, kt, record_path=record)
    assert result.frozen, result.reasons
    assert record.exists()


def test_committed_vacuous_fixture_rejected(tmp_path):
    """The vacuous fixture's VC1 passes against the CLI null artifact, so B1
    rejects it - by real logic, not by recognizing the fixture name."""
    contract = CANARIES / "vacuous-contract" / "contract.md"
    payload = kill_test_all_fail(contract)
    payload["results"][0]["passed_against_null"] = True  # VC1 satisfiable by absence
    kt = write_kill_test(tmp_path, payload)
    result = fz.freeze(contract, "vacuous-run", kt, record_path=tmp_path / "r.json")
    assert not result.frozen
    assert any("VC1" in r and "vacuous" in r for r in result.reasons)


# --------------------------------------------------------------------------- #
# Parser robustness
# --------------------------------------------------------------------------- #


def test_all_committed_contracts_parse():
    for fixture in ("known-good", "jet-fighters", "known-good-interactive", "vacuous-contract"):
        contract = fz.parse_contract_file(CANARIES / fixture / "contract.md")
        assert contract.criteria
        assert contract.cls in fz.CLASS_NAMES


def test_missing_yaml_block_is_parse_error(tmp_path):
    with pytest.raises(fz.ContractParseError):
        fz.parse_contract_text("# no yaml here\n")


def test_two_yaml_blocks_is_parse_error(tmp_path):
    body = CLI_CONTRACT + "\n```yaml\nclass: cli\n```\n"
    with pytest.raises(fz.ContractParseError):
        fz.parse_contract_text(body)


def test_bad_class_is_parse_error():
    with pytest.raises(fz.ContractParseError):
        fz.parse_contract_text("```yaml\nclass: bogus\ncriteria:\n  - id: X\n    tier: 1\n    action: \"a\"\n    observation: \"o\"\n```\n")


def test_duplicate_criterion_id_is_parse_error():
    body = (
        "```yaml\nclass: cli\ncriteria:\n"
        "  - id: D1\n    tier: 1\n    action: \"a\"\n    observation: \"o\"\n"
        "  - id: D1\n    tier: 1\n    action: \"a\"\n    observation: \"o\"\n```\n"
    )
    with pytest.raises(fz.ContractParseError):
        fz.parse_contract_text(body)


def test_invalid_tier_is_parse_error():
    body = "```yaml\nclass: cli\ncriteria:\n  - id: X\n    tier: 4\n    action: \"a\"\n    observation: \"o\"\n```\n"
    with pytest.raises(fz.ContractParseError):
        fz.parse_contract_text(body)


# --------------------------------------------------------------------------- #
# Malformed bound kill-test results: strict refusal, never a silent default
# --------------------------------------------------------------------------- #


def test_duplicate_result_id_raises_in_result_map():
    """A duplicate id in a bound results file is rejected, mirroring the strict
    duplicate-criterion-id rule in parse_contract_text - a later entry must not
    silently override an earlier one (e.g. a vacuous pass masking a fail)."""
    kill_test = {
        "contract_sha256": "irrelevant",
        "results": [
            {"id": "A1", "passed_against_null": True},
            {"id": "A1", "passed_against_null": False},
        ],
    }
    with pytest.raises(fz.KillTestError, match="duplicate kill-test result id"):
        fz._result_map(kill_test)


def test_duplicate_result_id_refuses_freeze_with_named_reason(tmp_path):
    contract = write_contract(tmp_path, CLI_CONTRACT)
    payload = kill_test_all_fail(contract)
    # A vacuous pass for A1 followed by a fail for the same id: last-wins would
    # have kept the fail and frozen; strict rejection refuses instead.
    payload["results"].insert(0, {"id": "A1", "passed_against_null": True})
    kt = write_kill_test(tmp_path, payload)
    result = fz.freeze(contract, RUN_ID, kt, record_path=tmp_path / "r.json")
    assert not result.frozen
    assert any("duplicate kill-test result id" in r for r in result.reasons)
    assert not (tmp_path / "r.json").exists()


def test_bound_results_not_a_list_refuses_cleanly(tmp_path):
    contract = write_contract(tmp_path, CLI_CONTRACT)
    payload = kill_test_all_fail(contract)
    payload["results"] = {"A1": True}  # object, not a list
    kt = write_kill_test(tmp_path, payload)
    result = fz.freeze(contract, RUN_ID, kt, record_path=tmp_path / "r.json")
    assert not result.frozen
    assert any("results.results must be a list" in r for r in result.reasons)
    assert not (tmp_path / "r.json").exists()


def test_bound_result_missing_id_refuses_cleanly(tmp_path):
    contract = write_contract(tmp_path, CLI_CONTRACT)
    payload = kill_test_all_fail(contract)
    del payload["results"][0]["id"]  # entry without an 'id'
    kt = write_kill_test(tmp_path, payload)
    result = fz.freeze(contract, RUN_ID, kt, record_path=tmp_path / "r.json")
    assert not result.frozen
    assert any("needs an 'id'" in r for r in result.reasons)
    assert not (tmp_path / "r.json").exists()


def test_main_emits_json_and_nonzero_exit_on_malformed_bound_results(tmp_path, capsys):
    """The documented fail-closed structured output must hold on the
    evaluate_kill_test path too: malformed bound results yield a non-zero exit
    and valid JSON on stdout, never an uncaught traceback with no JSON."""
    contract = write_contract(tmp_path, CLI_CONTRACT)
    payload = kill_test_all_fail(contract)
    payload["results"] = {"A1": True}  # object, not a list: raises in _result_map
    kt = write_kill_test(tmp_path, payload)
    code = fz.main([str(contract), "--run-id", RUN_ID, "--kill-test-results", str(kt)])
    assert code != 0
    out = json.loads(capsys.readouterr().out)  # valid JSON on stdout
    assert out["frozen"] is False
    assert any("results.results must be a list" in r for r in out["reasons"])
