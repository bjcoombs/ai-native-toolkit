"""The mutmut 3 adapter: a per-package, scratch-copy mutation pass.

mutmut 3 changed its command line: ``mutmut run`` takes no paths, reads its
scope from ``[tool.mutmut]`` in ``pyproject.toml`` or ``[mutmut]`` in
``setup.cfg`` *in the working directory*, dropped the ``junitxml`` subcommand,
and records results as ``mutants/<path>.meta`` JSON. It copies only
``source_paths`` (plus ``also_copy`` and a few defaults) into ``mutants/`` and
runs the suite from there, so a test that reads a file outside those paths
fails the baseline run and no mutant is ever checked.

The pass therefore groups the focus files by package root (the nearest
directory holding ``pyproject.toml``, ``setup.cfg`` or ``setup.py``) and runs
mutmut once per group, each in its own scratch copy of the repository, from the
package root:

- a package whose config mutmut 3 can read runs under that config unchanged;
- any other package gets a generated ``[mutmut]`` section whose ``also_copy``
  mirrors the rest of the repository around ``mutants/``, so a test that climbs
  from its own file to a repository file still finds it.

The assessed tree is never written to. Every group draws on one
``MUTATION_TIMEOUT`` budget: the git snapshot and the mutmut run are bounded by
what remains, a copy is not interruptible but is checked against the deadline
before and after, and a group that cannot run records its own ``reason``.
"""
from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from .common import MUTATION_TIMEOUT, _read

# mutmut 3 exit codes per mutant, read from ``status_by_exit_code`` in
# ``mutmut/__main__.py`` of mutmut 3.6.0: 1 and 3 killed; 24, -24, 36, 152 and
# 255 timeout; 37 caught by type check; 0 survived; 5 and 33 no tests. A
# timeout or a type-check catch pins the behaviour as well as a failing test;
# "no tests" means nothing exercises the mutant, which is a survivor for our
# purposes (the stryker parser treats NoCoverage the same way). Everything
# else (None not checked, 2 interrupted, 34 skipped, 35 suspicious, -9 and -11
# segfault, unknown codes) is left out of the totals.
_MUTMUT3_KILLED = frozenset({1, 3, 24, -24, 36, 37, 152, 255})
_MUTMUT3_SURVIVED = frozenset({0, 5, 33})

_COPY_IGNORE = shutil.ignore_patterns(
    ".git", ".assess", "mutants", "node_modules", ".venv", "venv",
    "__pycache__", ".mutmut-cache", ".tox", ".pytest_cache")

# Never mirrored into mutants/: VCS and tool state, and paths mutmut copies by
# default (also_copy always carries tests/, test/, setup.cfg, pyproject.toml).
_NEVER_MIRROR = frozenset({".git", ".assess", "mutants", ".mutmut-cache"})
_MUTMUT_DEFAULT_COPY = frozenset({"tests", "test", "setup.cfg", "pyproject.toml"})

_PACKAGE_MARKERS = ("pyproject.toml", "setup.cfg", "setup.py")

# A section header on its own line, so a commented-out ``# [tool.mutmut]`` or a
# mention inside a string does not count as configuration.
_TOML_MUTMUT_SECTION_RE = re.compile(r"^[ \t]*\[tool\.mutmut\][ \t]*(#.*)?$", re.M)
_INI_MUTMUT_SECTION_RE = re.compile(r"^\[mutmut\][ \t]*$", re.M)

# Python's warnings module prints "<file>:<line>: UserWarning: <text>" and then
# the source line that raised it; neither is the reason a run failed. Anchored
# to that format, so a traceback ending in a raised Warning (a suite run with
# filterwarnings = error) still yields its last line.
_WARNING_LINE_RE = re.compile(r"^\S+:\d+: \w*Warning: |^warnings\.warn\(")

_MAX_REASON_DETAIL = 300        # chars of tool error kept in a stored reason


# ── results ──────────────────────────────────────────────────────────────────

