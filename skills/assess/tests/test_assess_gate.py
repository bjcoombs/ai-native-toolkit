"""Tests for the CI regression gate (assess_gate.py) and its config loader.

The gate is the enforcement half of the frozen harness: same run-context +
config in, same pass/fail out, no LLM. These tests pin the warn-only defaults,
the fail_on / warn_on precedence, the threshold checks, and the exit codes.
"""
from __future__ import annotations

import json
from pathlib import Path


from assess_gate import (
    check_complexity_threshold,
    check_containment_threshold,
    check_diff_regression,
    check_finding_regressions,
    evaluate,
    format_verdict,
    main,
)
from lib.assess_config import (
    GATE_CONCERN_FINDINGS,
    load_gate_config,
    load_gate_config_file,
)


def _ctx(
    findings: list[dict] | None = None,
    ccn_p95: float = 50.0,
    safe_zones: int = 1,
    total_concerns: int = 5,
) -> dict:
    """A minimal run-context with the keys the gate reads."""
    return {
        "derived_findings": findings if findings is not None else [],
        "stats_summary": {"ccn": {"p95": ccn_p95}},
        "keyhole_summary": {"safe_zones": safe_zones, "total_concerns": total_concerns},
    }


def _diff_ctx(regressed: list[dict] | None = None, prior: bool = True, reliable: bool = True) -> dict:
    """A run-context carrying the cross-run diff the regression check reads."""
    ctx = _ctx([])
    ctx["prior_stats_exists"] = prior
    ctx["diff_reliable"] = reliable
    ctx["diff_detail"] = {"regressed": regressed or []}
    return ctx


def _write_config(tmp_path: Path, body: str) -> Path:
    (tmp_path / ".assess").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".assess" / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


# --- config loader --------------------------------------------------------


def test_load_gate_config_defaults_warn_only(tmp_path):
    """No config -> enabled, nothing fails, every concern warns, no thresholds."""
    gate = load_gate_config(tmp_path)
    assert gate["enabled"] is True
    assert gate["fail_on"] == []
    assert gate["warn_on"] == GATE_CONCERN_FINDINGS
    assert gate["ccn_p95_max"] is None
    assert gate["containment_min"] is None


def test_load_gate_config_reads_section(tmp_path):
    _write_config(
        tmp_path,
        '[gate]\n'
        'enabled = true\n'
        'fail_on = ["lying_map", "hidden_coupling"]\n'
        'ccn_p95_max = 80\n'
        'containment_min = 0.5\n',
    )
    gate = load_gate_config(tmp_path)
    assert gate["fail_on"] == ["lying_map", "hidden_coupling"]
    assert gate["ccn_p95_max"] == 80.0
    assert gate["containment_min"] == 0.5


def test_load_gate_config_explicit_empty_warn_on(tmp_path):
    """An explicit empty list silences warnings (distinct from missing key)."""
    _write_config(tmp_path, "[gate]\nwarn_on = []\n")
    assert load_gate_config(tmp_path)["warn_on"] == []


def test_load_gate_config_rejects_bad_threshold(tmp_path):
    """Non-positive / non-numeric / boolean thresholds degrade to None."""
    _write_config(tmp_path, "[gate]\nccn_p95_max = 0\ncontainment_min = true\n")
    gate = load_gate_config(tmp_path)
    assert gate["ccn_p95_max"] is None
    assert gate["containment_min"] is None


def test_load_gate_config_non_dict_section(tmp_path):
    """A malformed [gate] (scalar instead of table) falls back to defaults."""
    _write_config(tmp_path, 'gate = "nope"\n')
    gate = load_gate_config(tmp_path)
    assert gate["enabled"] is True
    assert gate["fail_on"] == []


# --- finding regressions --------------------------------------------------


def test_finding_fires_only_with_paths():
    ctx = _ctx([
        {"name": "lying_map", "paths": ["docs/old.md"]},
        {"name": "hidden_coupling", "paths": []},
    ])
    gate = {"fail_on": ["lying_map", "hidden_coupling"], "warn_on": []}
    failures, warnings = check_finding_regressions(ctx, gate)
    assert [f["finding"] for f in failures] == ["lying_map"]
    assert failures[0]["count"] == 1
    assert warnings == []


