"""Tests for lib/agent_environment.py: the agent working-environment block (#514).

Fixture repositories are built in tmp_path and committed, because the scan reads
the files at HEAD. GitHub is faked with a `gh` shell script first on PATH (the
pattern in test_gate_cost.py), so the real subprocess path through
lib/gh_cli.py runs.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.agent_environment import (  # noqa: E402
    COPILOT_PATH,
    check_copilot_setup,
    ci_duration,
    is_check_command,
    scan_agent_environment,
)
from lib.scan_registry import SCANS  # noqa: E402

CI = "name: Tests\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: make test\n"
COPILOT_OK = """name: Copilot Setup Steps
on: workflow_dispatch
jobs:
  copilot-setup-steps:  # the job name Copilot looks for
    runs-on: ubuntu-latest
    permissions:
      contents: read
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - run: |
          name: not-a-key
          make setup
"""
MAKEFILE = "test:\n\tpytest\n\nsetup:\n\tpip install -e .\n"


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _repo(tmp_path: Path, files: dict[str, str], *, untracked: dict[str, str] | None = None) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    for rel, body in (untracked or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    return root


def _scan(root: Path, instruction: tuple[str, ...] = (), harness: object = None) -> dict:
    files = {name: {"grade": "B"} for name in instruction}
    return dict(scan_agent_environment(root, files, harness, probe_ci=False))


# --- the three fixtures the issue names ------------------------------------------


def test_ci_only_repo_caps_at_partial_with_both_reasons(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/ci.yml": CI, "package.json": '{"dependencies": {"a": "1"}}'})
    block = _scan(root)
    cap = block["layer5_cap"]
    assert cap == {
        "applies": True, "ceiling": "Partial", "unmet": ["check_named", "setup_or_lockfile"],
        "reason": cap["reason"],
    }
    assert "no instruction file names a check command" in cap["reason"]
    assert "no lockfile for `package.json`" in cap["reason"]
    assert block["check"]["named"] == [] and block["setup"]["present"] is False


def test_ci_plus_copilot_and_named_make_test_is_eligible_for_present(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        ".github/workflows/ci.yml": CI, COPILOT_PATH: COPILOT_OK,
        "Makefile": "test:\n\tpytest\n", "package.json": "{}",
        "CLAUDE.md": "# Repo\n\nRun the checks:\n\n```bash\nmake test\n```\n",
    })
    block = _scan(root, ("CLAUDE.md",))
    assert block["findings"] == []
    assert block["setup"]["sources"] == [
        {"kind": "copilot_setup_steps", "path": COPILOT_PATH, "detail": "`copilot-setup-steps` job"}]
    assert block["check"]["named"][0] == {
        "file": "CLAUDE.md", "line": 6, "command": "make test", "verdict": "resolved",
        "reason": block["check"]["named"][0]["reason"]}
    cap = block["layer5_cap"]
    assert cap["applies"] is False and cap["ceiling"] is None and cap["unmet"] == []
    assert "`make test` named in CLAUDE.md:6" in cap["reason"]
    assert COPILOT_PATH in cap["reason"]


def test_malformed_copilot_setup_raises_findings_and_earns_no_credit(tmp_path: Path) -> None:
    body = """on: workflow_dispatch
jobs:
  copilot-setup-steps:
    runs-on: ubuntu-latest
    env:
      FOO: bar
    timeout-minutes: 90
  lint:
    runs-on: ubuntu-latest
