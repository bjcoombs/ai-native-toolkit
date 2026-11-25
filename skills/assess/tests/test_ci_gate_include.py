"""The toolkit's GitLab gate include is recognised by a known source only (#1179, lib/ci/ci_includes.py).

A component named ``ai-native-toolkit/assess-gate`` on any host or group is not the gate by its
name. It settles only when the file read from its clone runs what ``templates/assess-gate.yml``
runs; anything else is read like any component, and an include left unread keeps the test
signal null (not measured), never a false 0.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from lib.ci import ci_includes
from lib.ci.ci_checks import scan_ci_checks
from lib.ci.ci_includes import GATE_TEMPLATE, is_toolkit_gate, is_toolkit_gate_text, merged_config, resolve_includes

SHIPPED = GATE_TEMPLATE.read_text(encoding="utf-8")
LOOKALIKE = "gl.test/other/ai-native-toolkit/assess-gate@3.0.0"
BUILD_ONLY = "build:\n  script:\n    - make build\n"


def _commit(repo: Path, files: dict[str, str], *, tag: str | None = None) -> None:
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


def _app(tmp_path: Path, component: str = LOOKALIKE, jobs: str = BUILD_ONLY) -> Path:
    app = tmp_path / "app"
    _commit(app, {".gitlab-ci.yml": f"include:\n  - component: {component}\n{jobs}"})
    return app


def _mirror(tmp_path: Path, template: str, *, project: str = "other/ai-native-toolkit",
            tag: str = "3.0.0") -> Path:
    root = tmp_path / "root"
    clone = root / project / f"{project.rsplit('/', 1)[-1]}-main"
    _commit(clone, {"templates/assess-gate.yml": template}, tag=tag)
    return root


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    ci_includes._shipped_gate.cache_clear()
    yield
    ci_includes._shipped_gate.cache_clear()


# --- Success criterion 1: a lookalike reads null, not 0 ------------------------


def test_a_lookalike_component_on_an_unknown_group_reads_null_not_zero(tmp_path: Path) -> None:
    block = scan_ci_checks(_app(tmp_path), None)
    assert block["includes"][0]["status"] == "no_root"
    assert block["unresolved_includes"] == 1
    assert block["test_or_lint_configured"] is None   # was False (a false 0) before #1179


def test_a_lookalike_not_cloned_under_the_root_reads_null(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    block = scan_ci_checks(_app(tmp_path), root)
    assert block["includes"][0]["status"] == "not_cloned"
    assert block["test_or_lint_configured"] is None


def test_a_lookalike_that_runs_tests_is_read_and_its_tests_count(tmp_path: Path) -> None:
    root = _mirror(tmp_path, "assess-gate:\n  script:\n    - pytest -q\n")
    block = scan_ci_checks(_app(tmp_path), root)
    assert block["includes"][0]["status"] == "resolved"
    assert block["test_or_lint_configured"] is True


@pytest.mark.parametrize("change", [
    lambda t: t.replace("  script:\n", "  before_script:\n    - pytest -q\n  script:\n", 1),
    lambda t: t + "unit:\n  script:\n    - pytest -q\n",
    lambda t: t.replace('bash "$ASSESS_TOOLKIT_DIR/skills/assess/scripts/gitlab_gate.sh"', "pytest -q"),
])
def test_a_gate_copy_that_runs_anything_else_is_not_the_gate(tmp_path: Path, change) -> None:
    template = change(SHIPPED)
    assert template != SHIPPED and not is_toolkit_gate_text(template)
    root = _mirror(tmp_path, template)
    block = scan_ci_checks(_app(tmp_path), root)
    assert block["includes"][0]["status"] == "resolved"
    assert block["test_or_lint_configured"] is not False


def test_a_mirror_pinned_at_a_version_its_clone_lacks_reads_null(tmp_path: Path) -> None:
    root = _mirror(tmp_path, SHIPPED)
    block = scan_ci_checks(_app(tmp_path, LOOKALIKE.replace("3.0.0", "9.9.9")), root)
    assert block["includes"][0]["status"] == "ref_missing"
    assert block["test_or_lint_configured"] is None


def test_without_the_shipped_template_no_component_is_the_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ci_includes, "GATE_TEMPLATE", tmp_path / "absent.yml")
    assert not is_toolkit_gate_text(SHIPPED)


@pytest.mark.parametrize("text", [
    "",
    BUILD_ONLY,                                   # no spec: header
    SHIPPED.replace("---\n", "", 1),               # no document separator
    "variables:\n  X: 1\n" + SHIPPED,              # a second top-level key before ---
])
def test_text_without_the_gate_shape_is_not_the_gate(text: str) -> None:
    assert not is_toolkit_gate_text(text)


# --- Success criterion 2: the toolkit's own includes still settle ---------------


def _documented_remote() -> str:
    match = re.search(r"^#\s+- remote: (\S+)$", SHIPPED, re.MULTILINE)
    assert match is not None, "templates/assess-gate.yml documents its include line"
    return match.group(1)


def test_the_documented_remote_include_still_reads_settled(tmp_path: Path) -> None:
    app = tmp_path / "app"
    _commit(app, {".gitlab-ci.yml": f"include:\n  - remote: {_documented_remote()}\n{BUILD_ONLY}"})
    assert is_toolkit_gate("remote", _documented_remote())
    block = scan_ci_checks(app, None)
    assert block["includes"][0]["status"] == "toolkit_gate"
    assert block["unresolved_includes"] == 0
    assert block["test_or_lint_configured"] is False


def test_a_mirror_of_the_gate_with_its_own_input_defaults_reads_settled(tmp_path: Path) -> None:
    """The README's mirror form: same jobs, another version and clone URL in the inputs."""
    template = re.sub(r"default: v\d+\.\d+\.\d+", "default: v3.0.0", SHIPPED)
    template = template.replace("https://github.com/bjcoombs/ai-native-toolkit.git",
                                "https://gl.test/other/ai-native-toolkit.git")
    root = _mirror(tmp_path, template.replace("\n", "\r\n"))
    app = _app(tmp_path, "$CI_SERVER_FQDN/Other/AI-Native-Toolkit/assess-gate@3.0.0")
    block = scan_ci_checks(app, root)
    assert block["includes"] == [{"kind": "component", "id": "gitlab_component:other/ai-native-toolkit/assess-gate",
                                  "status": "toolkit_gate", "ref_basis": None, "depth": 1, "rename": None}]
    assert resolve_includes(app, root).unresolved == 0
    assert block["test_or_lint_configured"] is False
    config = merged_config(app, root)
    assert config is not None and not config.open
    assert set(config.jobs) == {"build"}   # the gate's own text is never merged