def _parse_mutmut3_meta(mutants_dir: Path) -> list[dict]:
    """Per-file killed/survived/total from mutmut 3's ``mutants/**/*.meta``."""
    out: list[dict] = []
    if not mutants_dir.is_dir():
        return out
    for meta in sorted(mutants_dir.rglob("*.meta")):
        try:
            codes = json.loads(meta.read_text(encoding="utf-8")).get(
                "exit_code_by_key") or {}
        except (OSError, ValueError, AttributeError):
            continue
        killed = sum(1 for c in codes.values() if c in _MUTMUT3_KILLED)
        survived = sum(1 for c in codes.values() if c in _MUTMUT3_SURVIVED)
        if killed + survived:
            rel = meta.relative_to(mutants_dir).as_posix()[:-len(".meta")]
            out.append({"file": rel, "killed": killed, "survived": survived,
                        "total": killed + survived})
    return out


def _tool_error_line(proc: subprocess.CompletedProcess) -> str:
    """The most telling line of a failed tool run: the last non-blank stderr
    line that is not a Python warning (a traceback ends with the exception),
    else the last of stdout. mutmut 3 prints its own stop messages ("failed to
    collect stats", a test/mutant key mismatch) on stdout, behind any
    deprecation warning on stderr."""
    for stream in (proc.stderr, proc.stdout):
        lines = [ln.strip() for ln in (stream or "").splitlines()
                 if ln.strip() and not _WARNING_LINE_RE.search(ln.strip())]
        if lines:
            return lines[-1]
    return ""


def _no_records_reason(tool: str, proc: subprocess.CompletedProcess,
                       scratch: Path | None = None) -> str:
    """``scratch`` is the directory the tool ran in when that was a temporary
    copy: its prefix is cut from the detail, leaving repo-relative paths, since
    the reason is stored in run-context.json and the directory is gone."""
    reason = (f"no mutant records recovered from {tool} "
              f"output (exit code {proc.returncode})")
    detail = _tool_error_line(proc) if proc.returncode != 0 else ""
    if scratch is not None:
        # Longest first: where the temp dir sits behind a symlink (macOS /var
        # -> /private/var) the unresolved form is a substring of the resolved
        # one, and cutting it first would leave "/private" glued to the rest.
        prefixes = sorted({str(scratch.resolve()), str(scratch)},
                          key=len, reverse=True)
        for prefix in prefixes:
            detail = detail.replace(prefix + os.sep, "").replace(prefix, ".")
    detail = detail[:_MAX_REASON_DETAIL]
    return f"{reason}: {detail}" if detail else reason


# ── configuration ────────────────────────────────────────────────────────────

def _mutmut3_reads_config(root: Path) -> bool:
    """Whether mutmut 3 would find its own configuration in ``root``. It reads
    exactly two places: ``[tool.mutmut]`` in that directory's
    ``pyproject.toml``, else ``[mutmut]`` in its ``setup.cfg``. A
    ``.mutmut.toml`` or a CI workflow that names mutmut is not configuration it
    can see, so ``detect_mutation_config`` (which counts all of those) is the
    wrong question here."""
    return bool(_TOML_MUTMUT_SECTION_RE.search(_read(root / "pyproject.toml"))
                or _INI_MUTMUT_SECTION_RE.search(_read(root / "setup.cfg")))


def _as_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [v.strip() for v in value.splitlines() if v.strip()]
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _read_mutmut3_config(root: Path) -> dict[str, list[str]]:
    """The scope keys of the config mutmut 3 reads in ``root``, read the way
    mutmut reads them (pyproject first, then setup.cfg). An unreadable file
    yields no keys, which leaves the scope checks to mutmut itself."""
    raw: dict[str, object] = {}
    if _TOML_MUTMUT_SECTION_RE.search(_read(root / "pyproject.toml")):
        try:
            data = tomllib.loads(_read(root / "pyproject.toml"))
            raw = dict(data.get("tool", {}).get("mutmut", {}))
        except (tomllib.TOMLDecodeError, AttributeError, TypeError, ValueError):
            raw = {}
    else:
        parser = configparser.ConfigParser()
        try:
            parser.read_string(_read(root / "setup.cfg"))
            raw = dict(parser["mutmut"]) if parser.has_section("mutmut") else {}
        except configparser.Error:
            raw = {}
    keys = ("source_paths", "paths_to_mutate", "only_mutate", "do_not_mutate")
    return {k: _as_list(raw.get(k)) for k in keys}


