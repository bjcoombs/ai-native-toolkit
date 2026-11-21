"""Windows correctness on any host (issues #712, #752): explicit UTF-8, POSIX paths, read-only rmtree.

The static guards scan every runtime Python tree in the repository (``skills/*/scripts``
and ``scripts/``), leaving out tests, fixtures and virtualenvs. Run with
``cd skills/assess && uv run --locked pytest tests/test_windows_portability.py``.

Windows behaviour is emulated: an ASCII locale with UTF-8 mode off stands in for a
non-UTF-8 code page, and an ``os.unlink`` shim that refuses read-only files stands
in for Windows deletion semantics.
"""
from __future__ import annotations

import ast
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from lib import fleet, fleet_cleanup
from lib.fleet_cleanup import delete_clone
from lib.run_scope import resolve_scope
from test_fleet import make_checkout
from test_fleet_run_stamp import _pushed_repo, _row, _target

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
REPO = SCRIPTS.parents[2]
# Runtime Python trees: every skill's scripts/ and the repo-level scripts/.
RUNTIME_TREES = [*sorted(REPO.glob("skills/*/scripts")), REPO / "scripts"]
# Directory names that hold tests, fixtures or tooling rather than runtime code.
SKIPPED_DIRS = {"tests", "test", "fixtures", "__pycache__", "node_modules"}
EXEMPT: set[str] = set()


def _runtime_files() -> list[Path]:
    out = []
    for tree in RUNTIME_TREES:
        for path in sorted(tree.rglob("*.py")):
            parts = path.relative_to(tree).parts[:-1]
            if any(p in SKIPPED_DIRS or p.startswith(".") for p in parts):
                continue
            out.append(path)
    return out


def _sources() -> list[tuple[str, ast.Module]]:
    out = []
    for path in _runtime_files():
        rel = path.relative_to(REPO).as_posix()
        if rel not in EXEMPT:
            out.append((rel, ast.parse(path.read_text(encoding="utf-8"))))
    return out


def test_the_scan_covers_every_runtime_python_tree():
    scanned = {rel for rel, _ in _sources()}
    for expected in ("skills/assess/scripts/assess_core.py", "skills/fleet/scripts/fleet.py",
                     "scripts/floor_check.py", "scripts/contract/freeze.py"):
        assert expected in scanned
    assert not any("/tests/" in rel or "/fixtures/" in rel for rel in scanned)


def _is_relative_to_call(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "relative_to")


# --- static guards ----------------------------------------------------------


def test_every_text_mode_subprocess_call_names_its_encoding():
    missing = []
    for rel, tree in _sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kwargs = {k.arg: k.value for k in node.keywords}
            text = kwargs.get("text")
            if isinstance(text, ast.Constant) and text.value is True and "encoding" not in kwargs:
                missing.append(f"{rel}:{node.lineno}")
    assert missing == []


def _is_text_write(call: ast.Call) -> bool:
    """``x.write_text(...)``, or ``open``/``os.fdopen`` with a constant ``"w"``/``"a"`` mode."""
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
    if name == "write_text":
        return True
    if name not in ("open", "fdopen"):
        return False
    mode = call.args[1] if len(call.args) > 1 else next(
        (k.value for k in call.keywords if k.arg == "mode"), None)
    return (isinstance(mode, ast.Constant) and isinstance(mode.value, str)
            and mode.value[:1] in "wa" and "b" not in mode.value)


def test_every_fleet_writer_pins_lf_line_endings():
    """Fleet run files are compared byte for byte across hosts (#713, #752)."""
    fleet_files = {rel for rel, _ in _sources()
                   if rel.startswith(("skills/assess/scripts/lib/fleet", "skills/fleet/scripts/"))}
    assert "skills/assess/scripts/lib/fleet_achievements.py" in fleet_files
    missing = []
    for rel, tree in _sources():
        if rel not in fleet_files:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and _is_text_write(node)
                    and not any(k.arg == "newline" for k in node.keywords)):
                missing.append(f"{rel}:{node.lineno}")
    assert missing == []


def _string_args(call: ast.Call) -> list[str]:
    """String constants in the call's first positional argument (a literal argv list)."""
    if not call.args or not isinstance(call.args[0], (ast.List, ast.Tuple)):
        return []
    return [e.value for e in call.args[0].elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]


def test_every_nul_separated_ls_files_call_decodes_with_surrogateescape():
    """``git ls-files -z`` names are bytes; a non-UTF-8 name must not abort the decode."""
    missing = []
    for rel, tree in _sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            argv = _string_args(node)
            if "ls-files" not in argv or "-z" not in argv:
                continue
            kwargs = {k.arg: k.value for k in node.keywords}
            text_mode = any(k in kwargs for k in ("text", "encoding", "universal_newlines"))
            errors = kwargs.get("errors")
            if text_mode and not (isinstance(errors, ast.Constant) and errors.value == "surrogateescape"):
                missing.append(f"{rel}:{node.lineno}")
    assert missing == []


def _first_call(source: str) -> ast.Call:
    return next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call))


def test_the_ls_files_guard_reads_a_literal_argv():
    call = _first_call("subprocess.run(['git', 'ls-files', '-z'], encoding='utf-8')")
    assert "ls-files" in _string_args(call) and "-z" in _string_args(call)


