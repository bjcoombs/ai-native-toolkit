"""Tests for lib.complexity.dart_complexity, the approximate Dart per-function scanner
(issue #364)."""
from __future__ import annotations

import gc
import time
from collections.abc import Callable
from pathlib import Path

import pytest

import lib.complexity.dart_complexity as dart_complexity
from lib.complexity.dart_complexity import dart_function_scores, scan_dart_functions

# The acceptance fixture: routeOrder has 10 decision points in its own body and
# 1 in a nested closure (ccn 12 with the closure folded in), behind about 20
# decoy keywords and unbalanced braces in comments and strings.
ROUTE_ORDER = """\
// Decoys: if (a && b) { while (x) { for (;;) {} } }
/* block comment: if else if while for && || { */
int helper(int v) {
  return v + 1;
}

int routeOrder(int a, int b, List<int> items) {
  var label = "if (a && b) { while (true) { ";
  var banner = \"\"\"
    if (x) { for (;;) { while (y || z) {
  \"\"\";
  // if (total > 5 && a < b) { while (true) {
  var total = 0;
  if (a > 0 && b > 0) {
    total += 1;
  } else if (a < 0 || b < 0) {
    total -= 1;
  }
  for (var i = 0; i < a; i++) {
    if (i == b) {
      total += i;
    }
  }
  while (total > 100) {
    total -= 10;
  }
  if (items.isEmpty) {
    return total;
  }
  for (final item in items) {
    if (item > total) {
      total = item;
    }
  }
  final bump = (int v) {
    if (v > 3) {
      return v + helper(v);
    }
    return v;
  };
  return bump(total) + label.length + banner.length;
}
"""


def test_dart_scanner_scores_route_order_fixture(tmp_path: Path) -> None:
    assert scan_dart_functions(ROUTE_ORDER) == [
        ("helper", 1.0), ("routeOrder", 12.0)]
    f = tmp_path / "order.dart"
    f.write_text(ROUTE_ORDER, encoding="utf-8")
    assert dart_function_scores(f) == ([1.0, 12.0], "routeOrder")


def test_dart_scanner_skips_every_string_and_comment_form() -> None:
    """Raw, escaped, triple-quoted and nested-comment decoys add nothing;
    ${...} interpolation is code and counts."""
    src = r"""
int f(bool c, String x) {
  var a = r'if (x) { \' ;
  var b = 'it\'s if && { ';
  var d = r'''if { while ''';
  var e = '''if {
  for ( ''';
  /* outer /* inner if { */ still comment while { */
  var g = "${c ? 'if {' : "while"} $x if {";
  return 0;
}
"""
    assert scan_dart_functions(src) == [("f", 2.0)]


def test_dart_scanner_folds_closures_and_scores_named_locals_apart() -> None:
    src = """
void main() {
  items.forEach((i) { if (i > 0) {} });
  final xs = items.where((i) => i > 0 && i < 9);
  bool inner(int k) { while (k > 0) { k--; } return true; }
}
"""
    assert scan_dart_functions(src) == [("main", 3.0), ("inner", 2.0)]


def test_dart_scanner_scores_top_level_closures_as_anonymous() -> None:
    src = """
final isReady = (x) => x > 0 && ready;
final handler = (req) { if (req.ok) {} };
"""
    assert scan_dart_functions(src) == [
        ("<anonymous>", 2.0), ("<anonymous>", 2.0)]


def test_dart_scanner_names_getters_generics_and_arrow_bodies() -> None:
    src = """
class A<T> extends B<T> {
  int get size => n > 0 ? n : 0;
  set size(int v) { if (v < 0) throw 1; }
  Future<void> load<R>(R r) async { try { await f(); } on E catch (e) {} }
  String? label(int? n) => n?.toString() ?? 'none';
}
"""
    assert scan_dart_functions(src) == [
        ("size", 2.0), ("size", 2.0), ("load", 2.0), ("label", 2.0)]


