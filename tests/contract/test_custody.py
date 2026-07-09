"""Custody tests for the verifier spawn chokepoint (PRD C4, criteria 11 & 15).

Two halves:

- **Structural (criterion 11).** A disk-derived invariant (assess-obey-thyself
  idiom): an AST scan of every `.py` under `scripts/` asserts `spawn_verifier.py`
  is the ONLY code path that writes verifier results / `verifier_provenance`
  into a completion record OR writes the `*.provenance.json` side-channel. The
  scan is future-proof: it does not enumerate known-good filenames beyond the
  one allowed writer, so a NEW file that starts writing verifier custody fails
  the invariant. It is scoped so `freeze.py` writing `freeze_evidence` (a sibling
  PR) and `validate_completion.py` *reading* the side-channel do not trip it.
- **Behavioural (criteria 11 & 15).** The chokepoint's real output round-trips
  through `validate_completion.py`: a token-stamped record certifies, and a
  record with results but no matching token (missing / forged) is
  `DEGRADED-custody` and cannot certify `PASS`.

No AI, no network - the acceptance floor's deterministic layer.

PRD criteria coverage (auditable map; entrypoint ``pytest tests/contract/``):
- **Criterion 11 (structural):** ``spawn_verifier.py`` is the sole custody writer
  and its interface is exactly {contract path, product path} -
  ``test_spawn_verifier_is_sole_custody_writer``,
  ``test_scan_*`` (positive/reader/new-writer/freeze-writer guards),
  ``test_interface_has_exactly_two_positionals`` and the other ``test_interface_*``.
- **Criterion 11 (behavioural):** results without a chokepoint token are
  ``DEGRADED-custody``, results with the token certify -
  ``test_results_without_token_are_degraded_custody``,
  ``test_chokepoint_stamped_record_certifies``.
- **Criterion 15 (all three forgery variants, real-chokepoint round-trip):**
  forged - ``test_forged_token_rejected_round_trip``; copied-from-another-run -
  ``test_copied_from_another_run_token_rejected_round_trip``; stale/reused -
  ``test_stale_token_rejected_round_trip`` (validator stamp) and
  ``test_superseded_spawn_refuses_to_ingest`` (loud refusal at ingest); the exact
  side-channel match certifies via ``test_chokepoint_stamped_record_certifies``.
  The validator-unit trio is in ``test_completion_record.py``.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import List

import pytest

REPO = Path(__file__).resolve().parents[2]
CONTRACT_DIR = REPO / "scripts" / "contract"
SCRIPTS_DIR = REPO / "scripts"
sys.path.insert(0, str(CONTRACT_DIR))

import spawn_verifier as sv  # noqa: E402
import validate_completion as vc  # noqa: E402


# --------------------------------------------------------------------------- #
# Structural invariant (criterion 11): the AST scan.
# --------------------------------------------------------------------------- #

# The completion-record fields only the chokepoint may write. `freeze_evidence`
# is deliberately NOT here: freeze.py (a sibling PR) legitimately writes it, so
# the invariant must not flag that. This is precisely the verifier-owned set.
VERIFIER_RECORD_KEYS = {"verifier_provenance", "criteria_results"}

PROVENANCE_LITERAL = sv.PROVENANCE_SUFFIX  # ".provenance.json"

# The single relative path allowed to write verifier custody.
ALLOWED_WRITER = "scripts/contract/spawn_verifier.py"

_WRITE_ATTRS = {"write_text", "write_bytes"}
_WRITE_MODE_CHARS = ("w", "a", "x", "+")


def _writes_verifier_record_key(tree: ast.AST) -> bool:
    """True if the module *constructs/assigns* a verifier-owned record key.

    A construction is a dict-literal key (`{"verifier_provenance": ...}`) or a
    Store-context subscript (`record["criteria_results"] = ...`). A *read*
    (`record.get("verifier_provenance")` or a Load subscript) is not a write, so
    `validate_completion.py`, which only reads these keys, is not flagged.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value in VERIFIER_RECORD_KEYS:
                    return True
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            sl = node.slice
            if isinstance(sl, ast.Constant) and sl.value in VERIFIER_RECORD_KEYS:
                return True
    return False


