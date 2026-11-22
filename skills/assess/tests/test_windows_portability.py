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
import codecs
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import lib.fleet.runner as fleet
import lib.fleet.fleet_cleanup as fleet_cleanup
from lib.fleet.fleet_cleanup import delete_clone
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
    """``x.write_text(...)``, or ``open``/``os.fdopen``/``Path.open`` with a constant ``"w"``/``"a"`` mode.

    ``Path.open`` takes the mode first; ``open``, ``io.open`` and ``os.fdopen`` take it second.
    """
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
    if name == "write_text":
        return True
    if name not in ("open", "fdopen"):
        return False
    module_open = not isinstance(func, ast.Attribute) or name == "fdopen" or (
        isinstance(func.value, ast.Name) and func.value.id in ("io", "codecs", "builtins"))
    index = 1 if module_open else 0
    mode = call.args[index] if len(call.args) > index else next(
        (k.value for k in call.keywords if k.arg == "mode"), None)
    return (isinstance(mode, ast.Constant) and isinstance(mode.value, str)
            and mode.value[:1] in "wa" and "b" not in mode.value)


def test_every_fleet_writer_pins_lf_line_endings():
    """Fleet run files are compared byte for byte across hosts (#713, #752)."""
    fleet_files = {rel for rel, _ in _sources()
                   if rel.startswith(("skills/assess/scripts/lib/fleet", "skills/fleet/scripts/"))}
    assert "skills/assess/scripts/lib/fleet/fleet_achievements.py" in fleet_files
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
    assert _is_text_write(_first_call("p.open('w', encoding='utf-8')"))
    assert _is_text_write(_first_call("io.open(p, 'a', encoding='utf-8')"))
    assert not _is_text_write(_first_call("p.open('rb')"))
    assert not _is_text_write(_first_call("p.open()"))


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
import locale, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import lib.fleet.runner as fleet
repo = sys.argv[2]
print("locale:" + locale.getpreferredencoding(False))
try:
    bare = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%s"],
                          capture_output=True, text=True, check=True).stdout.strip()
    print("default:" + ascii(bare))
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
    locale_encoding = lines[0].removeprefix("locale:")
    if sys.platform == "win32" and codecs.lookup(locale_encoding).name == "utf-8":
        # A Windows host whose ANSI code page is 65001 decodes UTF-8 by default.
        pytest.skip(f"the locale encoding is {locale_encoding}: no non-UTF-8 default to emulate")
    # The emulation is real: a bare text-mode call does not yield the subject in this
    # locale. POSIX raises UnicodeDecodeError; Windows takes its code page from the ANSI
    # setting, not LC_ALL, and decodes the UTF-8 bytes to mojibake without raising.
    if sys.platform == "win32":
        assert lines[1] == "default:" + ascii("café ✓".encode().decode(locale_encoding))
    else:
        assert lines[1] == "default:UnicodeDecodeError"
    assert lines[2] == "fleet:" + ascii("café ✓")


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


# --- the tests themselves read and write UTF-8 (#754) ------------------------


def _bare_text_io(tree: ast.Module) -> list[int]:
    """Lines of ``x.read_text()`` / ``x.write_text(data)`` calls with no encoding."""
    lines = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if any(kw.arg in ("encoding", None) for kw in node.keywords):
            continue
        attr, nargs = node.func.attr, len(node.args)
        if (attr == "read_text" and nargs == 0) or (attr == "write_text" and nargs == 1):
            lines.append(node.lineno)
    return lines


SUBPROCESS_TEXT_CALLS = {"run", "check_output", "Popen", "check_call"}
# Always decode with the locale encoding and take no encoding= argument.
SUBPROCESS_LOCALE_ONLY = {"getoutput", "getstatusoutput"}


def _subprocess_names(tree: ast.Module) -> tuple[set[str], dict[str, str]]:
    """Names bound to the ``subprocess`` module, and local names of functions imported from it."""
    modules: set[str] = set()
    functions: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.asname or a.name for a in node.names if a.name == "subprocess"}
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            functions |= {a.asname or a.name: a.name for a in node.names}
    return modules, functions


def _subprocess_function(call: ast.Call, modules: set[str], functions: dict[str, str]) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in modules:
        return func.attr
    if isinstance(func, ast.Name):
        return functions.get(func.id)
    return None


def _may_be_text_mode(kwargs: dict[str | None, ast.expr]) -> bool:
    """``**kwargs``, or a ``text``/``universal_newlines``/``errors`` that is not a constant
    false: ``errors=`` alone also switches subprocess into text mode."""
    if None in kwargs:
        return True
    return any(k in kwargs and not (isinstance(kwargs[k], ast.Constant) and not kwargs[k].value)
               for k in ("text", "universal_newlines", "errors"))


def _bare_text_subprocess(tree: ast.Module) -> list[int]:
    """Lines of subprocess calls that may decode with the locale encoding (#963).

    A ``run``/``check_output``/``Popen``/``check_call`` call (through any import alias)
    that may be in text mode must name a non-None ``encoding=``; ``getoutput`` and
    ``getstatusoutput`` cannot, so they are never allowed.
    """
    modules, functions = _subprocess_names(tree)
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _subprocess_function(node, modules, functions)
        if name in SUBPROCESS_LOCALE_ONLY:
            lines.append(node.lineno)
        if name not in SUBPROCESS_TEXT_CALLS:
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        encoding = kwargs.get("encoding")
        if encoding is not None and not (isinstance(encoding, ast.Constant) and encoding.value is None):
            continue
        if _may_be_text_mode(kwargs):
            lines.append(node.lineno)
    return lines


def test_the_bare_text_io_guard_sees_a_bare_call():
    assert _bare_text_io(ast.parse("p.read_text()\np.write_text(s)\n")) == [1, 2]
    assert _bare_text_io(ast.parse("p.read_text(encoding='utf-8')\np.write_text(s, 'utf-8')\n")) == []