def _covered_by_config(cfg: dict[str, list[str]], rel: str) -> bool:
    """Whether mutmut under ``cfg`` would mutate ``rel`` (package-relative)."""
    sources = cfg["source_paths"] or cfg["paths_to_mutate"]
    if sources and not any(rel == s.rstrip("/") or rel.startswith(s.rstrip("/") + "/")
                           for s in sources):
        return False
    if cfg["only_mutate"] and not any(fnmatch(rel, p) for p in cfg["only_mutate"]):
        return False
    return not any(fnmatch(rel, p) for p in cfg["do_not_mutate"])


def _config_file(pkg: Path, pkg_rel: str) -> str:
    """Repo-relative name of the file mutmut 3 reads its config from."""
    name = ("pyproject.toml" if _TOML_MUTMUT_SECTION_RE.search(_read(pkg / "pyproject.toml"))
            else "setup.cfg")
    return f"{pkg_rel}/{name}" if pkg_rel else name


def _config_spellings(pkg: Path, rel_scope: list[str]) -> dict[str, str]:
    """Each focus file (package-relative) as the package's own config sees it.
    A ``source_paths`` entry may be a link (this repo's ``src -> scripts``):
    mutmut walks and keys files under the link's name, so ``scripts/lib/x.py``
    is ``src/lib/x.py`` to its ``only_mutate`` and in ``mutants/``."""
    cfg = _read_mutmut3_config(pkg)
    links = [s.rstrip("/") for s in (cfg["source_paths"] or cfg["paths_to_mutate"])
             if (pkg / s.rstrip("/")).is_symlink()]
    out: dict[str, str] = {}
    for f in rel_scope:
        out[f] = f
        real = (pkg / f).resolve()
        for s in links:
            try:
                out[f] = f"{s}/{real.relative_to((pkg / s).resolve()).as_posix()}"
                break
            except ValueError:
                continue
    return out


def _repo_config_gap(pkg: Path, pkg_rel: str, rel_scope: list[str]) -> str | None:
    """Why the package's own mutmut config cannot measure ``rel_scope``, or
    None when it can. Checked against the scratch copy, so a ``source_paths``
    entry made at run time (a link CI creates, a generated tree) reads as
    missing: it is named, not recreated."""
    cfg = _read_mutmut3_config(pkg)
    sources = cfg["source_paths"] or cfg["paths_to_mutate"]
    missing = [s for s in sources if not (pkg / s).exists()]
    if missing:
        return (f"the mutmut config in {_config_file(pkg, pkg_rel)} names "
                f"source_paths missing from a clean copy ({', '.join(missing)}); "
                f"a path made at run time is not recreated")
    if not any(_covered_by_config(cfg, f) for f in rel_scope):
        return (f"no focus file is in the scope of the mutmut config in "
                f"{_config_file(pkg, pkg_rel)} (source_paths / only_mutate / "
                f"do_not_mutate)")
    return None


def _package_entries(pkg: Path, sources: list[str]) -> list[str]:
    """``also_copy`` entries that make ``mutants/`` stand in for the package:
    every top-level entry except the sources (mutmut copies those itself),
    the paths it copies by default, and tool state."""
    skip = _NEVER_MIRROR | _MUTMUT_DEFAULT_COPY | {s.split("/", 1)[0] for s in sources}
    return [e.name for e in sorted(pkg.iterdir()) if e.name not in skip]


def _copy_entry(src: Path, dest: Path) -> None:
    if src.is_dir() and not src.is_symlink():
        shutil.copytree(src, dest, symlinks=True)
    else:
        shutil.copy2(src, dest, follow_symlinks=False)


