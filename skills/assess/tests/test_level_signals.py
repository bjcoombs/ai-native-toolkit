"""lib/scoring/level_signals.py: schema-backed signal paths and the shipped default maturity model."""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from assess_test_support import use_real_lizard

from lib.ci.ci_checks import scan_ci_checks
from lib.counts_only.content_free import counts_only_scans
from lib.keyhole.promissory_markers import scan_promissory_markers
from lib.graph.structure_graph import analyze_structure
from lib.scoring.levels import Group, LevelsError, LevelsResult, dumps_result, evaluate, load_levels, resolve_signal
from lib.scoring.level_signals import (
    DEFAULT_LEVELS,
    ORG_PATHS,
    load_default_levels,
    scan_names,
    signal_path_validator,
    team_path_ok,
)
from lib.run.signals import build_document, known_scans

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
    """Headless scans scored by the installed lizard, whatever stub an earlier file left (#1117)."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        use_real_lizard(monkeypatch)
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
    # The pin is a claim instruction_claims checks: no claim reads not measured (#827).
    (repo / "CLAUDE.md").write_text(
        "# Guide\n\nRun `make check` before pushing.\n\nPython 3.12 is pinned in `.python-version`.\n",
        encoding="utf-8")
    (repo / ".python-version").write_text("3.12\n", encoding="utf-8")
    # A second agent system, so agent_consistency compares two root files.
    (repo / ".windsurfrules").write_text("Follow [CLAUDE.md](CLAUDE.md).\n", encoding="utf-8")
    (repo / "Makefile").write_text("check:\n\tpython -m pytest\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'x'\nversion = '0'\ndependencies = ['httpx']\n\n[tool.mypy]\nstrict = true\n", encoding="utf-8")
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
def owned_repo(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """A headless run of a repository whose CODEOWNERS declares one module path."""
    repo = tmp_path_factory.mktemp("owned") / "repo"
    (repo / ".github").mkdir(parents=True)
    (repo / ".github" / "CODEOWNERS").write_text("src/ @team\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    return _scans(_commit(repo))


@pytest.fixture(scope="module")
def measured_docs_repo(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Twenty modules, each a README beside its code: enough measured docs for a stale share (#822)."""
    repo = tmp_path_factory.mktemp("measured_docs") / "repo"
    for i in range(20):
        (repo / f"m{i}").mkdir(parents=True)
        (repo / f"m{i}" / "README.md").write_text(f"# m{i}\n", encoding="utf-8")
        (repo / f"m{i}" / "a.py").write_text(f"x = {i}\n", encoding="utf-8")
    _commit(repo)
    for i in range(20):  # a second commit per file, so the history is not degenerate
        (repo / f"m{i}" / "a.py").write_text(f"x = {i + 1}\n", encoding="utf-8")
    _git(repo, "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-qam", "two")
    return _scans(repo)


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


def test_the_pack_block_is_published_but_never_a_scan_a_criterion_names() -> None:
    """#1276: the schema allows the --pack block, but only a run's own packs make a pack path valid."""
    from lib.scoring.level_signals import SCHEMA_PATH, signal_path_validator
    enum = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["properties"]["scans"]["propertyNames"]["enum"]
    assert "pack" in enum and "pack" not in scan_names()
    assert not signal_path_validator()("team", "pack.available")


