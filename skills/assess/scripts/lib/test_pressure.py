"""Write-side truth pressure: does the test suite actually pin behaviour down?

Coverage says a line *ran*; it never says an assertion would *fail* if that line
were wrong. This module gathers the signals that distinguish a suite which holds
the code to account from one that merely visits it. Three tiers, cheapest first,
each degrading to "not assessed" rather than ever blocking the assessment:

**Mutation tier (decisive, expensive, opt-in).** The only direct evidence that a
test would catch a regression is to introduce one and watch a test go red. We
detect mutation-testing *configuration* (stryker / mutmut / cosmic-ray /
gremlins / go-mutesting / cargo-mutants) as a standing signal - a repo that runs
mutation testing in CI has already answered the question - and, only when
explicitly opted in, run a time-boxed mutation pass over the hottest files and
report survivor density and clusters. Mutation mutates and *runs* code, so it is
never part of a default read-only assessment.

**Cheap-heuristic tier (always-on, candidate signals only).** Three syntactic
fingerprints of hollow tests, each tuned for few false positives because they
are *candidates for human judgement, never verdicts*:
  1. **Assertion on internals** - a test that asserts on a private/internal
     field (`_x`, a Go unexported field, `#private`) with no assertion on any
     public side-effect. The "meridian resume-guard" fingerprint: the test
     pins the implementation, not the contract, so a correct refactor breaks it
     while a behavioural regression slips past.
  2. **Untested boundaries** - `<=`/`<`/`>=`/`>`/`+1`/`-1` comparisons that are
     covered but, as far as we can tell, exercised on only one side. Off-by-one
     bugs live exactly here. Requires coverage data to say anything.
  3. **Duplicate truth** - a field only ever assigned from another field
     (`self.x = self.y (+ k)`), never independently computed. Two names for one
     fact: a test asserting on one says nothing about the other.

Boundary: every signal here is a *candidate*. None is a verdict. A surviving
mutant might be equivalent; an internal-field assertion might be the only thing
that *can* be observed; a derived field might be a deliberate cache. The output
is grist for human (or LLM) judgement, scoped and labelled as such.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from lib.doc_graph import EXCLUDE_DIRS

# ── tuning constants ────────────────────────────────────────────────────────

MUTATION_TIMEOUT = 300          # seconds; a mutation run is bounded or it degrades
MAX_FILES_TO_MUTATE = 5         # only the hottest files - mutation is O(mutants)
CLUSTER_MIN_SURVIVORS = 3       # files at/above this survivor count are a "cluster"
HIGH_COVERAGE = 0.80            # gap-signal threshold: line coverage this high ...
LOW_MUTATION_SCORE = 0.50       # ... paired with a mutation score this low = a gap
MAX_FINDINGS = 50               # per-heuristic cap so a pathological repo can't bloat

CHEAP_HEURISTIC_NOTE = (
    "candidate signals for human judgement, never verdicts"
)

# ── shared file walking ──────────────────────────────────────────────────────

_PY_TEST_RE = re.compile(r"(^test_.*\.py$|.*_test\.py$)")
_TS_TEST_RE = re.compile(r".*\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs)$")
_GO_TEST_RE = re.compile(r".*_test\.go$")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _iter_files(repo_root: Path, exts: set[str] | None = None) -> list[Path]:
    """Files under repo_root with EXCLUDE_DIRS pruned. Best-effort, never raises."""
    out: list[Path] = []
    try:
        walker = repo_root.rglob("*")
    except OSError:  # pragma: no cover - defensive
        return out
    for path in walker:
        try:
            if not path.is_file():
                continue
            rel = path.relative_to(repo_root)
        except OSError:  # pragma: no cover - broken symlink etc.
            continue
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if exts is not None and path.suffix.lower() not in exts:
            continue
        out.append(path)
    return out


def _is_test_file(path: Path) -> bool:
    name = path.name
    return bool(
        _PY_TEST_RE.match(name) or _TS_TEST_RE.match(name) or _GO_TEST_RE.match(name)
    )


def _rel(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:  # pragma: no cover
        return str(path)


# ════════════════════════════════════════════════════════════════════════════
# TASK 2 - mutation detection + bounded-run infrastructure
# ════════════════════════════════════════════════════════════════════════════

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


# ── bounded mutation run (opt-in only) ───────────────────────────────────────

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


def run_bounded_mutation(repo_root: Path, hot_files: list | None = None,
                         opt_in: bool = False) -> dict:
    """Time-boxed, opt-in mutation pass over the hottest files. Never raises.

    Mutation testing mutates source and *runs* the suite, so it is never part of
    a default read-only assessment - ``opt_in`` must be explicitly true. When
    run, it is bounded by ``MUTATION_TIMEOUT`` and ``MAX_FILES_TO_MUTATE``.
    Degrades gracefully: no tool on PATH -> ``{mutation_run: False, available:
    False}``; a timeout or crash -> the same with a ``reason``.

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
    return {"mutation_run": True, "available": True, "tool": spec["tool"],
            "scope": scope, "per_file": per_file}


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


