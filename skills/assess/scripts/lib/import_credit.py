"""The import tier of the sibling-test probe: test files credit what they import.

A large test file split by concern (``test_keyhole_signals.py`` into eight
``test_keyhole_<family>.py`` files, as the file-size ratchet asks) leaves no
test named after its subject, so the name tiers in ``lib.sibling_tests`` read
the subject as untested. This tier reads the imports instead: a source module
counts as having a test file when some test file imports it.

The scan is static and bounded:

- Only files that are tests by name (``sibling_tests.is_test_path``) are read,
  and only those that define a test: a top-level ``test*`` function or
  ``Test*`` class (or a ``*TestCase`` subclass, or a class with ``test*``
  methods) in Python, an ``it(`` / ``test(`` / ``describe(`` call outside a
  comment in JS/TS. A ``conftest.py`` or a helper such as ``keyhole_helpers.py`` is not a
  test by name, and a helper named ``test_utils.py`` defines no test, so a
  support module that imports everything credits nothing.
- Python is parsed with ``ast`` (no execution). ``import a.b`` and
  ``from a.b import c`` yield the dotted names ``a.b`` and ``a.b.c``; a name
  credits the source whose module path ends with it. Relative imports resolve
  against the test's own directory to one exact module path.
- JS/TS is a regex over ``import ... from '<spec>'`` (``import type`` skipped:
  it is erased at runtime), ``export ... from '<spec>'``, side-effect
  ``import '<spec>'``, ``import('<spec>')`` and ``require('<spec>')``. Only
  relative specifiers (``./`` / ``../``) count; a package or alias specifier
  (``react``, ``@/lib/x``) needs a resolver config this tier does not read, so
  those tests keep only the name match. Other languages keep only the name
  match.
- Every module a qualifying test imports directly is credited, not only the
  first: a split test imports its subject by name, and an integration test that
  imports five modules does execute all five. The credit is
  ``sibling_test_only``, which routes the file to mutation testing, where an
  import that tests nothing shows up as surviving mutants.

Ambiguity is resolved the way the basename tier resolves it. A dotted name
matched by several sources (two ``util.py`` in different packages for
``import util``) credits the one whose directory shares the deepest common
ancestor with the test, and none on a tie. A one-part name (``import util``)
also needs that ancestor to be below the repository root, because a bare name
with no path relationship is as likely to be the standard library's ``json``
as the repository's ``tools/x/json.py``; a module at the root itself
(``pkg/__init__.py`` for ``import pkg``) is exactly that name and counts. A
one-part name that is a standard-library module (``import json`` from
``tools/tests/``) never credits, however close a same-named source sits. A
module that lives inside a test directory (``tests/``, ``test/``, ``spec/``,
``__tests__/``) is test support, never credited by this tier. Resolution
indexes test modules too, so ``from lib import test_focus`` names the source
``lib/test_focus.py`` (a test by name, so never credited) rather than falling
back to ``lib/__init__.py``.

Cost: one ``ast.parse`` (or one regex pass) per qualifying test file, at most
:data:`MAX_SCAN_FILES` files of at most :data:`MAX_FILE_BYTES` each, in sorted
path order, so a capped scan is deterministic. Each imported name is matched
against the sources sharing its last part, so the join is linear in the
imports times the same-stem sources. The scan runs once per index, on the
first probe the name tiers could not answer.

Inward-only: stdlib only; ``lib.sibling_tests`` imports it. Never raises.
"""
from __future__ import annotations

import ast
import posixpath
import re
from collections.abc import Callable, Iterable
from pathlib import PurePosixPath

PY_SUFFIX = ".py"
JS_SUFFIXES: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
                                ".mts", ".cts")
# Directories whose modules are test support, never a subject to credit.
SUPPORT_DIRS = frozenset({"tests", "test", "spec", "__tests__"})
# Bounds on the scan. Past MAX_SCAN_FILES the remaining tests (in sorted order)
# are not read: credits are only ever lost, never invented, by the cap.
MAX_SCAN_FILES = 10_000
MAX_FILE_BYTES = 1_000_000