def test_fail_on_takes_precedence_over_warn_on():
    ctx = _ctx([{"name": "lying_map", "paths": ["a", "b"]}])
    gate = {"fail_on": ["lying_map"], "warn_on": ["lying_map"]}
    failures, warnings = check_finding_regressions(ctx, gate)
    assert len(failures) == 1
    assert warnings == []  # not double-counted


def test_warn_on_reports_without_failing():
    ctx = _ctx([{"name": "candidate_dead_weight", "paths": ["x.py"]}])
    gate = {"fail_on": [], "warn_on": ["candidate_dead_weight"]}
    failures, warnings = check_finding_regressions(ctx, gate)
    assert failures == []
    assert [w["finding"] for w in warnings] == ["candidate_dead_weight"]


def test_paths_sample_capped_at_five():
    ctx = _ctx([{"name": "hidden_coupling", "paths": [str(i) for i in range(20)]}])
    gate = {"fail_on": ["hidden_coupling"], "warn_on": []}
    failures, _ = check_finding_regressions(ctx, gate)
    assert failures[0]["count"] == 20
    assert len(failures[0]["paths"]) == 5


# --- thresholds -----------------------------------------------------------


def test_complexity_threshold_breach():
    breaches = check_complexity_threshold(_ctx(ccn_p95=120.0), {"ccn_p95_max": 100.0})
    assert breaches[0]["metric"] == "ccn_p95"
    assert breaches[0]["value"] == 120.0


def test_complexity_threshold_within_budget():
    assert check_complexity_threshold(_ctx(ccn_p95=90.0), {"ccn_p95_max": 100.0}) == []


def test_complexity_threshold_unset():
    assert check_complexity_threshold(_ctx(ccn_p95=999.0), {"ccn_p95_max": None}) == []


def test_containment_threshold_breach():
    # 1 safe / (1 + 9) = 0.1, below the 0.5 floor.
    breaches = check_containment_threshold(
        _ctx(safe_zones=1, total_concerns=9), {"containment_min": 0.5}
    )
    assert breaches[0]["metric"] == "containment"
    assert breaches[0]["value"] == 0.1


def test_containment_threshold_met():
    assert check_containment_threshold(
        _ctx(safe_zones=9, total_concerns=1), {"containment_min": 0.5}
    ) == []


def test_containment_no_units_scored():
    """Nothing flagged either way -> no ratio -> no breach."""
    assert check_containment_threshold(
        _ctx(safe_zones=0, total_concerns=0), {"containment_min": 0.5}
    ) == []


# --- cross-run regression -------------------------------------------------


def test_regression_not_opted_in():
    ctx = _diff_ctx(regressed=[{"path": "a.py"}])
    assert check_diff_regression(ctx, {"fail_on_regression": False}) == []


def test_regression_fires_when_opted_in():
    ctx = _diff_ctx(regressed=[{"path": "a.py"}, {"path": "b.py"}])
    breaches = check_diff_regression(ctx, {"fail_on_regression": True})
    assert breaches[0]["metric"] == "regressed_hotspots"
    assert breaches[0]["count"] == 2
    assert breaches[0]["paths"] == ["a.py", "b.py"]


def test_regression_skipped_on_first_run():
    """No prior snapshot -> a freshly-cloned repo can't trip the regression gate."""
    ctx = _diff_ctx(regressed=[{"path": "a.py"}], prior=False)
    assert check_diff_regression(ctx, {"fail_on_regression": True}) == []


def test_regression_skipped_when_diff_unreliable():
    ctx = _diff_ctx(regressed=[{"path": "a.py"}], reliable=False)
    assert check_diff_regression(ctx, {"fail_on_regression": True}) == []


def test_regression_clean_diff_no_breach():
    ctx = _diff_ctx(regressed=[])
    assert check_diff_regression(ctx, {"fail_on_regression": True}) == []


def test_load_gate_config_fail_on_regression_default_false(tmp_path):
    assert load_gate_config(tmp_path)["fail_on_regression"] is False