def _mirror_ancestors(work: Path, pkg_rel: str) -> None:
    """Shift the repository down one level around the package, in the copy.

    mutmut runs the suite from ``<pkg>/mutants``, one level deeper than the
    package, so a test resolving ``Path(__file__).parents[n]`` (or running
    ``git`` there) lands one directory short of where it would in the
    repository. With ``mutants/`` standing in for the package, the package
    must stand in for its parent, the parent for the grandparent, and so on:
    each entry of the k-th ancestor is copied into the (k-1)-th, nearest
    level first so no level receives copies meant for the one below it.
    Names already present are skipped, so nothing a directory owns is
    overwritten, and the way down is skipped so the package is not nested in
    itself. The package itself is mutmut's working directory, so it never
    receives the parent's ``pyproject.toml``, ``setup.cfg``, ``tests/`` or
    ``test/``: mutmut would read a parent ``[tool.mutmut]`` before the
    generated section, and pytest the parent's settings or suite. Runs before
    the git snapshot, so the copies are in ``HEAD`` too.
    Cost is at most one copy of the repository per level of depth."""
    parts = PurePosixPath(pkg_rel).parts if pkg_rel else ()
    for k in range(1, len(parts) + 1):
        ancestor = work.joinpath(*parts[:len(parts) - k])
        below = work.joinpath(*parts[:len(parts) - k + 1])
        down = parts[len(parts) - k]
        for e in sorted(ancestor.iterdir()):
            if e.name in _NEVER_MIRROR or e.name == down or (below / e.name).exists():
                continue
            if k == 1 and e.name in _MUTMUT_DEFAULT_COPY:
                continue  # the package is mutmut's cwd: never its config or suite
            _copy_entry(e, below / e.name)


def _mutmut3_config(scope: list[str], also_copy: list[str] | None = None) -> str:
    """The ``[mutmut]`` setup.cfg section scoping a run to ``scope``.

    ``source_paths`` is the top-level entry of each file (mutmut copies it
    whole into ``mutants/`` so sibling imports still resolve); ``only_mutate``
    narrows mutation to the files themselves. mutmut 3.0-3.5 knows neither key:
    it reads ``paths_to_mutate`` (deprecated in 3.6, where ``source_paths``
    wins) and mutates every file under it, so the section carries both and the
    pass drops out-of-scope files from the results. ``also_copy`` (3.0+)
    carries the files the tests read outside the sources."""
    roots: list[str] = []
    for f in scope:
        parts = Path(f).parts
        root = parts[0] if len(parts) > 1 else f
        if root not in roots:
            roots.append(root)
    lines = ["[mutmut]"]
    for key in ("source_paths=", "paths_to_mutate="):
        lines.append(key)
        lines += [f"    {r}" for r in roots]
    lines.append("only_mutate=")
    lines += [f"    {Path(f).as_posix()}" for f in scope]
    if also_copy:
        lines.append("also_copy=")
        lines += [f"    {p}" for p in also_copy]
    return "\n".join(lines) + "\n"


# ── grouping ─────────────────────────────────────────────────────────────────

def _package_root(repo_root: Path, rel: str) -> str:
    """Repo-relative POSIX path of the nearest directory above ``rel`` that
    holds a package marker; ``""`` (the repository root) when none does."""
    parts = PurePosixPath(rel).parts[:-1]
    for i in range(len(parts), 0, -1):
        d = repo_root.joinpath(*parts[:i])
        if any((d / m).is_file() for m in _PACKAGE_MARKERS):
            return "/".join(parts[:i])
    return ""


def _group_scope(repo_root: Path, scope: list[str]) -> dict[str, list[str]]:
    """Focus files keyed by package root, in first-seen order."""
    groups: dict[str, list[str]] = {}
    for f in scope:
        rel = PurePosixPath(Path(f).as_posix()).as_posix()
        groups.setdefault(_package_root(repo_root, rel), []).append(rel)
    return groups


def _to_package(pkg_rel: str, rel: str) -> str:
    return rel[len(pkg_rel) + 1:] if pkg_rel else rel