"""
    root = _repo(tmp_path, {COPILOT_PATH: body})
    block = _scan(root)
    kinds = sorted(f["kind"] for f in block["findings"])
    assert kinds == ["copilot_extra_job", "copilot_missing_steps",
                     "copilot_timeout_over_limit", "copilot_unsupported_key"]
    by_kind = {f["kind"]: f for f in block["findings"]}
    assert by_kind["copilot_timeout_over_limit"]["line"] == 7
    assert by_kind["copilot_unsupported_key"]["line"] == 5
    assert by_kind["copilot_extra_job"]["line"] == 8
    assert all(f["file"] == COPILOT_PATH and f["fix"] for f in block["findings"])
    assert block["setup"]["present"] is False


# --- copilot-setup-steps validation ------------------------------------------------


@pytest.mark.parametrize("body, kinds, usable", [
    (COPILOT_OK, [], True),
    ("on: push\n", ["copilot_no_jobs"], False),
    ("jobs: {copilot-setup-steps: {steps: []}}\n", ["copilot_unparsed"], False),
    ("jobs: &anchor\n  copilot-setup-steps:\n    steps: []\n", ["copilot_unparsed"], False),
    ("jobs:\n  setup:\n    steps:\n      - run: x\n", ["copilot_extra_job", "copilot_missing_job"], False),
    ("jobs:\n  copilot-setup-steps:\n    timeout-minutes: ${{ inputs.t }}\n    steps:\n      - run: x\n",
     ["copilot_timeout_not_integer"], False),
    ("jobs:\n  copilot-setup-steps:\n    timeout-minutes: 59\n    steps:\n      - run: x\n", [], True),
    ("jobs:\n  copilot-setup-steps:\n    timeout-minutes: 60\n    steps:\n      - run: x\n",
     ["copilot_timeout_over_limit"], False),
    # An ignored key is a finding, but the job still runs its steps.
    ("jobs:\n  copilot-setup-steps:\n    container: node:20\n    steps:\n      - run: x\n",
     ["copilot_unsupported_key"], True),
    # Quoted keys and trailing comments still parse.
    ("'jobs':\n  \"copilot-setup-steps\":  # c\n    steps:  # c\n      - run: x\n", [], True),
])
def test_check_copilot_setup(body: str, kinds: list[str], usable: bool) -> None:
    ok, findings = check_copilot_setup(body)
    assert ok is usable
    assert sorted(f["kind"] for f in findings) == kinds


def test_copilot_yaml_extension_is_a_finding(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/copilot-setup-steps.yaml": COPILOT_OK})
    block = _scan(root)
    assert [f["kind"] for f in block["findings"]] == ["copilot_wrong_extension"]
    assert block["setup"]["present"] is False


# --- committed setup sources --------------------------------------------------------


def test_setup_sources_each_kind(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        ".devcontainer.json": "{}",
        ".devcontainer/devcontainer.json": "{}",
        ".devcontainer/python/devcontainer.json": "{}",
        ".devcontainer/a/b/devcontainer.json": "{}",  # too deep: not a dev container config
        ".cursor/environment.json": "{}",
        "script/setup": "#!/bin/sh\n", "script/bootstrap": "#!/bin/sh\n",
        "bin/setup": "#!/bin/sh\n", "init.sh": "#!/bin/sh\n",
        "Makefile": MAKEFILE, "justfile": "bootstrap:\n    echo hi\n",
    })
    sources = _scan(root)["setup"]["sources"]
    assert [(s["kind"], s["path"]) for s in sources] == [
        ("devcontainer", ".devcontainer.json"),
        ("devcontainer", ".devcontainer/devcontainer.json"),
        ("devcontainer", ".devcontainer/python/devcontainer.json"),
        ("cursor_environment", ".cursor/environment.json"),
        ("setup_script", "script/setup"), ("setup_script", "script/bootstrap"),
        ("setup_script", "bin/setup"), ("setup_script", "init.sh"),
        ("setup_target", "Makefile"), ("setup_target", "justfile"),
    ]


def test_session_start_hook_from_agent_harness(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".claude/settings.json": "{}"})
    harness = {"executes": [
        {"kind": "settings_hook", "file": ".claude/settings.json", "name": "SessionStart [startup]",
         "detail": "./scripts/setup.sh"},
        {"kind": "settings_hook", "file": ".claude/settings.json", "name": "PreToolUse [Bash]",
         "detail": "./guard.sh"},
        {"kind": "frontmatter_hook", "file": "agents/x.md", "name": "SessionStart", "detail": "y"},
    ]}
    sources = _scan(root, harness=harness)["setup"]["sources"]
    assert sources == [{"kind": "session_start_hook", "path": ".claude/settings.json",
                        "detail": "./scripts/setup.sh"}]


def test_session_start_fallback_when_harness_did_not_run(tmp_path: Path) -> None:
    settings = json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "x"}]}]}})
    root = _repo(tmp_path, {".claude/settings.json": settings})
    degraded = {"available": False, "reason": "agent_harness scan failed: boom"}
    assert [s["kind"] for s in _scan(root, harness=degraded)["setup"]["sources"]] == ["session_start_hook"]
    root2 = _repo(tmp_path / "b", {".claude/settings.json": "not json"})
    assert _scan(root2, harness=None)["setup"]["sources"] == []


def test_untracked_settings_is_not_setup(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"README.md": "x"}, untracked={".claude/settings.json": "{}"})
    harness = {"executes": [{"kind": "settings_hook", "file": ".claude/settings.json",
                             "name": "SessionStart", "detail": "x"}]}
    assert _scan(root, harness=harness)["setup"]["sources"] == []


# --- check commands -------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("make test", True), ("npm run test:unit", True), ("npm test", True), ("pytest -q", True),
    ("uv run pytest", True), ("GIT_CONFIG_GLOBAL=/dev/null uv run pytest", True),
    ("go test ./...", True), ("cargo test", True), ("tox -e py311", True), ("just check", True),
    ("./scripts/ci.sh", True), ("make build", False), ("cd tests", False), ("ls tests", False),
    ("npm install", False), ("pytest", True), ("", False), ("make --check build", False),
    # Install commands are setup, not checks, even with a check word in them.
    ("npm ci", False), ("uv sync --extra test", False), ("poetry install --with test", False),
    ("uv run --with pytest pytest -v", True), ("uv run python -m pytest", True),
    ("uv run --project skills/assess --with pytest-cov pytest skills/assess", True),
    ("uv run ruff check .", True), ("uvx tox", True), ("poetry run pytest", True),
    ("npm run ci", True), ("pnpm test", True), ("yarn run build", False), ("npm run", False),
    ("python -m pytest -q", True), ("python -m pip install x", False),
    ("python scripts/run_tests.py", True), ("python app.py", False),
    ("bash scripts/check.sh", True), ("bash deploy.sh", False), ("./gradlew check", True),
    ("mvn install", False), ("mvn verify", True), ("cargo check", True), ("go build ./...", False),
    ("make -C sub test", True), ("make ci", True), ("python -m", False),
])
def test_is_check_command(text: str, expected: bool) -> None:
    assert is_check_command(text) is expected


def test_unresolved_named_check_keeps_the_cap(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "AGENTS.md": "Run `make test` before pushing. Build with `make build`.\n",
        "script/setup": "#!/bin/sh\n",
    })
    block = _scan(root, ("AGENTS.md",))
    assert block["check"]["named"] == []
    assert [u["command"] for u in block["check"]["unresolved"]] == ["make test"]
    assert block["layer5_cap"]["unmet"] == ["check_named"]
    assert "(1 named, none resolved)" in block["layer5_cap"]["reason"]


def test_entry_points_per_family(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "package.json": json.dumps({"scripts": {"test:unit": "x", "lint": "y", "build": "z"}}),
        "Makefile": "check:\n\tx\nbuild:\n\ty\n",
        "justfile": "verify:\n    x\n",
        "tox.ini": "[tox]\nenvlist = py311\n[testenv:lint]\n",
        "noxfile.py": "import nox\n@nox.session\ndef tests(session):\n    pass\n",
        "pytest.ini": "[pytest]\n",
        "go.mod": "module x\n", "Cargo.toml": "[package]\nname='x'\n", "pom.xml": "<project/>",
        "build.gradle": "", "gradlew": "#!/bin/sh\n",
    })
    commands = [e["command"] for e in _scan(root)["check"]["entry_points"]]
    assert commands == [
        "npm run test:unit", "make check", "just verify", "nox -s tests", "tox", "pytest",
        "go test ./...", "cargo test", "mvn verify", "./gradlew check",
    ]


# --- pinning --------------------------------------------------------------------------


def test_pinning_rows(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "package.json": '{"dependencies": {"react": "18"}}', "pnpm-lock.yaml": "",
        # workspace package: the root lock covers it
        "packages/web/package.json": '{"devDependencies": {"vite": "5"}}',
        "scripts-only/package.json": '{"scripts": {"test": "x"}}',  # nothing to lock
        "bad/package.json": "{",  # does not parse: skipped
        "tools/pyproject.toml": "[tool.ruff]\nline-length = 100\n",  # tool config only
        "svc/pyproject.toml": "[project]\nname='s'\ndependencies=['httpx']\n", "svc/uv.lock": "",
        "poet/pyproject.toml": "[tool.poetry.dependencies]\npython='^3.11'\n",
        "broken/pyproject.toml": "[[[",
        "requirements.txt": "httpx==0.27.0\n# c\n-r base.txt\n",
        "requirements-dev.txt": "pytest>=8\n",
        "go.mod": "module x\nrequire example.com/y v1.0.0\n", "go.sum": "",
        "lib/go.mod": "module lib\n\ngo 1.22\n",  # requires nothing: no go.sum to expect
        "cli/go.mod": "module cli\nrequire (\n\texample.com/z v1\n)\n",
        "app/requirements.txt": "-r requirements/production.txt\n-c constraints.txt\n",  # includes only
        "tests/fixtures/app/package.json": "{}",  # fixture tree: not a real manifest
    })
    rows = {r["manifest"]: (r["locked"], r["lockfile"]) for r in _scan(root)["pinning"]["manifests"]}
    assert rows == {
        "cli/go.mod": (True, "go.sum"),
        "go.mod": (True, "go.sum"),
        "package.json": (True, "pnpm-lock.yaml"),
        "packages/web/package.json": (True, "pnpm-lock.yaml"),
        "poet/pyproject.toml": (False, None),
        "requirements-dev.txt": (False, None),
        "requirements.txt": (True, "requirements.txt"),
        "svc/pyproject.toml": (True, "svc/uv.lock"),
    }
    assert _scan(root)["pinning"]["all_locked"] is False


def test_locked_bootstrap_satisfies_setup(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "package.json": json.dumps({"scripts": {"test": "jest"}, "devDependencies": {"jest": "29"}}),
        "package-lock.json": "{}",
        "CLAUDE.md": "Run `npm test`.\n",
    })
    block = _scan(root, ("CLAUDE.md",))
    assert block["pinning"]["all_locked"] is True
    assert block["layer5_cap"]["applies"] is False
    assert "every manifest locked" in block["layer5_cap"]["reason"]


def test_no_manifest_means_nothing_to_install(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"Makefile": "test:\n\tx\n", "CLAUDE.md": "`make test`\n"})
    block = _scan(root, ("CLAUDE.md",))
    assert block["pinning"] == {"manifests": [], "all_locked": None}
    assert block["layer5_cap"]["applies"] is False
    assert "no dependency manifest to install" in block["layer5_cap"]["reason"]


# --- CI duration (fake gh) --------------------------------------------------------

FAKE_GH = """#!/bin/sh
echo "$*" >> "$FAKE_GH/log"
serve() { [ -f "$FAKE_GH/$1" ] && cat "$FAKE_GH/$1" && exit 0; }
case "$1:$*" in
  auth:*) [ -f "$FAKE_GH/noauth" ] || exit 0 ;;
  repo:*) serve repo.json ;;
  run:*) serve runs.json ;;
