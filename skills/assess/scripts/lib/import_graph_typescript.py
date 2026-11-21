"""TypeScript and JavaScript import-graph builder (issue #709).

Implements the ``lib.import_graph`` builder interface for TypeScript and
JavaScript with a deterministic regex scan; no Node, no ``tsc``, no install.

- **Modules** are the scanned source files, named by their repo-relative POSIX
  path with the extension kept (``src/app/index.ts``), so ``a.ts`` beside
  ``a.js`` and ``foo.ts`` beside ``foo/index.ts`` stay distinct. The counts-only
  PATH rule hashes them like any path.
- **Imports** are ``import ... from 'x'``, side-effect ``import 'x'``,
  ``export ... from 'x'``, ``import('x')``, ``require('x')`` and
  ``import x = require('x')``, read after comments are stripped.
- **Resolution**: a relative specifier against the importer's directory; a bare
  one through the nearest ``tsconfig.json`` / ``jsconfig.json`` (``paths``,
  then ``baseUrl``, following relative ``extends``), then through the workspace
  packages (every named ``package.json``, one with a ``name``, resolved to
  its entry file). A specifier names a file, the file with a source extension added, a
  ``.js`` name standing for its ``.ts`` source, or a directory's ``index``.
  Anything else (``node_modules``, assets, Node built-ins) is external and
  dropped, as the Python builder drops third-party imports.
- **Packages** (the A3/A4 boundary) are directories holding an ``index`` source
  file or a named ``package.json``: a module belongs to the nearest such directory at
  or above it, else to its top-level directory (``.`` for a root file).
- **Front doors** are every ``index`` module and each workspace package's entry.
- **Surface** (A1) is the count of exported declarations and ``export { ... }``
  names, plus CommonJS ``exports.x =`` assignments.

Skipped: ``lib.import_graph.EXCLUDE_DIRS`` and the configured excludes,
declaration files (``.d.ts``), minified bundles, test and story files
(``*.test.*``, ``*.spec.*``, ``__tests__``, ``__mocks__``), build and test tool
config files (``vite.config.ts`` and the like, ``TOOL_CONFIG_PREFIXES``; an
app's own ``app.config.ts`` stays), dotfiles and files over 1 MB.

Limits, all of a static text scan: a specifier built at run time
(``require(name)``, template literals) is not seen; the comment stripper does not
parse regex literals, so a quote inside one can let a commented-out import
through; ``exports`` maps are read for their ``"."`` entry only; package names
inside ``node_modules`` and ``extends`` from a package are not followed;
``pnpm-workspace.yaml`` globs are not read, since any named ``package.json``
already counts as a workspace package.

The builder covers both ``typescript`` and ``javascript`` (its ``languages``), so
``select_graph`` picks it for a repository dominated by either.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from lib.import_graph import EXCLUDE_DIRS, ModuleGraph

TS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts")
JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs")
# Resolution order when a specifier has no extension: TypeScript first.
SOURCE_EXTENSIONS = TS_EXTENSIONS + JS_EXTENSIONS
# A ``.js`` specifier in TypeScript ESM names the ``.ts`` file it compiles from.
_JS_TO_TS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
SKIP_DIRS = frozenset({"__tests__", "__mocks__"})
MAX_FILE_BYTES = 1_000_000
CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")
# Build and test tools whose ``<tool>.config.*`` file is tooling, not source. An
# application's own config module (Angular ``app.config.ts``, a NestJS
# ``database.config.ts``) stays in the graph.
TOOL_CONFIG_PREFIXES = frozenset({
    "vite", "jest", "webpack", "eslint", "rollup", "babel", "tsconfig", "prettier",
    "tailwind", "postcss", "next", "nuxt", "vitest", "playwright", "cypress", "karma",
})
_EXTENDS_DEPTH = 10

_IMPORT_PATTERNS = (
    # import ... from 'x' / export ... from 'x' (multi-line specifier lists too).
    re.compile(r"^\s*(?:import|export)\b[^;'\"`]*?\bfrom\s*(['\"])([^'\"\n]+)\1", re.M),
    # import 'x' (side effect only).
    re.compile(r"^\s*import\s*(['\"])([^'\"\n]+)\1", re.M),
    # require('x'), import x = require('x'), import('x').
    re.compile(r"(?<![\w$.])(?:require|import)\s*\(\s*(['\"])([^'\"\n]+)\1\s*\)"),
)
_EXPORT_DEFAULT = re.compile(r"^\s*export\s+default\b", re.M)
_EXPORT_DECL = re.compile(
    r"^\s*export\s+(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
    r"(?:function|class|const|let|var|interface|type|enum|namespace)\b", re.M)
_EXPORT_LIST = re.compile(r"^\s*export\s*(?:type\s*)?\{([^}]*)\}(?!\s*from)", re.M)
_CJS_EXPORT = re.compile(r"^\s*(?:module\.)?exports\.[\w$]+\s*=", re.M)


def strip_comments(text: str) -> str:
    """Drop ``//`` and ``/* */`` comments, keeping string literals intact.

    Quote tracking resets at a newline for ``'`` and ``"`` (they cannot span
    lines), so a quote the scan misreads, for example inside a regex literal,
    affects one line at most. A backslash outside a string escapes the next
    character, so a regex literal's escaped slash before ``*`` does not open a
    comment.
    """
    out: list[str] = []
    i, n = 0, len(text)
    quote = ""
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote or (c == "\n" and quote != "`"):
                quote = ""
            i += 1
            continue
        if c == "\\":
            # An escape outside a string (a regex literal's escaped slash): keep
            # both characters, so an escaped slash before ``*`` opens no comment.
            out.append(text[i:i + 2])
            i += 2
        elif c in "'\"`":
            quote = c
            out.append(c)
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def read_jsonc(path: Path) -> dict[str, object] | None:
    """A ``tsconfig``-style JSON file (comments, trailing commas), or None."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    text = re.sub(r",(\s*[}\]])", r"\1", strip_comments(text))
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def import_specifiers(text: str) -> list[str]:
    """Every import specifier in a source text, in first-seen order."""
    code = strip_comments(text)
    found: dict[str, None] = {}
    for pattern in _IMPORT_PATTERNS:
        for match in pattern.finditer(code):
            found.setdefault(match.group(2).split("?", 1)[0], None)
    return list(found)