def test_load_gate_config_reads_fail_on_regression(tmp_path):
    _write_config(tmp_path, "[gate]\nfail_on_regression = true\n")
    assert load_gate_config(tmp_path)["fail_on_regression"] is True


def test_regression_breach_renders_in_verdict():
    ctx = _diff_ctx(regressed=[{"path": "hot.py"}])
    verdict = evaluate(ctx, {"enabled": True, "fail_on": [], "warn_on": [], "fail_on_regression": True})
    assert verdict["failed"] is True
    assert "FAIL regression" in format_verdict(verdict)


# --- explicit --config file -----------------------------------------------


def test_load_gate_config_file_reads_explicit_path(tmp_path):
    cfg = tmp_path / "custom.toml"
    cfg.write_text('[gate]\nfail_on = ["lying_map"]\n', encoding="utf-8")
    gate = load_gate_config_file(cfg)
    assert gate["fail_on"] == ["lying_map"]


def test_load_gate_config_file_missing_uses_defaults(tmp_path):
    gate = load_gate_config_file(tmp_path / "nope.toml")
    assert gate["fail_on"] == []
    assert gate["enabled"] is True


# --- evaluate + verdict ---------------------------------------------------


def test_evaluate_clean_passes():
    gate = load_gate_config(Path("/nonexistent"))  # warn-only defaults
    verdict = evaluate(_ctx([]), gate)
    assert verdict["failed"] is False


def test_evaluate_fails_on_fail_on_finding():
    ctx = _ctx([{"name": "lying_map", "paths": ["doc.md"]}])
    verdict = evaluate(ctx, {"enabled": True, "fail_on": ["lying_map"], "warn_on": []})
    assert verdict["failed"] is True
    assert verdict["failures"][0]["finding"] == "lying_map"


def test_disabled_gate_never_fails_but_reports():
    ctx = _ctx([{"name": "lying_map", "paths": ["doc.md"]}])
    verdict = evaluate(ctx, {"enabled": False, "fail_on": ["lying_map"], "warn_on": []})
    assert verdict["failed"] is False
    assert verdict["failures"]  # still collected for the log
    assert "disabled" in format_verdict(verdict)


def test_format_verdict_clean():
    verdict = evaluate(_ctx([]), {"enabled": True, "fail_on": [], "warn_on": []})
    out = format_verdict(verdict)
    assert "RESULT: PASS" in out
    assert "clean snapshot" in out


# --- config-exclusion disclosure ------------------------------------------


def test_verdict_discloses_config_suppressed_findings():
    ctx = _ctx([])
    ctx["excluded_by_config"] = {
        "dirs": ["vendor"],
        "patterns": ["*.gen.py"],
        "affected_finding_paths": ["vendor/x.py", "a.gen.py"],
        "count": 2,
    }
    out = format_verdict(evaluate(ctx, {"enabled": True, "fail_on": [], "warn_on": []}))
    assert "2 findings suppressed by config excludes" in out
    assert "dirs: vendor" in out
    assert "patterns: *.gen.py" in out


def test_verdict_singular_suppression_noun():
    ctx = _ctx([])
    ctx["excluded_by_config"] = {
        "dirs": ["vendor"], "patterns": [],
        "affected_finding_paths": ["vendor/x.py"], "count": 1,
    }
    out = format_verdict(evaluate(ctx, {"enabled": True, "fail_on": [], "warn_on": []}))
    assert "1 finding suppressed by config excludes" in out
    assert "patterns: none" in out


def test_verdict_no_disclosure_when_no_excludes():
    ctx = _ctx([])
    ctx["excluded_by_config"] = {
        "dirs": [], "patterns": [], "affected_finding_paths": [], "count": 0,
    }
    out = format_verdict(evaluate(ctx, {"enabled": True, "fail_on": [], "warn_on": []}))
    assert "suppressed by config excludes" not in out


def test_verdict_no_disclosure_when_block_absent():
    out = format_verdict(evaluate(_ctx([]), {"enabled": True, "fail_on": [], "warn_on": []}))
    assert "suppressed by config excludes" not in out


# --- generated-file disclosure --------------------------------------------


