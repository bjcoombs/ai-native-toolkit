"""Docs and generated files kept true by CI (issue #852): ``lib.ci.docs_kept_true``."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import lib.scoring.level_tally as lt
from lib.ci.docs_kept_true import apply_kept_true, scan_docs_kept_true
from lib.counts_only.content_free import counts_only
from lib.docs.doc_complexity_join import is_stale

CID = "docs-kept-true.1"


def _workflow(*runs: str) -> str:
    steps = "".join(f"      - run: {r}\n" for r in runs)
    return ("name: ci\non: [push, pull_request]\njobs:\n  check:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - uses: actions/checkout@v4\n" + steps)


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _block(root: Path) -> dict[str, Any]:
    return dict(scan_docs_kept_true(root))


def _state(root: Path) -> str:
    block = {"doc_staleness": {"kept_true": scan_docs_kept_true(root)}}
    cond = next(c for c in lt.level_result(lt.default_model(), block)["conditions"] if c["id"] == CID)
    return str(cond["state"])


DOCS = {"docs/api.md": "# API\n", "docs/guide.md": "# Guide\n"}


def test_a_codegen_step_then_git_diff_exit_code_meets(tmp_path: Path) -> None:
    root = _repo(tmp_path, {**DOCS, ".github/workflows/ci.yml": _workflow(
        "go generate ./...", "git diff --exit-code -- docs/")})
    block = _block(root)
    assert block["checks"] == [{"path": ".github/workflows/ci.yml", "kind": "regen_diff", "covers": ["docs"]}]
    assert (block["present"], block["regen_diff"], block["files_read"]) == (True, 1, 1)
    assert _state(root) == "met"


def test_a_verify_update_pair_run_from_a_workflow_step_meets(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"hack/verify-codegen.sh": "#!/bin/sh\n./hack/update-codegen.sh\n",
                            "hack/update-codegen.sh": "#!/bin/sh\ncontroller-gen paths=./...\n",
                            ".github/workflows/ci.yml": _workflow("hack/verify-codegen.sh")})
    block = _block(root)
    assert block["verify_pair"] == 1 and block["present"] is True
    assert _state(root) == "met"


def test_a_verify_script_with_no_update_sibling_is_not_a_pair(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"hack/verify-all.sh": "#!/bin/sh\necho ok\n",
                            ".github/workflows/ci.yml": _workflow("hack/verify-all.sh")})
    assert _block(root)["verify_pair"] == 0


@pytest.mark.parametrize("line", [
    "git diff --exit-code || echo changed",
    "git diff --quiet || true",
])
def test_a_diff_used_only_to_detect_change_does_not_count(tmp_path: Path, line: str) -> None:
    root = _repo(tmp_path, {**DOCS, ".github/workflows/ci.yml": _workflow("make generate", line)})
    block = _block(root)
    assert (block["present"], block["regen_diff"]) == (False, 0)
    assert _state(root) == "unmet"


def test_a_diff_whose_fallback_re_raises_counts(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _workflow("make generate"),
                            "Makefile": "generate:\n\tgo generate ./...\n"})
    text = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    text += "      - run: git diff --exit-code || { echo 'run make generate'; exit 1; }\n"
    (root / ".github/workflows/ci.yml").write_text(text, encoding="utf-8")
    assert _block(root)["regen_diff"] == 1


def test_a_diff_with_nothing_regenerated_before_it_does_not_count(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": _workflow("git diff --exit-code")})
    assert _block(root)["present"] is False


def test_a_make_target_that_regenerates_then_diffs_is_followed(tmp_path: Path) -> None:
    root = _repo(tmp_path, {**DOCS,
                            "Makefile": "docs-check:\n\tpython gen_docs.py\n\tgit diff --exit-code docs\n",
                            ".github/workflows/ci.yml": _workflow("make docs-check")})
    assert _block(root)["checks"] == [
        {"path": ".github/workflows/ci.yml", "kind": "regen_diff", "covers": ["docs"]}]


def test_a_gitlab_job_counts(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".gitlab-ci.yml": "codegen:\n  script:\n    - make gen\n    - git diff --exit-code\n"})
    block = _block(root)
    assert (block["regen_diff"], block["checks"][0]["covers"]) == (1, [])


def test_a_ci_job_running_pytest_doctest_modules_meets(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/pkg/mod.py": "def f():\n    '''>>> 1\n    1'''\n",
                            ".github/workflows/ci.yml": _workflow("uv run pytest --doctest-modules src/pkg")})
    block = _block(root)
    assert block["checks"] == [{"path": ".github/workflows/ci.yml", "kind": "doc_tests", "covers": []}]
    assert _state(root) == "met"


@pytest.mark.parametrize("line", [
    "python -m doctest README.md",
    "sphinx-build -b doctest docs docs/_build",
    "cargo test --doc",
    "pytest --markdown-docs docs/",
    "pytest --doctest-glob='*.md' docs",
])
def test_doc_example_runners_count(tmp_path: Path, line: str) -> None:
    root = _repo(tmp_path, {**DOCS, "README.md": "x\n", ".github/workflows/ci.yml": _workflow(line)})
    assert _block(root)["doc_tests"] == 1


def test_doctest_in_config_with_no_ci_test_step_does_not_count(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"pyproject.toml": "[tool.pytest.ini_options]\naddopts = \"--doctest-modules\"\n",
                            ".github/workflows/ci.yml": _workflow("ruff check .")})
    assert _block(root)["present"] is False
    assert _state(root) == "unmet"


def test_doctest_in_config_with_a_ci_pytest_step_counts(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"pyproject.toml": "[tool.pytest.ini_options]\naddopts = \"--doctest-modules\"\n",
                            ".github/workflows/ci.yml": _workflow("python -m pytest")})
    assert _block(root)["doc_tests"] == 1


def test_a_continue_on_error_step_does_not_count(tmp_path: Path) -> None:
    text = _workflow("make gen") + "      - run: git diff --exit-code\n        continue-on-error: true\n"
    root = _repo(tmp_path, {".github/workflows/ci.yml": text})
    assert _block(root)["present"] is False


def test_an_unread_ci_system_leaves_the_condition_not_measured(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"Jenkinsfile": "pipeline {}\n"})
    block = _block(root)
    assert (block["present"], block["present_reason"]) == (None, "ci_not_read")
    assert _state(root) == "not_measured"


def test_counts_only_keeps_every_count_and_token(tmp_path: Path) -> None:
    root = _repo(tmp_path, {**DOCS, ".github/workflows/ci.yml": _workflow(
        "go generate ./...", "git diff --exit-code -- docs/")})
    kept = scan_docs_kept_true(root)
    out = counts_only({"doc_staleness": {"available": True, "kept_true": kept}})["doc_staleness"]["kept_true"]
    for field in ("files_read", "regen_diff", "verify_pair", "doc_tests", "present"):
        assert out[field] == kept[field], field  # type: ignore[literal-required]
    assert out["checks"] == kept["checks"]


def test_counts_only_keeps_the_present_reason(tmp_path: Path) -> None:
    kept = scan_docs_kept_true(_repo(tmp_path, {"Jenkinsfile": "pipeline {}\n"}))
    out = counts_only({"doc_staleness": {"available": True, "kept_true": kept}})["doc_staleness"]["kept_true"]
    assert (out["present"], out["present_reason"]) == (None, "ci_not_read")


def _put(root: Path, rel: str, text: str) -> None:
    _repo(root, {rel: text})


def _staleness_row(path: str, ratio: float) -> dict[str, Any]:
    return {"path": path, "subject_method": "nearest-ancestor", "ratio": ratio, "measured_code_count": 1}


def test_a_subtree_ci_keeps_true_is_not_stale_and_names_the_check(tmp_path: Path) -> None:
    """#852: staleness does not fire for docs a CI check covers; an uncovered sibling
    subtree with the same churn still reads stale."""
    _put(tmp_path, "docs/api/ref.md", "ref")
    _put(tmp_path, "docs/guide/intro.md", "intro")
    _put(tmp_path, ".github/workflows/docs.yml",
           "on: [push]\njobs:\n  docs:\n    runs-on: ubuntu-latest\n    steps:\n"
           "      - run: make api-docs\n      - run: git diff --exit-code -- docs/api\n")
    rows = [_staleness_row("docs/api/ref.md", 5.0), _staleness_row("docs/guide/intro.md", 5.0)]
    block = {"docs": rows, "history_truncated": False, "churn_degenerate": False,
             "summary": {"measured_docs": 2, "stale_docs": 2, "stale_pct": None}}
    out = apply_kept_true(block, scan_docs_kept_true(tmp_path))  # type: ignore[arg-type]
    by_path = {d["path"]: d for d in out["docs"]}
    assert by_path["docs/api/ref.md"]["kept_true_by"] == {"kind": "regen_diff", "path": ".github/workflows/docs.yml"}
    assert not is_stale(dict(by_path["docs/api/ref.md"]))
    assert "kept_true_by" not in by_path["docs/guide/intro.md"]
    assert is_stale(dict(by_path["docs/guide/intro.md"]))
    assert out["summary"]["stale_docs"] == 1
    assert out["kept_true"]["present"] is True


def test_a_docstring_only_doctest_run_meets_but_covers_no_doc(tmp_path: Path) -> None:
    """``--doctest-modules`` tests code docstrings: met, but a stale doc stays stale."""
    _put(tmp_path, "docs/x.md", "x")
    _put(tmp_path, ".github/workflows/ci.yml", _workflow("pytest --doctest-modules docs"))
    kept = scan_docs_kept_true(tmp_path)
    assert kept["present"] is True and kept["checks"][0]["covers"] == []
    block = {"docs": [_staleness_row("docs/x.md", 5.0)], "history_truncated": False, "churn_degenerate": False}
    out = apply_kept_true(block, kept)  # type: ignore[arg-type]
    assert "kept_true_by" not in out["docs"][0] and is_stale(dict(out["docs"][0]))


def test_a_later_commands_re_raise_does_not_unmask_a_passing_fallback(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"Makefile": "check:\n\tpython gen.py\n\tgit diff --exit-code docs || echo changed; make lint || exit 1\n",
                            **DOCS, ".github/workflows/ci.yml": _workflow("make check")})
    assert _block(root)["regen_diff"] == 0


def test_a_reader_fault_reads_not_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import lib.ci.docs_kept_true as dkt

    def boom(_root: Path) -> None:
        raise RuntimeError("x")
    monkeypatch.setattr(dkt, "scan_docs_kept_true", boom)
    kept = dkt.kept_true_or_unknown(tmp_path)
    assert (kept["present"], kept["present_reason"], kept["checks"]) == (None, "scan_failed", [])


def test_a_hub_ci_keeps_true_is_not_a_stale_hub() -> None:
    from lib.run.context_blocks import build_stale_hubs

    graph = {"available": True, "hubs": [{"path": "docs/a.md", "pagerank": 0.5}]}
    row = {**_staleness_row("docs/a.md", 5.0), "last_commit_days": 9, "code_churn_in_window": 9,
           "kept_true_by": {"kind": "regen_diff", "path": ".github/workflows/ci.yml"}}
    assert build_stale_hubs(graph, {"available": True, "docs": [row]}) == []


def test_a_doctest_glob_covers_only_the_files_it_matches(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"docs/a.md": "a\n", "docs/b.rst": "b\n",
                            ".github/workflows/ci.yml": _workflow("pytest --doctest-glob=*.rst docs")})
    assert _block(root)["checks"][0]["covers"] == ["docs/b.rst"]


def test_a_pytest_doctest_marker_is_not_a_doc_test_run(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"tests/test_x.py": "x\n", ".github/workflows/ci.yml": _workflow("pytest -m doctest tests")})
    assert _block(root)["present"] is False


def test_a_renamed_child_or_newer_source_stays_stale_under_a_covering_check() -> None:
    by = {"kind": "doc_tests", "path": ".github/workflows/ci.yml"}
    renamed = {**_staleness_row("docs/x.md", 0.0), "renamed_children_named": 1, "kept_true_by": by}
    newer = {**_staleness_row("docs/y.md", 0.0), "provenance": {"source_newer": True}, "kept_true_by": by}
    assert is_stale(renamed) and is_stale(newer)
    assert not is_stale({**_staleness_row("docs/z.md", 9.0), "kept_true_by": by})


def test_a_script_run_behind_a_passing_fallback_is_not_followed(tmp_path: Path) -> None:
    root = _repo(tmp_path, {**DOCS, "Makefile": "check:\n\t./hack/check.sh || true\n",
                            "hack/check.sh": "#!/bin/sh\nmake gen\ngit diff --exit-code docs\n",
                            ".github/workflows/ci.yml": _workflow("make check")})
    assert _block(root)["present"] is False


def test_a_covered_hub_that_still_reads_stale_stays_a_stale_hub() -> None:
    from lib.run.context_blocks import build_stale_hubs

    graph = {"available": True, "hubs": [{"path": "docs/a.md", "pagerank": 0.5}]}
    row = {**_staleness_row("docs/a.md", 0.0), "last_commit_days": 9, "code_churn_in_window": 9,
           "renamed_children_named": 1, "kept_true_by": {"kind": "regen_diff", "path": ".github/workflows/ci.yml"}}
    assert [h["path"] for h in build_stale_hubs(graph, {"available": True, "docs": [row]})] == ["docs/a.md"]


def test_a_gitlab_after_script_diff_cannot_fail_the_job(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".gitlab-ci.yml": "codegen:\n  script:\n    - make gen\n  after_script:\n    - git diff --exit-code\n"})
    assert _block(root)["present"] is False


def test_a_test_run_then_a_diff_is_not_a_regeneration(tmp_path: Path) -> None:
    root = _repo(tmp_path, {**DOCS, ".github/workflows/ci.yml": _workflow("pytest", "git diff --exit-code -- docs/")})
    assert _block(root)["present"] is False


def test_false_as_an_argument_does_not_re_raise(tmp_path: Path) -> None:
    root = _repo(tmp_path, {**DOCS, "Makefile": "check:\n\tpython gen.py\n\tgit diff --exit-code docs || echo false\n",
                            ".github/workflows/ci.yml": _workflow("make check")})
    assert _block(root)["present"] is False
