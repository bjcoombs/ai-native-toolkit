"""Tests for lib.dart_complexity, the approximate Dart per-function scanner
(issue #364)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from lib import dart_complexity
from lib.dart_complexity import dart_function_scores, scan_dart_functions

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
    f.write_text(ROUTE_ORDER)
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


@pytest.mark.parametrize("text", [
    "void f() {" + "{" * 200_000,
    "void f() {" * 100_000,
    "void f() {" * 50_000 + "}" * 50_000,
    "void f() { var s = '" + "if (x) " * 50_000,
    "void f() { var s = '''" + "${" * 50_000,
    "/*" * 100_000,
    "void f() { if (a && b || c) {} } " * 10_000,
    "x" * 1_000_000,
], ids=["deep-nesting", "unclosed-functions", "nested-functions", "unterminated-string", "unterminated-interpolation",
        "nested-comment", "long-line", "one-token"])
def test_dart_scanner_pathological_input_is_fast(text: str) -> None:
    start = time.perf_counter()
    scan_dart_functions(text)
    assert time.perf_counter() - start < 1.0


def test_dart_scanner_reads_a_bounded_prefix(tmp_path: Path,
                                             monkeypatch) -> None:
    monkeypatch.setattr(dart_complexity, "_READ_BYTES", 64)
    f = tmp_path / "big.dart"
    f.write_text("int a() { return 1; }\n" + " " * 100
                 + "int b(x) { if (x) {} }\n")
    assert dart_function_scores(f) == ([1.0], "a")


def test_dart_scanner_unreadable_or_empty_file_has_no_functions(
        tmp_path: Path) -> None:
    assert dart_function_scores(tmp_path / "missing.dart") == ([], None)
    empty = tmp_path / "empty.dart"
    empty.write_text("// only a comment\n")
    assert dart_function_scores(empty) == ([], None)