def _has_write_call(tree: ast.AST) -> bool:
    """True if the module performs any file-write operation."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in _WRITE_ATTRS:
                return True
            if (
                func.attr == "dump"
                and isinstance(func.value, ast.Name)
                and func.value.id == "json"
            ):
                return True
        if isinstance(func, ast.Name) and func.id == "open":
            for arg in node.args[1:]:
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and any(m in arg.value for m in _WRITE_MODE_CHARS)
                ):
                    return True
    return False


def _has_provenance_literal(tree: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and PROVENANCE_LITERAL in n.value
        for n in ast.walk(tree)
    )


def _writes_verifier_custody(source: str) -> bool:
    """True if this source writes verifier results/provenance or the side-channel.

    Side-channel detection pairs a `.provenance.json` literal with a write call
    in the same module. A pure *reader* of the side-channel (validate_completion)
    has the literal but no write call, so it is not flagged - the pairing is what
    distinguishes writing the side-channel from reading it.
    """
    tree = ast.parse(source)
    if _writes_verifier_record_key(tree):
        return True
    if _has_provenance_literal(tree) and _has_write_call(tree):
        return True
    return False


def _custody_writers() -> List[str]:
    writers: List[str] = []
    for path in sorted(SCRIPTS_DIR.rglob("*.py")):
        if _writes_verifier_custody(path.read_text(encoding="utf-8")):
            writers.append(str(path.relative_to(REPO)))
    return writers


def test_spawn_verifier_is_sole_custody_writer():
    """Criterion 11 structural half: exactly one code path writes verifier custody."""
    assert _custody_writers() == [ALLOWED_WRITER]


def test_scan_recognizes_spawn_verifier_as_a_writer():
    """Guard the scanner against silently degrading to 'finds nothing'.

    If the detector stopped recognizing writes, the sole-writer test would pass
    with an empty list too. Assert the positive directly.
    """
    source = (CONTRACT_DIR / "spawn_verifier.py").read_text(encoding="utf-8")
    assert _writes_verifier_custody(source) is True


def test_scan_does_not_flag_a_reader_of_the_side_channel():
    """validate_completion.py reads the side-channel and the record keys; it must
    not be flagged as a writer (the read/write distinction the scan turns on)."""
    source = (CONTRACT_DIR / "validate_completion.py").read_text(encoding="utf-8")
    assert _writes_verifier_custody(source) is False


def test_scan_flags_a_hypothetical_new_writer():
    """Future-proofing: a NEW file that constructs a verifier_provenance record
    key is caught without the scan enumerating its name."""
    rogue = (
        "import json\n"
        "def sneak(record, tok):\n"
        "    record['verifier_provenance'] = {'token': tok}\n"
        "    return record\n"
    )
    assert _writes_verifier_custody(rogue) is True


def test_scan_flags_a_hypothetical_new_side_channel_writer():
    """A NEW file that writes a *.provenance.json file is caught too."""
    rogue = (
        "from pathlib import Path\n"
        "def sneak(run_id, tok):\n"
        "    p = Path('.taskmaster/contract') / (run_id + '.provenance.json')\n"
        "    p.write_text(tok)\n"
    )
    assert _writes_verifier_custody(rogue) is True


def test_scan_does_not_flag_a_freeze_evidence_writer():
    """freeze.py (sibling PR) writes freeze_evidence into the completion record;
    the invariant is scoped to verifier-owned keys, so it must not trip on that."""
    freeze_like = (
        "import json\n"
        "from pathlib import Path\n"
        "def freeze(path, h):\n"
        "    record = {'run_id': 'x', 'freeze_evidence': {'contract_hash': h}}\n"
        "    Path(path).write_text(json.dumps(record))\n"
        "    return record\n"
    )
    assert _writes_verifier_custody(freeze_like) is False


# --------------------------------------------------------------------------- #
# Interface (criterion 11): exactly {contract path, product path}.
# --------------------------------------------------------------------------- #


def test_interface_has_exactly_two_positionals():
    parser = sv.build_parser()
    positionals = [a.dest for a in parser._actions if not a.option_strings]
    assert positionals == ["contract_path", "product_path"]


def test_interface_accepts_the_two_positionals():
    ns = sv.build_parser().parse_args(["run.contract.md", "build/"])
    assert ns.contract_path == "run.contract.md"
    assert ns.product_path == "build/"


def test_interface_rejects_a_third_positional():
    with pytest.raises(SystemExit):
        sv.build_parser().parse_args(["run.contract.md", "build/", "extra"])


def test_interface_rejects_a_missing_positional():
    with pytest.raises(SystemExit):
        sv.build_parser().parse_args(["run.contract.md"])


def test_interface_rejects_unknown_option():
    with pytest.raises(SystemExit):
        sv.build_parser().parse_args(["run.contract.md", "build/", "--prompt", "x"])


# --------------------------------------------------------------------------- #
# run_id derivation and path-safety.
# --------------------------------------------------------------------------- #


def test_run_id_derived_from_contract_filename(tmp_path):
    contract = tmp_path / "my-tag.contract.md"
    contract.write_text("class: cli\ncriteria: []\n", encoding="utf-8")
    spawn = sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=tmp_path / "ct")
    assert spawn.run_id == "my-tag"


def test_non_contract_filename_rejected(tmp_path):
    bad = tmp_path / "notacontract.md"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        sv.spawn_verifier(bad, tmp_path / "prod", contract_dir=tmp_path / "ct")


def test_run_id_with_traversal_rejected():
    with pytest.raises(ValueError):
        sv.run_id_from_contract("a..b.contract.md")


def test_empty_run_id_rejected():
    with pytest.raises(ValueError):
        sv.run_id_from_contract(".contract.md")


# --------------------------------------------------------------------------- #
# Fresh unique token per spawn, written to the side-channel.
# --------------------------------------------------------------------------- #


def _write_contract(tmp_path, name="run.contract.md"):
    contract = tmp_path / name
    contract.write_text(
        "# contract\n```yaml\nclass: cli\ncriteria:\n"
        "  - id: C1\n    tier: 1\n    action: \"run it\"\n    observation: \"exit 0\"\n```\n",
        encoding="utf-8",
    )
    return contract


def test_fresh_unique_token_per_spawn(tmp_path):
    contract = _write_contract(tmp_path)
    cdir = tmp_path / "ct"
    s1 = sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)
    s2 = sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)
    assert s1.token != s2.token
    assert s1.token and s2.token


def test_token_written_to_side_channel(tmp_path):
    contract = _write_contract(tmp_path)
    cdir = tmp_path / "ct"
    spawn = sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)
    side = json.loads((cdir / "run.provenance.json").read_text(encoding="utf-8"))
    assert side["run_id"] == "run"
    assert side["token"] == spawn.token
    assert side["contract_hash"] == spawn.contract_hash
    assert side["issued_at"]


def test_second_spawn_overwrites_side_channel_token(tmp_path):
    contract = _write_contract(tmp_path)
    cdir = tmp_path / "ct"
    sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)
    s2 = sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)
    side = json.loads((cdir / "run.provenance.json").read_text(encoding="utf-8"))
    assert side["token"] == s2.token  # the latest spawn's token wins


def test_prompt_contains_contract_and_product_not_extra_input(tmp_path):
    contract = _write_contract(tmp_path)
    cdir = tmp_path / "ct"
    spawn = sv.spawn_verifier(contract, tmp_path / "the-product", contract_dir=cdir)
    assert "the-product" in spawn.prompt
    assert spawn.contract_hash in spawn.prompt
    assert "exit 0" in spawn.prompt  # the frozen contract's observation goal


# --------------------------------------------------------------------------- #
# Behavioural round-trip (criteria 11 & 15) through the REAL validator.
# --------------------------------------------------------------------------- #


def _seed_freeze_and_readiness(cdir, run_id, contract_hash):
    """Pre-write the fields other stages own into the completion record, so the
    ingest merge (not clobber) can be asserted and the round-trip can reach PASS.
    """
    cdir.mkdir(parents=True, exist_ok=True)
    record = {
        "run_id": run_id,
        "freeze_evidence": {
            "contract_hash": contract_hash,
            "frozen_before_decomposition": True,
            "kill_test": {"null_artifact_all_fail": True, "sabotage_rejected": True},
        },
        "readiness_verdict": {"verdict": "ready", "source": "human"},
    }
    (cdir / (run_id + ".completion.json")).write_text(json.dumps(record), encoding="utf-8")


def test_chokepoint_stamped_record_certifies(tmp_path):
    contract = _write_contract(tmp_path)
    cdir = tmp_path / "ct"
    spawn = sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)
    _seed_freeze_and_readiness(cdir, "run", spawn.contract_hash)

    obs = sv.VerifierObservations(
        criteria_results=[{"id": "C1", "tier": 1, "result": "pass", "observation": "exit 0"}]
    )
    record = sv.ingest_verifier_results(spawn, obs)

    # freeze_evidence survived the merge (ingest did not clobber it).
    assert record["freeze_evidence"]["kill_test"]["null_artifact_all_fail"] is True
    # And the real validator certifies against the real side-channel.
    result = vc.validate(record, cdir)
    assert result.verdict == vc.VERDICT_PASS
    assert result.certified


def test_results_without_token_are_degraded_custody(tmp_path):
    """A completion record with verifier results but no chokepoint token cannot
    certify - the bypass the chokepoint exists to prevent (criterion 11)."""
    contract = _write_contract(tmp_path)
    cdir = tmp_path / "ct"
    spawn = sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)
    _seed_freeze_and_readiness(cdir, "run", spawn.contract_hash)

    hand_written = {
        "run_id": "run",
        "freeze_evidence": {
            "contract_hash": spawn.contract_hash,
            "frozen_before_decomposition": True,
            "kill_test": {"null_artifact_all_fail": True, "sabotage_rejected": True},
        },
        "readiness_verdict": {"verdict": "ready", "source": "human"},
        "criteria_results": [{"id": "C1", "tier": 1, "result": "pass", "observation": "exit 0"}],
        # No verifier_provenance: results appeared without going through the chokepoint.
    }
    result = vc.validate(hand_written, cdir)
    assert result.verdict == vc.VERDICT_DEGRADED_CUSTODY
    assert not result.certified
    assert vc.STAMP_DEGRADED_CUSTODY in result.stamps


def test_forged_token_rejected_round_trip(tmp_path):
    """A record with a token that was never issued by the chokepoint (not in the
    side-channel) is DEGRADED-custody (criterion 15 round-trip)."""
    contract = _write_contract(tmp_path)
    cdir = tmp_path / "ct"
    spawn = sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)
    _seed_freeze_and_readiness(cdir, "run", spawn.contract_hash)
    record = sv.ingest_verifier_results(
        spawn,
        sv.VerifierObservations(
            criteria_results=[{"id": "C1", "tier": 1, "result": "pass", "observation": "exit 0"}]
        ),
    )
    # Tamper: replace the authentic token with a forged one.
    record["verifier_provenance"]["token"] = "tok-forged-never-issued"
    result = vc.validate(record, cdir)
    assert result.verdict == vc.VERDICT_DEGRADED_CUSTODY
    assert not result.certified


def test_copied_from_another_run_token_rejected_round_trip(tmp_path):
    """Criterion 15 (copied-from-another-run): a token authentically issued to
    run B, pasted into run A's completion record, mismatches A's own side-channel
    entry - the real validator stamps DEGRADED-custody. Both tokens are genuine
    chokepoint issuances, so this defeats an "any issued token is fine" validator
    that fails to bind the token to THIS run's run_id."""
    body = _write_contract(tmp_path).read_text(encoding="utf-8")
    cdir = tmp_path / "ct"

    # Run A: a real spawn + ingest produces run A's record and side-channel.
    contract_a = tmp_path / "run-a.contract.md"
    contract_a.write_text(body, encoding="utf-8")
    spawn_a = sv.spawn_verifier(contract_a, tmp_path / "prod", contract_dir=cdir)
    _seed_freeze_and_readiness(cdir, "run-a", spawn_a.contract_hash)
    record_a = sv.ingest_verifier_results(
        spawn_a,
        sv.VerifierObservations(
            criteria_results=[{"id": "C1", "tier": 1, "result": "pass", "observation": "exit 0"}]
        ),
    )

    # Run B: a separate real spawn issues its own authentic token elsewhere.
    contract_b = tmp_path / "run-b.contract.md"
    contract_b.write_text(body, encoding="utf-8")
    spawn_b = sv.spawn_verifier(contract_b, tmp_path / "prod", contract_dir=cdir)

    # Paste run B's authentic token into run A's record: a run_id mismatch.
    record_a["verifier_provenance"]["token"] = spawn_b.token
    result = vc.validate(record_a, cdir)
    assert result.verdict == vc.VERDICT_DEGRADED_CUSTODY
    assert not result.certified