def test_the_lf_guard_sees_text_writes_and_skips_binary_and_reads():
    assert _is_text_write(_first_call("p.write_text(s, encoding='utf-8')"))
    assert _is_text_write(_first_call("os.fdopen(fd, 'w', encoding='utf-8')"))
    assert not _is_text_write(_first_call("os.fdopen(fd, 'wb')"))
    assert not _is_text_write(_first_call("open(p, encoding='utf-8')"))


def _relative_path_names(scope: ast.AST) -> set[str]:
    """Names bound directly to a ``relative_to(...)`` Path within one function."""
    return {t.id for node in ast.walk(scope) if isinstance(node, ast.Assign)
            and _is_relative_to_call(node.value) for t in node.targets if isinstance(t, ast.Name)}


def _native(node: ast.AST, names: set[str]) -> bool:
    return _is_relative_to_call(node) or (isinstance(node, ast.Name) and node.id in names)


def test_no_relative_path_is_stringified_with_native_separators():
    native = []
    for rel, tree in _sources():
        scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for scope in [tree, *scopes]:
            names = _relative_path_names(scope) if scope is not tree else set()
            for node in ast.walk(scope):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "str" and node.args and _native(node.args[0], names)):
                    native.append(f"{rel}:{node.lineno}")
                if isinstance(node, ast.FormattedValue) and _native(node.value, names):
                    native.append(f"{rel}:{node.lineno}")
    assert sorted(set(native)) == []


def test_the_separator_guard_sees_a_name_bound_from_relative_to():
    source = "def f(p, root):\n    rel = p.relative_to(root)\n    return f'doc: {rel}'\n"
    scope = ast.parse(source).body[0]
    fstring = next(n for n in ast.walk(scope) if isinstance(n, ast.FormattedValue))
    assert _native(fstring.value, _relative_path_names(scope))


# --- UTF-8 under a non-UTF-8 locale ------------------------------------------


def _ascii_locale_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("LC_", "LANG", "PYTHONIOENCODING"))}
    return {**env, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}


CHILD = """
import subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lib import fleet
repo = sys.argv[2]
try:
    subprocess.run(["git", "-C", repo, "log", "-1", "--format=%s"],
                   capture_output=True, text=True, check=True)
    print("default:decoded")
except UnicodeDecodeError:
    print("default:UnicodeDecodeError")
print("fleet:" + ascii(fleet._git(Path(repo), "log", "-1", "--format=%s")))
"""


@pytest.mark.windows_followup
def test_git_output_decodes_as_utf8_under_an_ascii_locale(tmp_path):
    repo = make_checkout(tmp_path / "repo")
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "café ✓"],
                   check=True)
    done = subprocess.run(
        [sys.executable, "-c", CHILD, str(SCRIPTS), str(repo)],
        capture_output=True, check=True, env=_ascii_locale_env(),
    )
    lines = done.stdout.decode("utf-8", errors="replace").splitlines()
    # The emulation is real: a bare text-mode call fails in this locale.
    assert lines[0] == "default:UnicodeDecodeError"
    assert lines[1] == "fleet:" + ascii("café ✓")


def test_the_scan_child_runs_in_python_utf8_mode():
    assert fleet._read_only_env()["PYTHONUTF8"] == "1"


# --- POSIX separators -----------------------------------------------------------


def test_scope_label_and_slug_use_forward_slashes(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    scope_abs, label, slug = resolve_scope(tmp_path, Path("a") / "b")
    assert (label, slug) == ("a/b", "a-b")
    assert scope_abs == (tmp_path / "a" / "b").resolve()


# --- read-only files under Windows deletion semantics ---------------------------


@pytest.fixture
def windows_unlink(monkeypatch):
    """``os.unlink`` that refuses a read-only file, as Windows does."""
    real = os.unlink

    def unlink(path, *, dir_fd=None):
        mode = os.stat(path, dir_fd=dir_fd, follow_symlinks=False).st_mode
        if not mode & stat.S_IWRITE:
            raise PermissionError(13, "Access is denied", path)
        real(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", unlink)
    monkeypatch.setattr(os, "remove", unlink)


def _read_only_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and not p.stat().st_mode & stat.S_IWRITE]


def test_a_clone_with_read_only_git_objects_is_deleted(tmp_path, windows_unlink):
    checkout = _pushed_repo(tmp_path / "root", "grp/svc")
    (checkout / "README.md").chmod(0o444)  # a tracked read-only file, too
    assert _read_only_files(checkout)  # git's own objects are read-only
    assert delete_clone(_target(checkout), _row("grp/svc"), []) == fleet_cleanup.DELETED
    assert not checkout.exists()


def test_a_file_that_stays_undeletable_keeps_the_clone(tmp_path, monkeypatch):
    checkout = _pushed_repo(tmp_path / "root", "grp/svc")
    real = os.unlink

    def unlink(path, *, dir_fd=None):
        if os.fspath(path).endswith("README.md"):
            raise PermissionError(13, "in use", path)
        real(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", unlink)
    assert delete_clone(_target(checkout), _row("grp/svc"), []) == fleet_cleanup.KEEP_DELETE_ERROR


def test_a_failure_other_than_deletion_is_not_retried(tmp_path):
    def scandir(path):
        raise PermissionError(13, "denied", path)

    with pytest.raises(PermissionError):
        fleet_cleanup._clear_read_only(scandir, str(tmp_path), PermissionError(13, "denied"))