@pytest.mark.parametrize("source", [
    "import subprocess\nsubprocess.run(a, text=True)",
    "import subprocess\nsubprocess.check_output(a, universal_newlines=True)",
    "import subprocess\nsubprocess.Popen(a, text=True)",
    "import subprocess\nsubprocess.check_call(a, text=True)",
    "from subprocess import run\nrun(a, text=True)",
    "from subprocess import run as r\nr(a, text=True)",
    "import subprocess as sp\nsp.run(a, text=True)",
    "import subprocess\nsubprocess.run(a, text=True, **kw)",
    "import subprocess\nsubprocess.run(a, **{'text': True})",
    "import subprocess\nsubprocess.run(a, text=t)",
    "import subprocess\nsubprocess.run(a, text=True, encoding=None)",
    "import subprocess\nsubprocess.run(a, errors='replace')",
    "import subprocess\nsubprocess.getoutput(cmd)",
    "from subprocess import getstatusoutput\ngetstatusoutput(cmd)",
])
def test_the_subprocess_guard_sees_a_bare_text_mode_call(source):
    assert _bare_text_subprocess(ast.parse(source)) == [2]


def test_the_subprocess_guard_passes_a_named_or_binary_call():
    named = ("import subprocess\n"
             "subprocess.run(a, text=True, encoding='utf-8', errors='replace')\n"
             "subprocess.run(a, encoding='utf-8', **kw)\n"
             "subprocess.run(a, capture_output=True)\nsubprocess.run(a, text=False)\n"
             "subprocess.run(a, encoding=None)\nsubprocess.run(a, errors=None)\n"
             "run(a, text=True)\n")
    assert _bare_text_subprocess(ast.parse(named)) == []


def _assess_test_sources() -> list[tuple[str, ast.Module]]:
    tests = Path(__file__).resolve().parent
    return [(path.relative_to(REPO).as_posix(), ast.parse(path.read_text(encoding="utf-8")))
            for path in sorted(tests.rglob("*.py")) if "fixtures" not in path.parts]


def test_assess_tests_name_utf8_for_text_io():
    """A test that reads a fixture holding "·" through the Windows code page
    fails there only (#754: test_golden_baseline, test_headline)."""
    bare = [f"{rel}:{line}" for rel, tree in _assess_test_sources() for line in _bare_text_io(tree)]
    assert not bare, "read_text()/write_text() without encoding=\"utf-8\": " + ", ".join(bare)


def test_assess_tests_name_utf8_for_text_mode_subprocess():
    """A text-mode subprocess call decodes through the Windows code page unless it
    names its encoding, so a non-ASCII commit subject or fixture fails there only (#963)."""
    bare = [f"{rel}:{line}" for rel, tree in _assess_test_sources()
            for line in _bare_text_subprocess(tree)]
    assert not bare, "text-mode subprocess call without encoding=\"utf-8\": " + ", ".join(bare)


# --- no parked Windows failures (#969) -----------------------------------------

# The retired skip marker for known Windows failures (#762). Every test now passes
# on Windows or carries a POSIX-only marker, so a new failure is fixed, not parked.
RETIRED_MARKER = "windows_followup"
POSIX_ONLY_MARKERS = {"needs_symlink", "needs_posix_perms", "needs_posix_shell"}


def _retired_marker_uses(tree: ast.Module) -> list[int]:
    """Lines of ``pytest.mark.<retired>`` / ``mark.<retired>`` uses, and of the bare name
    as a string (``getattr(pytest.mark, ...)``, a skip-table key, ``get_closest_marker``)."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == RETIRED_MARKER and (
                (isinstance(node.value, ast.Name) and node.value.id == "mark")
                or (isinstance(node.value, ast.Attribute) and node.value.attr == "mark")):
            lines.append(node.lineno)
        if isinstance(node, ast.Constant) and node.value == RETIRED_MARKER:
            lines.append(node.lineno)
    return lines


@pytest.mark.parametrize("source", [
    f"import pytest\n@pytest.mark.{RETIRED_MARKER}\ndef test_x(): pass",
    f"import pytest\npytestmark = [pytest.mark.{RETIRED_MARKER}]",
    f"from pytest import mark\n@mark.{RETIRED_MARKER}(reason='r')\ndef test_x(): pass",
    f"import pytest\nmarks = getattr(pytest.mark, {RETIRED_MARKER!r})",
    f"SKIP = {{}}\nSKIP[{RETIRED_MARKER!r}] = 'parked'",
])
def test_the_retired_marker_guard_sees_a_use(source):
    assert _retired_marker_uses(ast.parse(source)) == [2]


def test_the_retired_marker_guard_passes_the_posix_only_markers():
    source = "".join(f"import pytest\n@pytest.mark.{m}\ndef test_{m}(): pass\n"
                     for m in sorted(POSIX_ONLY_MARKERS))
    assert _retired_marker_uses(ast.parse(source)) == []


def test_no_assess_test_parks_a_windows_failure():
    """A Windows failure is fixed or skips under a POSIX-only marker; the retired
    marker cannot come back unnoticed (#969, #762)."""
    this = Path(__file__).resolve().relative_to(REPO).as_posix()
    uses = [f"{rel}:{line}" for rel, tree in _assess_test_sources() if rel != this
            for line in _retired_marker_uses(tree)]
    assert not uses, f"{RETIRED_MARKER} marker used: " + ", ".join(uses)


def test_the_windows_skip_markers_are_the_posix_only_set():
    from conftest import WINDOWS_SKIP_MARKERS
    assert set(WINDOWS_SKIP_MARKERS) == POSIX_ONLY_MARKERS
