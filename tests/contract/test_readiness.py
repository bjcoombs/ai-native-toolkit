"""Tests for the acceptance-contract readiness recorder and prompt template.

Covers the wiring the readiness check (PRD A2/A3) adds on top of the completion
validator (task 2, already unit-covered in test_completion_record.py):

- ``record_readiness.py`` round-trips through ``validate_completion``: a recorded
  ``source: none`` verdict yields the ``DEGRADED: no decorrelated review`` stamp,
  and ``source: human`` yields no such stamp (criterion 8, at the recorder seam).
- the recorder writes ``readiness_verdict`` without clobbering other fields, and
  creates the record when absent.
- the prompt template file exists and its declared output-format ``yaml`` block
  parses to the documented shape.

No AI, no network - the acceptance floor's deterministic layer. The output-format
block is parsed with a tiny stdlib reader (no third-party YAML dependency, in
keeping with validate_completion.py's dependency-free house style).

PRD criteria coverage (auditable map; entrypoint ``pytest tests/contract/``):
- **Criterion 8 (recorder seam):** a recorded ``source: none`` verdict yields the
  ``DEGRADED: no decorrelated review`` stamp
  (``test_recorded_source_none_yields_degraded_stamp``); ``source: human`` and
  ``source: non-claude-model`` yield no such stamp
  (``test_recorded_source_human_yields_no_degraded_stamp``,
  ``test_recorded_source_non_claude_model_yields_no_degraded_stamp``). The
  validator-unit view of criterion 8 is in ``test_completion_record.py``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONTRACT_DIR = REPO / "scripts" / "contract"
sys.path.insert(0, str(CONTRACT_DIR))

import record_readiness as rr  # noqa: E402
import validate_completion as vc  # noqa: E402

PROMPT_TEMPLATE = CONTRACT_DIR / "readiness_check_prompt.md"

RUN_ID = "acceptance-contract"
GOOD_TOKEN = "tok-abc123-thisrun"


# --------------------------------------------------------------------------- #
# Fixtures / builders
# --------------------------------------------------------------------------- #


def write_provenance(prov_dir: Path, run_id: str, token: str) -> None:
    prov_dir.mkdir(parents=True, exist_ok=True)
    (prov_dir / ("%s.provenance.json" % run_id)).write_text(
        json.dumps({"run_id": run_id, "token": token}), encoding="utf-8"
    )


def pass_worthy_record_without_readiness() -> dict:
    """A record that would certify PASS once a non-`none` readiness verdict is
    recorded onto it. Deliberately omits ``readiness_verdict`` so the recorder is
    what supplies it."""
    return {
        "run_id": RUN_ID,
        "contract_hash": "sha256:deadbeef",
        "freeze_evidence": {
            "contract_hash": "sha256:deadbeef",
            "frozen_before_decomposition": True,
            "kill_test": {"null_artifact_all_fail": True, "sabotage_rejected": True},
        },
        "criteria_results": [
            {"id": "c1", "tier": 1, "result": "pass", "observation": "exit 0"},
            {"id": "c2", "tier": 1, "result": "pass", "observation": "output matches"},
        ],
        "couldnt_drive": [],
        "stamps": [],
        "abort_events": [],
        "tier3_escalations": [],
        "verifier_provenance": {"token": GOOD_TOKEN, "run_id": RUN_ID, "spawned_by": "chokepoint"},
    }


@pytest.fixture
def contract_dir(tmp_path):
    """A contract dir holding a pre-written pass-worthy record (no readiness) and
    the matching provenance side-channel."""
    d = tmp_path / "contract"
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%s.completion.json" % RUN_ID)).write_text(
        json.dumps(pass_worthy_record_without_readiness()), encoding="utf-8"
    )
    write_provenance(d, RUN_ID, GOOD_TOKEN)
    return d


def _reload(contract_dir: Path, run_id: str = RUN_ID) -> dict:
    with (contract_dir / ("%s.completion.json" % run_id)).open(encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# Recorder round-trips through the validator (criterion 8 at the recorder seam)
# --------------------------------------------------------------------------- #


def test_recorded_source_none_yields_degraded_stamp(contract_dir):
    rr.record_readiness(RUN_ID, "ready", "none", contract_dir)
    record = _reload(contract_dir)
    assert record["readiness_verdict"] == {"verdict": "ready", "source": "none"}

    result = vc.validate(record, contract_dir)
    assert vc.STAMP_DEGRADED in result.stamps


def test_recorded_source_human_yields_no_degraded_stamp(contract_dir):
    rr.record_readiness(RUN_ID, "ready", "human", contract_dir)
    record = _reload(contract_dir)
    assert record["readiness_verdict"] == {"verdict": "ready", "source": "human"}

    result = vc.validate(record, contract_dir)
    assert vc.STAMP_DEGRADED not in result.stamps
    # The otherwise-pass-worthy record certifies once a human-sourced verdict lands.
    assert result.verdict == vc.VERDICT_PASS
    assert result.certified


def test_recorded_source_non_claude_model_yields_no_degraded_stamp(contract_dir):
    rr.record_readiness(RUN_ID, "ready", "non-claude-model", contract_dir)
    result = vc.validate(_reload(contract_dir), contract_dir)
    assert vc.STAMP_DEGRADED not in result.stamps


# --------------------------------------------------------------------------- #
# Recorder mechanics: no clobber, creates-if-absent, guards
# --------------------------------------------------------------------------- #


def test_recorder_does_not_clobber_other_fields(contract_dir):
    before = _reload(contract_dir)
    rr.record_readiness(RUN_ID, "needs-work", "human", contract_dir)
    after = _reload(contract_dir)

    for key in ("freeze_evidence", "criteria_results", "verifier_provenance", "contract_hash"):
        assert after[key] == before[key], "recorder clobbered %s" % key
    assert after["readiness_verdict"] == {"verdict": "needs-work", "source": "human"}


def test_recorder_creates_record_when_absent(tmp_path):
    d = tmp_path / "contract"  # does not exist yet
    path = rr.record_readiness("fresh-run", "ready", "human", d)
    assert path.exists()
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["run_id"] == "fresh-run"
    assert record["readiness_verdict"] == {"verdict": "ready", "source": "human"}


def test_recorder_overwrites_prior_readiness_verdict(contract_dir):
    rr.record_readiness(RUN_ID, "needs-work", "none", contract_dir)
    rr.record_readiness(RUN_ID, "ready", "human", contract_dir)
    record = _reload(contract_dir)
    assert record["readiness_verdict"] == {"verdict": "ready", "source": "human"}


def test_recorder_rejects_invalid_source(contract_dir):
    with pytest.raises(ValueError):
        rr.record_readiness(RUN_ID, "ready", "baboon", contract_dir)


def test_recorder_rejects_invalid_verdict(contract_dir):
    with pytest.raises(ValueError):
        rr.record_readiness(RUN_ID, "maybe", "human", contract_dir)


def test_recorder_rejects_run_id_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        rr.record_readiness("../evil", "ready", "human", tmp_path / "contract")


def test_recorder_refuses_run_id_mismatch(tmp_path):
    """An existing record whose internal run_id differs from the one being
    recorded is corrupt keying: refuse rather than silently overwrite it."""
    d = tmp_path / "contract"
    d.mkdir(parents=True, exist_ok=True)
    # File named for run "wanted" but internally claiming run "other".
    (d / "wanted.completion.json").write_text(
        json.dumps({"run_id": "other"}), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        rr.record_readiness("wanted", "ready", "human", d)


def test_cli_records_and_exits_zero(tmp_path, capsys):
    d = tmp_path / "contract"
    rc = rr.main(["cli-run", "--verdict", "ready", "--source", "human", "--contract-dir", str(d)])
    assert rc == 0
    record = json.loads((d / "cli-run.completion.json").read_text(encoding="utf-8"))
    assert record["readiness_verdict"] == {"verdict": "ready", "source": "human"}


def test_cli_bad_source_is_rejected(tmp_path):
    """argparse choices reject an out-of-enum source with a non-zero exit."""
    with pytest.raises(SystemExit):
        rr.main(["r", "--verdict", "ready", "--source", "nope", "--contract-dir", str(tmp_path)])


# --------------------------------------------------------------------------- #
# Prompt template: exists, and its output-format yaml block parses
# --------------------------------------------------------------------------- #


def _extract_single_yaml_block(text: str) -> str:
    """Return the body of the one fenced ```yaml block. Fails if there isn't
    exactly one - the template promises exactly one for machine parsing."""
    lines = text.splitlines()
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        stripped = line.strip()
        if current is None and stripped == "```yaml":
            current = []
        elif current is not None and stripped == "```":
            blocks.append(current)
            current = None
        elif current is not None:
            current.append(line)
    assert len(blocks) == 1, "expected exactly one ```yaml block, found %d" % len(blocks)
    return "\n".join(blocks[0])


