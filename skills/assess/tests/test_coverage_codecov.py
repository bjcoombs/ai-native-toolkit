"""Codecov status targets are read as coverage gates, and unknown is never 0 (#882).

A project ``target: 80%`` is a gate at 80, but Codecov posts its status server-side, so
whether it gates a merge (branch protection requiring it) is not read: Layer 6 level 1
reads null, never true and never false. ``target: auto`` (or no target) reads unknown
with ``codecov_target_not_read``. A patch status bounds changed lines only: it never
meets the minimum, and a patch-only config reads null with ``codecov_patch_only``, never
false (maintainer-approved on #882). A malformed config never raises.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.testing.coverage_codecov import (CODECOV_UNMEASURED, PATCH_ONLY, STATUS_UNCONFIRMED,
                                  codecov_statuses, config_path, target_percent)
from lib import unit_readings as ur
from lib.testing.coverage_gate import GO_RUST_UNMEASURED, detect_coverage_gate

CI = ".github/workflows/ci.yml"
WORKFLOW = ("on: [pull_request]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: pytest --cov --cov-fail-under={n}\n")


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _project(target: str) -> str:
    return f"coverage:\n  status:\n    project:\n      default:\n        target: {target}\n"


def _statuses(tmp_path: Path, text: str, name: str = "codecov.yml") -> list[tuple[str, str, float | None]]:
    _write(tmp_path, {name: text})
    return [(s.kind, s.name, s.target) for s in codecov_statuses(tmp_path)]


# -- the two acceptance readings -------------------------------------------------------

def test_project_target_auto_reads_layer_6_unknown_with_the_reason(tmp_path: Path) -> None:
    block = detect_coverage_gate(_write(tmp_path, {"codecov.yml": _project("auto")}))
    gate = block["gates"][0]
    assert (gate["tool"], gate["form"], gate["threshold"], gate["metric"]) == (
        "codecov", "coverage.status.project.default.target", None, "project")
    assert gate["line"] == 5
    assert block["configured"] is True
    assert block["threshold_meets_minimum"] is None
    assert block["unmeasured_reason"] == CODECOV_UNMEASURED
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (None, "threshold_not_read")


def test_project_target_80_reads_a_gate_at_80_whose_merge_hold_is_unknown(tmp_path: Path) -> None:
    block = detect_coverage_gate(_write(tmp_path, {"codecov.yml": _project("80%")}))
    gate = block["gates"][0]
    assert (gate["threshold"], gate["unit"], gate["threshold_percent"]) == (80.0, "percent", 80.0)
    assert block["max_threshold_percent"] == 80.0
    assert block["threshold_meets_minimum"] is True
    assert block["unmeasured_reason"] is None
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (None, STATUS_UNCONFIRMED)


def test_a_low_codecov_target_reads_below_minimum(tmp_path: Path) -> None:
    block = detect_coverage_gate(_write(tmp_path, {"codecov.yml": _project("30%")}))
    assert block["threshold_meets_minimum"] is False
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (
        False, "threshold_below_minimum")


def test_a_ci_gate_run_in_ci_still_reads_true_beside_a_codecov_target(tmp_path: Path) -> None:
    root = _write(tmp_path, {"codecov.yml": _project("auto"), CI: WORKFLOW.format(n=80)})
    block = detect_coverage_gate(root)
    assert block["threshold_meets_minimum"] is True
    assert block["unmeasured_reason"] is None
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (True, "ci_run")


def test_a_low_ci_gate_beside_an_auto_target_reads_unknown_not_unmet(tmp_path: Path) -> None:
    root = _write(tmp_path, {"codecov.yml": _project("auto"), CI: WORKFLOW.format(n=20)})
    block = detect_coverage_gate(root)
    assert block["threshold_meets_minimum"] is None
    assert block["unmeasured_reason"] == CODECOV_UNMEASURED


def test_another_unread_gate_keeps_the_reason_unnamed(tmp_path: Path) -> None:
    workflow = WORKFLOW.replace("{n}", "${{ env.MIN }}")
    root = _write(tmp_path, {"codecov.yml": _project("auto"), CI: workflow})
    block = detect_coverage_gate(root)
    assert block["threshold_meets_minimum"] is None
    assert block["unmeasured_reason"] is None


def test_go_rust_reason_keeps_its_precedence_over_a_low_codecov_target(tmp_path: Path) -> None:
    root = _write(tmp_path, {"codecov.yml": _project("20"), "go.mod": "module x\n"})
    block = detect_coverage_gate(root)
    assert block["threshold_meets_minimum"] is None
    assert block["unmeasured_reason"] == GO_RUST_UNMEASURED


@pytest.mark.parametrize("target", ["100%", "auto", "10"])
def test_a_patch_only_config_reads_unknown_never_met_or_unmet(tmp_path: Path, target: str) -> None:
    patch = f"coverage:\n  status:\n    patch:\n      default:\n        target: {target}\n"
    block = detect_coverage_gate(_write(tmp_path, {"codecov.yml": patch}))
    assert [g["metric"] for g in block["gates"]] == ["patch"]
    assert block["configured"] is True
    assert block["threshold_meets_minimum"] is None
    assert block["unmeasured_reason"] == PATCH_ONLY
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (None, "threshold_not_read")


def test_a_patch_status_never_meets_beside_a_low_project_target(tmp_path: Path) -> None:
    text = ("coverage:\n  status:\n    project:\n      default:\n        target: 20%\n"
            "    patch:\n      default:\n        target: 100%\n")
    block = detect_coverage_gate(_write(tmp_path, {"codecov.yml": text}))
    assert block["threshold_meets_minimum"] is None
    assert block["unmeasured_reason"] == PATCH_ONLY


def test_max_threshold_percent_leaves_out_a_patch_target(tmp_path: Path) -> None:
    text = ("coverage:\n  status:\n    project:\n      default:\n        target: 60%\n"
            "    patch:\n      default:\n        target: 95%\n")
    block = detect_coverage_gate(_write(tmp_path, {"codecov.yml": text}))
    assert block["threshold_meets_minimum"] is True
    assert block["max_threshold_percent"] == 60.0
    patch_only = "coverage:\n  status:\n    patch:\n      default:\n        target: 95%\n"
    block = detect_coverage_gate(_write(tmp_path, {"codecov.yml": patch_only}))
    assert block["max_threshold_percent"] is None


def test_an_auto_project_target_names_its_reason_beside_a_patch_status(tmp_path: Path) -> None:
    text = ("coverage:\n  status:\n    project:\n      default:\n        target: auto\n"
            "    patch:\n      default:\n        target: 100%\n")
    block = detect_coverage_gate(_write(tmp_path, {"codecov.yml": text}))
    assert block["threshold_meets_minimum"] is None
    assert block["unmeasured_reason"] == CODECOV_UNMEASURED


def test_a_ci_gate_run_in_ci_still_meets_beside_a_patch_status(tmp_path: Path) -> None:
    patch = "coverage:\n  status:\n    patch:\n      default:\n        target: 100%\n"
    block = detect_coverage_gate(_write(tmp_path, {"codecov.yml": patch, CI: WORKFLOW.format(n=80)}))
    assert (block["threshold_meets_minimum"], block["unmeasured_reason"]) == (True, None)
    assert (block["enforced_meets_minimum"], block["enforced_basis"]) == (True, "ci_run")


def test_no_codecov_config_leaves_the_reading_unchanged(tmp_path: Path) -> None:
    block = detect_coverage_gate(_write(tmp_path, {"README.md": "x\n"}))
    assert block["gates"] == []
    assert block["threshold_meets_minimum"] is False


def test_a_scoped_run_reads_the_repository_codecov_config(tmp_path: Path) -> None:
    root = _write(tmp_path, {".github/codecov.yml": _project("auto"), "pkg/a.py": "x = 1\n"})
    block = detect_coverage_gate(root, Path("pkg"))
    assert [g["file"] for g in block["gates"]] == [".github/codecov.yml"]
    assert block["unmeasured_reason"] == CODECOV_UNMEASURED


# -- the reader ------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "expected"), [
    ("80", 80.0), ("80%", 80.0), ('"80%"', 80.0), ("'75.5'", 75.5), ("80.5", 80.5),
    ("auto", None), ("AUTO", None), ("", None), ("eighty", None), ("80%%", None),
])
def test_target_spellings(text: str, expected: float | None) -> None:
    assert target_percent(text) == expected


def test_target_with_a_trailing_comment(tmp_path: Path) -> None:
    assert _statuses(tmp_path, _project("85%  # keep it high")) == [("project", "default", 85.0)]


def test_named_status_with_only_a_threshold_is_auto(tmp_path: Path) -> None:
    text = ("coverage:\n  status:\n    project:\n      required:\n        threshold: 1\n"
            "    patch:\n      required:\n        paths:\n          - a/\n        target: 100\n")
    assert _statuses(tmp_path, text) == [("project", "required", None), ("patch", "required", 100.0)]


def test_several_named_statuses(tmp_path: Path) -> None:
    text = ("coverage:\n  status:\n    project:\n      api:\n        target: 90%\n"
            "      web:\n        target: auto\n")
    assert _statuses(tmp_path, text) == [("project", "api", 90.0), ("project", "web", None)]


@pytest.mark.parametrize("text", [
    "coverage:\n  status:\n    project:\n      default:\n        target: 80%\n        informational: true\n",
    "coverage:\n  status:\n    project: off\n",
    "coverage:\n  status:\n    project:\n      default: false\n",
    "coverage:\n  status: off\n",
    "coverage:\n  status: false\n  range: 60..90\n",
    "coverage:\n  range: 70..100\n",
    "comment:\n  layout: diff\n",
    "codecov:\n  require_ci_to_pass: yes\n",
])
def test_non_gates(tmp_path: Path, text: str) -> None:
    assert _statuses(tmp_path, text) == []


def test_an_anchored_status_reads_its_block(tmp_path: Path) -> None:
    text = "coverage:\n  status:\n    project:\n      default: &base\n        target: 85%\n"
    assert _statuses(tmp_path, text) == [("project", "default", 85.0)]


def test_an_anchor_before_a_flow_map_keeps_the_map(tmp_path: Path) -> None:
    text = "coverage:\n  status:\n    project:\n      default: &b {target: 80%}\n"
    assert _statuses(tmp_path, text) == [("project", "default", 80.0)]
    assert detect_coverage_gate(tmp_path)["threshold_meets_minimum"] is True


def test_an_anchor_on_the_kind_key_reads_the_statuses_below(tmp_path: Path) -> None:
    text = "coverage:\n  status:\n    project: &p\n      default:\n        target: 70\n"
    assert _statuses(tmp_path, text) == [("project", "default", 70.0)]


@pytest.mark.parametrize("text", [
    "coverage:\n  status:\n    project:\n      default: *d\n",
    "coverage:\n  status:\n    project: *p\n",
])
def test_an_alias_reads_unknown_never_absent(tmp_path: Path, text: str) -> None:
    assert _statuses(tmp_path, text) == [("project", "default", None)]
    block = detect_coverage_gate(tmp_path)
    assert block["threshold_meets_minimum"] is None
    assert block["unmeasured_reason"] == CODECOV_UNMEASURED


def test_a_byte_order_mark_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "codecov.yml").write_bytes(b"\xef\xbb\xbf" + _project("80").encode())
    assert [s.target for s in codecov_statuses(tmp_path)] == [80.0]


def test_project_on_is_a_default_auto_status(tmp_path: Path) -> None:
    assert _statuses(tmp_path, "coverage:\n  status:\n    project: yes\n") == [
        ("project", "default", None)]


@pytest.mark.parametrize(("text", "expected"), [
    ("coverage:\n  status:\n    project:\n      default: {target: 80%, threshold: 1%}\n",
     [("project", "default", 80.0)]),
    ("coverage:\n  status:\n    project: {default: {target: 70}}\n", [("project", "default", 70.0)]),
    ("coverage:\n  status:\n    project:\n      default: {informational: true, target: 80}\n", []),
    ("coverage:\n  status:\n    project:\n      default: {threshold: 1%}\n", [("project", "default", None)]),
])
def test_flow_maps(tmp_path: Path, text: str, expected: list[tuple[str, str, float | None]]) -> None:
    assert _statuses(tmp_path, text) == expected


def test_tab_indentation_and_garbage_never_raise(tmp_path: Path) -> None:
    text = "coverage:\n\tstatus:\n\t\tproject: [\x00\n: :\n  - - :\n{{{\n"
    assert isinstance(_statuses(tmp_path, text), list)
    assert detect_coverage_gate(tmp_path)["available"] is True


def test_binary_and_oversize_configs_read_nothing(tmp_path: Path) -> None:
    (tmp_path / "codecov.yml").write_bytes(b"\xff\xfe\x00coverage:\x00")
    assert codecov_statuses(tmp_path) == []
    (tmp_path / "codecov.yml").write_text(_project("80") + "#" * 1_000_001, encoding="utf-8")
    assert codecov_statuses(tmp_path) == []


def test_the_first_config_codecov_reads_wins(tmp_path: Path) -> None:
    _write(tmp_path, {".github/codecov.yml": _project("90"), "dev/codecov.yml": _project("10"),
                      ".codecov.yml": _project("80")})
    assert config_path(tmp_path) == ".codecov.yml"
    assert [s.target for s in codecov_statuses(tmp_path)] == [80.0]


def test_an_excluded_config_is_not_read(tmp_path: Path) -> None:
    _write(tmp_path, {".github/codecov.yml": _project("90")})
    assert config_path(tmp_path, ({".github"}, [])) is None


def test_monorepo_units_inherit_the_repository_codecov_reading(tmp_path: Path) -> None:
    root = _write(tmp_path, {"codecov.yml": _project("auto"), "svc/a/x.py": "x = 1\n",
                             "svc/b/y.py": "y = 1\n"})
    repo = detect_coverage_gate(root)
    members = ["svc/a", "svc/b"]
    scoped = detect_coverage_gate(root, Path("svc/b"))
    # the config is the repository's: no member owns it, so units inherit the unknown
    assert ur._root_owns("coverage_gate", repo, members) is True
    assert ur._coverage_own("svc/b", scoped, {"coverage_gate": repo, "members": members}) is None
    assert scoped["threshold_meets_minimum"] is None
    assert scoped["unmeasured_reason"] == CODECOV_UNMEASURED
