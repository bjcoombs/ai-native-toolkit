"""The ghsync and ghreport scripts run under bash 3.2, macOS's /bin/bash (#1472).

macOS ships bash 3.2.57 as /bin/bash, and a Mac user without Homebrew bash
runs the skill scripts with it. Two kinds of failure matter there:

- Builtins and syntax added in bash 4 or later (`mapfile`, `readarray`,
  associative arrays, namerefs, case-changing expansions and the rest of
  ``BASH4_FEATURES``) are a syntax or "command not found" error. The lint below
  reads every script under skills/ghsync/scripts and skills/ghreport/scripts
  and fails on any of them in code (comments are ignored).
- Under `set -u`, bash 3.2 to 4.3 treat "${arr[@]}" on an empty array as an
  unbound variable and abort. No lint can tell which arrays can be empty, so
  the behavioural tests here run ghsync under /bin/bash itself when it is bash
  3.x, and the macOS workflow (.github/workflows/macos-bash32.yml) runs the
  whole ghsync and ghreport suites with /bin/bash first on PATH. Expand an
  array that can be empty as ${arr[@]+"${arr[@]}"}, or guard it with its
  length.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest
from gitbash import IS_WINDOWS, base_env, write_script

REPO = Path(__file__).resolve().parent.parent
SCRIPT_DIRS = (
    REPO / "skills" / "ghsync" / "scripts",
    REPO / "skills" / "ghreport" / "scripts",
)
GHSYNC = REPO / "skills" / "ghsync" / "scripts" / "ghsync.sh"

# Each pattern names a bash 4+ feature bash 3.2 rejects.
BASH4_FEATURES = {
    "mapfile": re.compile(r"\bmapfile\b"),
    "readarray": re.compile(r"\breadarray\b"),
    "associative array": re.compile(r"\b(?:declare|local|typeset)\s+-[a-zA-Z]*A"),
    "nameref": re.compile(r"\b(?:declare|local|typeset)\s+-[a-zA-Z]*n"),
    "case-changing expansion": re.compile(
        r"\$\{[A-Za-z_]\w*(?:\[[^]]*\])?(?:,,?|\^\^?)[^}]*\}"
    ),
    "@ operator expansion": re.compile(r"\$\{[^}]*@[QEPAaKkUuL]\}"),
    "&>> redirection": re.compile(r"&>>"),
    "|& pipe": re.compile(r"\|&"),
    "wait -n": re.compile(r"\bwait\s+-n\b"),
    "[[ -v": re.compile(r"\[\[\s+-v\b"),
    "coproc": re.compile(r"\bcoproc\b"),
    "case fall-through": re.compile(r";;&|(?<!;);&"),
    "declare -g": re.compile(r"\b(?:declare|typeset)\s+-[a-zA-Z]*g"),
    "negative array subscript": re.compile(r"\$\{[A-Za-z_]\w*\[\s*-\s*\d+\s*\]"),
}


def _scripts() -> list[Path]:
    return sorted(p for d in SCRIPT_DIRS for p in d.glob("*.sh"))


def _code(line: str) -> str:
    """The line without a trailing comment: a `#` at the start or after a space."""
    return re.split(r"(?:^|\s)#", line, maxsplit=1)[0]


def bash4_uses(text: str) -> list[tuple[int, str]]:
    """(line number, feature) for each bash 4+ feature in the code of ``text``."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        code = _code(line)
        for feature, pattern in BASH4_FEATURES.items():
            if pattern.search(code):
                found.append((number, feature))
    return found


def test_the_lint_reads_every_script() -> None:
    names = {p.name for p in _scripts()}
    assert {"ghsync.sh", "ghreport.sh", "ghreport_github.sh"} <= names


@pytest.mark.parametrize(
    "snippet,feature",
    [
        ('mapfile -t arr <<< "$x"', "mapfile"),
        ("readarray -t arr < f", "readarray"),
        ("declare -A seen=()", "associative array"),
        ("local -A seen", "associative array"),
        ("local -n ref=$1", "nameref"),
        ('echo "${name,,}"', "case-changing expansion"),
        ('echo "${name^^}"', "case-changing expansion"),
        ('echo "${name^}" "${name,}"', "case-changing expansion"),
        ('echo "${arr[@],,}"', "case-changing expansion"),
        ('echo "${name@Q}"', "@ operator expansion"),
        ("cmd &>> log", "&>> redirection"),
        ("cmd |& tee log", "|& pipe"),
        ("wait -n", "wait -n"),
        ("[[ -v name ]]", "[[ -v"),
        ("coproc cat", "coproc"),
        ("  a) echo a ;;&", "case fall-through"),
        ("  a) echo a ;&", "case fall-through"),
        ("declare -g name=1", "declare -g"),
        ("declare -ga names=()", "declare -g"),
        ('echo "${arr[-1]}"', "negative array subscript"),
    ],
)
def test_the_lint_flags_each_bash4_feature(snippet: str, feature: str) -> None:
    assert bash4_uses(snippet) == [(1, feature)]


