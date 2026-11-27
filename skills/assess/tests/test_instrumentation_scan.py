"""Tests for lib/keyhole/instrumentation_scan.py: observability rung 1 from manifests, code and config (#823)."""
from __future__ import annotations

from pathlib import Path

import pytest

import lib.keyhole.instrumentation_scan as inst
from lib.keyhole.instrumentation_scan import detect_instrumented
from lib.keyhole.liveness_scan import scan_observability


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    # newline="": the same bytes on every platform, so the head-size tests hold on
    # Windows; CRLF has its own test below.
    p.write_text(text, encoding="utf-8", newline="")


def _detect(root: Path) -> tuple[list[str], list[str]]:
    files = sorted(p for p in root.rglob("*") if p.is_file())
    labels, kinds = detect_instrumented(root, files)
    return labels, list(kinds)


# Each code form: (file name, content, label). Every one is credited in source
# and never under a test or fixture path.
CODE_FORMS = [
    ("app/log.py", "import logging\n\nlogger = logging.getLogger(__name__)\n", "Python logging"),
    ("app/log2.py", "from logging import getLogger\nlog = getLogger(__name__)\n", "Python logging"),
    ("cmd/main.go", 'package main\n\nimport (\n\t"fmt"\n\t"log/slog"\n)\n', "Go log/slog"),
    ("cmd/one.go", 'package main\n\nimport "log/slog"\n', "Go log/slog"),
    ("cmd/two.go", 'package main\n\nimport (\n\t"log/slog" // structured logging\n)\n',
     "Go log/slog"),
    ("app/log3.py", "from logging import (\n    Formatter,\n    getLogger,\n)\n",
     "Python logging"),
    ("src/main/java/a/A.java", "package a;\nimport java.util.logging.Logger;\n", "java.util.logging"),
    ("src/main/java/a/B.java", "package a;\nimport org.slf4j.LoggerFactory;\n", "SLF4J"),
    ("app/src/main/kotlin/C.kt", "package c\nimport android.util.Log\n", "Android Log"),
    ("Sources/Lib/Logger.swift", "import Foundation\nimport OSLog\n", "Apple unified logging (OSLog)"),
    ("lib/pool.ex", "defmodule P do\n  def f, do: :telemetry.execute([:p], %{})\nend\n",
     "telemetry (Erlang/Elixir)"),
    ("lib/assoc.rb", "ActiveSupport::Notifications.instrument('x') { }\n",
     "ActiveSupport::Notifications"),
]


@pytest.mark.parametrize(("rel", "text", "label"), CODE_FORMS)
def test_a_logger_in_source_is_credited_as_code(tmp_path: Path, rel: str, text: str,
                                                label: str) -> None:
    _write(tmp_path, rel, text)
    assert _detect(tmp_path) == ([label], ["code"])


@pytest.mark.parametrize("prefix", ["tests/", "tests/fixtures/", "testdata/", "examples/"])
@pytest.mark.parametrize(("rel", "text", "label"), CODE_FORMS)
def test_a_logger_in_a_test_or_fixture_is_not_credited(tmp_path: Path, prefix: str, rel: str,
                                                       text: str, label: str) -> None:
    _write(tmp_path, prefix + rel, text)
    assert _detect(tmp_path) == ([], [])


@pytest.mark.parametrize(("rel", "text"), [
    ("app/a.py", "# use logging.getLogger(__name__) here\n"),
    ("app/b.py", "import mylogging\nx = mylogging.getLogger(1)\n"),
    ("app/c.py", "from logging import Formatter  # getLogger later\n"),
    ("app/d.py", 'from logging import (\n    Formatter,\n)\nx = "getLogger"\n'),
    ("app/e.py", 's = "from logging import getLogger"\n'),
    ("cmd/b.go", 'package main\nvar s = "log/slog" + x // "log/slog"\n'),
    ("cmd/a.go", 'package main\n// see "log/slog"\nvar s = "use log/slog"\n'),
    ("src/A.java", "// import org.slf4j.Logger;\nclass A {}\n"),
    ("lib/a.ex", "# :telemetry.execute([:x], %{})\n"),
    ("lib/a.rb", "# ActiveSupport::Notifications.instrument\n"),
    ("Sources/A.swift", "import OSLogExtras\n"),
])
def test_a_mention_that_is_not_an_import_or_call_is_not_credited(tmp_path: Path, rel: str,
                                                                 text: str) -> None:
    _write(tmp_path, rel, text)
    assert _detect(tmp_path) == ([], [])


def test_django_shape_reads_rung_1(tmp_path: Path) -> None:
    """Success criterion: only `logging.getLogger(__name__)` reads rung 1."""
    _write(tmp_path, "pkg/views.py", "import logging\nlogger = logging.getLogger(__name__)\n")
    r = scan_observability(tmp_path).as_dict()
    assert r["rung"] == 1
    assert r["instrumented"] == {"present": True, "signals": ["Python logging"],
                                 "kinds": ["code"]}