def _parse_readiness_yaml(block: str) -> dict:
    """Tiny stdlib parser for the readiness output shape: top-level keys that are
    either a list of ``- "..."`` string items or a bare scalar. Genuinely parses
    the block into a dict; deterministic and dependency-free."""
    result: dict[str, object] = {}
    current_key: str | None = None
    for raw in block.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        item = raw.strip()
        if item.startswith("- "):
            assert current_key is not None, "list item before any key: %r" % raw
            assert isinstance(result[current_key], list)
            result[current_key].append(item[2:].strip().strip('"'))  # type: ignore[union-attr]
            continue
        assert ":" in raw, "expected 'key:' line, got %r" % raw
        key, _, rest = raw.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest:
            result[key] = rest  # scalar
            current_key = None
        else:
            result[key] = []  # list follows
            current_key = key
    return result


def test_prompt_template_exists():
    assert PROMPT_TEMPLATE.is_file(), "readiness_check_prompt.md is missing"
    text = PROMPT_TEMPLATE.read_text(encoding="utf-8")
    # The template must carry both interpolation placeholders it documents.
    assert "{spec_content}" in text
    assert "{contract_content}" in text


def test_prompt_output_yaml_block_parses():
    text = PROMPT_TEMPLATE.read_text(encoding="utf-8")
    parsed = _parse_readiness_yaml(_extract_single_yaml_block(text))

    assert set(parsed) == {
        "executable_criteria",
        "ambiguous_criteria",
        "missing_criteria",
        "verdict",
    }
    assert isinstance(parsed["executable_criteria"], list)
    assert isinstance(parsed["ambiguous_criteria"], list)
    assert isinstance(parsed["missing_criteria"], list)
    assert parsed["verdict"] in ("ready", "needs-work")