def test_verdict_names_each_generated_header_reason():
    ctx = _ctx([])
    ctx["excluded_generated"] = [
        {"path": "db/schema.sql", "reason": "generated-header"},
        {"path": "db/other.sql", "reason": "generated-header"},
        {"path": "assets/font.ts", "reason": "long-lines"},
    ]
    out = format_verdict(evaluate(ctx, {"enabled": True, "fail_on": [], "warn_on": []}))
    line = next(ln for ln in out.splitlines() if "generated-header" in ln)
    assert "3 files excluded from scoring as generated" in line
    assert "long-lines" in line
    assert line.count("generated-header") == 1
    assert "    db/schema.sql (generated-header)" in out
    assert "    assets/font.ts (long-lines)" in out
    assert "RESULT: PASS" in out


def test_verdict_caps_generated_header_path_lines():
    ctx = _ctx([])
    ctx["excluded_generated"] = [
        {"path": f"gen/f{i:02}.sql", "reason": "generated-header"} for i in range(13)
    ]
    out = format_verdict(evaluate(ctx, {"enabled": True, "fail_on": [], "warn_on": []}))
    assert "gen/f09.sql (generated-header)" in out
    assert "gen/f10.sql" not in out
    assert "+3 more" in out


def test_verdict_generated_header_disclosure_silent_when_empty():
    for ctx in (_ctx([]), {**_ctx([]), "excluded_generated": []}):
        out = format_verdict(evaluate(ctx, {"enabled": True, "fail_on": [], "warn_on": []}))
        assert "excluded from scoring as generated" not in out


# --- CLI ------------------------------------------------------------------


def _write_ctx(tmp_path: Path, ctx: dict) -> Path:
    (tmp_path / ".assess").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".assess" / "run-context.json").write_text(json.dumps(ctx), encoding="utf-8")
    return tmp_path


def test_main_passes_clean(tmp_path, capsys):
    _write_ctx(tmp_path, _ctx([]))
    assert main([str(tmp_path)]) == 0
    assert "RESULT: PASS" in capsys.readouterr().out


def test_main_fails_when_configured(tmp_path, capsys):
    _write_ctx(tmp_path, _ctx([{"name": "lying_map", "paths": ["doc.md"]}]))
    _write_config(tmp_path, '[gate]\nfail_on = ["lying_map"]\n')
    assert main([str(tmp_path)]) == 1
    assert "RESULT: FAIL" in capsys.readouterr().out


def test_main_warn_only_default_passes_with_findings(tmp_path):
    """Findings present but no fail_on config -> warn-only -> exit 0."""
    _write_ctx(tmp_path, _ctx([{"name": "lying_map", "paths": ["doc.md"]}]))
    assert main([str(tmp_path)]) == 0


def test_main_accepts_config_flag_without_consuming_repo_root(tmp_path):
    _write_ctx(tmp_path, _ctx([]))
    cfg = tmp_path / ".assess" / "config.toml"
    assert main([str(tmp_path), "--config", str(cfg)]) == 0


def test_main_honors_explicit_config_path(tmp_path):
    """--config pointing at a relocated file drives the gate, not .assess/."""
    _write_ctx(tmp_path, _ctx([{"name": "lying_map", "paths": ["doc.md"]}]))
    cfg = tmp_path / "elsewhere.toml"
    cfg.write_text('[gate]\nfail_on = ["lying_map"]\n', encoding="utf-8")
    # No .assess/config.toml exists, so this fails only if --config is honored.
    assert main([str(tmp_path), "--config", str(cfg)]) == 1


def test_main_missing_context_skips_not_fails(tmp_path, capsys):
    """A missing run-context is an infrastructure failure, not a finding: the
    gate skips with a clear notice and exits 0 so it never red-checks an
    unrelated PR (it runs again once the core produces a clean run-context)."""
    assert main([str(tmp_path)]) == 0
    err = capsys.readouterr().err
    assert "gate skipped" in err
    assert "infrastructure failure" in err
    assert "not a finding" in err
    assert "next push" in err


def test_main_corrupt_context_skips_not_fails(tmp_path, capsys):
    """A corrupt (unparseable) run-context is also infra, not a finding: skip
    with a notice and exit 0 rather than fail the PR."""
    (tmp_path / ".assess").mkdir(parents=True)
    (tmp_path / ".assess" / "run-context.json").write_text(
        "{ not valid json", encoding="utf-8"
    )
    assert main([str(tmp_path)]) == 0
    err = capsys.readouterr().err
    assert "gate skipped" in err
    assert "infrastructure failure" in err