# ── manifests ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("rel", "text", "label"), [
    ("go.mod", "require go.opentelemetry.io/otel v1.0.0\n", "OpenTelemetry"),
    ("pyproject.toml", '[project]\ndependencies = ["opentelemetry-sdk"]\n', "OpenTelemetry"),
    ("package.json", '{"dependencies":{"prom-client":"1"}}', "Prometheus"),
    ("go.mod", "require go.uber.org/zap v1.0.0\n", "structured logging (zap)"),
    ("composer.json", '{"require":{"monolog/monolog":"^3"}}', "logging (Monolog)"),
    ("App.csproj", '<PackageReference Include="Serilog" />', "structured logging (Serilog)"),
    ("App.csproj", '<PackageReference Include="NLog" />', "logging (NLog)"),
    ("Package.swift", '.package(url: "https://github.com/apple/swift-log.git", from: "1.0.0")',
     "logging (swift-log)"),
    ("pubspec.yaml", "dependencies:\n  sentry_flutter: ^7.0.0\n", "Sentry"),
    ("gradle/libs.versions.toml",
     '[libraries]\ntimber = { module = "com.jakewharton.timber:timber", version = "5" }\n',
     "logging (Timber)"),
])
def test_a_telemetry_dependency_is_credited(tmp_path: Path, rel: str, text: str,
                                            label: str) -> None:
    _write(tmp_path, rel, text)
    assert _detect(tmp_path) == ([label], ["dependency"])


@pytest.mark.parametrize(("rel", "text"), [
    ("package.json", '{"dependencies":{"hotel-booking":"1"}}'),
    ("package.json", '{"dependencies":{"zapier-platform-core":"1"}}'),
    ("package.json", '{"dependencies":{"pinot-client":"1"}}'),
    ("requirements.txt", "pdfslogan==1.0\n"),
    ("package.json", '{"description":"request tracing and telemetry helpers"}'),
    ("gradle/libs.versions.toml", '[libraries]\ntimber = { module = "org.example:timber" }\n'),
])
def test_a_name_that_only_contains_a_needle_is_not_credited(tmp_path: Path, rel: str,
                                                            text: str) -> None:
    _write(tmp_path, rel, text)
    assert _detect(tmp_path) == ([], [])


@pytest.mark.parametrize("prefix", ["tests/fixtures/", "examples/demo/", "vendor/x/"])
def test_a_manifest_outside_production_source_is_not_credited(tmp_path: Path,
                                                               prefix: str) -> None:
    _write(tmp_path, prefix + "package.json", '{"dependencies":{"@sentry/node":"7"}}')
    assert _detect(tmp_path) == ([], [])


@pytest.mark.parametrize("text", [
    '[dependencies]\ntracing = "0.1"\n',
    '[dependencies]\ntracing = { workspace = true }\n',
    '[workspace.dependencies]\ntracing = "0.1"\n',
    '[target.\'cfg(unix)\'.dependencies]\ntracing = "0.1"\n',
])
def test_the_rust_tracing_crate_is_credited(tmp_path: Path, text: str) -> None:
    _write(tmp_path, "Cargo.toml", text)
    assert _detect(tmp_path) == (["Rust tracing"], ["dependency"])


def test_the_rust_log_crate_is_credited(tmp_path: Path) -> None:
    _write(tmp_path, "crates/a/Cargo.toml", '[dependencies]\nlog = "0.4"\n')
    assert _detect(tmp_path) == (["Rust log"], ["dependency"])


@pytest.mark.parametrize("text", [
    '[dev-dependencies]\ntracing = "0.1"\n',
    '[build-dependencies]\nlog = "0.4"\n',
    '[package]\nname = "x"\ndescription = "fast tracing of log files"\n',
    '[dependencies\nbroken = ',
    'workspace = 1\n',
])
def test_rust_tracing_outside_runtime_dependencies_is_not_credited(tmp_path: Path,
                                                                   text: str) -> None:
    _write(tmp_path, "Cargo.toml", text)
    assert _detect(tmp_path) == ([], [])


def test_the_ruff_shape_reads_rung_1(tmp_path: Path) -> None:
    _write(tmp_path, "Cargo.toml", '[workspace.dependencies]\ntracing = "0.1"\n')
    _write(tmp_path, "crates/a/Cargo.toml", "[dependencies]\ntracing = { workspace = true }\n")
    _write(tmp_path, "crates/a/src/lib.rs", "use tracing::info;\n")
    r = scan_observability(tmp_path).as_dict()
    assert r["rung"] == 1
    assert r["instrumented"]["kinds"] == ["code", "dependency"]


# ── declared dependencies, imports and launchers (#922) ──────────────────────