# ``import`` / ``export ... from '<spec>'`` with an optional ``type`` keyword
# (group 1: a type-only import is erased at runtime and runs nothing), then a
# side-effect ``import '<spec>'``, a dynamic ``import('<spec>')`` and a
# ``require('<spec>')`` (group 2 or 3 is the specifier).
_JS_SPEC_RE = re.compile(
    r"""\b(?:import|export)\s+(type\s+)?[\w*{}\s,$]*?\bfrom\s*['"](\.{1,2}/[^'"\n]+)['"]"""
    r"""|\b(?:import\s*\(?|require\s*\()\s*['"](\.{1,2}/[^'"\n]+)['"]""")
# ``//`` comments and ``/* */`` blocks that start a line, stripped before the
# JS scan (a ``/*`` mid-line may sit in a string such as a glob pattern)
# so a commented-out import or ``it(`` is not evidence. Strings are not parsed:
# an import spelled inside a string literal still matches.
_JS_COMMENT_RE = re.compile(r"^[ \t]*(?:/\*.*?\*/|//[^\n]*)", re.DOTALL | re.MULTILINE)
_JS_TEST_RE = re.compile(r"\b(?:it|test|describe)(?:\.\w+)?\s*\(")

Module = tuple[str, ...]

# Top-level standard-library module names: the union of
# ``sys.stdlib_module_names`` over CPython 3.10-3.14, private names dropped,
# pinned so the output does not depend on the interpreter running the scan. A
# one-part import of one of these (``import json``) names the standard library
# at runtime, whatever ``json.py`` the repository holds, so it credits nothing.
_STDLIB_TOP_LEVEL = frozenset("""
abc aifc annotationlib antigravity argparse array ast asynchat asyncio
asyncore atexit audioop base64 bdb binascii binhex bisect builtins bz2
calendar cgi cgitb chunk cmath cmd code codecs codeop collections colorsys
compileall compression concurrent configparser contextlib contextvars copy
copyreg cProfile crypt csv ctypes curses dataclasses datetime dbm decimal
difflib dis distutils doctest email encodings ensurepip enum errno
faulthandler fcntl filecmp fileinput fnmatch fractions ftplib functools gc
genericpath getopt getpass gettext glob graphlib grp gzip hashlib heapq hmac
html http idlelib imaplib imghdr imp importlib inspect io ipaddress
itertools json keyword lib2to3 linecache locale logging lzma mailbox mailcap
marshal math mimetypes mmap modulefinder msilib msvcrt multiprocessing netrc
nis nntplib nt ntpath nturl2path numbers opcode operator optparse os
ossaudiodev pathlib pdb pickle pickletools pipes pkgutil platform plistlib
poplib posix posixpath pprint profile pstats pty pwd py_compile pyclbr pydoc
pydoc_data pyexpat queue quopri random re readline reprlib resource
rlcompleter runpy sched secrets select selectors shelve shlex shutil signal
site smtpd smtplib sndhdr socket socketserver spwd sqlite3 sre_compile
sre_constants sre_parse ssl stat statistics string stringprep struct
subprocess sunau symtable sys sysconfig syslog tabnanny tarfile telnetlib
tempfile termios textwrap this threading time timeit tkinter token tokenize
tomllib trace traceback tracemalloc tty turtle turtledemo types typing
unicodedata unittest urllib uu uuid venv warnings wave weakref webbrowser
winreg winsound wsgiref xdrlib xml xmlrpc zipapp zipfile zipimport zlib
zoneinfo
""".split())


def module_parts(rel_path: str) -> Module:
    """Dotted module path of a Python source: directories plus the stem, the
    stem dropped for a package ``__init__.py``."""
    p = PurePosixPath(rel_path)
    dirs = tuple(p.parts[:-1])
    return dirs if p.stem == "__init__" else (*dirs, p.stem)


