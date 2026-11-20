"""lib/level_signals.py: schema-backed signal paths and the shipped default maturity model."""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lib.counts_only import counts_only_scans
from lib.promissory_markers import scan_promissory_markers
from lib.structure_graph import analyze_structure
from lib.levels import Group, LevelsError, LevelsResult, dumps_result, evaluate, load_levels, resolve_signal
from lib.level_signals import (
    DEFAULT_LEVELS,
    ORG_PATHS,
    load_default_levels,
    scan_names,
    signal_path_validator,
    team_path_ok,
)
from lib.signals import build_document, known_scans

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN_RUN_CONTEXT = FIXTURES / "golden" / "run-context-baseline.json"
_UNAVAILABLE = resolve_signal(None, "x")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})


def _commit(repo: Path) -> Path:
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-qm", "init")
    return repo


def _scans(repo: Path) -> dict[str, Any]:
    return dict(build_document(repo)["scans"])


@pytest.fixture(scope="module")
def golden_repos(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    """Headless scans of the golden fixture repositories, each its own git repo."""
    root = tmp_path_factory.mktemp("golden")
    out = {}
    for name in ("golden-doc-repo", "golden-svg-repo"):
        repo = root / name
        shutil.copytree(FIXTURES / name, repo)
        out[name] = _scans(_commit(repo))
    return out


@pytest.fixture(scope="module")
def instructed_repo(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """golden-doc-repo plus a CLAUDE.md naming a check that resolves, and agent ops."""
    repo = tmp_path_factory.mktemp("instructed") / "repo"
    shutil.copytree(FIXTURES / "golden-doc-repo", repo)
    (repo / "CLAUDE.md").write_text("# Guide\n\nRun `make check` before pushing.\n", encoding="utf-8")
    (repo / "Makefile").write_text("check:\n\tpython -m pytest\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\nversion = '0'\n", encoding="utf-8")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(make check)"]}}), encoding="utf-8")
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text(_CI_WORKFLOW, encoding="utf-8")
    return _scans(_commit(repo))


_CI_WORKFLOW = "on: pull_request\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: make check\n"


@pytest.fixture(scope="module")
def suppressed_repo(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """A full run's promissory_markers block for a repository with one lint suppression."""
    repo = tmp_path_factory.mktemp("suppressed") / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("import os  # noqa: F401\n", encoding="utf-8")
    return {"promissory_markers": scan_promissory_markers(_commit(repo)).summary()}


@pytest.fixture(scope="module")
def two_package_repo(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """A full run's structure block for two packages, one importing the other's front door."""
    root = tmp_path_factory.mktemp("two_package")
    for name, init, modules in (
        ("core", "from core.api import run\n", {
            "api": "from core import store, rules\ndef run(): return store.X + rules.Y\n",
            "store": "X = 1\n", "rules": "from core import store\nY = store.X\n"}),
        ("app", "", {"cli": "import core\nfrom app import views\n",
                     "views": "from app import forms\n", "forms": "Z = 1\n"}),
    ):
        (root / name).mkdir()
        (root / name / "__init__.py").write_text(init, encoding="utf-8")
        for stem, src in modules.items():
            (root / name / f"{stem}.py").write_text(src, encoding="utf-8")
    return {"structure": dict(analyze_structure(root).as_dict())}


def _run(data: dict[str, dict[str, Any]]) -> LevelsResult:
    group: Group = {"name": "g", "settings": None, "repos": data}
    return evaluate(load_default_levels(), group)


def _levels(result: LevelsResult) -> dict[str, int | None]:
    return {row["id"]: row["level"] for row in result["rows"]}


def _row(result: LevelsResult, row_id: str) -> Any:
    return next(r for r in result["rows"] if r["id"] == row_id)


# --- the validator -----------------------------------------------------------


def test_scan_names_come_from_the_published_schema() -> None:
    assert scan_names() == frozenset(known_scans())


@pytest.mark.parametrize("path", [
    "observability.rung",                          # typed block, scalar leaf
    "agent_environment.layer5_cap.applies",        # nested typed field
    "agent_environment.ci_duration.over_target",   # NotRequired field
    "test_pressure.reason",                        # a union arm (unavailable shape)
    "stats_summary.loc.p95",                       # typed field under an untyped block
    "coverage_gate.enforced",                      # untyped block: open
    "review_reality.anything.deeper",              # untyped block: open
    "agent_ops.available",                         # every block may report status
    "layer_grades.instructions_grade",             # deterministic layer grade (#620)
    "layer_grades.instruction_file_present",
])
def test_team_paths_accepted(path: str) -> None:
    assert team_path_ok(path)


@pytest.mark.parametrize("path", [
    "instructions_grade",                      # run-context key outside the schema
    "skills_count",
    "no_such_scan.x",
    "observability.no_such_field",             # typed block, unknown field
    "agent_environment.check",                 # ends on an object
    "agent_environment.check.named",           # ends on a list
    "agent_environment.check.named.command",   # steps into a list
    "observability.rung.deeper",               # past a scalar
    "stats_summary.loc.no_such",
    "layer_grades.layer_3_grade",              # no such layer grade
    "layer_grades",                            # ends on an object
])
def test_team_paths_rejected(path: str) -> None:
    assert not team_path_ok(path)


def test_org_paths_are_the_ghreport_settings_fragment() -> None:
    validate = signal_path_validator()
    assert validate("org", "two_factor.value")
    assert validate("org", "default_branch_protection.reason")
    assert not validate("org", "two_factor")
    assert not validate("org", "observability.rung")
    assert not validate("team", "two_factor.value")
    assert "rulesets.value" in ORG_PATHS


def test_unknown_signal_path_fails_at_load_with_the_path_named(tmp_path: Path) -> None:
    maturity_model = tmp_path / "r.toml"
    maturity_model.write_text(
        'version = "v"\n[[rows]]\nid = "r"\ntitle = "R"\nscope = "team"\naggregation = "min"\n'
        '[[rows.levels]]\nlevel = 1\ncriteria = [\n'
        '  { signal = "observability.rung", op = "gte", value = 1 },\n'
        '  { signal = "observability.rungg", op = "gte", value = 1 },\n]\n',
        encoding="utf-8")
    with pytest.raises(LevelsError, match=r"team:observability\.rungg \(row 'r'\)"):
        load_levels(maturity_model, signal_path_validator())


def _scalar_leaves(prefix: str, node: Any) -> list[str]:
    if isinstance(node, dict):
        return [p for k, v in node.items() if "." not in k
                for p in _scalar_leaves(f"{prefix}.{k}", v)]
    return [] if isinstance(node, list) else [prefix]


def test_every_scalar_in_real_output_is_an_accepted_path(
    golden_repos: dict[str, dict[str, Any]], instructed_repo: dict[str, Any],
) -> None:
    """The typed shapes never reject a field a scan actually emits."""
    run_context = json.loads(GOLDEN_RUN_CONTEXT.read_text(encoding="utf-8"))
    documents = [*golden_repos.values(), instructed_repo,
                 {k: v for k, v in run_context.items() if k in scan_names()}]
    rejected = sorted({
        path for scans in documents for name, block in scans.items()
        for path in _scalar_leaves(name, block)
        if not team_path_ok(path)
    })
    assert rejected == []


# --- the default maturity model ------------------------------------------------------


def test_default_levels_loads_against_the_schema() -> None:
    maturity_model = load_default_levels()
    assert maturity_model["version"].startswith("default-")
    assert all(row["scope"] == "team" for row in maturity_model["rows"])
    assert all(len(row["levels"]) == 2 for row in maturity_model["rows"])  # Partial, Present
    assert "disagree" in DEFAULT_LEVELS.read_text(encoding="utf-8").lower()


def test_every_default_signal_is_emitted_by_some_real_output(
    golden_repos: dict[str, dict[str, Any]], instructed_repo: dict[str, Any],
    suppressed_repo: dict[str, Any], two_package_repo: dict[str, Any],
) -> None:
    """Catches a typo in an open block, which the validator cannot."""
    run_context = json.loads(GOLDEN_RUN_CONTEXT.read_text(encoding="utf-8"))
    documents = [*golden_repos.values(), instructed_repo, suppressed_repo, two_package_repo,
                 run_context]
    # review_reality needs GitHub; its fields are named in its module doc.
    offline = {"review_reality.reviewed_share", "review_reality.approved_share"}
    signals = {c["signal"] for row in load_default_levels()["rows"]
               for level in row["levels"] for c in level["criteria"]}
    never = sorted(
        s for s in signals - offline
        if all(resolve_signal(doc, s) is _UNAVAILABLE for doc in documents)
    )
    assert never == []


def test_default_levels_on_the_golden_fixture_repos(golden_repos: dict[str, dict[str, Any]]) -> None:
    result = _run(golden_repos)
    for name in golden_repos:
        assert _levels(_run({name: golden_repos[name]})) == {
            "layer-0-instructions": 0,   # no CLAUDE.md: level 0, not unknown
            "layer-1-liveness": None,    # only a full /assess run measures the rung
            "layer-2-design": None,      # no Python package: no_python_packages
            "layer-3-linters": None,     # no lint suppression: no suppression family
            "layer-4-architecture": None,  # no ownership map: no_ownership_map
            "layer-5-ci": 0,
            "layer-6-coverage": 0,
            "layer-7-review": None,      # needs GitHub
            "layer-8-agent-ops": 0,
        }
    for scans in golden_repos.values():   # measured headless, each unknown for a named reason
        assert scans["structure"]["reason"].startswith("no_python_packages:")
        assert scans["structure_drift"]["reason"].startswith("no_ownership_map:")
        assert scans["promissory_markers"]["available"] is True
    layer1 = _row(result, "layer-1-liveness")
    assert (layer1["status"], layer1["missing_signals"]) == ("unknown", ["observability.rung"])
    assert layer1["unknown_repos"] == sorted(golden_repos)


def test_default_levels_on_the_golden_run_context() -> None:
    """The captured full run predates most headless scans: Layers 1 and 4 are read."""
    run_context = json.loads(GOLDEN_RUN_CONTEXT.read_text(encoding="utf-8"))
    result = _run({"toolkit": run_context})
    assert _levels(result) == {
        "layer-0-instructions": None,
        "layer-1-liveness": 0,   # observability.rung = 0 in the capture
        # modularity_q 0.5662 meets level 1; the capture is one package (all lib.*)
        # and predates cross_package_edges, so its front_door_ratio 1.0 is no
        # evidence: level 1 or 2, unknown.
        "layer-2-design": None,
        "layer-3-linters": None,  # no promissory_markers block in the capture
        "layer-4-architecture": 2,  # 115 matched ownership patterns, loc p95 483.65
        "layer-5-ci": None,
        "layer-6-coverage": None,
        "layer-7-review": None,
        "layer-8-agent-ops": None,
    }
    assert "layer_grades.instruction_file_present" in _row(
        result, "layer-0-instructions")["missing_signals"]
    layer2 = _row(result, "layer-2-design")
    assert layer2["repos"]["toolkit"]["bounds"] == [1, 2]
    assert layer2["missing_signals"] == ["structure.cross_package_edges"]
    # A current run of this single-package capture reports no boundary crossed.
    single = copy.deepcopy(run_context)
    single["structure"]["cross_package_edges"] = 0
    assert _row(_run({"toolkit": single}), "layer-2-design")["level"] == 1
    assert dumps_result(result) == dumps_result(_run({"toolkit": copy.deepcopy(run_context)}))


def test_layer_two_reaches_present_only_across_a_package_boundary(
    two_package_repo: dict[str, Any],
) -> None:
    structure = two_package_repo["structure"]
    assert structure["cross_package_edges"] > 0 and structure["front_door_ratio"] == 1.0
    assert structure["modularity_q"] >= 0.3
    assert _row(_run({"r": two_package_repo}), "layer-2-design")["level"] == 2


def test_default_levels_covers_every_layer_on_the_golden_run_context() -> None:
    """Contract I564-2: one row per layer 0-8, each a whole level or a named unknown."""
    rows = load_default_levels()["rows"]
    assert len(rows) >= 9
    assert [row["id"].split("-")[1] for row in rows] == [str(n) for n in range(9)]
    run_context = json.loads(GOLDEN_RUN_CONTEXT.read_text(encoding="utf-8"))
    for row in _run({"toolkit": run_context})["rows"]:
        if row["status"] == "level":
            assert isinstance(row["level"], int) and not isinstance(row["level"], bool), row
        else:
            assert (row["status"], row["level"]) == ("unknown", None), row
            assert row["missing_signals"], row


def test_layer_two_is_unknown_on_a_repository_with_no_python_package(tmp_path: Path) -> None:
    """The import graph is Python-only: no package is unmeasured, not modularity 0."""
    (tmp_path / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
    structure = analyze_structure(tmp_path).as_dict()
    assert structure["available"] is False
    row = _row(_run({"r": {"structure": structure}}), "layer-2-design")
    assert (row["status"], row["level"]) == ("unknown", None)
    assert "structure.modularity_q" in row["missing_signals"]


def test_layer_two_is_unknown_on_an_import_graph_with_no_edges(tmp_path: Path) -> None:
    """Modules that never import each other: modularity is unmeasured, not tangled (#682)."""
    (tmp_path / "pkg").mkdir()
    for name in ("__init__", "a", "b"):
        (tmp_path / "pkg" / f"{name}.py").write_text("x = 1\n", encoding="utf-8")
    structure = analyze_structure(tmp_path).as_dict()
    assert structure["available"] is True
    assert structure["modularity_reason"] == "no_import_edges"
    row = _row(_run({"r": {"structure": dict(structure)}}), "layer-2-design")
    assert (row["status"], row["level"]) == ("unknown", None)
    assert "structure.modularity_q" in row["missing_signals"]


def test_layer_three_reads_the_suppression_footprint(suppressed_repo: dict[str, Any]) -> None:
    row = _row(_run({"r": suppressed_repo}), "layer-3-linters")
    assert row["level"] == 2   # one fresh suppression: a linter runs, none stale

    stale = copy.deepcopy(suppressed_repo)
    stale["promissory_markers"]["families"]["suppression"]["stale"] = 11
    assert _row(_run({"r": stale}), "layer-3-linters")["level"] == 1

    shallow = copy.deepcopy(suppressed_repo)   # degenerate history: stale 0 says nothing
    shallow["promissory_markers"]["aging_reliable"] = False
    assert _row(_run({"r": shallow}), "layer-3-linters")["level"] == 1

    none = copy.deepcopy(suppressed_repo)
    del none["promissory_markers"]["families"]["suppression"]
    row = _row(_run({"r": none}), "layer-3-linters")
    assert (row["status"], row["level"]) == ("unknown", None)   # no footprint is not credit
    assert "promissory_markers.families.suppression.total" in row["missing_signals"]


def test_instructions_raise_layer_zero_and_five(instructed_repo: dict[str, Any]) -> None:
    levels = _levels(_run({"r": instructed_repo}))
    assert levels["layer-0-instructions"] == 2
    assert levels["layer-5-ci"] == 2
    assert levels["layer-8-agent-ops"] == 1


def _ci_repo(root: Path, workflow: str | None, instructions: bool) -> dict[str, Any]:
    root.mkdir()
    (root / "Makefile").write_text("test:\n\tpython -m pytest\n", encoding="utf-8")
    if workflow is not None:
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "workflows" / "ci.yml").write_text(workflow, encoding="utf-8")
    if instructions:
        (root / "CLAUDE.md").write_text("# Guide\n\nRun `make test`.\n", encoding="utf-8")
    return _scans(_commit(root))


def test_layer_five_level_one_reads_ci_not_the_instruction_file(tmp_path: Path) -> None:
    """Issue #698: a CI test job is level 1 with no instruction file; naming the check is 2."""
    ci = _CI_WORKFLOW.replace("make check", "make test")
    bare = _ci_repo(tmp_path / "bare", ci, instructions=False)
    assert bare["ci_checks"]["test_or_lint_runs"] is True
    assert _row(_run({"r": bare}), "layer-5-ci")["level"] == 1
    named = _ci_repo(tmp_path / "named", ci, instructions=True)
    assert _row(_run({"r": named}), "layer-5-ci")["level"] == 2


def test_layer_five_is_zero_without_a_ci_check_even_when_a_check_is_named(tmp_path: Path) -> None:
    """The accepted regression: a named check alone no longer reaches any Layer 5 level."""
    deploy = _CI_WORKFLOW.replace("make check", "make deploy")
    for name, workflow in (("none", None), ("deploy", deploy)):
        scans = _ci_repo(tmp_path / name, workflow, instructions=True)
        assert scans["agent_environment"]["layer5_cap"]["applies"] is False
        assert scans["ci_checks"]["test_or_lint_runs"] is False
        assert _row(_run({"r": scans}), "layer-5-ci")["level"] == 0


def test_layer_five_is_unknown_when_ci_jobs_come_only_from_includes(tmp_path: Path) -> None:
    root = tmp_path / "included"
    root.mkdir()
    (root / ".gitlab-ci.yml").write_text(
        "include:\n  - component: gitlab.com/acme/ci/python@1.0\n", encoding="utf-8")
    scans = _scans(_commit(root))
    assert scans["ci_checks"]["basis"] == "included_jobs"
    row = _row(_run({"r": scans}), "layer-5-ci")
    assert (row["status"], row["level"]) == ("unknown", None)
    assert "ci_checks.test_or_lint_runs" in row["missing_signals"]


def test_no_instruction_file_is_zero_but_a_missing_block_is_unknown(
    golden_repos: dict[str, dict[str, Any]],
) -> None:
    scans = copy.deepcopy(golden_repos["golden-doc-repo"])
    assert scans["layer_grades"] == {"instructions_grade": None, "instruction_file_present": False}
    assert _row(_run({"r": scans}), "layer-0-instructions")["level"] == 0

    del scans["layer_grades"]
    row = _row(_run({"r": scans}), "layer-0-instructions")
    assert (row["status"], row["level"]) == ("unknown", None)
    assert "layer_grades.instruction_file_present" in row["missing_signals"]


def test_another_tools_instruction_file_reaches_layer_zero_partial(tmp_path: Path) -> None:
    """A committed Copilot file is an instruction file; the Claude Code budget is 0."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "golden-doc-repo", repo)
    (repo / ".github").mkdir(exist_ok=True)
    (repo / ".github" / "copilot-instructions.md").write_text(
        "# Guide\n\nRun the tests before pushing.\n", encoding="utf-8")
    scans = _scans(_commit(repo))
    assert scans["layer_grades"]["instruction_file_present"] is True
    assert scans["layer_grades"]["instructions_grade"] in {"A", "B", "C", "D", "F"}
    assert scans["nested_instructions"]["budget"]["claude_code"]["bytes"] == 0
    assert _row(_run({"r": scans}), "layer-0-instructions")["level"] == 1


def test_a_maturity_model_row_on_a_layer_grade_loads_and_evaluates(
    tmp_path: Path, golden_repos: dict[str, dict[str, Any]], instructed_repo: dict[str, Any],
) -> None:
    """Issue #620: a layer grade is a signal path; an unknown one fails at load."""
    row = ('version = "v"\n[[rows]]\nid = "graded"\ntitle = "G"\nscope = "team"\n'
           'aggregation = "min"\n[[rows.levels]]\nlevel = 1\ncriteria = [\n'
           '  { signal = "layer_grades.%s", op = "in", value = ["A", "B", "C", "D", "F"] },\n]\n')
    path = tmp_path / "r.toml"
    path.write_text(row % "instructions_grade", encoding="utf-8")
    maturity_model = load_levels(path, signal_path_validator())
    graded = instructed_repo["layer_grades"]["instructions_grade"]
    assert isinstance(graded, str)
    result = evaluate(maturity_model, {"name": "g", "settings": None, "repos": {"r": instructed_repo}})
    assert result["rows"][0]["level"] == 1
    bare = evaluate(maturity_model, {"name": "g", "settings": None,
                             "repos": {"r": golden_repos["golden-doc-repo"]}})
    assert bare["rows"][0]["status"] == "unknown"   # null grade: no instruction file

    path.write_text(row % "layer_3_grade", encoding="utf-8")
    with pytest.raises(LevelsError, match=r"team:layer_grades\.layer_3_grade"):
        load_levels(path, signal_path_validator())


def test_counts_only_output_reaches_the_same_levels(
    golden_repos: dict[str, dict[str, Any]], instructed_repo: dict[str, Any],
    suppressed_repo: dict[str, Any], two_package_repo: dict[str, Any],
) -> None:
    """A fleet run is counts-only by default; the maturity model reads only kept fields."""
    run_context = json.loads(GOLDEN_RUN_CONTEXT.read_text(encoding="utf-8"))
    full = {**golden_repos, "instructed": instructed_repo, "suppressed": suppressed_repo,
            "two_package": two_package_repo,
            "run_context": {k: v for k, v in run_context.items() if k in scan_names()}}
    counts = {name: counts_only_scans(scans) for name, scans in full.items()}
    for name in full:
        assert _levels(_run({name: counts[name]})) == _levels(_run({name: full[name]}))


def test_rerun_is_byte_identical(golden_repos: dict[str, dict[str, Any]]) -> None:
    assert dumps_result(_run(golden_repos)) == dumps_result(_run(copy.deepcopy(golden_repos)))