_OTEL_API = "[project]\nname = 'x'\nversion = '0'\ndependencies = ['opentelemetry-api']\n"


def test_a_dependency_alone_is_declared_not_counted(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", _OTEL_API)
    r = scan_observability(tmp_path).as_dict()
    assert r["rung"] == 0
    assert r["instrumented"] == {"present": False, "signals": ["OpenTelemetry"],
                                 "kinds": ["dependency"]}


@pytest.mark.parametrize(("rel", "text", "label"), [
    ("Dockerfile", 'FROM python:3.12\nCMD ["opentelemetry-instrument", "python", "app.py"]\n',
     "telemetry launcher"),
    ("Procfile", "web: opentelemetry-instrument gunicorn app:app\n", "telemetry launcher"),
    ("Procfile", "web: ddtrace-run python app.py\n", "telemetry launcher"),
    ("Procfile", "web: node -r newrelic app.js\n", "telemetry launcher"),
    ("package.json", '{"scripts":{"start":"node --require '
     '@opentelemetry/auto-instrumentations-node/register app.js"}}', "telemetry launcher"),
    ("bin/start.sh", "#!/bin/sh\nexec java -javaagent:/otel/opentelemetry-javaagent.jar "
     "-jar app.jar\n", "JVM telemetry agent"),
    ("deploy/app.yaml", "env:\n  - name: JAVA_TOOL_OPTIONS\n"
     "    value: -javaagent:/dd/dd-java-agent.jar\n", "JVM telemetry agent"),
    ("Dockerfile.prod", "ENTRYPOINT java -javaagent:newrelic/newrelic.jar -jar a.jar\n",
     "JVM telemetry agent"),
])
def test_a_launcher_counts_with_a_dependency_alongside(tmp_path: Path, rel: str, text: str,
                                                       label: str) -> None:
    _write(tmp_path, "pyproject.toml", _OTEL_API)
    _write(tmp_path, rel, text)
    labels, kinds = _detect(tmp_path)
    assert label in labels and kinds == ["dependency", "launcher"]
    assert scan_observability(tmp_path).as_dict()["rung"] == 1


@pytest.mark.parametrize(("rel", "text"), [
    ("bin/test.sh", "java -javaagent:jacoco/jacocoagent.jar -jar app.jar\n"),
    ("Dockerfile", "RUN pip install -r newrelic-requirements.txt\n"),
    ("bin/run.sh", "java -javaagent:/opt/hotelbooking/jacoco.jar -jar app.jar\n"),
    ("Dockerfile", "# CMD opentelemetry-instrument python app.py\nCMD python app.py\n"),
    ("tests/run.sh", "opentelemetry-instrument python app.py\n"),
    ("app/notes.txt", "opentelemetry-instrument python app.py\n"),
])
def test_a_launch_line_that_is_not_telemetry_or_not_read_does_not_count(
        tmp_path: Path, rel: str, text: str) -> None:
    _write(tmp_path, rel, text)
    assert _detect(tmp_path) == ([], [])


@pytest.mark.parametrize(("rel", "text", "language"), [
    ("app/otel.py", "from opentelemetry import trace\n", "Python"),
    ("app/m.py", "import prometheus_client\n", "Python"),
    ("src/t.ts", "import { trace } from '@opentelemetry/api';\n", "JavaScript"),
    ("src/l.js", "const pino = require('pino');\n", "JavaScript"),
    ("cmd/z.go", 'package main\n\nimport (\n\t"go.uber.org/zap"\n)\n', "Go"),
    ("cmd/o.go", 'package main\nimport otel "go.opentelemetry.io/otel"\n', "Go"),
    ("src/main/java/a/M.java", "package a;\nimport io.micrometer.core.instrument.Counter;\n",
     "JVM"),
    ("src/App.cs", "using Serilog;\n", "C#"),
    ("src/main.rs", "fn main() { tracing::info!(\"up\"); }\n", "Rust"),
    ("src/L.php", "<?php\nuse Monolog\\Logger;\n", "PHP"),
    ("config/otel.rb", "require 'opentelemetry/sdk'\n", "Ruby"),
    ("Sources/A/L.swift", "import Logging\n", "Swift"),
    ("lib/main.dart", "import 'package:sentry_flutter/sentry_flutter.dart';\n", "Dart"),
])
def test_a_telemetry_library_import_is_credited_as_code(tmp_path: Path, rel: str, text: str,
                                                        language: str) -> None:
    _write(tmp_path, rel, text)
    assert _detect(tmp_path) == ([f"telemetry library import ({language})"], ["code"])


@pytest.mark.parametrize(("rel", "text"), [
    ("app/a.py", "# from opentelemetry import trace\n"),
    ("app/b.py", "import opentelemetry_fake_free\n"),
    ("src/a.ts", "// import x from 'pino';\n"),
    ("src/b.ts", "import x from 'pinot-client';\n"),
    ("src/main.rs", "use my_log::x;\n"),
])
def test_a_mention_of_a_telemetry_library_is_not_an_import(tmp_path: Path, rel: str,
                                                           text: str) -> None:
    _write(tmp_path, rel, text)
    assert _detect(tmp_path) == ([], [])


def test_elixir_telemetry_dependency_is_credited_only_in_mix(tmp_path: Path) -> None:
    _write(tmp_path, "mix.exs", 'defp deps do\n  [{:telemetry, "~> 1.0"}]\nend\n')
    assert _detect(tmp_path) == (["telemetry (Erlang/Elixir)"], ["dependency"])


def test_the_word_telemetry_in_a_manifest_is_not_credited(tmp_path: Path) -> None:
    _write(tmp_path, "mix.exs", '# no :telemetry dependency yet\n[{:jason, "~> 1.0"}]\n')
    _write(tmp_path, "package.json", '{"keywords":["telemetry"]}')
    assert _detect(tmp_path) == ([], [])


# ── config and kinds ─────────────────────────────────────────────────────────

def test_a_collector_config_is_credited_as_config(tmp_path: Path) -> None:
    _write(tmp_path, "deploy/prometheus.yml", "scrape_configs: []\n")
    assert _detect(tmp_path) == (["telemetry config present"], ["config"])


def test_a_collector_config_under_a_test_path_is_not_credited(tmp_path: Path) -> None:
    _write(tmp_path, "tests/fixtures/prometheus.yml", "scrape_configs: []\n")
    assert _detect(tmp_path) == ([], [])


def test_every_kind_that_matched_is_recorded(tmp_path: Path) -> None:
    _write(tmp_path, "requirements.txt", "structlog\n")
    _write(tmp_path, "app/x.py", "import logging\nlog = logging.getLogger(__name__)\n")
    _write(tmp_path, "prometheus.yml", "x: 1\n")
    labels, kinds = _detect(tmp_path)
    assert kinds == ["code", "config", "dependency"]
    assert "Python logging" in labels


def test_a_rule_that_matched_stops_reading_its_files(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    for n in range(5):
        _write(tmp_path, f"app/m{n}.py", "import logging\nlog = logging.getLogger(__name__)\n")
    reads: list[Path] = []
    real = inst._read_head

    def counting(path: Path) -> str:
        reads.append(path)
        return real(path)

    monkeypatch.setattr(inst, "_read_head", counting)
    # Only the logger rule: the telemetry-import rule for .py would keep reading.
    monkeypatch.setattr(inst, "CODE_RULES", inst.CODE_RULES[:1])
    assert _detect(tmp_path) == (["Python logging"], ["code"])
    assert len(reads) == 1


def test_an_oversized_source_file_is_not_read(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "app/big.py", "import logging\nlog = logging.getLogger(__name__)\n")
    monkeypatch.setattr(inst, "MAX_CODE_BYTES", 4)
    assert _detect(tmp_path) == ([], [])


def test_a_logger_within_the_head_is_found(tmp_path: Path) -> None:
    body = "x = 1\n" * ((inst.HEAD_BYTES - 200) // 6)
    _write(tmp_path, "app/late.py", body + "log = logging.getLogger(__name__)\n")
    assert _detect(tmp_path) == (["Python logging"], ["code"])


def test_a_logger_past_the_head_is_not_read(tmp_path: Path) -> None:
    body = "x = 1\n" * (inst.HEAD_BYTES // 6 + 10)
    _write(tmp_path, "app/late.py", body + "log = logging.getLogger(__name__)\n")
    assert _detect(tmp_path) == ([], [])


def test_bytes_read_on_a_large_no_match_tree_are_bounded(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tree with no match is read at most HEAD_BYTES per file, counted not timed."""
    filler = "x = 1\n" * (100 * 1024 // 6)
    for n in range(30):
        _write(tmp_path, f"pkg/m{n}.py", filler)
        _write(tmp_path, f"cmd/m{n}.go", "package main\n" + filler)
    read_bytes: list[int] = []
    real = inst._read_head

    def counting(path: Path) -> str:
        text = real(path)
        read_bytes.append(len(text.encode("utf-8")))
        return text

    monkeypatch.setattr(inst, "_read_head", counting)
    assert _detect(tmp_path) == ([], [])
    assert len(read_bytes) == 60
    assert sum(read_bytes) <= 60 * inst.HEAD_BYTES


@pytest.mark.parametrize(("rel", "text", "label"), CODE_FORMS)
def test_a_crlf_source_file_is_read_like_an_lf_one(tmp_path: Path, rel: str, text: str,
                                                   label: str) -> None:
    """Windows checkouts write CRLF; a "$"-anchored rule must still match."""
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    assert _detect(tmp_path) == ([label], ["code"])