# ════════════════════════════════════════════════════════════════════════════
# TASK 3 - cheap, always-on hollow-test heuristics
# ════════════════════════════════════════════════════════════════════════════

# ── heuristic 1: assertion on internal state ──────────────────────────────────

def _root_name(node: ast.AST) -> str:
    """Leftmost Name in an attribute chain (``a.b.c`` -> ``a``)."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else "?"


def _py_assertion_internal(path: Path, rel: str) -> list[dict]:
    """Python (AST): test functions that assert on a private ``_field`` but on no
    public attribute or method. Conservative - both conditions must hold."""
    try:
        tree = ast.parse(_read(path))
    except (SyntaxError, ValueError):
        return []
    findings: list[dict] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not func.name.startswith("test"):
            continue
        # Gather the expressions that are actually asserted on. For unittest-style
        # `self.assertEqual(a, b)` we look at the args, not the assert* method
        # name itself; for pytest `assert expr` we look at the tested expression.
        assert_exprs: list[ast.AST] = []
        for node in ast.walk(func):
            if isinstance(node, ast.Assert):
                assert_exprs.append(node.test)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr.startswith("assert")):
                assert_exprs.extend(node.args)
        internal: list[tuple[str, str]] = []  # (subject, field)
        has_public = False
        for expr in assert_exprs:
            for sub in ast.walk(expr):
                if isinstance(sub, ast.Attribute):
                    name = sub.attr
                    if name.startswith("_") and not name.startswith("__"):
                        internal.append((_root_name(sub.value), name))
                    elif not name.startswith("__"):
                        has_public = True
        if internal and not has_public:
            seen: set[str] = set()
            for subject, fieldname in internal:
                if fieldname in seen:
                    continue
                seen.add(fieldname)
                findings.append({
                    "test_file": rel, "subject_function": f"{func.name}:{subject}",
                    "internal_field": fieldname, "confidence": "medium",
                })
    return findings


_TS_EXPECT_INTERNAL_RE = re.compile(
    r"expect\s*\(\s*[\w.$\[\]'\"]*?[.#](_\w+|#\w+|\w+)")
_GO_ASSERT_INTERNAL_RE = re.compile(
    r"(?:assert|require)\.\w+\([^)]*?\b\w+\.([a-z]\w*)")


def _regex_assertion_internal(path: Path, rel: str, lang: str) -> list[dict]:
    """TS/JS and Go: conservative regex for assertions reading a private field.
    Lower confidence than the Python AST path - no per-test public-side-effect
    check, just the presence of an internal-field assertion."""
    text = _read(path)
    findings: list[dict] = []
    seen: set[str] = set()
    if lang == "ts":
        # Only flag genuinely private accessors: _underscore or #private.
        rx = re.compile(r"expect\s*\(\s*[\w.$\[\]'\"]*?[.#](_\w+|#\w+)")
        for m in rx.finditer(text):
            fld = m.group(1)
            if fld in seen:
                continue
            seen.add(fld)
            findings.append({"test_file": rel, "subject_function": "(file)",
                             "internal_field": fld, "confidence": "low"})
    elif lang == "go":
        for m in _GO_ASSERT_INTERNAL_RE.finditer(text):
            fld = m.group(1)
            if fld in seen:
                continue
            seen.add(fld)
            findings.append({"test_file": rel, "subject_function": "(file)",
                             "internal_field": fld, "confidence": "low"})
    return findings


def detect_assertion_on_internal(repo_root: Path,
                                 test_files: list | None = None) -> list[dict]:
    """Tests that pin private/internal state instead of public behaviour.

    The meridian resume-guard fingerprint. Returns
    ``[{test_file, subject_function, internal_field, confidence}]``. Degrades
    per-file: a parse failure on one test file skips it, never the whole scan.
    """
    repo_root = Path(repo_root)
    if test_files is None:
        files = [p for p in _iter_files(
            repo_root, {".py", ".ts", ".tsx", ".js", ".jsx", ".go"})
            if _is_test_file(p)]
    else:
        files = [Path(f) for f in test_files]
    findings: list[dict] = []
    for path in files:
        rel = _rel(repo_root, path)
        try:
            if path.suffix == ".py":
                findings.extend(_py_assertion_internal(path, rel))
            elif path.suffix in {".ts", ".tsx", ".js", ".jsx"}:
                findings.extend(_regex_assertion_internal(path, rel, "ts"))
            elif path.suffix == ".go":
                findings.extend(_regex_assertion_internal(path, rel, "go"))
        except Exception:  # pragma: no cover - never crash the assessment
            continue
        if len(findings) >= MAX_FINDINGS:
            break
    return findings[:MAX_FINDINGS]


# ── heuristic 2: untested boundaries ──────────────────────────────────────────

_CMP_SYMBOL = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}
_BOUNDARY_RE = re.compile(r"(<=|>=|<|>|\+\s*1\b|-\s*1\b)")


def _normalise_coverage(coverage_data) -> set[tuple[str, int]] | None:
    """Accept {relpath: [lines]} or {relpath: {line: hits}} -> {(relpath, line)}.
    None stays None (we cannot assess boundaries without it). A reserved
    ``_overall`` key (the line-coverage ratio) is ignored here."""
    if coverage_data is None:
        return None
    covered: set[tuple[str, int]] = set()
    try:
        for fname, lines in coverage_data.items():
            if fname == "_overall":
                continue
            if isinstance(lines, dict):
                iterable = (ln for ln, hits in lines.items() if hits)
            else:
                iterable = lines
            for ln in iterable:
                covered.add((str(fname), int(ln)))
    except (AttributeError, TypeError, ValueError):  # pragma: no cover - defensive
        return set()
    return covered


def _py_boundaries(path: Path, rel: str,
                   covered: set[tuple[str, int]]) -> list[dict]:
    try:
        tree = ast.parse(_read(path))
    except (SyntaxError, ValueError):
        return []
    out: list[dict] = []
    for node in ast.walk(tree):
        op_symbol = None
        if isinstance(node, ast.Compare):
            for op in node.ops:
                if type(op) in _CMP_SYMBOL:
                    op_symbol = _CMP_SYMBOL[type(op)]
                    break
        elif (isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub))
              and isinstance(node.right, ast.Constant) and node.right.value == 1):
            op_symbol = "+1" if isinstance(node.op, ast.Add) else "-1"
        if op_symbol is None:
            continue
        line = getattr(node, "lineno", None)
        if line is None or (rel, line) not in covered:
            continue
        out.append({"file": rel, "line": line, "operator": op_symbol,
                    "covered": True, "boundary_tested": False})
    return out


def _regex_boundaries(path: Path, rel: str,
                      covered: set[tuple[str, int]]) -> list[dict]:
    out: list[dict] = []
    for i, line in enumerate(_read(path).splitlines(), start=1):
        if (rel, i) not in covered:
            continue
        m = _BOUNDARY_RE.search(line)
        if m:
            op = re.sub(r"\s+", "", m.group(1))
            out.append({"file": rel, "line": i, "operator": op,
                        "covered": True, "boundary_tested": False})
    return out


def detect_untested_boundaries(repo_root: Path, coverage_data=None) -> list[dict]:
    """Boundary comparisons that are covered but, as far as we can tell, exercised
    on only one side. Off-by-one territory.

    Requires ``coverage_data`` (``{relpath: [lines]}`` or ``{relpath: {line:
    hits}}``) - without it we cannot say a boundary was reached, so the result is
    empty. ``boundary_tested`` is always False here: line coverage cannot prove
    both sides of a comparison were exercised, so every hit is a *candidate*.
    Returns ``[{file, line, operator, covered, boundary_tested}]``.
    """
    repo_root = Path(repo_root)
    covered = _normalise_coverage(coverage_data)
    if not covered:
        return []
    findings: list[dict] = []
    for path in _iter_files(repo_root, {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"}):
        if _is_test_file(path):
            continue
        rel = _rel(repo_root, path)
        try:
            if path.suffix == ".py":
                findings.extend(_py_boundaries(path, rel, covered))
            else:
                findings.extend(_regex_boundaries(path, rel, covered))
        except Exception:  # pragma: no cover - never crash
            continue
        if len(findings) >= MAX_FINDINGS:
            break
    return findings[:MAX_FINDINGS]


# ── heuristic 3: duplicate truth ──────────────────────────────────────────────

def _classify_rhs(node: ast.AST) -> tuple[bool, str | None]:
    """Is RHS a derivation of another single field? Returns (is_derived, source).
    Derived := a bare Name/Attribute, or ``<Name/Attribute> +/- <constant>``."""
    if isinstance(node, ast.Name):
        return True, node.id
    if isinstance(node, ast.Attribute):
        return True, node.attr
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        left_derived, src = _classify_rhs(node.left)
        if left_derived and isinstance(node.right, ast.Constant):
            return True, src
    return False, None


def _target_field(node: ast.AST) -> str | None:
    """Field name for an assignment target: ``self.x`` -> ``x``, bare ``x`` -> ``x``."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _py_duplicate_truth(path: Path, rel: str) -> list[dict]:
    try:
        tree = ast.parse(_read(path))
    except (SyntaxError, ValueError):
        return []
    assignments: dict[str, list[tuple[bool, str | None]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        derived, src = _classify_rhs(node.value)
        for tgt in node.targets:
            fieldname = _target_field(tgt)
            if fieldname is None:
                continue
            assignments.setdefault(fieldname, []).append((derived, src))
    findings: list[dict] = []
    for fieldname, recs in assignments.items():
        if not recs:
            continue
        # All assignments must be derivations from a single consistent source
        # that is a different field. Any independent assignment disqualifies it.
        if not all(derived for derived, _ in recs):
            continue
        sources = {src for _, src in recs if src is not None}
        if len(sources) != 1:
            continue
        source = sources.pop()
        if source == fieldname:
            continue
        findings.append({"file": rel, "field_name": fieldname,
                         "derives_from": source, "confidence": "medium"})
    return findings


_TS_DERIVE_RE = re.compile(r"this\.(\w+)\s*=\s*this\.(\w+)\s*(?:[+-]\s*\d+\s*)?;")
_TS_ANY_ASSIGN_RE = re.compile(r"this\.(\w+)\s*=")


def _ts_duplicate_truth(path: Path, rel: str) -> list[dict]:
    """TS/JS regex: ``this.x = this.y (+ k)`` where *every* assignment of ``x``
    matches the derive pattern. Conservative - any non-derive assignment of the
    same field disqualifies it."""
    text = _read(path)
    derive_sources: dict[str, set[str]] = {}
    derive_count: dict[str, int] = {}
    for m in _TS_DERIVE_RE.finditer(text):
        fieldname = m.group(1)
        derive_sources.setdefault(fieldname, set()).add(m.group(2))
        derive_count[fieldname] = derive_count.get(fieldname, 0) + 1
    total_assign: dict[str, int] = {}
    for m in _TS_ANY_ASSIGN_RE.finditer(text):
        total_assign[m.group(1)] = total_assign.get(m.group(1), 0) + 1
    findings: list[dict] = []
    for fieldname, sources in derive_sources.items():
        # Every assignment of the field must be a derivation.
        if total_assign.get(fieldname, 0) != derive_count.get(fieldname, 0):
            continue
        if len(sources) != 1:
            continue
        source = next(iter(sources))
        if source == fieldname:
            continue
        findings.append({"file": rel, "field_name": fieldname,
                         "derives_from": source, "confidence": "low"})
    return findings


def detect_duplicate_truth(repo_root: Path) -> list[dict]:
    """Fields only ever assigned from another field, never independently computed
    - two names for one fact. A test asserting on one says nothing about the
    other; a refactor that decouples them silently breaks the invariant.

    Returns ``[{file, field_name, derives_from, confidence}]``. The PRD flags
    this as a possible Layer 2 (coupling) signal; it lives here because the
    detection is a single-file AST pass identical in shape to the other cheap
    heuristics. The wiring teammate can cross-reference it from Layer 2 rather
    than recomputing. Degrades per-file.
    """
    repo_root = Path(repo_root)
    findings: list[dict] = []
    for path in _iter_files(repo_root, {".py", ".ts", ".tsx", ".js", ".jsx"}):
        if _is_test_file(path):
            continue
        rel = _rel(repo_root, path)
        try:
            if path.suffix == ".py":
                findings.extend(_py_duplicate_truth(path, rel))
            else:
                findings.extend(_ts_duplicate_truth(path, rel))
        except Exception:  # pragma: no cover - never crash
            continue
        if len(findings) >= MAX_FINDINGS:
            break
    return findings[:MAX_FINDINGS]


def compute_cheap_heuristics(repo_root: Path, coverage_data=None) -> dict:
    """Aggregate the three always-on hollow-test heuristics. Never raises - each
    detector degrades independently, so a failure in one still returns the
    others. Every finding is a *candidate*, flagged by ``confidence_note``.
    """
    repo_root = Path(repo_root)
    return {
        "assertion_on_internal": detect_assertion_on_internal(repo_root),
        "untested_boundaries": detect_untested_boundaries(repo_root, coverage_data),
        "duplicate_truth": detect_duplicate_truth(repo_root),
        "confidence_note": CHEAP_HEURISTIC_NOTE,
    }


# ════════════════════════════════════════════════════════════════════════════
# Public entry point
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class TestPressureResult:
    mutation_config_present: bool = False
    mutation_tools_detected: list = field(default_factory=list)
    ci_integrated: bool = False
    mutation_run: bool = False
    mutation_scope: list = field(default_factory=list)
    per_file: list = field(default_factory=list)
    survivor_density: dict = field(default_factory=dict)
    survivor_clusters: list = field(default_factory=list)
    gap_signal: str = "not assessed"
    cheap_heuristics: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "mutation_config_present": self.mutation_config_present,
            "mutation_tools_detected": self.mutation_tools_detected,
            "ci_integrated": self.ci_integrated,
            "mutation_run": self.mutation_run,
            "mutation_scope": self.mutation_scope,
            "per_file": self.per_file,
            "survivor_density": self.survivor_density,
            "survivor_clusters": self.survivor_clusters,
            "gap_signal": self.gap_signal,
            "cheap_heuristics": self.cheap_heuristics,
        }


def _overall_coverage(coverage_data) -> float | None:
    """Reduce coverage_data to a single line-coverage ratio, if supplied.
    We only have covered lines, not total lines, so we cannot compute a true
    ratio here - return None unless an explicit ``{"_overall": ratio}`` is
    present. Kept separate so the wiring teammate can pass a real ratio."""
    if isinstance(coverage_data, dict):
        overall = coverage_data.get("_overall")
        if isinstance(overall, (int, float)):
            return float(overall)
    return None


def scan_test_pressure(repo_root: Path, hot_files: list | None = None,
                       opt_in: bool = False, coverage_data=None) -> dict:
    """Top-level Layer-1 write-side scan. Merges the mutation tier and the cheap
    always-on heuristics into one ``test_pressure`` block ready to drop into
    run-context.json. Never raises.

    ``opt_in`` gates the (mutating, code-running) bounded mutation pass; the
    cheap heuristics and config detection always run. ``coverage_data``
    (optional) feeds both the boundary heuristic and the mutation gap signal -
    absent, both report "not assessed" rather than guessing.
    """
    repo_root = Path(repo_root)

    config = detect_mutation_config(repo_root)
    mutation = run_bounded_mutation(repo_root, hot_files, opt_in=opt_in)
    per_file = mutation.get("per_file", [])
    density = compute_survivor_density(per_file)
    clusters = identify_survivor_clusters(per_file)

    coverage = _overall_coverage(coverage_data)
    mutation_score = (1.0 - density["overall"]) if density["overall"] is not None else None
    gap = compute_gap_signal(coverage, mutation_score)

    cheap = compute_cheap_heuristics(repo_root, coverage_data)

    result = TestPressureResult(
        mutation_config_present=config["present"],
        mutation_tools_detected=config["tools"],
        ci_integrated=config["ci_integrated"],
        mutation_run=mutation.get("mutation_run", False),
        mutation_scope=mutation.get("scope", []),
        per_file=per_file,
        survivor_density=density,
        survivor_clusters=clusters,
        gap_signal=gap,
        cheap_heuristics=cheap,
    )
    block = result.as_dict()
    # Surface why a mutation run didn't happen, for the report prose.
    if "reason" in mutation:
        block["mutation_note"] = mutation["reason"]
    return block