def test_main_no_args_is_usage_error(capsys):
    assert main([]) == 2
    assert "Usage" in capsys.readouterr().err


# --- gate loss (#1152) ----------------------------------------------------

def _gate_conditions(states: dict[str, str]) -> list[dict]:
    """Every condition a default-model Step gate reads, met unless ``states`` names
    the gate (``{"ci_runs_tests": "unmet"}`` sets each of its conditions)."""
    from lib.level_tally import default_model
    out: dict[str, dict] = {}
    for step in default_model().get("steps", []):
        for g in step["gates"]:
            for n in range(1, g["level"] + 1):
                cid = f"{g['row']}.{n}"
                out[cid] = {"id": cid, "state": states.get(g["id"], "met")}
    return list(out.values())


def _loss_ctx(lost: list[str] | None, states: dict[str, str] | None = None,
              held: list[str] | None = None) -> dict:
    """A run that lost ``lost`` since a committed baseline holding ``held`` (default:
    instructions plus every lost gate)."""
    ctx = _ctx([])
    ctx["since_last_run"] = {"available": True, "first_run": False, "gates_lost": lost}
    ctx["level"] = {"step": 2, "conditions": _gate_conditions(states or {})}
    held = held if held is not None else ["instructions", *(lost or [])]
    ctx["prior_level"] = {"baseline": {"run_id": "r", "head_sha": "a" * 40, "held_gates": held}}
    return ctx


def test_gate_loss_is_off_by_default_and_warns():
    """Behaviour-neutral for the GitHub Action: without the opt-in a lost gate is a warn line."""
    gate = load_gate_config(Path("/nonexistent"))
    assert gate["fail_on_gate_loss"] is False
    verdict = evaluate(_loss_ctx(["ci_runs_tests"], {"ci_runs_tests": "unmet"}), gate)
    assert verdict["failed"] is False
    assert verdict["gates_lost_warning"] == ["ci_runs_tests"]
    text = format_verdict(verdict)
    assert "warn gate lost: ci_runs_tests" in text
    assert "no findings fired" not in text


def test_gate_loss_fails_when_opted_in_and_unmet(tmp_path):
    _write_config(tmp_path, "[gate]\nfail_on_gate_loss = true\n")
    gate = load_gate_config(tmp_path)
    assert gate["fail_on_gate_loss"] is True
    verdict = evaluate(_loss_ctx(["ci_runs_tests"], {"ci_runs_tests": "unmet"}), gate)
    assert verdict["failed"] is True
    assert verdict["threshold_breaches"] == [
        {"metric": "gates_lost", "count": 1, "paths": ["ci_runs_tests"], "step_from": 3, "step_to": 2}]
    assert "FAIL gate lost: ci_runs_tests" in format_verdict(verdict)


def test_gate_loss_unread_never_fails(tmp_path):
    """A gate that could not be read this run is unknown, never a loss."""
    _write_config(tmp_path, "[gate]\nfail_on_gate_loss = true\n")
    verdict = evaluate(_loss_ctx(["ci_runs_tests"], {"ci_runs_tests": "not_measured"}),
                       load_gate_config(tmp_path))
    assert verdict["failed"] is False
    assert verdict["gates_unread_warning"] == ["ci_runs_tests"]
    assert "warn no longer measured: ci_runs_tests" in format_verdict(verdict)


def test_gate_loss_nothing_on_first_run_or_unknown(tmp_path):
    _write_config(tmp_path, "[gate]\nfail_on_gate_loss = true\n")
    gate = load_gate_config(tmp_path)
    for lost in (None, []):
        verdict = evaluate(_loss_ctx(lost), gate)
        assert verdict["failed"] is False and "gates_lost_warning" not in verdict
    degraded = _loss_ctx(["ci_runs_tests"], {"ci_runs_tests": "unmet"})
    degraded["since_last_run"] = {"available": False, "reason": "scan_error"}
    assert evaluate(degraded, gate)["failed"] is False