def test_stale_token_rejected_round_trip(tmp_path):
    """Criterion 15 (stale/reused): a token valid in an earlier verification run,
    reused after a newer spawn overwrote the side-channel with a fresh token, no
    longer matches - the real validator stamps DEGRADED-custody. This is the
    validator-stamp view; test_superseded_spawn_refuses_to_ingest is the loud
    refusal at the ingest seam."""
    contract = _write_contract(tmp_path)
    cdir = tmp_path / "ct"
    spawn_old = sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)
    _seed_freeze_and_readiness(cdir, "run", spawn_old.contract_hash)

    stale_record = {
        "run_id": "run",
        "freeze_evidence": {
            "contract_hash": spawn_old.contract_hash,
            "frozen_before_decomposition": True,
            "kill_test": {"null_artifact_all_fail": True, "sabotage_rejected": True},
        },
        "readiness_verdict": {"verdict": "ready", "source": "human"},
        "criteria_results": [{"id": "C1", "tier": 1, "result": "pass", "observation": "exit 0"}],
        # The token spawn_old issued, still referenced after it was superseded.
        "verifier_provenance": {"token": spawn_old.token, "run_id": "run"},
    }
    # A newer verification run supersedes the side-channel token for this run.
    sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)

    result = vc.validate(stale_record, cdir)
    assert result.verdict == vc.VERDICT_DEGRADED_CUSTODY
    assert not result.certified


def test_superseded_spawn_refuses_to_ingest(tmp_path):
    """A stale spawn (a newer spawn overwrote the side-channel token) cannot
    stamp results - custody fails loudly rather than writing a doomed record."""
    contract = _write_contract(tmp_path)
    cdir = tmp_path / "ct"
    stale = sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)
    sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)  # supersedes
    with pytest.raises(sv.ChokepointError):
        sv.ingest_verifier_results(stale, sv.VerifierObservations())
