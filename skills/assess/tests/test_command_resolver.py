"""Tests for lib/command_resolver.py: command extraction and per-runner resolution.

Each runner is checked in three repo states: the target exists (resolved), the
config exists without the target (missing), and the config is absent
(missing, or unknown for a command that installs or runs an outside tool).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.command_resolver import Command, build_index, extract_commands, resolve

FULL_REPO: dict[str, str] = {
    "package.json": json.dumps({
        "scripts": {"test": "jest", "lint": "eslint .", "build": "tsc", "start": "node x"},
        "devDependencies": {"jest": "29", "@scope/tool": "1"},
    }),
    "web/package.json": json.dumps({"scripts": {"dev": "vite"}}),
    "Makefile": "check: lint test\n\t@echo ok\nlint:\n\t ruff .\n.PHONY: check lint\nVAR := 1\n",
    "justfile": "set shell := [\"bash\"]\nalias t := test\ntest *args:\n  pytest\n@fmt:\n  ruff format\n",
    "pyproject.toml": (
        "[project]\nname='x'\ndependencies=['httpx>=0.27']\n"
        "[project.scripts]\nmycli='x:main'\n"
        "[dependency-groups]\ndev=['pytest>=8', 'mypy']\n"
        "[tool.ruff]\nline-length=100\n"
    ),
    "tox.ini": "[tox]\nenvlist = py311, lint\n[testenv:docs]\ncommands = sphinx\n",
    "noxfile.py": "import nox\n\n@nox.session\ndef tests(session):\n    pass\n\n"
                  "@nox.session(name='type-check')\ndef typecheck(session):\n    pass\n",
    "go.mod": "module x\n",
    "Cargo.toml": "[package]\nname='x'\n",
    "pom.xml": "<project/>",
    "build.gradle": "",
    "gradlew": "#!/bin/sh\n",
    "mvnw": "#!/bin/sh\n",
    "scripts/check.sh": "#!/bin/sh\n",
    "scripts/tool.py": "print(1)\n",
    "tests/test_x.py": "def test_x(): pass\n",
    "server.js": "",
    ".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - run: shellcheck scripts/*.sh\n      - run: |\n          deno task verify\n",
}


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


@pytest.fixture
def full(tmp_path: Path) -> Path:
    return _repo(tmp_path / "full", FULL_REPO)


@pytest.fixture
def empty(tmp_path: Path) -> Path:
    return _repo(tmp_path / "empty", {"README.md": "# x\n"})


RESOLVED_IN_FULL = [
    "npm test", "npm t", "npm run lint", "npm run-script build", "npm start", "npm install",
    "npm run dev", "npm --prefix web run dev", "npm run",
    "pnpm lint", "pnpm run build", "pnpm install", "pnpm --filter web dev", "yarn test",
    "yarn add left-pad", "bun test", "bun build ./src/index.ts", "pnpm build", "yarn build", "bun run lint", "bun scripts/tool.py",
    "npx jest", "npx @scope/tool@1 --fix", "bunx jest",
    "make", "make check", "make lint check", "make -C . check", "make check VERBOSE=1",
    "make -j 4 check", "gmake check",
    "just", "just test", "just t", "just fmt", "just --list",
    "uv run pytest", "uv run pytest tests/test_x.py::test_x", "uv run mypy", "uv run ruff check .",
    "uv run mycli", "uv run httpx", "uv run --with black black .", "uv run --with=black black",
    "uv run scripts/tool.py", "uv run python scripts/tool.py", "uv run -m pytest",
    "uv run python -m pytest -q", "uv sync", "uv lock", "uvx ruff", "uv tool run ruff",
    "poetry run pytest", "poetry install",
    "tox", "tox -e lint", "tox -e py311,docs", "tox -e py312", "tox -e=docs", "tox run -e ALL",
    "nox", "nox -s tests", "nox -s type-check", "nox --session tests-3.12",
    "pytest", "pytest -q tests/", "py.test", "python -m pytest", "python3 -m pytest -k x",
    "python scripts/tool.py", "python -m scripts.tool",
    "go test ./...", "go vet ./...", "go build", "go mod tidy",
    "cargo test", "cargo clippy -p x", "cargo fmt",
    "mvn verify", "./mvnw test", "gradle build", "./gradlew test",
    "bash scripts/check.sh", "sh scripts/check.sh", "./scripts/check.sh", "scripts/check.sh",
    "FOO=1 npm test", "sudo make check", "time npm test", "npm test 2>&1",
    "shellcheck scripts/*.sh", "deno task verify",
]


@pytest.mark.parametrize("command", RESOLVED_IN_FULL)
def test_resolves_when_target_exists(full: Path, command: str) -> None:
    result = resolve(command, full)
    assert result.verdict == "resolved", result.reason
    assert result.resolved


MISSING_IN_FULL = [
    ("npm run deploy", "script `deploy`"),
    ("npm stop", "script `stop`"),
    ("pnpm deploy", "script `deploy`"),
    ("pnpm typecheck", "script `typecheck`"),
    ("yarn run e2e", "script `e2e`"),
    ("make release", "target `release`"),
    ("make check release", "target `release`"),
    ("just deploy", "recipe `deploy`"),
    ("uv run scripts/missing.py", "no file"),
    ("uv run python scripts/gone.py", "no file"),
    ("uv run pytest tests/test_missing.py", "no file"),
    ("tox -e integration", "tox environment `integration`"),
    ("nox -s lint", "nox session `lint`"),
    ("python scripts/gone.py", "no file"),
    ("bash scripts/gone.sh", "no file"),
    ("./scripts/gone.sh", "no file"),
]


@pytest.mark.parametrize(("command", "reason"), MISSING_IN_FULL)
def test_missing_target_in_existing_config(full: Path, command: str, reason: str) -> None:
    result = resolve(command, full)
    assert result.verdict == "missing"
    assert reason in result.reason


MISSING_IN_EMPTY = [
    ("npm test", "no package.json"),
    ("npm run lint", "no package.json"),
    ("pnpm lint", "no package.json"),
    ("make check", "no Makefile"),
    ("make", "no Makefile"),
    ("just test", "no justfile"),
    ("just --list", "no justfile"),
    ("tox", "no tox configuration"),
    ("nox -s tests", "no noxfile.py"),
    ("pytest", "no pytest configuration"),
    ("uv run pytest", "no pytest configuration"),
    ("go test ./...", "no go.mod"),
    ("cargo test", "no Cargo.toml"),
    ("mvn verify", "no pom.xml"),
    ("./mvnw verify", "no file"),
    ("gradle build", "no Gradle build file"),
    ("./gradlew test", "no file"),
    ("poetry install", "no pyproject.toml"),
]


@pytest.mark.parametrize(("command", "reason"), MISSING_IN_EMPTY)
def test_missing_config(empty: Path, command: str, reason: str) -> None:
    result = resolve(command, empty)
    assert result.verdict == "missing"
    assert reason in result.reason


UNKNOWN_ANYWHERE = [
    "rg -n TODO src",            # runner not modelled
    "source .venv/bin/activate",  # excluded tree, never tracked
    "./node_modules/.bin/eslint .",
    "./target/release/app",
    "bash /tmp/x.sh",            # absolute
    "source ~/.bashrc",          # home
    "bash $HOME/x.sh",           # variable
    "./dist/cli.js",             # gitignored in the git test below; excluded tree here
    "git status",
    "make <target>",             # placeholder
    "npm run ${SCRIPT}",
    "gh pr view {PR_NUMBER}",
    "npx some-random-tool",      # undeclared package
    "uv run undeclared-tool",
    "uvx black",
    "go install golang.org/x/tools/cmd/goimports@latest",
    "cargo install ripgrep",
    "cargo nextest run",
    "python -c 'print(1)'",
    "python3 -c 'print(open(\"docs/x.md\").read())'",  # a / in the program is not a path
    "python -m http.server",
    "bash -c 'echo hi'",
    "python",
    "uv",
    "uv frobnicate",
    "uv run",
    "npx",
    "python -m",
    "",
]


@pytest.mark.parametrize("command", UNKNOWN_ANYWHERE)
def test_unknown_is_never_a_failure(full: Path, command: str) -> None:
    result = resolve(command, full)
    assert result.verdict == "unknown"
    assert result.reason.startswith("unresolved") or "not" in result.reason or "tool" in result.reason


@pytest.mark.parametrize("command", ["npm install", "pnpm add x", "uv sync", "go mod tidy"])
def test_builtins_outside_a_project_are_unknown(empty: Path, command: str) -> None:
    assert resolve(command, empty).verdict == "unknown"


def test_lenient_makefile_and_tox(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "Makefile": "$(BIN)/%: src\n\tcc\n",
        "tox.ini": "[tox]\nenvlist = py{311,312}-{unit,int}\n",
        "justfile": "mod deploy\n",
    })
    index = build_index(repo)
    assert resolve("make anything", index=index).resolved
    assert resolve("tox -e py311-unit", index=index).resolved
    assert resolve("just deploy::prod", index=index).resolved


def test_empty_makefile_has_no_default_target(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"Makefile": "# nothing\n"})
    result = resolve("make", repo)
    assert result.verdict == "missing"
    assert "no target" in result.reason


def test_cwd_from_cd_resolves_relative_paths(full: Path) -> None:
    (full / "web" / "run.sh").write_text("", encoding="utf-8")
    index = build_index(full)
    assert resolve(Command("bash run.sh", 1, cwd="web"), index=index).resolved
    assert resolve(Command("bash run.sh", 1, cwd=""), index=index).verdict == "missing"


def test_resolve_needs_a_repo() -> None:
    with pytest.raises(ValueError):
        resolve("npm test")


def test_malformed_configs_do_not_raise(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "package.json": "{not json",
        "sub/package.json": "[1, 2]",
        "pyproject.toml": "[[[broken",
        "requirements.txt": "pytest==8\n# comment\n",
    })
    index = build_index(repo)
    assert resolve("npm run x", index=index).verdict == "missing"
    assert resolve("pytest", index=index).resolved  # requirements.txt names pytest


def test_poetry_pyproject_and_setup_cfg_pytest(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "pyproject.toml": "[tool.poetry]\nname='x'\n[tool.poetry.dependencies]\nrequests='*'\n"
                          "[tool.poetry.group.dev.dependencies]\nblack='*'\n"
                          "[project.optional-dependencies]\nlint=['flake8']\n",
        "setup.cfg": "[tool:pytest]\naddopts = -q\n",
    })
    index = build_index(repo)
    assert index.has_poetry
    for cmd in ("poetry run black .", "poetry run requests", "uv run flake8", "pytest"):
        assert resolve(cmd, index=index).resolved, cmd


def test_tracked_files_only_in_a_git_repo(git_repo) -> None:
    repo, commit = git_repo
    (repo / "package.json").write_text(json.dumps({"scripts": {"test": "x"}}), encoding="utf-8")
    commit("init")
    (repo / "Makefile").write_text("check:\n\ttrue\n", encoding="utf-8")  # untracked
    (repo / "link.sh").symlink_to("package.json")
    index = build_index(repo)
    assert resolve("npm test", index=index).resolved
    assert resolve("make check", index=index).verdict == "missing"
    assert "link.sh" not in index.files  # untracked symlink is not at HEAD


def test_build_is_a_script_except_for_bun(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"package.json": json.dumps({"scripts": {"test": "x"}})})
    index = build_index(repo)
    for cmd in ("pnpm build", "yarn build", "npm run build"):
        result = resolve(cmd, index=index)
        assert result.verdict == "missing" and "script `build`" in result.reason, cmd
    assert resolve("bun build ./x.ts", index=index).resolved


def test_gitignored_script_path_is_unknown(git_repo) -> None:
    repo, commit = git_repo
    _repo(repo, {".gitignore": "out/\n", "README.md": ""})
    commit("init")
    index = build_index(repo)
    assert resolve("./out/run.sh", index=index).verdict == "unknown"
    assert resolve("bash scripts/missing.sh", index=index).verdict == "missing"


def test_ci_line_resolves_only_unknown_runners(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "package.json": json.dumps({"scripts": {}}),
        ".gitlab-ci.yml": "test:\n  script:\n    - npm run lint\n    - shellcheck x\n",
    })
    index = build_index(repo)
    # A CI step running a missing script does not make the script exist.
    assert resolve("npm run lint", index=index).verdict == "missing"
    assert resolve("shellcheck x", index=index).resolved
    assert resolve("ls", index=index).verdict == "unknown"  # too short to match a CI line


# --- Extraction ---------------------------------------------------------------

def test_extract_fenced_shell_and_inline() -> None:
    text = (
        "Run `npm test` before pushing; `git status` is not a runner.\n"
        "```bash\n"
        "# comment\n"
        "$ make check   # trailing comment\n"
        "cd web && npm run dev | tee log\n"
        "uv run pytest \\\n"
        "  -q\n"
        "```\n"
        "```python\n"
        "make = 1\n"
        "```\n"
        "```\n"
        "├── src/\n"
        "npm run build\n"
        "```\n"
    )
    cmds = extract_commands(text)
    found = [(c.text, c.line, c.cwd, c.source) for c in cmds]
    assert ("npm test", 1, "", "inline") in found
    assert ("make check", 4, "", "fenced") in found
    assert ("npm run dev", 5, "web", "fenced") in found
    assert ("tee log", 5, "web", "fenced") in found
    assert ("uv run pytest  -q", 6, "web", "fenced") in found
    assert ("npm run build", 14, "", "fenced") in found
    assert not any(c.text.startswith(("git", "make =", "├")) for c in cmds)


def test_inline_bare_tool_names_are_prose() -> None:
    text = "Use `pnpm`, not `npm` or `yarn`; we do not use `tox`. Run `./deploy` or `make check`.\n"
    assert [c.text for c in extract_commands(text)] == ["./deploy", "make check"]
    # A ./ path alone is a command only when it looks executable.
    docs = "See `./docs/guide.md`, `./config.json` and `./run.sh`; run `./bin/tool`.\n"
    assert [c.text for c in extract_commands(docs)] == ["./run.sh", "./bin/tool"]
    # In a fence, a bare runner line is still a command.
    assert [c.text for c in extract_commands("```bash\nmake\n```\n")] == ["make"]


def test_extract_console_only_reads_prompt_lines() -> None:
    text = "```console\n$ npm test\nPASS  all tests\n> npm run lint\n```\n"
    assert [c.text for c in extract_commands(text)] == ["npm test", "npm run lint"]


def test_extract_joins_multiline_quotes_and_subshells() -> None:
    text = (
        "```sh\n"
        "gh api graphql -f query='\n"
        "  query { x }\n"
        "'\n"
        "(cd scripts && pytest -q)\n"
        "echo a 2>&1 && make check\n"
        "(cd docs && make html) && make lint\n"
        "```\n"
    )
    cmds = extract_commands(text)
    assert cmds[0].text.startswith("gh api graphql") and cmds[0].line == 2
    assert [(c.text, c.cwd) for c in cmds[1:]] == [
        ("pytest -q", "scripts"), ("echo a 2>&1", ""), ("make check", ""),
        ("make html", "docs"), ("make lint", ""),
    ]


def test_cd_out_of_repo_resets_cwd() -> None:
    text = "```bash\ncd /tmp && make check\ncd ../.. && make lint\ncd && make x\n```\n"
    assert [c.cwd for c in extract_commands(text)] == ["", "", ""]


def test_unterminated_fence_and_unbalanced_quotes() -> None:
    cmds = extract_commands("```bash\nnpm test 'oops\n")
    assert cmds == []  # quote never closes: nothing complete to extract
    assert [c.text for c in extract_commands("```bash\nnpm test\n")] == ["npm test"]