@pytest.mark.parametrize(("src", "expected"), [
    # A raw string at a token start: its `${` and braces are text.
    ("void f() { var s = r'${if (a) {}'; g(); }", [("f", 1.0)]),
    # An `r` or a digit run directly before a quote is not a raw prefix.
    ("void f() { var s = bar'x'; if (a) {} }", [("f", 2.0)]),
    ("void f() { x = 1r'{'; }", [("f", 1.0)]),
    # A ternary `?` after a comment counts; after a word it does not.
    ("void f() { a /* c */ ? b : c; a/*c*/? b : c; }", [("f", 3.0)]),
    # Whitespace before `?` is any Unicode whitespace, a newline included.
    ("void f() { a = b\n? c : d; e = f\u00a0? g : h; }", [("f", 3.0)]),
    # A lone `/` is division; `??=` and `??` count, `?.` does not.
    ("void f() { x = a / b; y ??= z; w = v?.u ?? 0; }", [("f", 3.0)]),
    # Code inside `${...}`, braces included, belongs to the enclosing function.
    ("void f() { var s = 'x ${m.map((e) { if (e) {} return e; })} y'; }",
     [("f", 2.0)]),
    # Functions closed out of order still come back in source order.
    ("void a() { void b() => c ? 1 : 2; } void d() {}",
     [("a", 1.0), ("b", 2.0), ("d", 1.0)]),
], ids=["raw-string", "not-raw-after-word", "not-raw-after-digit",
        "ternary-after-comment", "ternary-after-unicode-space",
        "division-and-null-ops", "interpolation-closure", "source-order"])
def test_dart_scanner_run_boundaries_keep_the_token_rules(
        src: str, expected: list[tuple[str, float]]) -> None:
    """The fast path stops at quotes, `/`, `?` and raw prefixes (#967); each
    boundary scores as the one-token-at-a-time scanner did before it."""
    assert scan_dart_functions(src) == expected



# Pathological inputs, each as (generator of a size-n input, n, reps). The
# 1 s budget test scans gen(n) once; the scaling test scans gen(n) `reps` times
# against gen(n // 4) `4 * reps` times, so both sides do the same work when the
# scan is linear. `reps` keeps each side at about 50 ms on an M-series Mac and
# more on CI, so scheduler jitter of a millisecond or two moves the ratio by a
# few percent at most (#1125).
PATHOLOGICAL = {
    "deep-nesting": (lambda n: "void f() {" + "{" * n, 200_000, 2),
    "unclosed-functions": (lambda n: "void f() {" * n, 100_000, 1),
    "nested-functions": (lambda n: "void f() {" * n + "}" * n, 50_000, 1),
    "unterminated-string": (
        lambda n: "void f() { var s = '" + "if (x) " * n, 50_000, 64),
    "unterminated-interpolation": (
        lambda n: "void f() { var s = '''" + "${" * n, 50_000, 2),
    "nested-comment": (lambda n: "/*" * n, 100_000, 4),
    "long-line": (
        lambda n: "void f() { if (a && b || c) {} } " * n, 10_000, 2),
    "one-token": (lambda n: "x" * n, 1_000_000, 4),
    # A `=>` body still open when its enclosing function's `}` arrives: closing
    # the function first scanned `fns` on every `}`, quadratic in depth (#929).
    "nested-arrow-bodies": (
        lambda n: "void f() { g() => 1 " * n + "}" * n, 20_000, 1),
}

# Each side of the scaling ratio must take at least this long, so the clock's
# resolution and scheduler jitter stay small against it. A table entry whose
# small side falls under it needs a larger `reps` (#1125).
_MIN_SIDE_SECONDS = 0.02


def _fastest_cpu_seconds(text: str, reps: int) -> float:
    """CPU time of `reps` scans of ``text``, the fastest of 3 runs."""
    def once() -> float:
        start = time.process_time()
        for _ in range(reps):
            scan_dart_functions(text)
        return time.process_time() - start

    return min(once() for _ in range(3))