@pytest.mark.parametrize(
    "snippet",
    [
        "# a comment about mapfile",
        'echo hi  # not readarray',
        'while IFS= read -r line; do arr+=("$line"); done <<< "$x"',
        'for x in ${arr[@]+"${arr[@]}"}; do :; done',
        'echo "${#arr[@]}" "${name#prefix}" "${name%%/*}" "${1#*=}"',
        "local -a opts=(-c core.longpaths=true)",
        "  a) echo a ;;",
        'echo "${x:-a,}" "${x#^}" "${x/,/ }"',
        "cmd >/dev/null 2>&1 &",
        'echo "${arr[0]}" "${arr[@]:0:$n}" "${#arr[@]}"',
    ],
)
def test_the_lint_passes_bash32_code(snippet: str) -> None:
    assert bash4_uses(snippet) == []


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.name)
def test_script_uses_no_bash4_feature(script: Path) -> None:
    uses = bash4_uses(script.read_text(encoding="utf-8"))
    assert uses == [], f"{script.relative_to(REPO).as_posix()}: bash 4+ features at {uses}"


# ── Behaviour under the real bash 3.2 ────────────────────────────────────────


def _bin_bash_major() -> int | None:
    """The major version of /bin/bash, or None when there is none to run."""
    if IS_WINDOWS or not Path("/bin/bash").is_file():
        return None
    res = subprocess.run(
        ["/bin/bash", "-c", "echo ${BASH_VERSINFO[0]}"],
        capture_output=True, encoding="utf-8", errors="replace", timeout=30,
    )
    return int(res.stdout.strip()) if res.stdout.strip().isdigit() else None


needs_bash3 = pytest.mark.skipif(
    _bin_bash_major() != 3, reason="/bin/bash is not bash 3.x (macOS only)"
)

# The discovery calls --porcelain makes: an Organization with no teams and an
# org repo list that REPOS_JSON fills.
GH_STUB = """\
#!/bin/sh
case "$1" in
  auth) exit 0 ;;
  api)
    case "$2" in
      users/*) echo "Organization" ;;
      *) echo "" ;;
    esac ;;
  repo) cat "$REPOS_JSON" ;;
  *) exit 0 ;;
esac
"""


def _porcelain(tmp_path: Path, repos_json: str) -> subprocess.CompletedProcess[str]:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    write_script(stub_dir / "gh", GH_STUB)
    (tmp_path / "repos.json").write_text(repos_json, encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    env = {
        **base_env(),
        "HOME": str(tmp_path),
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "REPOS_JSON": str(tmp_path / "repos.json"),
    }
    env.pop("GHSYNC_BLACKLIST", None)
    return subprocess.run(
        ["/bin/bash", str(GHSYNC), "--org", "testorg", "--root", str(root), "--porcelain"],
        capture_output=True, encoding="utf-8", errors="replace", env=env, timeout=60,
    )


@needs_bash3
def test_ghsync_lists_repos_under_bash32_with_no_blacklist(tmp_path: Path) -> None:
    # The default path: GHSYNC_BLACKLIST unset leaves REPO_BLACKLIST empty.
    res = _porcelain(
        tmp_path,
        '[{"name":"zebra","isArchived":false,"defaultBranchRef":{"name":"main"}},'
        ' {"name":"alpha","isArchived":false,"defaultBranchRef":{"name":"main"}}]',
    )
    assert res.returncode == 0, res.stderr
    assert "unbound variable" not in res.stderr
    assert res.stdout.splitlines() == ["alpha", "zebra"]


@needs_bash3
def test_ghsync_handles_an_empty_org_under_bash32(tmp_path: Path) -> None:
    res = _porcelain(tmp_path, "[]")
    assert res.returncode == 0, res.stderr
    assert "unbound variable" not in res.stderr
    assert res.stdout == ""