def _to_repo(pkg_rel: str, rel: str) -> str:
    return f"{pkg_rel}/{rel}" if pkg_rel else rel


# ── the scratch copy ─────────────────────────────────────────────────────────

def _copy_repo(repo_root: Path, dest: Path) -> bool:
    """Copy the working tree (tracked plus untracked-but-not-ignored files) to
    ``dest``. Falls back to a filtered tree copy outside a git repository.
    Symlinks are copied as links. Returns True when the copy came from a git
    listing."""
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=60,
            check=False)
        listed = [f for f in (proc.stdout or "").split("\0") if f] \
            if proc.returncode == 0 else []
    except (subprocess.TimeoutExpired, OSError):
        listed = []
    if not listed:
        shutil.copytree(repo_root, dest, ignore=_COPY_IGNORE, dirs_exist_ok=True,
                        symlinks=True)
        return False
    for rel in listed:
        if rel.split("/", 1)[0] in {".assess", "mutants"}:
            continue
        src = repo_root / rel
        target = dest / rel
        if src.is_symlink():
            # git tracks a link as one entry; recreate it, never its target's
            # contents (a directory link would otherwise be skipped as no file).
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.readlink(src), target)
            continue
        if not src.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    return True


def _snapshot_git(work: Path, deadline: float) -> None:
    """Commit the copy to a fresh repository, so a test that asks git about
    the repository (``git show HEAD:...``, ``git ls-files``) finds the copied
    tree. Best effort: the user's git config, hooks and signing are kept out,
    and any failure leaves a plain copy."""
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    ident = ["-c", "user.name=assess", "-c", "user.email=assess@localhost",
             "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}"]
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", *ident, "commit", "-q", "--no-verify", "-m", "snapshot"]):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            proc = subprocess.run(cmd, cwd=str(work), env=env, capture_output=True,
                                  text=True, timeout=min(60.0, remaining), check=False)
        except (subprocess.TimeoutExpired, OSError):
            return
        if proc.returncode != 0:
            return


def _prepare_group(work: Path, pkg_rel: str, rel_scope: list[str],
                   snapshot_by: float | None) -> str | None:
    """Ready ``work`` for one group's run; returns a reason it cannot run.

    A package with its own config runs it unchanged. Any other package gets a
    generated section whose ``also_copy`` lists its own entries, after the
    ancestors are mirrored down (``_mirror_ancestors``); the git snapshot
    comes last so it records the mirrored tree and the generated section,
    leaving the copy clean against ``HEAD`` (``snapshot_by`` is its
    deadline; None when the copy did not come from git and needs none)."""
    pkg = work / pkg_rel if pkg_rel else work
    generate = bool(rel_scope) and not _mutmut3_reads_config(pkg)
    if rel_scope and not generate:
        gap = _repo_config_gap(pkg, pkg_rel, rel_scope)
        if gap:
            return gap
    own = _package_entries(pkg, [Path(f).parts[0] for f in rel_scope]) if generate else []
    if generate:
        _mirror_ancestors(work, pkg_rel)
        with open(pkg / "setup.cfg", "a", encoding="utf-8") as fh:
            fh.write("\n" + _mutmut3_config(rel_scope, own))
    if snapshot_by is not None:
        _snapshot_git(work, snapshot_by)
    return None


def _group_record(pkg_rel: str, config: str, scope: list[str]) -> dict:
    return {"root": pkg_rel or ".", "config": config, "scope": scope}


