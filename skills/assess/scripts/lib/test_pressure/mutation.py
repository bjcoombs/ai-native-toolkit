"""Mutation tier: config detection, bounded opt-in runs, survivor aggregation.

The only direct evidence that a test would catch a regression is to introduce
one and watch a test go red. We detect mutation-testing *configuration* as a
standing signal - a repo that runs mutation testing in CI has already answered
the question - and, only when explicitly opted in, run a time-boxed mutation
pass over the hottest files and report survivor density and clusters. Mutation
mutates and *runs* code, so it is never part of a default read-only assessment.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .common import _iter_files, _read

# ── tuning constants ────────────────────────────────────────────────────────

MUTATION_TIMEOUT = 300          # seconds; a mutation run is bounded or it degrades
MAX_FILES_TO_MUTATE = 5         # only the hottest files - mutation is O(mutants)
CLUSTER_MIN_SURVIVORS = 3       # files at/above this survivor count are a "cluster"
HIGH_COVERAGE = 0.80            # gap-signal threshold: line coverage this high ...
LOW_MUTATION_SCORE = 0.50       # ... paired with a mutation score this low = a gap
MAX_FINDINGS = 50               # per-heuristic cap so a pathological repo can't bloat

# ── mutation config detection ────────────────────────────────────────────────

# Exact config filenames -> tool. Presence of any of these is itself a signal
# that the project takes test strength seriously, independent of whether we run
# anything.
_MUTATION_CONFIG_FILES: dict[str, str] = {
    "stryker.conf.js": "stryker", "stryker.conf.json": "stryker",
    "stryker.conf.mjs": "stryker", "stryker.config.js": "stryker",
    "stryker.config.json": "stryker", "stryker.config.mjs": "stryker",
    "mutmut.toml": "mutmut", ".mutmut.toml": "mutmut",
    "cosmic-ray.toml": "cosmic-ray",
}

# Tokens that, when found in a CI file, indicate a mutation tool is invoked.
_MUTATION_CI_TOKENS: dict[str, str] = {
    "stryker": "stryker", "mutmut": "mutmut", "cosmic-ray": "cosmic-ray",
    "gremlins": "gremlins", "go-mutesting": "go-mutesting",
    "cargo-mutants": "cargo-mutants", "cargo mutants": "cargo-mutants",
}

_CI_FILE_NAMES = (".gitlab-ci.yml", ".gitlab-ci.yaml", "Jenkinsfile")


def detect_mutation_config(repo_root: Path) -> dict:
    """Detect mutation-testing configuration and CI integration. Never raises.

    Returns ``{present, tools, ci_integrated}``. ``present`` is true if any
    config file *or* CI invocation is found - presence of mutation testing is
    itself the signal, so a repo that merely configures it scores differently
    from one that has nothing. ``tools`` is the sorted union of tools seen in
    config files and CI; ``ci_integrated`` is true only when a CI file invokes
    one of them.
    """
    repo_root = Path(repo_root)
    config_tools: set[str] = set()
    ci_tools: set[str] = set()

    # 1. Config files at any depth.
    for path in _iter_files(repo_root):
        name = path.name
        if name in _MUTATION_CONFIG_FILES:
            config_tools.add(_MUTATION_CONFIG_FILES[name])
        elif name == "Cargo.toml":
            text = _read(path).lower()
            if "cargo-mutants" in text or "metadata.mutants" in text:
                config_tools.add("cargo-mutants")
        elif name == "pyproject.toml":
            text = _read(path).lower()
            if "[tool.mutmut]" in text:
                config_tools.add("mutmut")
            if "[tool.cosmic-ray]" in text or "[tool.cosmic_ray]" in text:
                config_tools.add("cosmic-ray")
        elif name == "setup.cfg":
            if "[mutmut]" in _read(path).lower():
                config_tools.add("mutmut")

    # 2. CI invocations.
    for ci_file in _ci_files(repo_root):
        text = _read(ci_file).lower()
        for token, tool in _MUTATION_CI_TOKENS.items():
            if token in text:
                ci_tools.add(tool)

    tools = sorted(config_tools | ci_tools)
    return {
        "present": bool(tools),
        "tools": tools,
        "ci_integrated": bool(ci_tools),
    }


def _ci_files(repo_root: Path) -> list[Path]:
    out: list[Path] = []
    wf_dir = repo_root / ".github" / "workflows"
    if wf_dir.is_dir():
        for p in wf_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in {".yml", ".yaml"}:
                out.append(p)
    for name in _CI_FILE_NAMES:
        p = repo_root / name
        if p.is_file():
            out.append(p)
    return out


# ── mutation output parsers ──────────────────────────────────────────────────

def _parse_stryker_json(stdout: str) -> list[dict]:
    """Stryker JSON report (mutation-testing-elements schema):
    ``{"files": {"<path>": {"mutants": [{"status": "Killed"|"Survived"|...}]}}}``."""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return []
    out: list[dict] = []
    for path, info in (data.get("files") or {}).items():
        killed = survived = 0
        for m in info.get("mutants", []):
            status = str(m.get("status", "")).lower()
            if status in {"killed", "timeout"}:
                killed += 1
            elif status in {"survived", "nocoverage", "no coverage"}:
                survived += 1
        total = killed + survived
        if total:
            out.append({"file": path, "killed": killed,
                        "survived": survived, "total": total})
    return out


def _parse_mutmut(stdout: str) -> list[dict]:
    """mutmut survivor listing: lines of ``<path>:<line>``. We can only see
    survivors, so killed and total are unknown (None) - density treats them as
    missing rather than guessing."""
    out: dict[str, int] = {}
    rx = re.compile(r"^([\w./\\-]+\.py):(\d+)")
    for line in stdout.splitlines():
        m = rx.match(line.strip())
        if m:
            out[m.group(1)] = out.get(m.group(1), 0) + 1
    return [{"file": f, "killed": None, "survived": n, "total": None}
            for f, n in out.items()]


def _testcase_file(testcase: ET.Element) -> str | None:
    """Derive the source file path for a mutmut junitxml ``<testcase>``.

    mutmut 2.x emits ``<testcase ... file="src/foo.py">`` - the file attribute
    is the source path directly. Some versions/tools instead encode it in
    ``classname`` as a dotted module path (``mutmut.src.foo`` -> ``src/foo.py``),
    so we fall back to that. Returns None when neither yields a path."""
    file_attr = testcase.get("file")
    if file_attr:
        return file_attr
    classname = testcase.get("classname") or ""
    if classname.startswith("mutmut."):
        dotted = classname[len("mutmut."):]
        if dotted:
            return dotted.replace(".", "/") + ".py"
    return None


def _parse_mutmut_junitxml(xml_path: Path) -> list[dict]:
    """Parse ``mutmut junitxml`` output into per-file killed/survived/total.

    Unlike the survivor-only stdout listing, junitxml reports *every* mutant -
    one ``<testcase>`` each - so we recover real totals. A testcase with a
    ``<failure>`` child is a survivor (the suite did not catch the mutation);
    otherwise it was killed. Returns ``list[{file, killed, survived, total}]``
    with ``total = killed + survived``. Never raises - returns ``[]`` on any
    error (missing file, malformed XML, unexpected shape) so the run degrades
    gracefully to the stdout fallback."""
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError, ValueError):
        return []
    per_file: dict[str, dict[str, int]] = {}
    try:
        for testcase in root.iter("testcase"):
            fname = _testcase_file(testcase)
            if not fname:
                continue
            survived = testcase.find("failure") is not None
            agg = per_file.setdefault(fname, {"killed": 0, "survived": 0})
            agg["survived" if survived else "killed"] += 1
    except Exception:  # pragma: no cover - defensive: never crash the run
        return []
    return [{"file": f, "killed": d["killed"], "survived": d["survived"],
             "total": d["killed"] + d["survived"]}
            for f, d in per_file.items()]


def _parse_gremlins(stdout: str) -> list[dict]:
    """gremlins per-mutant lines: ``KILLED|LIVED|TIMED OUT|NOT COVERED ... <file>:<line>``.
    LIVED / NOT COVERED == survived."""
    killed: dict[str, int] = {}
    survived: dict[str, int] = {}
    rx = re.compile(r"^(KILLED|LIVED|TIMED OUT|NOT COVERED)\s+.*?([\w./\\-]+\.go):\d+")
    for line in stdout.splitlines():
        m = rx.match(line.strip())
        if not m:
            continue
        status, fname = m.group(1), m.group(2)
        if status in {"LIVED", "NOT COVERED"}:
            survived[fname] = survived.get(fname, 0) + 1
        elif status == "KILLED":
            killed[fname] = killed.get(fname, 0) + 1
    return _merge_killed_survived(killed, survived)


def _parse_cargo_mutants(stdout: str) -> list[dict]:
    """cargo-mutants text outcomes: ``MISSED|CAUGHT|TIMEOUT|UNVIABLE ... <file>:<line>``.
    MISSED == survived; CAUGHT == killed; UNVIABLE/TIMEOUT ignored."""
    killed: dict[str, int] = {}
    survived: dict[str, int] = {}
    rx = re.compile(r"^(MISSED|CAUGHT|TIMEOUT|UNVIABLE)\s+.*?([\w./\\-]+\.rs):\d+")
    for line in stdout.splitlines():
        m = rx.match(line.strip())
        if not m:
            continue
        status, fname = m.group(1), m.group(2)
        if status == "MISSED":
            survived[fname] = survived.get(fname, 0) + 1
        elif status == "CAUGHT":
            killed[fname] = killed.get(fname, 0) + 1
    return _merge_killed_survived(killed, survived)


def _merge_killed_survived(killed: dict[str, int],
                           survived: dict[str, int]) -> list[dict]:
    out: list[dict] = []
    for fname in sorted(set(killed) | set(survived)):
        k = killed.get(fname, 0)
        s = survived.get(fname, 0)
        out.append({"file": fname, "killed": k, "survived": s, "total": k + s})
    return out


# ── bounded mutation run (opt-in only) ───────────────────────────────────────

# Per-tool run spec. `cmd` builds the argv given (repo_root, scoped_files);
# `parser` maps stdout -> list[{file, killed, survived, total}]. Tried in order;
# the first whose language is present and which is on PATH wins (config-detected
# tools are preferred via _select_mutation_tool).
_MUTATION_TOOLS: list[dict] = [
    {"language": "typescript", "tool": "stryker", "exts": {".ts", ".tsx", ".js", ".jsx"},
     "cmd": lambda root, files: ["stryker", "run", "--reporters", "json"],
     "parser": _parse_stryker_json},
    {"language": "python", "tool": "mutmut", "exts": {".py"},
     "cmd": lambda root, files: ["mutmut", "run"],
     "parser": _parse_mutmut},
    {"language": "go", "tool": "gremlins", "exts": {".go"},
     "cmd": lambda root, files: ["gremlins", "unleash"],
     "parser": _parse_gremlins},
    {"language": "rust", "tool": "cargo-mutants", "exts": {".rs"},
     "cmd": lambda root, files: ["cargo", "mutants", "--no-shuffle"],
     "parser": _parse_cargo_mutants},
]


def _has_ext(repo_root: Path, exts: set[str]) -> bool:
    for _ in _iter_files(repo_root, exts):
        return True
    return False


def _select_mutation_tool(repo_root: Path, detected_tools: list[str]) -> dict | None:
    """Pick a tool: prefer one whose config we detected, else any whose language
    is present. Must be on PATH (first argv token). Returns the spec or None."""
    by_pref = sorted(
        _MUTATION_TOOLS,
        key=lambda s: 0 if s["tool"] in detected_tools else 1,
    )
    for spec in by_pref:
        if not _has_ext(repo_root, spec["exts"]):
            continue
        if shutil.which(spec["cmd"](repo_root, [])[0]) is None:
            continue
        return spec
    return None


# ── mutmut 3.x adapter ───────────────────────────────────────────────────────
#
# mutmut 3 changed its command line: ``mutmut run`` takes no paths, reads its
# scope from ``setup.cfg``/``pyproject.toml`` in the working directory, aborts
# when neither names ``source_paths`` and it cannot guess them, dropped the
# ``junitxml`` subcommand, and records results as ``mutants/<path>.meta`` JSON.
# The adapter runs it in a scratch copy of the repo carrying a generated
# ``[mutmut]`` section, so the assessed tree is never written to.

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


# The second line of the sh trampoline written when the interpreter path is too
# long for a shebang (or holds a space). uv single-quotes the interpreter, pip
# (distlib) leaves it bare and double-quotes it only around a space:
#   '''exec' '/path/to/python' "$0" "$@"
#   '''exec' /path/to/python "$0" "$@"
#   '''exec' "/path with space/python" "$0" "$@"
_TRAMPOLINE_EXEC_RE = re.compile(
    r"""^\s*'''exec' (?:'([^']+)'|"([^"]+)"|(\S+))""")


def _launcher_interpreter(exe: str) -> str | None:
    """The Python interpreter a console-script launcher runs under: named by
    the shebang, else by the sh trampoline's exec line, else the ``python``
    beside the resolved launcher (a uv tool or pipx launcher is a symlink into
    its environment's ``bin``)."""
    try:
        with open(exe, "rb") as fh:
            head = fh.read(2048).decode("utf-8", "replace").splitlines()[:2]
    except OSError:
        return None
    candidates: list[str] = []
    if head and head[0].startswith("#!"):
        candidates.append(head[0][2:].strip().split(" ")[0])
    m = _TRAMPOLINE_EXEC_RE.match(head[1]) if len(head) > 1 else None
    if m:
        candidates.append(next(g for g in m.groups() if g))
    for c in candidates:
        if "python" in Path(c).name:
            return c
    sibling = Path(exe).resolve().parent / "python"
    return str(sibling) if sibling.is_file() else None


def _mutmut_major_from_metadata(exe: str) -> int | None:
    interpreter = _launcher_interpreter(exe)
    if not interpreter:
        return None
    try:
        proc = subprocess.run(
            [interpreter, "-c",
             "from importlib.metadata import version; print(version('mutmut'))"],
            capture_output=True, text=True, timeout=15, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    m = re.match(r"\s*(\d+)\.", proc.stdout or "")
    return int(m.group(1)) if m else None


def _mutmut_major_from_cli() -> int | None:
    """Read the major version off ``mutmut --version`` run in an empty
    directory. Observed: 3.0-3.5 print ``mutmut, version 3.5.0``; 3.6 loads its
    configuration at import and aborts with "Could not figure out where the
    code to mutate is" before it parses arguments; 2.x has a ``version``
    command but no such option, and says so."""
    try:
        with tempfile.TemporaryDirectory(prefix="assess-mutmut-probe-") as tmp:
            proc = subprocess.run(
                ["mutmut", "--version"], cwd=tmp, capture_output=True,
                text=True, timeout=30, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    m = re.search(r"version\s+(\d+)\.", out)
    if m:
        return int(m.group(1))
    if "Could not figure out where the code to mutate" in out:
        return 3
    if "No such option" in out:
        return 2
    return None


def _mutmut_major(exe: str | None) -> int | None:
    """Major version of the ``mutmut`` on PATH, or None when it cannot be told.

    The package metadata of the interpreter the launcher runs under is the
    direct answer; a launcher that names no interpreter (a Windows ``.exe``, a
    custom wrapper) falls back to what ``mutmut --version`` reveals. Getting
    this wrong matters: mutmut 3 sent down the in-place path writes a
    ``mutants/`` tree into the assessed repo."""
    if not exe:
        return None
    major = _mutmut_major_from_metadata(exe)
    return major if major is not None else _mutmut_major_from_cli()


def _mutmut3_config(scope: list[str]) -> str:
    """The ``[mutmut]`` setup.cfg section scoping a run to ``scope``.

    ``source_paths`` is the top-level directory of each file (mutmut copies it
    whole into ``mutants/`` so sibling imports still resolve); ``only_mutate``
    narrows mutation to the files themselves. mutmut 3.0-3.5 knows neither key:
    it reads ``paths_to_mutate`` (deprecated in 3.6, where ``source_paths``
    wins) and mutates every file under it, so the section carries both and
    ``_run_mutmut3`` drops out-of-scope files from the results."""
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
    return "\n".join(lines) + "\n"


def _copy_repo(repo_root: Path, dest: Path) -> None:
    """Copy the working tree (tracked plus untracked-but-not-ignored files) to
    ``dest``. Falls back to a filtered tree copy outside a git repository."""
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
        shutil.copytree(repo_root, dest, ignore=_COPY_IGNORE, dirs_exist_ok=True)
        return
    for rel in listed:
        if rel.split("/", 1)[0] in {".assess", "mutants"}:
            continue
        src = repo_root / rel
        if not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)


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


_MAX_REASON_DETAIL = 300        # chars of tool error kept in a stored reason


def _tool_error_line(proc: subprocess.CompletedProcess) -> str:
    """The most telling line of a failed tool run: the last non-blank stderr
    line (a Python traceback ends with the exception), else of stdout."""
    for stream in (proc.stderr, proc.stdout):
        lines = [ln.strip() for ln in (stream or "").splitlines() if ln.strip()]
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


# A section header on its own line, so a commented-out ``# [tool.mutmut]`` or a
# mention inside a string does not count as configuration.
_TOML_MUTMUT_SECTION_RE = re.compile(r"^[ \t]*\[tool\.mutmut\][ \t]*(#.*)?$", re.M)
_INI_MUTMUT_SECTION_RE = re.compile(r"^\[mutmut\][ \t]*$", re.M)


def _mutmut3_reads_config(root: Path) -> bool:
    """Whether mutmut 3 would find its own configuration in ``root``. It reads
    exactly two places: ``[tool.mutmut]`` in the root ``pyproject.toml``, else
    ``[mutmut]`` in the root ``setup.cfg``. A nested config file, a
    ``.mutmut.toml`` or a CI workflow that names mutmut is not configuration it
    can see, so ``detect_mutation_config`` (which counts all of those) is the
    wrong question here."""
    return bool(_TOML_MUTMUT_SECTION_RE.search(_read(root / "pyproject.toml"))
                or _INI_MUTMUT_SECTION_RE.search(_read(root / "setup.cfg")))


def _run_mutmut3(repo_root: Path, scope: list[str]) -> dict:
    """The mutmut 3 pass, in a scratch copy of the repo. A repo whose root
    config mutmut 3 can read keeps it; otherwise a generated ``[mutmut]``
    section limits the run to ``scope``. Either way only ``scope``'s files are
    reported, so the ``scope`` in the result is the set the figures describe
    (with no scope given, it is rebuilt from the files that were). Copying
    spends the same ``MUTATION_TIMEOUT`` budget as the run. Same result shape
    and the same no-records-no-run rule as ``run_bounded_mutation``."""
    base = {"available": True, "tool": "mutmut", "scope": scope}
    timed_out = {**base, "mutation_run": False, "per_file": [],
                 "reason": f"exceeded {MUTATION_TIMEOUT}s timeout"}
    started = time.monotonic()
    # ignore_cleanup_errors: a read-only directory carried over by the copy, or
    # debris from the test run, must not raise on the way out of the block
    # and turn a named result into a generic scan failure.
    with tempfile.TemporaryDirectory(prefix="assess-mutmut-",
                                     ignore_cleanup_errors=True) as tmp:
        work = Path(tmp) / "repo"
        work.mkdir()
        try:
            _copy_repo(repo_root, work)
            if scope and not _mutmut3_reads_config(work):
                with open(work / "setup.cfg", "a", encoding="utf-8") as fh:
                    fh.write("\n" + _mutmut3_config(scope))
            remaining = MUTATION_TIMEOUT - (time.monotonic() - started)
            if remaining <= 0:
                return timed_out
            proc = subprocess.run(
                ["mutmut", "run"], cwd=str(work), capture_output=True,
                text=True, timeout=remaining, check=False)
        except subprocess.TimeoutExpired:
            return timed_out
        except OSError as e:
            return {**base, "mutation_run": False, "per_file": [],
                    "reason": str(e)}
        per_file = _parse_mutmut3_meta(work / "mutants")
        no_records = _no_records_reason("mutmut", proc, scratch=work)
    produced = len(per_file)
    if scope:
        wanted = {Path(f).as_posix() for f in scope}
        per_file = [p for p in per_file if p["file"] in wanted]
    else:
        base["scope"] = [p["file"] for p in per_file]
    if not per_file:
        reason = (f"mutmut produced mutants for {produced} file(s), none of "
                  f"them in the focus set") if produced else no_records
        return {**base, "mutation_run": False, "per_file": [], "reason": reason}
    return {**base, "mutation_run": True, "per_file": per_file}


def run_bounded_mutation(repo_root: Path, hot_files: list | None = None,
                         opt_in: bool = False) -> dict:
    """Time-boxed, opt-in mutation pass over the hottest files. Never raises.

    Mutation testing mutates source and *runs* the suite, so it is never part of
    a default read-only assessment - ``opt_in`` must be explicitly true. When
    run, it is bounded by ``MUTATION_TIMEOUT`` and ``MAX_FILES_TO_MUTATE``.
    Degrades gracefully: no tool on PATH -> ``{mutation_run: False, available:
    False}``; a timeout or crash -> the same with a ``reason``. ``mutation_run``
    is True only when the parser recovered mutant records: a tool that runs but
    yields no parsed mutants returns ``mutation_run: False`` with a ``reason``.

    Returns ``{mutation_run, available, tool, scope, per_file, reason}`` (keys
    present as relevant).
    """
    repo_root = Path(repo_root)
    if not opt_in:
        return {"mutation_run": False, "available": False,
                "reason": "opt-in required: mutation testing mutates and runs code"}

    detected = detect_mutation_config(repo_root)["tools"]
    spec = _select_mutation_tool(repo_root, detected)
    if spec is None:
        return {"mutation_run": False, "available": False,
                "reason": "no supported mutation tool on PATH for languages present"}

    scope = [str(f) for f in (hot_files or [])][:MAX_FILES_TO_MUTATE]
    if spec["tool"] == "mutmut" and (_mutmut_major(shutil.which("mutmut")) or 0) >= 3:
        return _run_mutmut3(repo_root, scope)
    started = time.monotonic()
    try:
        proc = subprocess.run(
            spec["cmd"](repo_root, scope), cwd=str(repo_root),
            capture_output=True, text=True, timeout=MUTATION_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"mutation_run": False, "available": True, "tool": spec["tool"],
                "scope": scope, "per_file": [],
                "reason": f"exceeded {MUTATION_TIMEOUT}s timeout"}
    except (OSError, FileNotFoundError) as e:  # pragma: no cover - defensive
        return {"mutation_run": False, "available": True, "tool": spec["tool"],
                "scope": scope, "per_file": [], "reason": str(e)}

    try:
        per_file = spec["parser"](proc.stdout)
    except Exception:  # pragma: no cover - parser must never crash the run
        per_file = []

    # mutmut's stdout only lists survivors (no totals), so density can't be
    # derived from it. A second `mutmut junitxml` call reports every mutant -
    # giving real killed/survived/total per file. The two-step run shares the
    # MUTATION_TIMEOUT budget; junitxml is best-effort and falls back to the
    # survivor-only stdout parse on any failure or empty result.
    if spec["tool"] == "mutmut":
        remaining = MUTATION_TIMEOUT - (time.monotonic() - started)
        if remaining > 0:
            xml_per_file = _run_mutmut_junitxml(repo_root, remaining)
            if xml_per_file:
                per_file = xml_per_file

    # A returned subprocess is not a run: the tool can exit (cleanly or not)
    # without the parser recovering a single mutant - a stryker spec on a repo
    # whose runner is not wired, a JSON reporter writing to a file instead of
    # stdout. Claiming a run off an empty parse would lift the Layer 6 cap with
    # no evidence behind it, so only parsed mutant data counts.
    if not per_file:
        return {"mutation_run": False, "available": True, "tool": spec["tool"],
                "scope": scope, "per_file": [],
                "reason": _no_records_reason(spec["tool"], proc)}

    return {"mutation_run": True, "available": True, "tool": spec["tool"],
            "scope": scope, "per_file": per_file}


def _run_mutmut_junitxml(repo_root: Path, timeout: float) -> list[dict]:
    """Run ``mutmut junitxml`` (after a completed ``mutmut run``) and parse it
    into per-file totals. mutmut writes the XML report to stdout, so we capture
    it to a temp file and hand that to ``_parse_mutmut_junitxml``. Best-effort:
    returns ``[]`` on any failure (older mutmut without the subcommand, timeout,
    crash, malformed output) so the caller falls back to the stdout parse."""
    try:
        proc = subprocess.run(
            ["mutmut", "junitxml"], cwd=str(repo_root),
            capture_output=True, text=True, timeout=max(1.0, timeout), check=False,
        )
    except (subprocess.TimeoutExpired, OSError):  # pragma: no cover - defensive
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".xml", delete=False, encoding="utf-8") as fh:
            fh.write(proc.stdout)
            tmp_path = fh.name
        return _parse_mutmut_junitxml(Path(tmp_path))
    except OSError:  # pragma: no cover - defensive
        return []
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:  # pragma: no cover - defensive
                pass


# ── aggregation over per-file mutation results ────────────────────────────────

def compute_survivor_density(per_file: list[dict]) -> dict:
    """Survivors normalised by total mutants. Returns ``{overall, total_survived,
    total_mutants, by_file}``. ``overall`` is None when no totals are known
    (e.g. mutmut, which only lists survivors)."""
    total_survived = 0
    total_mutants = 0
    have_totals = False
    by_file: dict[str, int] = {}
    for f in per_file or []:
        survived = f.get("survived") or 0
        by_file[f.get("file", "?")] = survived
        total_survived += survived
        total = f.get("total")
        if total is not None:
            total_mutants += total
            have_totals = True
    overall = (total_survived / total_mutants) if (have_totals and total_mutants) else None
    return {
        "overall": overall,
        "total_survived": total_survived,
        "total_mutants": total_mutants if have_totals else None,
        "by_file": by_file,
    }


def identify_survivor_clusters(per_file: list[dict]) -> list[dict]:
    """Files whose survivor count clears ``CLUSTER_MIN_SURVIVORS`` - a cluster of
    survivors in one file points at a specific under-tested unit. Sorted by
    survivor count, descending."""
    clusters = [
        {"file": f.get("file", "?"), "survived": f.get("survived") or 0}
        for f in per_file or []
        if (f.get("survived") or 0) >= CLUSTER_MIN_SURVIVORS
    ]
    clusters.sort(key=lambda c: c["survived"], reverse=True)
    return clusters[:MAX_FINDINGS]


def compute_gap_signal(coverage: float | None,
                       mutation_score: float | None) -> str:
    """The decisive cross-signal: high line coverage paired with a low mutation
    score means the suite *runs* the code but doesn't *test* it. Returns
    ``"high coverage + low mutation score"`` | ``"no gap"`` | ``"not assessed"``."""
    if coverage is None or mutation_score is None:
        return "not assessed"
    if coverage >= HIGH_COVERAGE and mutation_score <= LOW_MUTATION_SCORE:
        return "high coverage + low mutation score"
    return "no gap"