esac
cat "$FAKE_GH/fail" >&2
exit 1
"""


@pytest.fixture
def gh_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _repo(tmp_path, {"README.md": "x"})
    _git(root, "remote", "add", "origin", "https://github.com/acme/widget.git")
    bindir, ghdir = tmp_path / "bin", tmp_path / "gh"
    bindir.mkdir()
    ghdir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    (ghdir / "log").write_text("")
    (ghdir / "fail").write_text("gh: Not Found (HTTP 404)\n")
    (ghdir / "repo.json").write_text(json.dumps({"defaultBranchRef": {"name": "main"}}))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH", str(ghdir))
    return root, ghdir


def _run(workflow: str, start: str, end: str) -> dict:
    return {"workflowName": workflow, "startedAt": start, "updatedAt": end}


def test_ci_duration_medians_per_workflow(gh_world) -> None:
    root, ghdir = gh_world
    (ghdir / "runs.json").write_text(json.dumps([
        _run("Tests", "2026-10-01T10:00:00Z", "2026-10-01T10:02:00Z"),
        _run("Tests", "2026-10-02T10:00:00Z", "2026-10-02T10:04:00Z"),
        _run("Tests", "2026-10-03T10:00:00Z", "2026-10-03T10:20:00Z"),
        _run("Lint", "2026-10-01T10:00:00Z", "2026-10-01T10:00:30Z"),
        _run("Lint", "bad", "2026-10-01T10:00:30Z"),
        _run("Lint", "2026-10-01T10:00:30Z", "2026-10-01T10:00:00Z"),  # negative: dropped
        "not a run",
    ]))
    block = ci_duration(root)
    assert block["available"] is True
    assert block["branch"] == "main" and block["runs_sampled"] == 4
    assert block["workflows"] == [
        {"workflow": "Tests", "runs": 3, "median_seconds": 240},
        {"workflow": "Lint", "runs": 1, "median_seconds": 30},
    ]
    assert block["slowest_median_seconds"] == 240 and block["over_target"] is False
    call = next(c for c in (ghdir / "log").read_text().splitlines() if c.startswith("run list"))
    assert "--branch main" in call and "--status success" in call and "--event push" in call
    assert "--jq" not in call


def test_ci_duration_over_target(gh_world) -> None:
    root, ghdir = gh_world
    (ghdir / "runs.json").write_text(json.dumps([
        _run("Tests", "2026-10-01T10:00:00Z", "2026-10-01T10:15:00Z")]))
    assert ci_duration(root)["over_target"] is True


@pytest.mark.parametrize("setup, prefix", [
    ("noauth", "not_authenticated"),
    ("no_runs", "no_runs"),
    ("bad_branch", "gh_bad_json"),
    ("run_fails", "not_found"),
])
def test_ci_duration_degrades(gh_world, setup: str, prefix: str) -> None:
    root, ghdir = gh_world
    if setup == "noauth":
        (ghdir / "noauth").write_text("")
    elif setup == "no_runs":
        (ghdir / "runs.json").write_text("[]")
    elif setup == "bad_branch":
        (ghdir / "repo.json").write_text(json.dumps({"defaultBranchRef": None}))
    block = ci_duration(root)
    assert block["available"] is False
    assert block["reason"].startswith(prefix)


def test_ci_duration_without_gh_or_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path, {"README.md": "x"})
    assert ci_duration(root)["reason"].startswith("no_remote")
    _git(root, "remote", "add", "origin", "git@github.com:acme/widget.git")
    git_dir = tmp_path / "only-git"
    git_dir.mkdir()
    real_git = subprocess.run(["which", "git"], capture_output=True, text=True).stdout.strip()
    (git_dir / "git").symlink_to(real_git)
    monkeypatch.setenv("PATH", str(git_dir))
    assert ci_duration(root)["reason"].startswith("gh_not_installed")


def test_probe_off_is_recorded_as_not_probed(tmp_path: Path) -> None:
    block = _scan(_repo(tmp_path, {"README.md": "x"}))
    assert block["ci_duration"] == {
        "available": False, "reason": "not_probed: the CI-duration probe was turned off"}


def test_scan_probes_ci_by_default(gh_world) -> None:
    root, ghdir = gh_world
    (ghdir / "runs.json").write_text(json.dumps([
        _run("Tests", "2026-10-01T10:00:00Z", "2026-10-01T10:01:00Z")]))
    block = scan_agent_environment(root, {}, None)
    assert block["ci_duration"]["available"] is True


def test_registered_after_agent_harness() -> None:
    keys = [s.key for s in SCANS]
    spec = next(s for s in SCANS if s.key == "agent_environment")
    assert keys.index("agent_environment") > keys.index("agent_harness")
    assert spec.reads == ("repo_root", "instruction_files", "agent_harness", "excludes")


def test_user_excludes_drop_a_manifest(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "package.json": '{"dependencies": {"a": "1"}}', "package-lock.json": "{}",
        "examples/demo/package.json": '{"dependencies": {"b": "1"}}',
        "tools/x/requirements.txt": "httpx\n",
    })
    rows = scan_agent_environment(root, {}, None, ({"examples"}, ["requirements.txt"]),
                                  probe_ci=False)["pinning"]
    assert [r["manifest"] for r in rows["manifests"]] == ["package.json"]
    assert rows["all_locked"] is True
    unexcluded = _scan(root)["pinning"]["manifests"]
    assert [r["manifest"] for r in unexcluded] == [
        "examples/demo/package.json", "package.json", "tools/x/requirements.txt"]


def test_only_agent_loaded_files_name_a_check(tmp_path: Path) -> None:
    files = {
        "Makefile": "test:\n\tx\n",
        ".github/claude-review-instructions.md": "Run `make test`.\n",
        "docs/CLAUDE.md": "Run `make test`.\n",
    }
    root = _repo(tmp_path, files)
    block = _scan(root, (".github/claude-review-instructions.md", "docs/CLAUDE.md"))
    assert block["check"]["named"] == [] and "check_named" in block["layer5_cap"]["unmet"]
    (root / ".github/copilot-instructions.md").write_text("Run `make test`.\n")
    _git(root, "add", "-A")
    block = _scan(root, (".github/copilot-instructions.md", "docs/CLAUDE.md"))
    assert [n["file"] for n in block["check"]["named"]] == [".github/copilot-instructions.md"]