def _scaling_ratio(gen: Callable[[int], str], n: int,
                   reps: int) -> tuple[float, float, float]:
    """``(big / small, big, small)``: `reps` scans of gen(n) against
    `4 * reps` scans of gen(n // 4), each side the fastest of 5 runs.

    The clock is ``perf_counter``, not ``process_time``: on Windows
    ``process_time`` ticks every 15.6 ms, so a side of a few ticks reads
    one or two ticks off and crosses the bound (#1125). ``perf_counter``
    resolves well under a microsecond there. The runs alternate big and
    small, so a slow spell on the runner lands on both sides, and garbage
    collection is off while a run is timed. A linear scan reads about 1, a
    quadratic one about 4.
    """
    big_text, small_text = gen(n), gen(n // 4)

    def once(text: str, count: int) -> float:
        start = time.perf_counter()
        for _ in range(count):
            scan_dart_functions(text)
        return time.perf_counter() - start

    bigs: list[float] = []
    smalls: list[float] = []
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(5):
            bigs.append(once(big_text, reps))
            smalls.append(once(small_text, 4 * reps))
    finally:
        if was_enabled:
            gc.enable()
    big, small = min(bigs), min(smalls)
    return big / small, big, small


def test_dart_scanner_closes_an_open_arrow_body_with_its_enclosing_brace() -> None:
    """A `}` closes the `=>` bodies inside it and its own function, in either
    nesting shape, and the result stays in source order (#929)."""
    src = "void f() { g() => 1 }  int h() { k() => a ?? b }  void m() {}"
    assert scan_dart_functions(src) == [
        ("f", 1.0), ("g", 1.0), ("h", 1.0), ("k", 2.0), ("m", 1.0)]
    nested = "void f() { g() => 1 " * 3 + "}" * 3
    assert scan_dart_functions(nested) == [
        ("f", 1.0), ("g", 1.0)] * 3


# pytest-cov's line tracing (CI runs this suite under --cov) slows the scanner
# past the 1s budget; no_cover pauses tracing for this test so the budget stays
# exact instead of being widened to fit the tracer.
@pytest.mark.no_cover
@pytest.mark.cpu_budget
@pytest.mark.parametrize("case", list(PATHOLOGICAL))
def test_dart_scanner_pathological_input_is_fast(case: str) -> None:
    """Each pathological input scans in under 1 s, the fastest of 3 runs by CPU time.

    CPU time, not wall time, and the fastest of 3, to drop first-run and
    scheduler noise. CPU time still doubles when a busy xdist worker shares the
    core, so ``cpu_budget`` keeps this test in the serial pass (conftest.py,
    #930). The 1 s bound itself is unchanged.
    """
    gen, n, _reps = PATHOLOGICAL[case]
    assert _fastest_cpu_seconds(gen(n), 1) < 1.0


@pytest.mark.no_cover
@pytest.mark.cpu_budget
@pytest.mark.parametrize("case", list(PATHOLOGICAL))
def test_dart_scanner_pathological_input_scales_linearly(case: str) -> None:
    """A pathological input's scaling ratio time(n) / time(n/4) stays under 8 (#929).

    The small side runs four times the scans, so ``_scaling_ratio`` reads 1
    for a linear scan and 4 for a quadratic one; the bound of 2 (a per-scan
    ratio of 8) separates them with room for runner noise. The check compares
    the scanner with itself on the same machine at the same moment, so it
    holds on a slow runner where an absolute budget would not, and the 1 s
    budget above stays the absolute guard. Both sides must clear
    ``_MIN_SIDE_SECONDS``, so the measurement stays far above the clock's
    resolution (#1125).
    """
    gen, n, reps = PATHOLOGICAL[case]
    ratio, big, small = _scaling_ratio(gen, n, reps)
    assert small >= _MIN_SIDE_SECONDS, (
        f"{case}: small side {small:.4f} s is too short to time; raise reps")
    assert ratio < 2, (case, ratio, big, small)


def _close_brace_function_first(self: dart_complexity._Scanner) -> None:
    """``_on_close_brace`` as it was before #1078: the brace's own function
    closes before the `=>` bodies inside it, which makes `_close` scan `fns`
    on every `}`."""
    if self.interps:
        self.interps[-1][2] -= 1
    opened = self.braces.pop() if self.braces else None
    if opened is not None:
        self._close(opened)
    self._close_arrows()


@pytest.mark.no_cover
@pytest.mark.cpu_budget
def test_dart_scaling_ratio_catches_the_929_quadratic(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Kill test for the scaling bound (#1125): with the #929 quadratic put
    back, ``_scaling_ratio`` on nested `=>` bodies crosses the bound of 2.

    n is a fifth of the table's, so the quadratic scan stays near 0.25 s.
    """
    monkeypatch.setitem(dart_complexity._OP_HANDLERS, "}",
                        _close_brace_function_first)
    gen, _n, _reps = PATHOLOGICAL["nested-arrow-bodies"]
    ratio, big, small = _scaling_ratio(gen, 4_000, 1)
    assert small >= _MIN_SIDE_SECONDS, small
    assert ratio >= 2, (ratio, big, small)


def test_dart_scanner_reads_a_bounded_prefix(tmp_path: Path,
                                             monkeypatch) -> None:
    monkeypatch.setattr(dart_complexity, "_READ_BYTES", 64)
    f = tmp_path / "big.dart"
    f.write_text("int a() { return 1; }\n" + " " * 100
                 + "int b(x) { if (x) {} }\n", encoding="utf-8")
    assert dart_function_scores(f) == ([1.0], "a")


def test_dart_scanner_unreadable_or_empty_file_has_no_functions(
        tmp_path: Path) -> None:
    assert dart_function_scores(tmp_path / "missing.dart") == ([], None)
    empty = tmp_path / "empty.dart"
    empty.write_text("// only a comment\n", encoding="utf-8")
    assert dart_function_scores(empty) == ([], None)