def test_gate_loss_without_level_reads_unread(tmp_path):
    """No level result: the lost gate's state is unknown, so it cannot fail."""
    _write_config(tmp_path, "[gate]\nfail_on_gate_loss = true\n")
    ctx = _loss_ctx(["ci_runs_tests"])
    ctx["level"] = {"available": False}
    verdict = evaluate(ctx, load_gate_config(tmp_path))
    assert verdict["failed"] is False
    assert verdict["gates_unread_warning"] == ["ci_runs_tests"]
    ctx["level"] = {"conditions": [{"id": "other.1", "state": "met"}]}  # another model
    assert evaluate(ctx, load_gate_config(tmp_path))["gates_unread_warning"] == ["ci_runs_tests"]


def test_gate_loss_malformed_config_value_defaults_off(tmp_path):
    _write_config(tmp_path, '[gate]\nfail_on_gate_loss = "yes"\n')
    assert load_gate_config(tmp_path)["fail_on_gate_loss"] is False


def test_main_flags_fail_on_gate_loss_and_summary(tmp_path, capsys):
    ctx = _loss_ctx(["ci_runs_tests"], {"ci_runs_tests": "unmet"})
    ctx["headline"] = {"summary_lines": ["Step 2 of 4: line one.", "Next: line two.", 3]}
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "run-context.json").write_text(json.dumps(ctx), encoding="utf-8")
    assert main([str(tmp_path)]) == 0
    assert "Step 2 of 4" not in capsys.readouterr().out
    assert main([str(tmp_path), "--fail-on-gate-loss", "--summary"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("Step 2 of 4: line one.\nNext: line two.\n\n/assess gate")
    assert "FAIL gate lost: ci_runs_tests" in out


def test_main_summary_absent_headline_prints_verdict_only(tmp_path, capsys):
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "run-context.json").write_text(json.dumps(_ctx([])), encoding="utf-8")
    assert main([str(tmp_path), "--summary"]) == 0
    assert capsys.readouterr().out.startswith("/assess gate")


def test_gate_loss_names_the_step_lost(tmp_path):
    _write_config(tmp_path, "[gate]\nfail_on_gate_loss = true\n")
    ctx = _loss_ctx(["ci_runs_tests"], {"ci_runs_tests": "unmet"})
    verdict = evaluate(ctx, load_gate_config(tmp_path))
    assert ("FAIL gate lost: ci_runs_tests held at the committed baseline and no longer holds "
            "(Step 3 -> Step 2)") in format_verdict(verdict)
    assert verdict["threshold_breaches"][0]["step_from"] == 3


def test_no_baseline_yet_is_reported_and_passes(tmp_path):
    _write_config(tmp_path, "[gate]\nfail_on_gate_loss = true\n")
    ctx = _loss_ctx(None)
    ctx["since_last_run"]["first_run"] = True
    verdict = evaluate(ctx, load_gate_config(tmp_path))
    assert verdict["failed"] is False and verdict["no_baseline"] is True
    assert "no baseline yet" in format_verdict(verdict)
    # Off by default: the Action's log is unchanged.
    assert "no_baseline" not in evaluate(ctx, load_gate_config(Path("/nonexistent")))


def test_a_loss_without_a_committed_baseline_only_warns(tmp_path):
    """since_last_run can compare with an earlier run-context.json left on disk; only
    the committed baseline can confirm a loss that fails the gate."""
    _write_config(tmp_path, "[gate]\nfail_on_gate_loss = true\n")
    gate = load_gate_config(tmp_path)
    ctx = _loss_ctx(["ci_runs_tests"], {"ci_runs_tests": "unmet"})
    del ctx["prior_level"]
    verdict = evaluate(ctx, gate)
    assert verdict["failed"] is False
    assert verdict["gates_lost_warning"] == ["ci_runs_tests"] and verdict["no_baseline"] is True
    assert "no baseline yet" in format_verdict(verdict)
    # A committed baseline that did not hold the gate confirms nothing either.
    ctx = _loss_ctx(["ci_runs_tests"], {"ci_runs_tests": "unmet"}, held=["instructions"])
    assert evaluate(ctx, gate)["failed"] is False