@pytest.mark.parametrize("path", [
    "observability.rung",                          # typed block, scalar leaf
    "agent_environment.layer5_cap.applies",        # nested typed field
    "agent_environment.ci_duration.over_target",   # NotRequired field
    "test_pressure.reason",                        # a union arm (unavailable shape)
    "stats_summary.loc.p95",                       # typed field under an untyped block
    "coverage_gate.configured",                    # typed since #793
    "agent_ops.summary.permissions_encoded",       # typed since #793
    "promissory_markers.families.suppression.total",  # through an identifier-keyed map
    "structure_drift.tier_0.matched_patterns",     # typed since #793
    "review_reality.reviewed_share",               # typed since #793
    "instruction_claims.failed",                   # typed since #793
    "config_drift.anything.deeper",                # untyped block: open
    "coverage_gate.threshold_meets_minimum",          # #781
    "agent_ops.available",                         # every block may report status
    "layer_grades.instructions_grade",             # deterministic layer grade (#620)
    "layer_grades.instruction_file_present",
    "ci_checks.test_or_lint_configured",           # #783 config-only name
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
    "coverage_gate.threshold_minimum",         # typed since #781
    "layer_grades.layer_3_grade",              # no such layer grade
    "layer_grades",                            # ends on an object
    # #793: the blocks a default criterion reads are typed, so a misspelt
    # sub-path fails at load instead of evaluating unknown.
    "coverage_gate.enforcd",
    "agent_ops.summary.permissions_encodd",
    "agent_ops.summary",                       # ends on an object
    "promissory_markers.families.suppression.totl",
    "structure_drift.tier_0.matched",
    "structure_drift.tier_1.human_static_agree",
    "review_reality.anything.deeper",
    "instruction_claims.failures.kind",        # steps into a list
    # #783: the names before the config-only renames fail at load, never unknown.
    "coverage_gate.enforced",
    "ci_checks.test_or_lint_runs",
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
    one = {"skill-disclosure", "skill-script-tests", "skill-personal-data", "model-ci-gated", "contributor-files",
           "skill-library"}  # #1045, #848; else 2
    assert all(len(row["levels"]) == (1 if row["id"] in one else 2) for row in maturity_model["rows"])
    assert "disagree" in DEFAULT_LEVELS.read_text(encoding="utf-8").lower()


def test_every_default_signal_is_emitted_by_some_real_output(
    golden_repos: dict[str, dict[str, Any]], instructed_repo: dict[str, Any],
    suppressed_repo: dict[str, Any], two_package_repo: dict[str, Any], owned_repo: dict[str, Any],
    measured_docs_repo: dict[str, Any],
) -> None:
    """Catches a typo in an open block, which the validator cannot."""
    run_context = json.loads(GOLDEN_RUN_CONTEXT.read_text(encoding="utf-8"))
    documents = [*golden_repos.values(), instructed_repo, suppressed_repo, two_package_repo,
                 owned_repo, measured_docs_repo, run_context]
    # review_reality needs GitHub; its fields are named in its module doc. review_evidence
    # is unavailable without it unless an AI review step is committed (test_review_automation).
    # contributor_files.policy.present needs a SECURITY file in the tree or the GitHub read of
    # the owner's .github repository (#1045); tests/test_contributor_files.py emits it.
    offline = {"review_reality.reviewed_share", "review_reality.approved_share",
               "review_evidence.present", "contributor_files.policy.present"}
    signals = {c["signal"] for row in load_default_levels()["rows"]
               for level in row["levels"] for c in level["criteria"]}
    never = sorted(
        s for s in signals - offline
        if all(resolve_signal(doc, s) is _UNAVAILABLE for doc in documents)
    )
    assert never == []


def test_scans_score_sizes_after_an_earlier_file_left_a_lizard_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1117: the treemap loaders' empty lizard stub cannot turn the fixtures' sizes unknown."""
    stub = type(os)("lizard")
    monkeypatch.setitem(sys.modules, "lizard", stub)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert _scans(_commit(tmp_path))["stats_summary"]["loc_by_class"]["source"]["p95"] == 1.0
    assert sys.modules["lizard"] is stub   # the stub other files rely on is put back
    monkeypatch.delitem(sys.modules, "lizard")
    _scans(tmp_path)
    assert "lizard" not in sys.modules     # and no entry stays no entry


def test_default_levels_on_the_golden_fixture_repos(golden_repos: dict[str, dict[str, Any]]) -> None:
    result = _run(golden_repos)
    for name in golden_repos:
        assert _levels(_run({name: golden_repos[name]})) == {
            "layer-0-instructions": 0,   # no CLAUDE.md: level 0, not unknown
            "layer-1-liveness": 0,       # no telemetry committed: rung 0 (#699)
            "layer-2-design": None,      # no Python package: no_python_packages
            "layer-3-linters": 0,        # no CI file: no linter runs in CI (#782)
            "layer-4-architecture": None,  # no ownership map: no_ownership_map
            "layer-5-ci": 0,
            "layer-6-coverage": 0,
            "layer-7-review": None,      # needs GitHub
            "layer-8-agent-ops": 0,
            "doc-freshness": None,       # under 20 measured docs: too_few_measured_docs (#822)
            "guardrails-stated": None,   # no configured gate: nothing to state
            "agent-consistency": None,   # no instruction file: nothing to agree with
            "skill-disclosure": None, "skill-personal-data": None,  # no SKILL.md or agent file: no_skill_files
            "skill-script-tests": None,  # no skill holds a script: no_skill_scripts
            "model-ci-gated": None,      # no model-calling CI job detected: no_model_jobs
            "contributor-files": 0,      # no CONTRIBUTING or SECURITY file
            "secrets-guard": 0,          # no secrets hook or CI scan, and no remote
            "skill-library": 0,          # no committed SKILL.md
        }
    for scans in golden_repos.values():   # measured headless, each unknown for a named reason
        assert scans["structure"]["reason"].startswith("no_python_packages:")
        assert scans["structure_drift"]["reason"].startswith("no_ownership_map:")
        assert scans["promissory_markers"]["available"] is True
    layer1 = _row(result, "layer-1-liveness")
    assert (layer1["status"], layer1["level"], layer1["missing_signals"]) == ("level", 0, [])
    for scans in golden_repos.values():   # the rung is read headless from committed files
        assert scans["observability"]["rung"] == 0


def test_default_levels_on_the_golden_run_context() -> None:
    """The captured full run predates most headless scans: only Layer 1 is read."""
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
        # 115 matched ownership patterns, but the capture predates boundary_patterns (#785)
        "layer-4-architecture": None,
        "layer-5-ci": None,
        "layer-6-coverage": None,
        "layer-7-review": None,
        "layer-8-agent-ops": None,
        "doc-freshness": None,  # the capture predates doc_staleness.summary
        "guardrails-stated": None,  # the capture predates the guardrails block
        "agent-consistency": None,  # the capture predates agent_consistency
        "skill-disclosure": None,  # the capture predates skill_disclosure
        "skill-script-tests": None,  # the capture predates skill_script_tests
        "skill-personal-data": None, "contributor-files": None,  # the capture predates both blocks
        "secrets-guard": None, "model-ci-gated": None,  # the capture predates both blocks
        "skill-library": None,  # the capture predates skill_library
    }
    assert "layer_grades.instruction_file_present" in _row(
        result, "layer-0-instructions")["missing_signals"]
    layer2 = _row(result, "layer-2-design")
    assert layer2["repos"]["toolkit"]["bounds"] == [1, 2]
    assert layer2["missing_signals"] == ["structure.cross_package_edges"]
    # A current run of this single-package capture reports no boundary crossed.
    single = copy.deepcopy(run_context)
    single["structure"]["cross_package_edges"] = 0
    single["structure"]["front_door_ratio"] = None  # #760: no boundary, no ratio
    assert _row(_run({"toolkit": single}), "layer-2-design")["level"] == 1
    # A current run counts its non-catch-all boundaries; with a source p95 of 483.65 that is
    # level 2 (the capture predates loc_by_class, #820, so the split is added here).
    single["structure_drift"]["tier_0"]["boundary_patterns"] = 115
    single["stats_summary"]["loc_by_class"] = {"source": {"files_scored": 100, "p95": 483.65}}
    assert _row(_run({"toolkit": single}), "layer-4-architecture")["level"] == 2
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
    layers = [row["id"].split("-")[1] for row in rows if row["id"].startswith("layer-")]
    assert layers == [str(n) for n in range(9)]
    run_context = json.loads(GOLDEN_RUN_CONTEXT.read_text(encoding="utf-8"))
    for row in _run({"toolkit": run_context})["rows"]:
        if row["status"] == "level":
            assert isinstance(row["level"], int) and not isinstance(row["level"], bool), row
        else:
            assert (row["status"], row["level"]) == ("unknown", None), row
            assert row["missing_signals"], row


def test_layer_two_is_unknown_on_a_repository_with_no_import_graph(tmp_path: Path) -> None:
    """A language with no builder is unmeasured, not modularity 0."""
    (tmp_path / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
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


_LINT_WORKFLOW = ("on: pull_request\njobs:\n  lint:\n    runs-on: ubuntu-latest\n    steps:\n"
                  "      - run: ruff check .\n")


def _linted(repo: Path, files: dict[str, str]) -> dict[str, Any]:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    _commit(repo)
    return {"ci_checks": scan_ci_checks(repo),
            "promissory_markers": scan_promissory_markers(repo).summary()}


def test_layer_three_level_one_reads_a_linter_run_in_ci(tmp_path: Path) -> None:
    """Issue #782: a linter in CI earns level 1; a suppression comment alone earns nothing."""
    clean = _linted(tmp_path / "clean", {".github/workflows/ci.yml": _LINT_WORKFLOW,
                                         "app.py": "import os\n"})
    row = _row(_run({"r": clean}), "layer-3-linters")
    assert row["repos"]["r"]["bounds"][0] == 1   # level 2 unknown: no suppression family
    assert "promissory_markers.families.suppression.stale" in row["missing_signals"]

    noqa_only = _linted(tmp_path / "noqa", {"app.py": "import os  # noqa: F401\n"})
    row = _row(_run({"r": noqa_only}), "layer-3-linters")
    assert (row["status"], row["level"]) == ("level", 0)

    target = _linted(tmp_path / "target", {".github/workflows/ci.yml": _CI_WORKFLOW,
                                           "Makefile": "check:\n\ttrue\n"})
    row = _row(_run({"r": target}), "layer-3-linters")
    assert row["status"] == "unknown" and row["repos"]["r"]["bounds"][0] == 0


def test_layer_three_reads_the_suppression_footprint(suppressed_repo: dict[str, Any]) -> None:
    linted = {**copy.deepcopy(suppressed_repo), "ci_checks": {"lint_runs": True}}
    row = _row(_run({"r": linted}), "layer-3-linters")
    assert row["level"] == 2   # a linter in CI, one fresh suppression, none stale

    stale = copy.deepcopy(linted)
    stale["promissory_markers"]["families"]["suppression"]["stale"] = 11
    assert _row(_run({"r": stale}), "layer-3-linters")["level"] == 1

    shallow = copy.deepcopy(linted)   # degenerate history: stale 0 says nothing
    shallow["promissory_markers"]["aging_reliable"] = False
    assert _row(_run({"r": shallow}), "layer-3-linters")["level"] == 1

    none = copy.deepcopy(linted)
    del none["promissory_markers"]["families"]["suppression"]
    row = _row(_run({"r": none}), "layer-3-linters")
    # No footprint leaves only level 2 unknown: level 1 still reads the linter run.
    assert (row["status"], row["repos"]["r"]["bounds"]) == ("unknown", [1, 2])
    assert row["missing_signals"] == ["promissory_markers.families.suppression.stale"]

    unlinted = {**copy.deepcopy(suppressed_repo), "ci_checks": {"lint_runs": False}}
    row = _row(_run({"r": unlinted}), "layer-3-linters")
    assert (row["status"], row["level"]) == ("level", 0)   # a suppression never grants the row


def test_instructions_raise_layer_zero_and_five(instructed_repo: dict[str, Any]) -> None:
    levels = _levels(_run({"r": instructed_repo}))
    assert levels["layer-0-instructions"] == 2
    assert levels["layer-5-ci"] == 2
    assert levels["layer-8-agent-ops"] == 1


def test_layer_four_rejects_a_catch_all_only_codeowners(tmp_path: Path) -> None:
    """Issue #785: `* @x` declares no module boundary; `src/ @team` does."""
    for name, codeowners, low in (("star", "* @x\n", 0), ("src", "src/ @team\n", 1)):
        root = tmp_path / name
        (root / ".github").mkdir(parents=True)
        (root / ".github" / "CODEOWNERS").write_text(codeowners, encoding="utf-8")
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        row = _row(_run({"r": _scans(_commit(root))}), "layer-4-architecture")
        if low == 0:
            assert row["level"] == 0
        else:  # level 2 reads lizard's loc p95, which may be unavailable: check level 1 alone
            assert row["repos"]["r"]["levels"][0]["met"] is True


def _ci_repo(root: Path, workflow: str | None, instructions: bool,
             extra: dict[str, str] | None = None) -> dict[str, Any]:
    root.mkdir()
    (root / "Makefile").write_text("test:\n\tpython -m pytest\n", encoding="utf-8")
    if workflow is not None:
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "workflows" / "ci.yml").write_text(workflow, encoding="utf-8")
    if instructions:
        (root / "CLAUDE.md").write_text("# Guide\n\nRun `make test`.\n", encoding="utf-8")
    for rel, text in (extra or {}).items():
        (root / rel).write_text(text, encoding="utf-8")
    return _scans(_commit(root))


_UNLOCKED = {"package.json": json.dumps({"name": "x", "dependencies": {"left-pad": "1.3.0"}})}
_LOCKED = {**_UNLOCKED, "package-lock.json": "{}"}


def test_layer_five_level_one_reads_ci_not_the_instruction_file(tmp_path: Path) -> None:
    """Issue #698: a CI test job is level 1 with no instruction file; level 2 is the setup."""
    ci = _CI_WORKFLOW.replace("make check", "make test")
    unlocked = _ci_repo(tmp_path / "unlocked", ci, instructions=True, extra=_UNLOCKED)
    assert unlocked["ci_checks"]["test_or_lint_configured"] is True
    assert unlocked["ci_checks"]["enforced_check_runs"] is True
    assert unlocked["agent_environment"]["pinning"]["unlocked_total"] == 1
    assert _row(_run({"r": unlocked}), "layer-5-ci")["level"] == 1
    named = _ci_repo(tmp_path / "named", ci, instructions=True, extra=_LOCKED)
    assert _row(_run({"r": named}), "layer-5-ci")["level"] == 2
    # #927: with no manifest there is nothing to lock, so level 2 is not measured.
    bare = _row(_run({"r": _ci_repo(tmp_path / "bare", ci, instructions=True)}), "layer-5-ci")
    assert (bare["status"], bare["level"]) == ("unknown", None)
    assert bare["repos"]["r"]["levels"][1]["unmet"][0]["reason"] == "no_manifests"


def test_layer_five_level_two_does_not_read_the_named_check(tmp_path: Path) -> None:
    """Issue #786: naming a check is read once, by Layer 0 level 2, never by a Layer 5 level."""
    layer5 = next(r for r in load_default_levels()["rows"] if r["id"].startswith("layer-5-"))
    read = {c["signal"] for lv in layer5["levels"] for c in lv["criteria"]}
    assert not read & {"agent_environment.check.named_total", "agent_environment.layer5_cap.applies"}
    ci = _CI_WORKFLOW.replace("make check", "make test")
    unnamed = _ci_repo(tmp_path / "unnamed", ci, instructions=False,
                       extra={**_LOCKED, "CLAUDE.md": "# Project\n\nNotes.\n"})
    assert unnamed["agent_environment"]["check"]["named_total"] == 0
    levels = _levels(_run({"r": unnamed}))
    assert levels["layer-0-instructions"] == 1   # the missing named check costs Layer 0 only
    assert levels["layer-5-ci"] == 2


def test_layer_five_level_two_needs_a_lockfile_even_with_setup(tmp_path: Path) -> None:
    """Issue #786: committed setup does not stand in for a lockfile at Layer 5 level 2."""
    ci = _CI_WORKFLOW.replace("make check", "make test")
    setup = _ci_repo(tmp_path / "setup", ci, instructions=True,
                     extra={**_UNLOCKED, "init.sh": "#!/bin/sh\nnpm install\n"})
    env = setup["agent_environment"]
    assert env["setup"]["present"] is True and env["setup_ready"] is True
    assert env["pinning"]["unlocked_total"] == 1
    row = _row(_run({"r": setup}), "layer-5-ci")
    assert row["level"] == 1


def test_layer_five_is_unknown_on_a_ci_system_the_scanner_cannot_read(tmp_path: Path) -> None:
    """Issue #787: a Jenkins-only repository is not measured at Layer 5, never level 0."""
    scans = _ci_repo(tmp_path / "jenkins", None, instructions=False,
                     extra={"Jenkinsfile": "pipeline { stages { stage('t') { steps { sh 'pytest' } } } }\n"})
    assert (scans["ci_checks"]["basis"], scans["ci_checks"]["unread_ci_files"]) == ("unread_ci_system", 1)
    row = _row(_run({"r": scans}), "layer-5-ci")
    assert (row["status"], row["level"]) == ("unknown", None)
    assert "ci_checks.enforced_check_runs" in row["missing_signals"]
    layer3 = _row(_run({"r": scans}), "layer-3-linters")
    assert (scans["ci_checks"]["lint_runs"], scans["ci_checks"]["lint_basis"]) == (
        None, "unread_ci_system")
    assert (layer3["status"], layer3["level"]) == ("unknown", None)
    assert "ci_checks.lint_runs" in layer3["missing_signals"]
    both = _ci_repo(tmp_path / "both", _CI_WORKFLOW.replace("make check", "make test"),
                    instructions=False, extra={"Jenkinsfile": "sh 'pytest'\n"})
    assert _row(_run({"r": both}), "layer-5-ci")["repos"]["r"]["levels"][0]["met"] is True


def test_layer_five_is_zero_without_a_ci_check_even_when_a_check_is_named(tmp_path: Path) -> None:
    """The accepted regression: a named check alone no longer reaches any Layer 5 level."""
    deploy = _CI_WORKFLOW.replace("make check", "make deploy")
    for name, workflow in (("none", None), ("deploy", deploy)):
        scans = _ci_repo(tmp_path / name, workflow, instructions=True)
        assert scans["agent_environment"]["layer5_cap"]["applies"] is False
        assert scans["ci_checks"]["test_or_lint_configured"] is False
        assert scans["ci_checks"]["enforced_check_runs"] is False
        assert _row(_run({"r": scans}), "layer-5-ci")["level"] == 0


def test_layer_five_is_zero_when_ci_only_names_pytest_or_tox_as_a_value(tmp_path: Path) -> None:
    """Issue #725: ``needs: - pytest`` and a ``matrix:`` axis ``- tox`` are values, not CI runs."""
    head = "name: ci\non: [pull_request]\njobs:\n"
    needs = (head + "  pytest:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
             "  deploy:\n    needs:\n      - pytest\n    runs-on: ubuntu-latest\n"
             "    steps:\n      - run: echo deploy\n")
    matrix = (head + "  t:\n    runs-on: ubuntu-latest\n    strategy:\n      matrix:\n"
              "        tool:\n          - tox\n    steps:\n      - run: echo x\n")
    for name, workflow in (("needs", needs), ("matrix", matrix)):
        scans = _ci_repo(tmp_path / name, workflow, instructions=False)
        assert scans["ci_checks"]["check_commands"] == 0
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
    assert "ci_checks.enforced_check_runs" in row["missing_signals"]


def test_layer_five_level_one_needs_an_enforced_check(tmp_path: Path) -> None:
    """Issue #775: a CI check that cannot fail its job does not meet Layer 5 level 1."""
    masked = _ci_repo(tmp_path / "masked", _CI_WORKFLOW.replace("make check", "pytest || true"),
                      instructions=False)
    assert masked["ci_checks"]["test_or_lint_configured"] is True
    assert masked["ci_checks"]["enforced_check_runs"] is False
    assert _row(_run({"r": masked}), "layer-5-ci")["level"] == 0
    plain = _ci_repo(tmp_path / "plain", _CI_WORKFLOW.replace("make check", "pytest"), instructions=False,
                     extra=_LOCKED)
    assert _row(_run({"r": plain}), "layer-5-ci")["level"] >= 1


def test_layer_five_is_unknown_on_a_scan_without_enforced_check_runs(tmp_path: Path) -> None:
    """A stored scan from before #775 has no enforced_check_runs: unknown, never 0."""
    scans = _ci_repo(tmp_path / "old", _CI_WORKFLOW.replace("make check", "make test"), instructions=False)
    del scans["ci_checks"]["enforced_check_runs"]
    row = _row(_run({"r": scans}), "layer-5-ci")
    assert row["status"] == "unknown"
    assert "ci_checks.enforced_check_runs" in row["missing_signals"]


def test_no_instruction_file_is_zero_but_a_missing_block_is_unknown(
    golden_repos: dict[str, dict[str, Any]],
) -> None:
    scans = copy.deepcopy(golden_repos["golden-doc-repo"])
    assert scans["layer_grades"] == {"instructions_grade": None, "instruction_file_present": False,
                                     "instruction_presence": "none"}
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


def test_nested_only_instructions_meet_layer_zero_level_one(tmp_path: Path) -> None:
    """#821: a repository whose only instruction file is pkg/AGENTS.md reads nested_only,
    with a non-null grade, and meets Layer 0 level 1 (harnesses load it on demand)."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "golden-doc-repo", repo)
    (repo / "pkg").mkdir(exist_ok=True)
    (repo / "pkg" / "AGENTS.md").write_text(
        "# Pkg\n\nRun `make test` before every commit.\n", encoding="utf-8")
    scans = _scans(_commit(repo))
    assert scans["layer_grades"]["instruction_presence"] == "nested_only"
    assert scans["layer_grades"]["instruction_file_present"] is True
    assert scans["layer_grades"]["instructions_grade"] in {"A", "B", "C", "D", "F"}
    assert _row(_run({"r": scans}), "layer-0-instructions")["level"] >= 1


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
    suppressed_repo: dict[str, Any], two_package_repo: dict[str, Any], owned_repo: dict[str, Any],
) -> None:
    """A fleet run is counts-only by default; the maturity model reads only kept fields."""
    run_context = json.loads(GOLDEN_RUN_CONTEXT.read_text(encoding="utf-8"))
    full = {**golden_repos, "instructed": instructed_repo, "suppressed": suppressed_repo,
            "two_package": two_package_repo, "owned": owned_repo,
            "run_context": {k: v for k, v in run_context.items() if k in scan_names()}}
    counts = {name: counts_only_scans(scans) for name, scans in full.items()}
    for name in full:
        assert _levels(_run({name: counts[name]})) == _levels(_run({name: full[name]}))
    assert counts["owned"]["structure_drift"]["tier_0"]["boundary_patterns"] == 1
    owned_row = _row(_run({"owned": counts["owned"]}), "layer-4-architecture")
    assert owned_row["repos"]["owned"]["levels"][0]["met"] is True


def test_rerun_is_byte_identical(golden_repos: dict[str, dict[str, Any]]) -> None:
    assert dumps_result(_run(golden_repos)) == dumps_result(_run(copy.deepcopy(golden_repos)))


def _coverage_repo(root: Path, files: dict[str, str]) -> dict[str, Any]:
    root.mkdir()
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return _scans(_commit(root))


def test_layer_six_level_one_needs_a_threshold_of_at_least_fifty(tmp_path: Path) -> None:
    """Issue #781: a 1 percent gate is level 0; 85 is level 1; a templated one is unknown."""
    report = "[project]\nname = 'x'\nversion = '0'\n\n[tool.coverage.report]\nfail_under = {}\n"
    low = _coverage_repo(tmp_path / "low", {"pyproject.toml": report.format(1)})
    assert low["coverage_gate"]["threshold_meets_minimum"] is False
    assert _row(_run({"r": low}), "layer-6-coverage")["level"] == 0

    # Issue #921: a threshold no CI line runs is level 0.
    config_only = _coverage_repo(tmp_path / "config", {"pyproject.toml": report.format(85)})
    assert _row(_run({"r": config_only}), "layer-6-coverage")["level"] == 0

    run = ("on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
           "      - run: pytest --cov\n")
    high = _coverage_repo(tmp_path / "high", {"pyproject.toml": report.format(85),
                                              ".github/workflows/ci.yml": run})
    row = _row(_run({"r": high}), "layer-6-coverage")
    # Mutation (level 2) is not measured headless, so level 1 shows as the floor.
    assert (row["status"], row.get("floor")) == ("unknown", 1)

    workflow = ("on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - run: pytest --cov-fail-under=${{ env.COV_MIN }}\n")
    templated = _coverage_repo(tmp_path / "templated", {".github/workflows/ci.yml": workflow})
    row = _row(_run({"r": templated}), "layer-6-coverage")
    assert (row["status"], row["level"], row.get("floor")) == ("unknown", None, None)
    assert "coverage_gate.enforced_meets_minimum" in row["missing_signals"]


def test_layer_six_is_unknown_on_a_go_repository_with_no_readable_gate(tmp_path: Path) -> None:
    """Issue #788: a Go repository with no gate found is not measured, never 0."""
    go = _coverage_repo(tmp_path / "go", {"go.mod": "module example.com/x\n\ngo 1.22\n",
                                          "main.go": "package main\n\nfunc main() {}\n"})
    assert go["coverage_gate"]["unmeasured_reason"] == "go_rust_gate_form_not_read"
    row = _row(_run({"r": go}), "layer-6-coverage")
    assert (row["status"], row["level"]) == ("unknown", None)
    assert row.get("floor") is None
    gated = _coverage_repo(tmp_path / "gated", {
        "go.mod": "module example.com/x\n", ".testcoverage.yml": "threshold:\n  total: 80\n",
        ".github/workflows/ci.yml": "on: push\njobs:\n  t:\n    runs-on: x\n    steps:\n"
                                    "      - uses: vladopajic/go-test-coverage@v2\n"})
    row = _row(_run({"r": gated}), "layer-6-coverage")
    assert (row["status"], row.get("floor")) == ("unknown", 1)
