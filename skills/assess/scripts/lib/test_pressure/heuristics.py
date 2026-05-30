"""Cheap, always-on hollow-test heuristics (candidate signals only).

Three syntactic fingerprints of hollow tests, each tuned for few false
positives because they are *candidates for human judgement, never verdicts*:

  1. **Assertion on internals** - a test that asserts on a private/internal
     field (`_x`, a Go unexported field, `#private`) with no assertion on any
     public side-effect. The "meridian resume-guard" fingerprint.
  2. **Untested boundaries** - `<=`/`<`/`>=`/`>`/`+1`/`-1` comparisons that are
     covered but, as far as we can tell, exercised on only one side.
  3. **Duplicate truth** - a *field* (class/instance attribute or module-level
     name) only ever assigned from another field. Two names for one fact.

Every signal here is a candidate. None is a verdict.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from .common import MAX_FINDINGS, _is_test_file, _iter_files, _read, _rel

CHEAP_HEURISTIC_NOTE = (
    "candidate signals for human judgement, never verdicts"
)


# ── heuristic 1: assertion on internal state ──────────────────────────────────

def _root_name(node: ast.AST) -> str:
    """Leftmost Name in an attribute chain (``a.b.c`` -> ``a``)."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else "?"


def _asserted_expressions(func: ast.AST) -> list[ast.AST]:
    """Expressions actually asserted on inside a test function. For unittest-style
    ``self.assertEqual(a, b)`` we take the args, not the assert* method name
    itself; for pytest ``assert expr`` we take the tested expression."""
    exprs: list[ast.AST] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            exprs.append(node.test)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr.startswith("assert")):
            exprs.extend(node.args)
    return exprs


def _classify_assertion(expr: ast.AST) -> tuple[list[tuple[str, str]], bool]:
    """Split an asserted expression into (private-field reads, saw_public_effect).

    Attributes in call-position (``x.method(...)``) are invocations, not field
    reads. A *private* such attribute is the subject under test (``mod._helper(
    ...)``), so it is neither an internal read nor a public effect; a *public*
    method call still counts as observable behaviour.
    """
    called_attr_ids = {
        id(sub.func) for sub in ast.walk(expr)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
    }
    internal: list[tuple[str, str]] = []  # (subject, field)
    has_public = False
    for sub in ast.walk(expr):
        if not isinstance(sub, ast.Attribute):
            continue
        name = sub.attr
        is_private = name.startswith("_") and not name.startswith("__")
        if is_private:
            if id(sub) in called_attr_ids:
                continue  # private helper invoked as the subject under test
            internal.append((_root_name(sub.value), name))
        elif not name.startswith("__"):
            has_public = True
    return internal, has_public


def _py_assertion_internal(path: Path, rel: str) -> list[dict]:
    """Python (AST): test functions that assert on a private ``_field`` but on no
    public attribute or method. Conservative - both conditions must hold.

    A private attribute used as the *callee* of a call (``mod._helper(...)``) is
    the subject under test, not internal state being read: testing a private
    helper directly is a legitimate, common pattern and is excluded. Only a
    private attribute *read as a value* counts as an internal-state assertion.
    """
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
        internal: list[tuple[str, str]] = []
        has_public = False
        for expr in _asserted_expressions(func):
            expr_internal, expr_public = _classify_assertion(expr)
            internal.extend(expr_internal)
            has_public = has_public or expr_public
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


def _target_field(node: ast.AST, in_function: bool) -> str | None:
    """Field name for an assignment target, or None if the target is not a
    *field*.

    A field is an instance/class attribute (``self.x`` -> ``x``) at any depth,
    or a module/class-level bare name (``x`` -> ``x``). A bare name assigned
    *inside a function body* is a transient local variable - ordinary aliasing
    like ``raw = result.stdout`` - and is explicitly NOT a duplicate-truth field.
    """
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name) and not in_function:
        return node.id
    return None


def _collect_field_assignments(
    tree: ast.AST,
) -> dict[str, list[tuple[bool, str | None]]]:
    """Walk the tree tracking lexical function scope so transient locals are
    excluded. Returns ``{field_name: [(is_derived, source), ...]}``."""
    assignments: dict[str, list[tuple[bool, str | None]]] = {}

    def visit(node: ast.AST, in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Assign):
                derived, src = _classify_rhs(child.value)
                for tgt in child.targets:
                    fieldname = _target_field(tgt, in_function)
                    if fieldname is not None:
                        assignments.setdefault(fieldname, []).append((derived, src))
            child_in_function = in_function or isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            visit(child, child_in_function)

    visit(tree, False)
    return assignments


def _py_duplicate_truth(path: Path, rel: str) -> list[dict]:
    try:
        tree = ast.parse(_read(path))
    except (SyntaxError, ValueError):
        return []
    assignments = _collect_field_assignments(tree)
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
    same field disqualifies it. ``this.x`` is already an instance attribute, so
    no scope filtering is needed here."""
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

    Scoped to *fields*: instance/class attributes (``self.x = self.y``) and
    module/class-level names. Function-local bare-name aliasing (``raw =
    result.stdout``, ``current = node.parent``) is a transient binding, not a
    duplicate source of truth, and is excluded.

    Returns ``[{file, field_name, derives_from, confidence}]``. Degrades per-file.
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
