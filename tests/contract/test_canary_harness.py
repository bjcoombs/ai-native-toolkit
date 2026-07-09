"""Tests for the acceptance-contract canary harness (`scripts/canaries/run_canaries.py`).

Covers the harness's DETERMINISTIC internals (PRD E3/E4, success criteria 2, 3,
12, 14) so the regular pytest suites exercise it without a JS runtime:

- blind-pair GENERATION properties: anonymized names are fresh per run, the
  behaviour-preserving transform is behaviour-identical, the planted defect is
  parse-valid, non-crashing, and observable only by driving (criterion 12);
- the freeze-blind generator produces honest, drive-derived kill-test results and
  the REAL freeze gate discriminates the generated vacuous/sound pair (criterion 14);
- the machine-executable CLI checks certify the known-good build and reject the
  vacuous contract through the REAL gate pipeline (criteria 2, 3);
- the interactive classifiers turn getState snapshots into binary outcomes; and
- the interactive layer FAILS CLOSED (NOT-RUN) when neither observations nor a JS
  runtime is available - it never invents an observation.

The full semantic harness (the interactive fixtures driven live) is the semantic
layer wired into `floor.yml` by the E-wave; its invocation is documented in the
module docstring of `run_canaries.py`. A single node-gated test here confirms the
headless drive path when `node` is present.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "contract"))
sys.path.insert(0, str(REPO / "scripts" / "canaries"))

import freeze as fz  # noqa: E402
import run_canaries as rc  # noqa: E402
import validate_completion as vc  # noqa: E402

NODE = shutil.which("node") is not None
requires_node = pytest.mark.skipif(not NODE, reason="node runtime not available")


# --------------------------------------------------------------------------- #
# CLI porcelain driver (machine-executable, criterion 2 building block).
# --------------------------------------------------------------------------- #


def test_porcelain_driver_passes_on_real_build():
    product = rc.KNOWN_GOOD / "reference_implementation" / "porcelain.py"
    driven = rc.drive_porcelain(product)
    assert driven == {"KG1": "pass", "KG2": "pass", "KG3": "pass"}


def test_porcelain_driver_fails_on_absent_build():
    driven = rc.drive_porcelain(REPO / "__definitely_absent__.py")
    assert all(v == "fail" for v in driven.values())


def test_porcelain_kill_test_is_all_fail_against_null():
    # Every criterion fails against the absent/no-op null -> none passes -> the
    # contract is not vacuous, so freeze is permitted (matches test_freeze's
    # all-False convention for committed sound fixtures).
    assert rc.porcelain_kill_test_results() == {"KG1": False, "KG2": False, "KG3": False}


# --------------------------------------------------------------------------- #
# Exit blind pair (criterion 12).
# --------------------------------------------------------------------------- #


def test_behaviour_preserving_transform_is_behaviour_identical(tmp_path):
    src = (rc.KNOWN_GOOD / "reference_implementation" / "porcelain.py").read_text(encoding="utf-8")
    transformed = rc.behaviour_preserving_transform(src, "deadbeef")
    assert transformed != src  # bytes differ (static comparison defeated)
    copy = tmp_path / "copy.py"
    copy.write_text(transformed, encoding="utf-8")
    # Behaviour identical: the transformed copy still certifies every criterion.
    assert rc.drive_porcelain(copy) == {"KG1": "pass", "KG2": "pass", "KG3": "pass"}


def test_exit_blind_pair_anonymized_names_fresh_each_run(tmp_path):
    import random

    rng = random.Random(1)
    a = rc.make_exit_blind_pair(tmp_path, rng)
    b = rc.make_exit_blind_pair(tmp_path, rng)
    names = {a.intact_path.name, a.mutant_path.name, b.intact_path.name, b.mutant_path.name}
    assert len(names) == 4  # all four temp names distinct
    # No copy carries the committed fixture's identity in its name.
    assert all("porcelain" not in n for n in names)


def test_exit_blind_pair_defect_parse_valid_and_observable(tmp_path):
    import random

    for seed in range(8):  # exercise the mutator space
        rng = random.Random(seed)
        pair = rc.make_exit_blind_pair(tmp_path, rng)
        # Intact copy: behaviour-preserving -> all criteria pass.
        assert rc.drive_porcelain(pair.intact_path) == {"KG1": "pass", "KG2": "pass", "KG3": "pass"}
        # Mutant: parse-valid (compiles) and non-crashing but observably wrong.
        compile(pair.mutant_path.read_text(encoding="utf-8"), "<mutant>", "exec")
        mutant_driven = rc.drive_porcelain(pair.mutant_path)
        assert any(v == "fail" for v in mutant_driven.values())


def test_exit_blind_pair_full_check_discriminates(tmp_path):
    import random

    result = rc.check_exit_blind_pair(tmp_path, random.Random(3))
    assert result.ok, result.detail


# --------------------------------------------------------------------------- #
# Freeze blind pair (criterion 14).
# --------------------------------------------------------------------------- #


def test_freeze_blind_pair_kill_results_are_drive_derived():
    import random

    pair = rc.make_freeze_blind_pair(random.Random(5))
    # Sound criteria require positive output the null lacks -> none passes null.
    assert set(pair.sound_kill.values()) == {False}
    # The vacuous criterion is absence-satisfiable -> passes the null.
    assert pair.vacuous_kill == {pair.vacuous_id: True}


def test_freeze_blind_pair_contracts_parse_as_cli():
    import random

    pair = rc.make_freeze_blind_pair(random.Random(6))
    sound = fz.parse_contract_text(pair.sound_text)
    vacuous = fz.parse_contract_text(pair.vacuous_text)
    assert sound.cls == "cli" and len(sound.criteria) == 2
    assert vacuous.cls == "cli" and [c.id for c in vacuous.criteria] == [pair.vacuous_id]


def test_freeze_blind_pair_full_check_discriminates(tmp_path):
    import random

    result = rc.check_freeze_blind_pair(tmp_path, random.Random(7))
    assert result.ok, result.detail


def test_freeze_blind_wording_randomized_each_run():
    import random

    a = rc.make_freeze_blind_pair(random.Random(10))
    b = rc.make_freeze_blind_pair(random.Random(11))
    # Fresh ids/tokens/paths per run: no fixture identity to match on.
    assert a.vacuous_id != b.vacuous_id
    assert a.vacuous_text != b.vacuous_text


# --------------------------------------------------------------------------- #
# Machine-executable checks through the real pipeline (criteria 2, 3).
# --------------------------------------------------------------------------- #


def test_known_good_cli_certifies_pass(tmp_path):
    result = rc.check_known_good_cli(tmp_path)
    assert result.ok, result.detail
    assert result.criterion == "2"


def test_vacuous_fixture_rejected_at_freeze(tmp_path):
    result = rc.check_vacuous_fixture_freeze(tmp_path)
    assert result.ok, result.detail
    assert result.criterion == "3"


# --------------------------------------------------------------------------- #
# Interactive classifiers (deterministic, no runtime).
# --------------------------------------------------------------------------- #


def test_classify_jet_detects_broken_vs_working():
    # JF3: launcher.lane must change on input.
    broken = [{"launcher": {"lane": 1}}, {"launcher": {"lane": 1}}]
    working = [{"launcher": {"lane": 1}}, {"launcher": {"lane": 0}}]
    assert rc.classify_jet("JF3", broken) == "fail"
    assert rc.classify_jet("JF3", working) == "pass"
    # Null snapshots -> fail, never a crash.
    assert rc.classify_jet("JF2", [None, None]) == "fail"


def test_classify_lr_phase_and_score():
    ready = {"phase": "READY", "lane": 1, "score": 0}
    playing = {"phase": "PLAYING", "lane": 1, "score": 0}
    assert rc.classify_lr("LR1", [ready]) == "pass"
    assert rc.classify_lr("LR2", [ready, playing]) == "pass"
    assert rc.classify_lr("LR2", [ready, ready]) == "fail"
    assert rc.classify_lr("LR4", [{"score": 2}, {"score": 3}]) == "pass"
    assert rc.classify_lr("LR4", [{"score": 2}, {"score": 2}]) == "fail"


# --------------------------------------------------------------------------- #
# Fail-closed: the interactive layer never invents an observation.
# --------------------------------------------------------------------------- #


def test_interactive_fails_closed_without_driver_or_observations(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "node_available", lambda: False)
    results = rc.check_interactive(tmp_path, observations=None)
    assert len(results) == 2
    assert all((not r.ok) and r.not_run for r in results)
    assert {r.criterion for r in results} == {"1", "2b"}


def test_supplied_observations_must_cover_tier1(tmp_path):
    # An observations file that omits tier-1 criteria is rejected, not silently
    # passed - the harness will not certify on partial evidence.
    incomplete = {"jet-fighters": {"JF1": "pass"}}
    with pytest.raises(rc.HarnessError):
        rc.check_jet_fighters(tmp_path, incomplete)


# --------------------------------------------------------------------------- #
# Node-gated: the headless drive path (criteria 1 & 2b) when a JS runtime exists.
# --------------------------------------------------------------------------- #


@requires_node
def test_headless_jet_fighters_never_certifies(tmp_path):
    result = rc.check_jet_fighters(tmp_path, observations=None)
    assert result.ok, result.detail


@requires_node
def test_headless_known_good_interactive_stalls_at_tier3(tmp_path):
    result = rc.check_known_good_interactive(tmp_path, observations=None)
    assert result.ok, result.detail


@requires_node
def test_full_harness_green_end_to_end(tmp_path):
    results = rc.run_all(tmp_path, observations=None, seed=12345)
    assert len(results) == 6
    assert all(r.ok for r in results), [r.detail for r in results if not r.ok]
    assert {r.criterion for r in results} == {"1", "2", "2b", "3", "12", "14"}