def test_a_gate_mirror_under_rules_stays_conditional(tmp_path: Path) -> None:
    root = _mirror(tmp_path, SHIPPED)
    app = tmp_path / "app"
    _commit(app, {".gitlab-ci.yml": f"include:\n  - component: {LOOKALIKE}\n    rules:\n"
                                    "      - if: $X\n" + "assess-gate:\n  script:\n    - make\n"})
    resolution = resolve_includes(app, root)
    assert resolution.includes[0]["status"] == "toolkit_gate"
    assert resolution.slots[0].conditional and resolution.slots[0].jobs == ci_includes.TOOLKIT_GATE_JOBS


# --- the template body is an input to four collectors (lib/README.md) ----------

# The sha256 of the shipped gate's lines below ``---``, recorded at these collector revisions.
GATE_BODY_SHA256 = "b09841c1c95a88582ea8ef36431cb58bbd5d00bfffca2502895fe94b7b92bfc7"
GATE_READERS = ("core.ci_checks", "core.secrets_guard", "core.review_automation", "core.ci_model_jobs")


def test_a_gate_body_edit_is_recorded_with_the_collector_revisions() -> None:
    """A body edit changes what four collectors report for a gate mirror, and the fleet golden
    holds no mirror. Bump each GATE_READERS revision, then record the new digest here."""
    body = ci_includes._shipped_gate()
    assert body is not None
    digest = hashlib.sha256("\n".join(body).encode("utf-8")).hexdigest()
    assert digest == GATE_BODY_SHA256, (
        "templates/assess-gate.yml changed below `---`: bump the `revision` of "
        f"{', '.join(GATE_READERS)} in checks/core/, then set GATE_BODY_SHA256 = {digest!r}")
