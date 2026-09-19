"""Dart capability entries (#352): linting and liveness for a Dart/Flutter repo.

A repository is Dart when it holds a ``pubspec.yaml`` outside excluded paths.
Linting is credited to the analyzer when the nearest ``analysis_options.yaml``
enables lint rules and honest-degrades naming ``dart analyze`` otherwise. Liveness always
honest-degrades naming the analyzer's built-in ``unused_*`` diagnostics, never a
third-party package, and ``dead_code.tools`` carries a matching Dart entry.
"""
from __future__ import annotations

import json
from pathlib import Path

from assess_core import build_run_context
from lib.dart_capabilities import scan_dart_capabilities
from lib.liveness_scan import scan_liveness


def _write(root: Path, rel: str, text: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _flutter_app(root: Path, *, analysis_options: bool) -> Path:
    """A Flutter app with its generated Gradle wrapper, as `flutter create` lays it out."""
    _write(root, "pubspec.yaml", "name: demo\n")
    _write(root, "lib/main.dart", "void main() {}\n")
    _write(root, "android/build.gradle.kts", "plugins {}\n")
    _write(root, "android/app/build.gradle.kts", "plugins {}\n")
    if analysis_options:
        _write(root, "analysis_options.yaml",
               "include: package:flutter_lints/flutter.yaml\n")
    return root


def _dart_tools(dead_code: dict) -> list[dict]:
    return [t for t in dead_code.get("tools", []) if t.get("language") == "dart"]


# ── detection ──────────────────────────────────────────────────────────────

def test_no_pubspec_is_not_dart(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 1\n")
    _write(tmp_path, "analysis_options.yaml", "linter: {}\n")
    assert scan_dart_capabilities(tmp_path) == {"available": False, "pubspec_files": []}


def test_pubspec_under_excluded_path_is_not_dart(tmp_path: Path) -> None:
    _write(tmp_path, "node_modules/pkg/pubspec.yaml", "name: vendored\n")
    _write(tmp_path, "tests/fixtures/app/pubspec.yaml", "name: fixture\n")
    assert scan_dart_capabilities(tmp_path)["available"] is False


def test_pubspec_under_user_exclude_is_not_dart(tmp_path: Path) -> None:
    _write(tmp_path, "vendor_sdk/pubspec.yaml", "name: sdk\n")
    result = scan_dart_capabilities(tmp_path, extra_exclude_dirs={"vendor_sdk"})
    assert result["available"] is False


# ── linting ────────────────────────────────────────────────────────────────

def test_linting_credited_when_analysis_options_present(tmp_path: Path) -> None:
    result = scan_dart_capabilities(_flutter_app(tmp_path, analysis_options=True))
    linting = result["capabilities"]["linting"]
    assert linting["state"] == "credited"
    assert linting["served_by"] == ["dart analyze"]
    assert "analysis_options.yaml" in linting["note"]
    assert {"candidate_tool", "gloss", "note"} <= linting.keys()


def test_linting_credits_flutter_analyze_for_a_flutter_package(tmp_path: Path) -> None:
    _flutter_app(tmp_path, analysis_options=True)
    _write(tmp_path, "pubspec.yaml",
           "name: demo\ndependencies:\n  flutter:\n    sdk: flutter\n")
    linting = scan_dart_capabilities(tmp_path)["capabilities"]["linting"]
    assert linting["served_by"] == ["flutter analyze"]


def test_analysis_options_in_an_ancestor_directory_credits_linting(tmp_path: Path) -> None:
    # The analyzer resolves analysis_options.yaml by walking up from each file,
    # so a monorepo root config serves a nested package.
    _write(tmp_path, "analysis_options.yaml", "include: package:lints/core.yaml\n")
    _write(tmp_path, "packages/core/pubspec.yaml", "name: core\n")
    linting = scan_dart_capabilities(tmp_path)["capabilities"]["linting"]
    assert linting["state"] == "credited"


def test_analysis_options_outside_every_package_does_not_credit(tmp_path: Path) -> None:
    _write(tmp_path, "pubspec.yaml", "name: demo\n")
    _write(tmp_path, "tool/analysis_options.yaml", "include: package:lints/core.yaml\n")
    linting = scan_dart_capabilities(tmp_path)["capabilities"]["linting"]
    assert linting["state"] == "honest_degrade"


def test_analysis_options_that_enables_no_rules_does_not_credit(tmp_path: Path) -> None:
    # Dart lints are opt-in: an exclude-only file (the usual codegen workaround)
    # enables no rule, so it must not read as served linting.
    _write(tmp_path, "pubspec.yaml", "name: demo\n")
    _write(tmp_path, "analysis_options.yaml",
           "# include: package:lints/recommended.yaml\n"
           "analyzer:\n  exclude:\n    - \"**/*.g.dart\"\n")
    linting = scan_dart_capabilities(tmp_path)["capabilities"]["linting"]
    assert linting["state"] == "honest_degrade"
    assert "enables no lint rules" in linting["note"]


def test_analysis_options_with_linter_rules_credits(tmp_path: Path) -> None:
    _write(tmp_path, "pubspec.yaml", "name: demo\n")
    _write(tmp_path, "analysis_options.yaml",
           "analyzer:\n  exclude: []\nlinter:\n  rules:\n    - avoid_print\n")
    linting = scan_dart_capabilities(tmp_path)["capabilities"]["linting"]
    assert linting["state"] == "credited"


def test_linter_rules_after_a_comment_and_a_blank_line_credit(tmp_path: Path) -> None:
    _write(tmp_path, "pubspec.yaml", "name: demo\n")
    _write(tmp_path, "analysis_options.yaml",
           "linter:\n  # house rules\n\n  rules:\n    - avoid_print\n")
    linting = scan_dart_capabilities(tmp_path)["capabilities"]["linting"]
    assert linting["state"] == "credited"


def test_rules_under_another_top_level_key_does_not_credit(tmp_path: Path) -> None:
    _write(tmp_path, "pubspec.yaml", "name: demo\n")
    _write(tmp_path, "analysis_options.yaml",
           "linter:\n  enabled: true\nformatter:\n  rules: []\n")
    linting = scan_dart_capabilities(tmp_path)["capabilities"]["linting"]
    assert linting["state"] == "honest_degrade"


def test_long_linter_block_without_rules_is_linear(tmp_path: Path) -> None:
    # A linter: block of many indented lines and no rules: child used to
    # backtrack exponentially in a regex; the line scan must stay linear.
    import time
    _write(tmp_path, "pubspec.yaml", "name: demo\n")
    body = "".join(f"    key{i}: value {i}\n" for i in range(30))
    _write(tmp_path, "analysis_options.yaml", "linter:\n" + body + "  \n")
    start = time.monotonic()
    linting = scan_dart_capabilities(tmp_path)["capabilities"]["linting"]
    assert time.monotonic() - start < 2.0
    assert linting["state"] == "honest_degrade"


def test_nearest_analysis_options_decides(tmp_path: Path) -> None:
    # The analyzer uses the nearest file only: a package-level file that enables
    # nothing shadows a root file that does.
    _write(tmp_path, "analysis_options.yaml", "include: package:lints/core.yaml\n")
    _write(tmp_path, "packages/core/pubspec.yaml", "name: core\n")
    _write(tmp_path, "packages/core/analysis_options.yaml",
           "analyzer:\n  exclude: []\n")
    linting = scan_dart_capabilities(tmp_path)["capabilities"]["linting"]
    assert linting["state"] == "honest_degrade"


def test_linting_honest_degrades_without_analysis_options(tmp_path: Path) -> None:
    result = scan_dart_capabilities(_flutter_app(tmp_path, analysis_options=False))
    linting = result["capabilities"]["linting"]
    assert linting["state"] == "honest_degrade"
    assert "dart analyze" in linting["candidate_tool"]
    assert "served_by" not in linting


# ── liveness ───────────────────────────────────────────────────────────────

def test_liveness_names_the_analyzer_unused_lints(tmp_path: Path) -> None:
    for with_options in (True, False):
        root = tmp_path / str(with_options)
        liveness = scan_dart_capabilities(
            _flutter_app(root, analysis_options=with_options))["capabilities"]["liveness"]
        assert liveness["state"] == "honest_degrade"
        assert "unused_" in liveness["candidate_tool"]
        assert "dart_code_metrics" not in json.dumps(liveness)


def test_dead_code_tools_carry_one_dart_honest_degrade_entry(tmp_path: Path) -> None:
    out = scan_liveness(_flutter_app(tmp_path, analysis_options=True), run_dead_code=False)
    dart = _dart_tools(out["dead_code"])
    assert len(dart) == 1
    assert dart[0]["status"] == "honest_degrade"
    assert "unused_" in dart[0]["tool"]
    assert dart[0]["reason"]
    assert out["dead_code"]["available"] is False


def test_non_dart_repo_gets_no_dart_tool_entry(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 1\n")
    out = scan_liveness(tmp_path, run_dead_code=False)
    assert _dart_tools(out["dead_code"]) == []
    assert "dart_capabilities" not in out


# ── run-context wiring ─────────────────────────────────────────────────────

def test_run_context_carries_language_capabilities_dart(tmp_path: Path) -> None:
    ctx = build_run_context(repo_root=_flutter_app(tmp_path, analysis_options=True),
                            run_date="2026-09-18", non_interactive=True)
    dart = ctx["language_capabilities"]["dart"]
    assert set(dart) == {"linting", "liveness"}
    assert dart["linting"]["state"] == "credited"
    assert dart["liveness"]["state"] == "honest_degrade"
    # The Gradle wrapper under android/ is not a JVM codebase: no JVM offer, no
    # JVM tool named anywhere.
    blob = json.dumps([ctx.get("capability_offers"), ctx["language_capabilities"],
                       ctx["dead_code"]])
    for jvm_name in ("mvn", "jdeps", "Checkstyle", "OpenRewrite"):
        assert jvm_name not in blob
    assert "dart_code_metrics" not in blob


def test_run_context_omits_language_capabilities_without_dart(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 1\n")
    ctx = build_run_context(repo_root=tmp_path, run_date="2026-09-18",
                            non_interactive=True)
    assert "dart" not in (ctx.get("language_capabilities") or {})