def export_surface(text: str) -> int:
    """Exported declarations and names: the module's public surface (A1)."""
    code = strip_comments(text)
    listed = sum(
        len([name for name in m.group(1).split(",") if name.strip()])
        for m in _EXPORT_LIST.finditer(code)
    )
    return (len(_EXPORT_DEFAULT.findall(code)) + len(_EXPORT_DECL.findall(code))
            + listed + len(_CJS_EXPORT.findall(code)))


def _is_source(name: str) -> bool:
    lower = name.lower()
    if not lower.endswith(SOURCE_EXTENSIONS) or lower.startswith("."):
        return False
    if re.search(r"\.d\.[mc]?ts$", lower) or lower.endswith((".min.js", ".min.mjs")):
        return False
    parts = lower.split(".")
    if "config" in parts[1:-1] and parts[0] in TOOL_CONFIG_PREFIXES:
        return False
    return not any(tag in parts[1:-1] for tag in ("test", "spec", "stories"))


def _stem(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0]


@dataclass
class _Config:
    """The resolution settings one tsconfig/jsconfig gives its files."""

    base_url: Path | None = None
    paths: dict[str, list[str]] = field(default_factory=dict)
    paths_dir: Path | None = None


def _load_config(path: Path, depth: int = 0) -> _Config:
    """Merge a config with the relative configs it ``extends`` (child wins)."""
    data = read_jsonc(path) or {}
    merged = _Config()
    extends = data.get("extends")
    parents = [extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []
    for parent in parents:
        if isinstance(parent, str) and parent.startswith(".") and depth < _EXTENDS_DEPTH:
            target = (path.parent / parent)
            if target.suffix != ".json":
                target = target.with_name(target.name + ".json")
            if target.is_file():
                inherited = _load_config(target, depth + 1)
                merged.base_url = inherited.base_url or merged.base_url
                if inherited.paths:
                    merged.paths, merged.paths_dir = inherited.paths, inherited.paths_dir
    options = data.get("compilerOptions")
    if isinstance(options, dict):
        base_url = options.get("baseUrl")
        if isinstance(base_url, str):
            merged.base_url = Path(os.path.normpath(path.parent / base_url))
        paths = options.get("paths")
        if isinstance(paths, dict):
            merged.paths = {
                k: [t for t in v if isinstance(t, str)]
                for k, v in paths.items() if isinstance(k, str) and isinstance(v, list)
            }
            merged.paths_dir = path.parent
    return merged


class _Resolver:
    """Resolves import specifiers to scanned files (absolute path -> module id)."""

    def __init__(self, repo_root: Path, files: dict[str, str],
                 workspaces: dict[str, Path]) -> None:
        self.repo_root = repo_root
        self.files = files
        self.workspaces = workspaces
        self._configs: dict[Path, _Config | None] = {}
        self.entries: dict[str, str] = {}
        for name, directory in sorted(workspaces.items()):
            entry = self._package_entry(directory)
            if entry is not None:
                self.entries[name] = entry

    def file(self, base: Path) -> str | None:
        """The module a path names, trying extensions, ``.js`` -> ``.ts``, ``index``."""
        raw = os.path.normpath(base)
        if raw in self.files:
            return self.files[raw]
        stem, ext = os.path.splitext(raw)
        for alt in _JS_TO_TS.get(ext.lower(), ()):
            if stem + alt in self.files:
                return self.files[stem + alt]
        for candidate in [raw + e for e in SOURCE_EXTENSIONS] + [
                os.path.join(raw, "index" + e) for e in SOURCE_EXTENSIONS]:
            if candidate in self.files:
                return self.files[candidate]
        return None

    def _package_entry(self, directory: Path) -> str | None:
        data = read_jsonc(directory / "package.json") or {}
        targets: list[str] = []
        exports = data.get("exports")
        if isinstance(exports, dict):
            exports = exports.get(".", exports)
            if isinstance(exports, dict):
                exports = next((exports[k] for k in ("source", "import", "default", "require")
                                if isinstance(exports.get(k), str)), None)
        for value in (data.get("source"), exports, data.get("module"), data.get("main")):
            if isinstance(value, str):
                targets.append(value)
        for target in targets:
            rel = Path(target)
            tries = [rel]
            if rel.parts and rel.parts[0].lstrip("./") in ("dist", "build", "lib", "out"):
                tries.append(Path("src", *rel.parts[1:]))
            for t in tries:
                hit = self.file(directory / t)
                if hit is not None:
                    return hit
        return self.file(directory / "index") or self.file(directory / "src" / "index")

    def _config_for(self, directory: Path) -> _Config | None:
        """The nearest tsconfig/jsconfig at or above ``directory`` (within the repo)."""
        if directory in self._configs:
            return self._configs[directory]
        config: _Config | None = None
        for name in CONFIG_NAMES:
            if (directory / name).is_file():
                config = _load_config(directory / name)
                break
        else:
            if directory != self.repo_root and self.repo_root in directory.parents:
                config = self._config_for(directory.parent)
        self._configs[directory] = config
        return config

    def _via_paths(self, spec: str, config: _Config) -> str | None:
        base = config.base_url or config.paths_dir
        if base is None:
            return None
        exact = [p for p in config.paths if "*" not in p and p == spec]
        wild = sorted(
            (p for p in config.paths if p.count("*") == 1
             and spec.startswith(p.split("*")[0]) and spec.endswith(p.split("*")[1])
             and len(spec) >= len(p) - 1),
            key=lambda p: -len(p.split("*")[0]),
        )
        for pattern in exact + wild:
            head, _, tail = pattern.partition("*")
            star = spec[len(head):len(spec) - len(tail)] if "*" in pattern else ""
            for target in config.paths[pattern]:
                hit = self.file(base / target.replace("*", star))
                if hit is not None:
                    return hit
        return None

    def _via_workspace(self, spec: str) -> str | None:
        parts = spec.split("/")
        name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
        directory = self.workspaces.get(name)
        if directory is None:
            return None
        sub = spec[len(name):].lstrip("/")
        if not sub:
            return self.entries.get(name)
        return self.file(directory / sub) or self.file(directory / "src" / sub)

    def resolve(self, importer: Path, spec: str) -> str | None:
        if spec.startswith((".", "/")):
            return self.file(importer.parent / spec) if spec.startswith(".") else None
        if spec.startswith(("node:", "#")):
            return None
        config = self._config_for(importer.parent)
        if config is not None:
            hit = self._via_paths(spec, config) if config.paths else None
            if hit is None and config.base_url is not None:
                hit = self.file(config.base_url / spec)
            if hit is not None:
                return hit
        return self._via_workspace(spec)


def _walk(start: Path, extra: set[str]) -> tuple[list[Path], list[Path]]:
    """(source files, package.json files) under ``start``, sorted."""
    sources: list[Path] = []
    manifests: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in EXCLUDE_DIRS and d not in extra and d not in SKIP_DIRS
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if name == "package.json":
                manifests.append(path)
            elif _is_source(name):
                try:
                    if path.is_file() and path.stat().st_size <= MAX_FILE_BYTES:
                        sources.append(path)
                except OSError:
                    continue
    return sources, manifests


def _workspaces(manifests: list[Path]) -> dict[str, Path]:
    """Workspace package name -> directory; the shallowest copy of a name wins."""
    found: dict[str, Path] = {}
    for manifest in sorted(manifests, key=lambda p: (len(p.parts), p.as_posix())):
        data = read_jsonc(manifest) or {}
        name = data.get("name")
        if isinstance(name, str) and name and name not in found:
            found[name] = manifest.parent
    return found


def _package_of(module: str, boundaries: set[str]) -> str:
    """The nearest boundary directory at or above ``module``, else its top directory."""
    parts = module.split("/")[:-1]
    for depth in range(len(parts), -1, -1):
        directory = "/".join(parts[:depth]) or "."
        if directory in boundaries:
            return directory
    return parts[0] if parts else "."


class TypeScriptBuilder:
    """The TypeScript/JavaScript :class:`lib.import_graph.ImportGraphBuilder`.

    One builder graphs both languages, since they import each other. The graph's
    ``language`` is ``typescript`` when any scanned file is TypeScript, else
    ``javascript``, so a plain-JavaScript repository is not reported as
    TypeScript.
    """

    language = "typescript"
    languages = ("typescript", "javascript")

    def is_graph_source(self, rel: Path) -> bool:
        """True when the scan would graph ``rel`` (a path below the walk's start).

        ``select_graph`` counts only such files toward the dominant language, so
        a Python app's ``static/js/*.min.js`` does not make it JavaScript.
        """
        return _is_source(rel.name) and not SKIP_DIRS & set(rel.parts[:-1])

    def build(
        self, repo_root: Path, extra_exclude_dirs: set[str], scope: Path | None,
    ) -> ModuleGraph | None:
        repo_root = Path(repo_root).resolve()
        all_sources, manifests = _walk(repo_root, extra_exclude_dirs)
        # Workspaces and configs come from the whole repo; modules from the scope.
        start = scope.resolve() if scope is not None else repo_root
        sources = [p for p in all_sources if p.is_relative_to(start)]
        if not sources:
            return None
        ids = {p: p.relative_to(repo_root).as_posix() for p in sources}
        files = {os.path.normpath(p): m for p, m in ids.items()}
        workspaces = _workspaces(manifests)
        resolver = _Resolver(repo_root, files, workspaces)
        edges: dict[str, set[str]] = {}
        surfaces: dict[str, int] = {}
        for path, module in ids.items():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            targets = {resolver.resolve(path, spec) for spec in import_specifiers(text)}
            edges[module] = {t for t in targets if t is not None and t != module}
            surfaces[module] = export_surface(text)
        modules = sorted(ids.values())
        index_modules = {m for m in modules if _stem(m) == "index"}
        boundaries = {m.rsplit("/", 1)[0] if "/" in m else "." for m in index_modules}
        for directory in workspaces.values():
            try:
                rel = directory.relative_to(repo_root).as_posix()
            except ValueError:  # pragma: no cover - manifests come from under the root
                continue
            boundaries.add(rel)
        has_ts = any(m.lower().endswith(TS_EXTENSIONS) for m in modules)
        return ModuleGraph(
            language="typescript" if has_ts else "javascript",
            modules=modules,
            edges=edges,
            front_doors=index_modules | set(resolver.entries.values()),
            module_file={m: p for p, m in ids.items()},
            package_of={m: _package_of(m, boundaries) for m in modules},
            surfaces=surfaces,
        )


TYPESCRIPT_BUILDER = TypeScriptBuilder()