def _run_group(repo_root: Path, pkg_rel: str, rel_scope: list[str],
               deadline: float) -> tuple[dict, list[dict]]:
    """One package's run in its own scratch copy. Returns the group record
    and its per-file results (repo-relative paths, focus files only)."""
    pkg_on_disk = repo_root / pkg_rel if pkg_rel else repo_root
    config = "repo" if _mutmut3_reads_config(pkg_on_disk) else "generated"
    record = _group_record(pkg_rel, config, [_to_repo(pkg_rel, f) for f in rel_scope])
    spelled = (_config_spellings(pkg_on_disk, rel_scope) if config == "repo"
               else {f: f for f in rel_scope})
    cfg_scope = list(spelled.values())
    back = {v: k for k, v in spelled.items()}
    timed_out = {**record, "mutation_run": False,
                 "reason": f"exceeded {MUTATION_TIMEOUT}s timeout"}
    # ignore_cleanup_errors: a read-only directory carried over by the copy, or
    # debris from the test run, must not raise on the way out of the block
    # and turn a named result into a generic scan failure.
    with tempfile.TemporaryDirectory(prefix="assess-mutmut-",
                                     ignore_cleanup_errors=True) as tmp:
        work = Path(tmp) / "repo"
        work.mkdir()
        if deadline - time.monotonic() <= 0:
            return timed_out, []
        try:
            from_git = _copy_repo(repo_root, work)
            gap = _prepare_group(work, pkg_rel, cfg_scope,
                                 deadline if from_git else None)
            if gap:
                return {**record, "mutation_run": False, "reason": gap}, []
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return timed_out, []
            pkg = work / pkg_rel if pkg_rel else work
            proc = subprocess.run(
                ["mutmut", "run"], cwd=str(pkg), capture_output=True,
                text=True, timeout=remaining, check=False)
        except subprocess.TimeoutExpired:
            return timed_out, []
        except OSError as e:
            return {**record, "mutation_run": False, "reason": str(e)}, []
        per_file = _parse_mutmut3_meta(pkg / "mutants")
        no_records = _no_records_reason("mutmut", proc, scratch=work)
    return _group_result(record, pkg_rel, cfg_scope, per_file, no_records, back)


def _group_result(record: dict, pkg_rel: str, rel_scope: list[str],
                  per_file: list[dict], no_records: str,
                  back: dict[str, str]) -> tuple[dict, list[dict]]:
    """``rel_scope`` and the parsed files use the config's spelling; ``back``
    maps that spelling to the package-relative path the focus set named."""
    produced = len(per_file)
    if rel_scope:
        wanted = set(rel_scope)
        per_file = [p for p in per_file if p["file"] in wanted]
    per_file = [{**p, "file": _to_repo(pkg_rel, back.get(p["file"], p["file"]))}
                for p in per_file]
    if not rel_scope:
        record["scope"] = [p["file"] for p in per_file]
    if not per_file:
        reason = (f"mutmut produced mutants for {produced} file(s), none of "
                  f"them in the focus set") if produced else no_records
        return {**record, "mutation_run": False, "reason": reason}, []
    return {**record, "mutation_run": True}, per_file


def _run_mutmut3(repo_root: Path, scope: list[str]) -> dict:
    """The mutmut 3 pass: one run per package root, each in a scratch copy.

    Only the focus files are reported, so the result's ``scope`` names the
    files its figures describe (with no scope given, one run at the repository
    root under whatever config it has, and the scope is rebuilt from the files
    that were measured). ``groups`` records each package's run; the pass counts
    as run when any group recovered mutant records, and when none did its
    ``reason`` names each group's cause. Same result shape and the same
    no-records-no-run rule as ``run_bounded_mutation``."""
    deadline = time.monotonic() + MUTATION_TIMEOUT
    grouped = _group_scope(repo_root, scope) if scope else {"": []}
    groups: list[dict] = []
    per_file: list[dict] = []
    for pkg_rel, files in grouped.items():
        rel_scope = [_to_package(pkg_rel, f) for f in files]
        record, results = _run_group(repo_root, pkg_rel, rel_scope, deadline)
        groups.append(record)
        per_file += results
    out_scope = scope or [f for g in groups for f in g["scope"]]
    result = {"available": True, "tool": "mutmut", "scope": out_scope,
              "groups": groups, "mutation_run": bool(per_file), "per_file": per_file}
    if not per_file:
        reasons = [g["reason"] for g in groups]
        result["reason"] = reasons[0] if len(groups) == 1 else "; ".join(
            f"{g['root']}: {g['reason']}" for g in groups)
    return result
