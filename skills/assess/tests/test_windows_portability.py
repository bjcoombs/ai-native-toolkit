"""Windows correctness on any host (issue #712): explicit UTF-8, POSIX paths, read-only rmtree.

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
# Its separator and subprocess sites are owned by #699.
EXEMPT = {"lib/doc_staleness.py"}


def _sources() -> list[tuple[str, ast.Module]]:
    out = []
    for path in sorted(SCRIPTS.rglob("*.py")):
        rel = path.relative_to(SCRIPTS).as_posix()
        if rel not in EXEMPT:
            out.append((rel, ast.parse(path.read_text(encoding="utf-8"))))
    return out


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
