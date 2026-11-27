"""Observability rung 1 (instrumented): telemetry the repository emits, and how it was found (#823).

``detect_instrumented`` reads the file list ``liveness_scan.scan_observability``
already walked and returns the instrumentation labels it found plus the kinds of
evidence behind them, each kind a token:

- ``dependency``: a telemetry or logging dependency named in a build manifest
  (``MANIFEST_NAMES``, plus any ``.csproj``). Distinctive names (``opentelemetry``,
  ``prom-client``, ``monolog``) match as whole names: the characters either side
  must not be a letter or digit, so ``go.opentelemetry.io/otel`` and
  ``opentelemetry-sdk`` count and ``hotel-booking`` or ``zapier`` do not. Ordinary
  words count only as dependency keys in their own manifest: the Rust ``tracing``
  and ``log`` crates under a ``Cargo.toml`` dependency table (``dev-dependencies``
  and ``build-dependencies`` do not count), and ``{:telemetry, ...}`` in ``mix.exs``.
- ``code``: a logger from the standard library or a logging facade in use, read
  from an import or call line (``CODE_RULES``): Python ``logging.getLogger``, Go
  ``log/slog``, ``java.util.logging``, ``org.slf4j``, ``android.util.Log``, Apple
  ``OSLog``, Erlang/Elixir ``:telemetry.execute`` and
  ``ActiveSupport::Notifications.instrument``. A line that starts with ``#`` or
  ``//`` is skipped, but a mention inside a Python docstring or an Erlang ``%``
  comment is not excluded and is credited. Only the first ``HEAD_BYTES`` of
  each file are read, where imports and module-level loggers sit, so a large
  repository with no match costs a bounded read per file rather than its whole
  tree; a call made only deeper in a file is not seen. Once a rule has matched,
  files only it could match are not read again. A suffix with several rules (a
  ``.py`` file has the logger and the telemetry-import rule, a ``.yml`` file both
  launcher rules) is read until every one of its rules has matched, so a repository
  with a logger but no telemetry import still reads each ``.py`` head once.
  The same reader credits an import of a telemetry library (``CODE_RULES``
  labelled ``telemetry library import (<language>)``): ``opentelemetry``,
  ``prometheus_client``, ``structlog`` or ``sentry_sdk`` in Python,
  ``@opentelemetry/*``, ``prom-client`` or ``pino`` in JavaScript, ``go.uber.org/zap``
  or ``go.opentelemetry.io/*`` in Go, ``io.opentelemetry`` or ``io.micrometer`` on the
  JVM, ``using Serilog`` in C#, ``tracing::`` or ``log::`` in Rust, and the Monolog,
  Ruby, Swift and Dart equivalents.
- ``launcher`` (#922): a launch line that instruments the process with no import,
  read from a Dockerfile, Procfile, Makefile, shell script, YAML file, ``package.json``
  or Gradle or Maven build (``LAUNCHER_FILES``): ``opentelemetry-instrument``,
  ``ddtrace-run``, ``newrelic-admin run-program``, ``node --require`` of an
  auto-instrumentation entry point, or a ``-javaagent:`` flag whose jar names a
  telemetry agent (OpenTelemetry, Datadog, New Relic, Elastic APM, Application
  Insights, the Prometheus JMX exporter, Pyroscope). Another ``-javaagent`` (a
  coverage agent) is not telemetry and does not count.
- ``config``: a committed collector or scrape config (``prometheus.yml``,
  ``otel-collector-config.yaml``).

A ``dependency`` alone is declared, not counted (maintainer decision on #922): a
library correctly depends on ``opentelemetry-api`` and leaves the SDK to the
application, so the manifest line shows intent, not telemetry emitted.
``counts_as_instrumented`` is true when any other kind matched, and
``liveness_scan`` gives rung 1 only then; the labels and kinds still list a
declared dependency.

Only production source counts: every file is put through
``lib.path_classes.path_class`` first, so a manifest, logger import or config
under a test, fixture, example, vendored or generated path is never credited.
Tests: ``tests/test_instrumentation_scan.py``.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from lib.path_classes import is_production

InstrumentationKind = Literal["code", "config", "dependency", "launcher"]

# Exact manifest filenames. .NET .csproj files are matched by suffix instead
# (the name varies per project), so they are not listed here.
MANIFEST_NAMES = frozenset({
    "package.json", "pyproject.toml", "requirements.txt", "Pipfile",
    "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts",
    "Gemfile", "composer.json", "mix.exs", "Package.swift", "pubspec.yaml",
    "libs.versions.toml",
})

# Distinctive dependency name -> human signal name. Matched against manifest
# text (lowercased) with no letter or digit either side.
DEPENDENCY_NAMES: dict[str, str] = {
    "opentelemetry": "OpenTelemetry", "otel": "OpenTelemetry",
    "prom-client": "Prometheus", "prometheus": "Prometheus",
    "micrometer": "Micrometer/Prometheus",
    "dd-trace": "Datadog APM", "datadog": "Datadog",
    "newrelic": "New Relic", "elastic-apm": "Elastic APM", "sentry": "Sentry",
    "structlog": "structured logging (structlog)", "zerolog": "structured logging (zerolog)",
    "logrus": "structured logging (logrus)", "zap": "structured logging (zap)",
    "winston": "structured logging (winston)", "pino": "structured logging (pino)",
    "slog": "structured logging (slog)",
    "monolog": "logging (Monolog)", "serilog": "structured logging (Serilog)",
    "nlog": "logging (NLog)", "swift-log": "logging (swift-log)",
    "jakewharton.timber": "logging (Timber)",
}
_DEPENDENCY_RE = re.compile(
    r"(?<![a-z0-9])("
    + "|".join(re.escape(n) for n in sorted(DEPENDENCY_NAMES, key=len, reverse=True))
    + r")(?![a-z0-9])")

# Rust crates that are ordinary words, so they count only as Cargo dependency keys.
CARGO_CRATES: dict[str, str] = {
    "tracing": "Rust tracing", "tracing-subscriber": "Rust tracing", "log": "Rust log",
}
# Elixir: `{:telemetry, ...}` (and telemetry_metrics / telemetry_poller) in mix.exs.
_MIX_TELEMETRY_RE = re.compile(r"\{\s*:telemetry(?:_[a-z]+)?\s*,")
TELEMETRY_LABEL = "telemetry (Erlang/Elixir)"

CONFIG_NAMES = frozenset({
    "otel-collector-config.yaml", "otel-collector-config.yml",
    "prometheus.yml", "prometheus.yaml",
})
CONFIG_LABEL = "telemetry config present"

# A line that is not a comment (# or //) before the match.
_LINE = r"^(?![ \t]*(?:#|//))[^\n]*?"


@dataclass(frozen=True)
class CodeRule:
    """Telemetry in use, read from files whose ``_file_keys`` meet ``suffixes``
    (a suffix, or a file name such as ``Procfile``)."""

    label: str
    suffixes: frozenset[str]
    pattern: re.Pattern[str]
    kind: InstrumentationKind = "code"


def _rule(label: str, suffixes: str, pattern: str,
          kind: InstrumentationKind = "code") -> CodeRule:
    return CodeRule(label, frozenset(suffixes.split()), re.compile(pattern, re.MULTILINE), kind)


def _imports(language: str, suffixes: str, pattern: str) -> CodeRule:
    return _rule(f"telemetry library import ({language})", suffixes, pattern)


def _file_keys(path: Path) -> set[str]:
    """The keys a rule's ``suffixes`` are matched against: the suffix and the name,
    plus ``Dockerfile`` for ``Dockerfile.prod`` or ``app.dockerfile``."""
    name = path.name
    keys = {path.suffix.lower(), name}
    if name.startswith("Dockerfile") or name.lower().endswith(".dockerfile"):
        keys.add("Dockerfile")
    return keys


_JVM = ".java .kt .kts .scala .groovy"
_JS = ".js .mjs .cjs .jsx .ts .mts .cts .tsx"
# Files a launch line sits in (#922).
LAUNCHER_FILES = ("Dockerfile Procfile Makefile package.json build.gradle build.gradle.kts "
                  "pom.xml jvm.config .sh .bash .yml .yaml")
_JS_MODULES = (r"@opentelemetry/[^'\"]+|prom-client|dd-trace(?:/[^'\"]*)?|newrelic"
               r"|elastic-apm-node(?:/[^'\"]*)?|@sentry/[^'\"]+|winston|pino")
_GO_MODULES = (r"go\.opentelemetry\.io/[^\"]+|github\.com/prometheus/client_golang/[^\"]+"
               r"|go\.uber\.org/zap|github\.com/sirupsen/logrus|github\.com/rs/zerolog[^\"]*"
               r"|(?:gopkg\.in|github\.com)/DataDog/dd-trace-go[^\"]*"
               r"|github\.com/newrelic/go-agent[^\"]*|github\.com/getsentry/sentry-go")
CODE_RULES: tuple[CodeRule, ...] = (
    _rule("Python logging", ".py",
          _LINE + r"\blogging\.getLogger\s*\("
          # `from logging import getLogger`, or a parenthesised list across lines.
          r"|^[ \t]*from[ \t]+logging[ \t]+import[ \t]*"
          r"(?:[^\n(#]*\bgetLogger\b|\([^)]*\bgetLogger\b)"),
    _rule("Go log/slog", ".go", r'^[ \t]*(?:import[ \t]+)?(?:[\w.]+[ \t]+)?"log/slog"[ \t]*\)?[ \t]*(?://[^\n]*)?$'),
    _rule("java.util.logging", _JVM, r"^[ \t]*import[ \t]+(?:static[ \t]+)?java\.util\.logging\."),
    _rule("SLF4J", _JVM, r"^[ \t]*import[ \t]+(?:static[ \t]+)?org\.slf4j\."),
    _rule("Android Log", ".java .kt", r"^[ \t]*import[ \t]+android\.util\.Log\b"),
    _rule("Apple unified logging (OSLog)", ".swift", r"^[ \t]*import[ \t]+(?:OSLog|os\.log)\b"),
    _rule(TELEMETRY_LABEL, ".ex .exs .erl", _LINE + r":?telemetry[.:](?:execute|span)\s*\("),
    _rule("ActiveSupport::Notifications", ".rb",
          _LINE + r"\bActiveSupport::Notifications\.instrument\b"),
    # Telemetry libraries in use (#922): the import a declared dependency needs to count.
    _imports("Python", ".py", r"^[ \t]*(?:from|import)[ \t]+(?:opentelemetry|prometheus_client"
             r"|ddtrace|datadog|newrelic|elasticapm|sentry_sdk|structlog)\b"),
    _imports("JavaScript", _JS, _LINE + r"(?:\bfrom[ \t]+|\brequire\([ \t]*|^[ \t]*import[ \t]+)"
             r"['\"](?:" + _JS_MODULES + r")['\"]"),
    _imports("Go", ".go", r'^[ \t]*(?:import[ \t]+)?(?:[\w.]+[ \t]+)?"(?:' + _GO_MODULES + r')"'),
    _imports("JVM", _JVM, r"^[ \t]*import[ \t]+(?:static[ \t]+)?(?:io\.opentelemetry|io\.micrometer"
             r"|io\.prometheus|datadog\.trace|com\.newrelic|co\.elastic\.apm|io\.sentry"
             r"|timber\.log)\."),
    _imports("C#", ".cs", r"^[ \t]*using[ \t]+(?:OpenTelemetry|Serilog|NLog|Prometheus|Sentry"
             r"|Datadog\.Trace|Elastic\.Apm|NewRelic)\b"),
    _imports("Rust", ".rs", _LINE + r"\b(?:tracing|log|opentelemetry|sentry|prometheus)::"),
    _imports("PHP", ".php", r"^[ \t]*use[ \t]+\\?(?:Monolog|OpenTelemetry|Prometheus|Sentry)\\"),
    _imports("Ruby", ".rb", r"^[ \t]*require[ \t(]+['\"](?:opentelemetry[^'\"]*|prometheus/client"
             r"|ddtrace|datadog|newrelic_rpm|sentry-ruby|elastic-apm)['\"]"),
    _imports("Swift", ".swift", r"^[ \t]*import[ \t]+(?:Logging|Metrics|OpenTelemetryApi"
             r"|OpenTelemetrySdk|Sentry)\b"),
    _imports("Dart", ".dart", r"^[ \t]*import[ \t]+['\"]package:(?:sentry\w*|opentelemetry"
             r"|logging|logger)/"),
    # Launch lines that instrument a process with no import (#922).
    _rule("telemetry launcher", LAUNCHER_FILES, _LINE + r"(?:\bopentelemetry-instrument\b"
          r"|\bddtrace-run\b|\bnewrelic-admin[ \t]+run-program\b"
          r"|(?:--require|--import|-r)[ \t=]+['\"]?(?:@opentelemetry/auto-instrumentations-node"
          r"|dd-trace/init|newrelic\b|elastic-apm-node/start))", "launcher"),
    _rule("JVM telemetry agent", LAUNCHER_FILES, _LINE + r"-javaagent:[^\s'\"]*(?:opentelemetry"
          r"|otel|dd-java-agent|newrelic|elastic-apm|applicationinsights|jmx_prometheus"
          r"|pyroscope)", "launcher"),
)
# Files larger than this are not read for code rules (generated or data files).
MAX_CODE_BYTES = 512 * 1024
# Code rules read only this much of each file: the import block and module-level
# logger set-up are at the top.
HEAD_BYTES = 16 * 1024


def counts_as_instrumented(kinds: list[InstrumentationKind]) -> bool:
    """Rung 1 evidence: any kind but a declared ``dependency`` (#922)."""
    return any(kind != "dependency" for kind in kinds)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _read_head(path: Path) -> str:
    """The first ``HEAD_BYTES`` of a file as text, empty when it cannot be read."""
    try:
        with path.open("rb") as handle:
            head = handle.read(HEAD_BYTES).decode("utf-8", errors="ignore")
    except OSError:
        return ""
    # Line-anchored rules end at "$", which stops before "\n" but not before a
    # CRLF file's "\r", so normalise line endings first.
    return head.replace("\r\n", "\n").replace("\r", "\n")


def _cargo_crates(text: str) -> set[str]:
    """Labels of ``CARGO_CRATES`` keys in a Cargo.toml's runtime dependency tables."""
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return set()
    workspace = doc.get("workspace")
    tables: list[Any] = [doc.get("dependencies"),
                         workspace.get("dependencies") if isinstance(workspace, dict) else None]
    target = doc.get("target")
    if isinstance(target, dict):
        tables += [t.get("dependencies") for t in target.values() if isinstance(t, dict)]
    return {CARGO_CRATES[key] for table in tables if isinstance(table, dict)
            for key in table if key in CARGO_CRATES}


def _manifest_labels(path: Path) -> set[str]:
    text = _read(path)
    labels = {DEPENDENCY_NAMES[m.group(1)] for m in _DEPENDENCY_RE.finditer(text.lower())}
    if path.name == "Cargo.toml":
        labels |= _cargo_crates(text)
    if path.name == "mix.exs" and _MIX_TELEMETRY_RE.search(text):
        labels.add(TELEMETRY_LABEL)
    return labels


def detect_instrumented(repo_root: Path,
                        files: list[Path]) -> tuple[list[str], list[InstrumentationKind]]:
    """The instrumentation labels found in production files, and their kinds, both sorted."""
    labels: set[str] = set()
    kinds: set[InstrumentationKind] = set()
    pending = list(CODE_RULES)
    for path in files:
        if not is_production(path.relative_to(repo_root)):
            continue
        if path.name in MANIFEST_NAMES or path.suffix == ".csproj":
            found = _manifest_labels(path)
            if found:
                labels |= found
                kinds.add("dependency")
        if path.name.lower() in CONFIG_NAMES:
            labels.add(CONFIG_LABEL)
            kinds.add("config")
        keys = _file_keys(path)
        rules = [r for r in pending if keys & r.suffixes]
        if not rules:
            continue
        try:
            if path.stat().st_size > MAX_CODE_BYTES:
                continue
        except OSError:
            continue
        text = _read_head(path)
        for rule in rules:
            if rule.pattern.search(text):
                labels.add(rule.label)
                kinds.add(rule.kind)
                pending.remove(rule)
    return sorted(labels), sorted(kinds)
