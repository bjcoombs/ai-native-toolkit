"""Tests for lib/ci/ci_include_inputs.py: ``$[[ inputs.<name> ]]`` include refs resolved (#1339)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.ci.ci_checks import scan_ci_checks
from lib.ci.ci_include_inputs import file_inputs, interpolate, item_inputs, spec_defaults
from lib.ci.ci_includes import STATUSES, IncludeItem, include_items, resolve_includes


def _commit(repo: Path, files: dict[str, str], tag: str | None = None) -> None:
    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "c"],
                   cwd=repo, check=True)
    if tag:
        subprocess.run(["git", "tag", tag], cwd=repo, check=True)


def _clone(root: Path, path: str) -> Path:
    return root / path / f"{path.rsplit('/', 1)[-1]}-main"


TEST_TPL = "unit:\n  script:\n    - pytest -q\n"
DOCS_TPL = "docs:\n  script:\n    - make docs\n"
NESTED = "include:\n  - component: gl.test/grp/comps/unit@$[[ inputs.version ]]\n"


def _outer(default: str | None) -> str:
    spec = "spec:\n  inputs:\n    version:\n"
    spec += f"      default: '{default}'\n" if default is not None else "      description: the pin\n"
    return spec + "---\n" + NESTED


def _fleet(tmp_path: Path, outer: str, ci: str) -> tuple[Path, Path]:
    """grp/comps holds unit at 1.0 (docs only) and 2.0 (tests); grp/outer includes it by input."""
    root = tmp_path / "root"
    comps = _clone(root, "grp/comps")
    _commit(comps, {"templates/unit.yml": DOCS_TPL}, tag="1.0")
    _commit(comps, {"templates/unit.yml": TEST_TPL}, tag="2.0")
    _commit(_clone(root, "grp/outer"), {"templates/outer.yml": outer}, tag="3.0")
    app = _clone(root, "grp/app")
    _commit(app, {".gitlab-ci.yml": ci, "src/a.py": "x = 1\n"})
    return root, app


def _nested(root: Path, app: Path) -> tuple[str, str | None]:
    record = resolve_includes(app.resolve(), root.resolve()).includes[1]
    return record["status"], record["ref_basis"]


# --- the parser ---------------------------------------------------------------


@pytest.mark.parametrize(("text", "inputs"), [
    ("include:\n  - component: gl.test/g/c@1\n    inputs:\n      version: '2.0'\n      stage: test\n",
     (("version", "'2.0'"), ("stage", "test"))),
    ("include:\n  - component: gl.test/g/c@1\n    inputs:\n      version: 2.0 # pinned\n    rules:\n"
     "      - if: $X\n", (("version", "2.0"),)),
    ("include:\n  - inputs:\n      version: 2.0\n    component: gl.test/g/c@1\n", (("version", "2.0"),)),
    ("include:\n  component: gl.test/g/c@1\n  inputs:\n    version: 2.0\n", (("version", "2.0"),)),
    ("include:\n  - component: gl.test/g/c@1\n    inputs: {version: '2.0', stage: test}\n",
     (("version", "'2.0'"), ("stage", "test"))),
    ("include:\n  - {component: gl.test/g/c@1, inputs: {version: 2.0}}\n", (("version", "2.0"),)),
    ("include:\n  - component: gl.test/g/c@1\n    inputs:\n      jobs:\n        - a\n      version: 3\n",
     (("jobs", ""), ("version", "3"))),
    ("include:\n  - component: gl.test/g/c@1\n", ()),
])
def test_an_include_items_inputs_are_parsed(text: str, inputs: tuple[tuple[str, str], ...]) -> None:
    items, _, dropped = include_items(text)
    assert dropped == 0 and len(items) == 1
    assert items[0].inputs == inputs and items[0].source == "gl.test/g/c@1"


def test_an_inputs_key_named_like_an_include_key_names_no_source() -> None:
    items, _, _ = include_items("include:\n  - component: gl.test/g/c@1\n    inputs:\n      project: x/y\n")
    assert items == [IncludeItem("component", "gl.test/g/c@1", (), None, 2, inputs=(("project", "x/y"),))]


@pytest.mark.parametrize(("text", "defaults"), [
    (_outer("1.0"), {"version": "1.0"}),
    (_outer(None), {}),
    ("---\n" + _outer("1.0"), {"version": "1.0"}),
    ("\n---\n" + _outer("1.0"), {"version": "1.0"}),
    ("spec:\n  inputs:\n    version: {default: 1.5, description: x}\n    stage:\n      default: test\n---\n",
     {"version": "1.5", "stage": "test"}),
    ("spec:\n  inputs:\n    jobs:\n      default:\n        - a\n---\n", {"jobs": None}),
    ("spec:\n  inputs:\n    version:\n      default: 1.0\n", {}),  # no --- : no spec header
    ("unit:\n  script:\n    - make\n", {}),
])
def test_spec_defaults(text: str, defaults: dict[str, str | None]) -> None:
    assert spec_defaults(text) == defaults


@pytest.mark.parametrize(("ref", "values", "out"), [
    ("$[[ inputs.version ]]", {"version": "2.0"}, "2.0"),
    ("v$[[inputs.version]]", {"version": "2"}, "v2"),
    ("1.0", {}, "1.0"),
    ("$[[ inputs.version ]]", {}, None),
    ("$[[ inputs.version ]]", {"version": None}, None),
    ("$[[ inputs.version | expand_vars ]]", {"version": "2.0"}, None),
    ("$[[ inputs.version ]]", {"version": ""}, None),
])
def test_interpolate(ref: str, values: dict[str, str | None], out: str | None) -> None:
    assert interpolate(ref, values) == out


def test_values_travel_down_a_chain() -> None:
    passed = item_inputs((("version", "$[[ inputs.pin ]]"), ("stage", "'test'"), ("jobs", "")),
                         {"pin": "4.0"})
    assert passed == {"version": "4.0", "stage": "test", "jobs": None}
    assert file_inputs(_outer("1.0"), passed)["version"] == "4.0"
    assert file_inputs(_outer("1.0"), {})["version"] == "1.0"


# --- resolution ---------------------------------------------------------------


def test_the_parent_items_inputs_pin_the_nested_ref(tmp_path: Path) -> None:
    ci = "include:\n  - component: gl.test/grp/outer/outer@3.0\n    inputs:\n      version: '2.0'\n"
    root, app = _fleet(tmp_path, _outer("1.0"), ci)
    assert _nested(root, app) == ("resolved", "ref")
    assert scan_ci_checks(app, root)["test_or_lint_configured"] is True  # read at 2.0, not 1.0


def test_the_spec_default_pins_the_nested_ref(tmp_path: Path) -> None:
    root, app = _fleet(tmp_path, _outer("1.0"), "include:\n  - component: gl.test/grp/outer/outer@3.0\n")
    assert _nested(root, app) == ("resolved", "ref")
    block = scan_ci_checks(app, root)
    assert (block["test_or_lint_configured"], block["basis"]) == (False, "no_check_command")


def test_an_input_with_no_value_reads_input_ref_never_ref_missing(tmp_path: Path) -> None:
    root, app = _fleet(tmp_path, _outer(None), "include:\n  - component: gl.test/grp/outer/outer@3.0\n")
    assert _nested(root, app) == ("input_ref", None)
    block = scan_ci_checks(app, root)
    assert block["test_or_lint_configured"] is None and block["unresolved_includes"] == 1


def test_a_blank_input_value_reads_input_ref_never_head(tmp_path: Path) -> None:
    root, app = _fleet(tmp_path, _outer(""), "include:\n  - component: gl.test/grp/outer/outer@3.0\n")
    assert _nested(root, app) == ("input_ref", None)


def test_a_value_passed_from_the_parents_own_input_travels_down(tmp_path: Path) -> None:
    middle = ("spec:\n  inputs:\n    pin:\n      default: '2.0'\n---\ninclude:\n"
              "  - component: gl.test/grp/outer/outer@3.0\n    inputs:\n      version: $[[ inputs.pin ]]\n")
    root, app = _fleet(tmp_path, _outer(None), "include:\n  - component: gl.test/grp/mid/mid@1.0\n")
    _commit(_clone(root, "grp/mid"), {"templates/mid.yml": middle}, tag="1.0")
    statuses = [r["status"] for r in resolve_includes(app.resolve(), root.resolve()).includes]
    assert statuses == ["resolved", "resolved", "resolved"]
    assert scan_ci_checks(app, root)["test_or_lint_configured"] is True


def test_a_project_ref_by_input_resolves(tmp_path: Path) -> None:
    outer = ("spec:\n  inputs:\n    ref:\n      default: '2.0'\n---\ninclude:\n"
             "  - project: grp/comps\n    file: /templates/unit.yml\n    ref: $[[ inputs.ref ]]\n")
    root, app = _fleet(tmp_path, outer, "include:\n  - component: gl.test/grp/outer/outer@3.0\n")
    assert _nested(root, app) == ("resolved", "ref")


def test_an_input_ref_in_the_repositorys_own_file_reads_input_ref(tmp_path: Path) -> None:
    root, app = _fleet(tmp_path, _outer("1.0"), NESTED)
    record = resolve_includes(app.resolve(), root.resolve()).includes[0]
    assert record["status"] == "input_ref" and "input_ref" in STATUSES