def _is_test_class(node: ast.ClassDef) -> bool:
    """A pytest ``Test*`` class, a ``unittest`` / Django ``*TestCase``
    subclass, or any class defining a ``test*`` method."""
    if node.name.startswith("Test"):
        return True
    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
        if name.endswith("TestCase"):
            return True
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name.startswith("test") for n in node.body)


def _defines_python_test(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                return True
        elif isinstance(node, ast.ClassDef) and _is_test_class(node):
            return True
    return False


def _relative_base(test_dirs: Module, level: int) -> Module | None:
    """The package a relative import of ``level`` dots names, or ``None`` when
    it climbs above the repository root."""
    up = level - 1
    if up > len(test_dirs):
        return None
    return test_dirs[: len(test_dirs) - up]


# One imported module, as the names it may resolve to in priority order:
# ``from pkg import mod`` is ``pkg.mod`` when that module exists, else ``pkg``
# (``mod`` is then a name defined in the package). A flag says whether the
# names are exact repo paths (a relative import) or suffixes (an absolute one).
Target = tuple[bool, tuple[Module, ...]]


def _from_targets(node: ast.ImportFrom, test_dirs: Module) -> list[Target]:
    mod: Module = tuple(node.module.split(".")) if node.module else ()
    exact = node.level > 0
    if exact:
        base = _relative_base(test_dirs, node.level)
        if base is None:
            return []
        mod = (*base, *mod)
    out: list[Target] = []
    for alias in node.names:
        sub: Module = (*mod, alias.name)
        if alias.name == "*":
            out.append((exact, (mod,)))
        else:
            out.append((exact, (sub, mod) if mod else (sub,)))
    return out


def python_imports(source: str, test_dirs: Module) -> list[Target]:
    """The modules a Python test imports, or an empty list when it does not
    parse or defines no test."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []  # deeply nested input can exhaust the parser
    if not _defines_python_test(tree):
        return []
    targets: list[Target] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend((False, (tuple(a.name.split(".")),)) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            targets.extend(_from_targets(node, test_dirs))
    return targets


# TypeScript ESM (``NodeNext`` / ``Node16``) writes a relative specifier with
# the emitted extension: ``'../src/foo.js'`` for the file ``src/foo.ts``.
_EMITTED_TO_SOURCE: dict[str, tuple[str, ...]] = {
    ".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",),
}


def _emitted_to_source(target: str) -> list[str]:
    stem, ext = posixpath.splitext(target)
    return [stem + s for s in _EMITTED_TO_SOURCE.get(ext, ())]


def js_imports(source: str, test_dir: str, files: frozenset[str]) -> list[str]:
    """Repo-relative files a JS/TS test imports through relative specifiers, or
    an empty list when it defines no test. A specifier resolves to the file as
    written, then as the TypeScript source of an emitted ``.js`` / ``.jsx`` /
    ``.mjs`` / ``.cjs`` name, then with each JS/TS suffix, then to an
    ``index`` file."""
    source = _JS_COMMENT_RE.sub("", source)
    if not _JS_TEST_RE.search(source):
        return []
    out: list[str] = []
    for type_only, from_spec, call_spec in _JS_SPEC_RE.findall(source):
        if type_only:
            continue
        spec = from_spec or call_spec
        target = posixpath.normpath(posixpath.join(test_dir, spec))
        if target.startswith("../") or target == "..":
            continue  # above the repository root
        candidates = [target, *_emitted_to_source(target),
                      *(target + s for s in JS_SUFFIXES),
                      *(f"{target}/index{s}" for s in JS_SUFFIXES)]
        hit = next((c for c in candidates if c in files), None)
        if hit is not None:
            out.append(hit)
    return out


def _is_support(rel_path: str) -> bool:
    return any(part in SUPPORT_DIRS for part in PurePosixPath(rel_path).parts[:-1])


def _common_depth(a: Module, b: Module) -> int:
    depth = 0
    for x, y in zip(a, b):
        if x != y:
            break
        depth += 1
    return depth


class ImportCredits:
    """Sources credited by a test file's imports, computed on first use from a
    repository's test and source file lists (repo-relative POSIX paths)."""

    def __init__(self, read: Callable[[str], str | None], tests: Iterable[str],
                 sources: Iterable[str]) -> None:
        self._read = read
        self._tests = sorted(t for t in tests
                             if t.endswith(PY_SUFFIX) or t.endswith(JS_SUFFIXES))
        self._sources = sorted(s for s in sources if not _is_support(s))
        self._support = sorted(s for s in sources if _is_support(s))
        self._credited: frozenset[str] | None = None

    def credits(self, rel_path: str) -> bool:
        """True when some qualifying test file imports ``rel_path``."""
        if self._credited is None:
            self._credited = self._scan()
        return rel_path in self._credited

    def _scan(self) -> frozenset[str]:
        # Every Python module, tests included: ``from lib import test_focus``
        # names the module ``lib/test_focus.py`` even though its name reads as
        # a test, so it must not fall back to crediting ``lib/__init__.py``.
        # A hit on a test or support module resolves the import to no credit.
        by_stem: dict[str, list[tuple[Module, str]]] = {}
        by_module: dict[Module, str] = {}
        for path in (*self._sources, *self._support, *self._tests):
            if path.endswith(PY_SUFFIX):
                parts = module_parts(path)
                if parts:
                    by_stem.setdefault(parts[-1], []).append((parts, path))
                    by_module.setdefault(parts, path)
        files = frozenset(self._sources)
        credited: set[str] = set()
        for test in self._tests[:MAX_SCAN_FILES]:
            text = self._read(test)
            if text is None:
                continue
            test_dirs = tuple(PurePosixPath(test).parts[:-1])
            if test.endswith(PY_SUFFIX):
                for target in python_imports(text, test_dirs):
                    hit = _resolve(target, test_dirs, by_stem, by_module)
                    if hit in files:
                        credited.add(hit)
            else:
                credited.update(js_imports(text, "/".join(test_dirs), files))
        return frozenset(credited)


def _resolve(target: Target, test_dirs: Module,
             by_stem: dict[str, list[tuple[Module, str]]],
             by_module: dict[Module, str]) -> str | None:
    """The module file the first resolvable name of ``target`` names (a test or
    support module included), or ``None``."""
    exact, names = target
    for name in names:
        if not name or (not exact and len(name) == 1 and name[0] in _STDLIB_TOP_LEVEL):
            continue
        hit = (by_module.get(name) if exact
               else _closest(by_stem.get(name[-1], []), name, test_dirs))
        if hit is not None:
            return hit
    return None


def _closest(candidates: list[tuple[Module, str]], name: Module,
             test_dirs: Module) -> str | None:
    """The one source whose module path ends with ``name`` and whose directory
    shares the deepest common ancestor with the test; ``None`` on a tie, and
    for a one-part name whose best ancestor is the repository root unless the
    source sits at the root itself (``pkg/__init__.py`` is exactly ``pkg``)."""
    best: list[str] = []
    best_parts: Module = ()
    best_depth = -1
    for parts, src in candidates:
        if parts[-len(name):] != name:
            continue
        depth = _common_depth(parts[:-1], test_dirs)
        if depth > best_depth:
            best, best_depth, best_parts = [src], depth, parts
        elif depth == best_depth:
            best.append(src)
    if len(best) != 1:
        return None
    if len(name) == 1 and best_depth == 0 and best_parts != name:
        return None
    return best[0]


class FileReader:
    """Reads a repo-relative text file under a root, ``None`` when it is
    missing, unreadable, over :data:`MAX_FILE_BYTES` or not UTF-8."""

    def __init__(self, root: str) -> None:
        self._root = root

    def __call__(self, rel_path: str) -> str | None:
        path = posixpath.join(self._root, rel_path)
        try:
            with open(path, "rb") as fh:
                data = fh.read(MAX_FILE_BYTES + 1)
        except OSError:
            return None
        if len(data) > MAX_FILE_BYTES:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None
