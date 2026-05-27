"""Layer 1 inputs: runtime legibility / liveness signals.

Two tiers, both deterministic and best-effort -- they degrade to "not assessed"
rather than ever blocking the assessment.

**Dead-code tier (cheap, traditional).** Flags *intra-repo* candidate-dead code
(unused exports / unreferenced symbols) using a language-appropriate tool when
one is on PATH (``vulture`` for Python, ``ts-prune``/``knip`` for TS,
``staticcheck``/``deadcode`` for Go, clippy for Rust). The hard limit, stated in
the report: static reachability proves "nothing in *this* repo calls it", never
"no external consumer calls it." Cross-boundary liveness needs the next tier.

**Observability tier (the decisive one), scored by three rungs:**
  1. **Instrumented** -- telemetry is emitted (OpenTelemetry, Prometheus,
     Datadog/APM, structured logging). Necessary, not sufficient.
  2. **Discoverable** -- an ``OBSERVABILITY.md`` / runbook tells the agent where
     runtime truth lives. Orients, but grants no access.
  3. **Reachable** -- the agent has an *invokable* path to runtime state: an MCP
     server over logs/metrics/traces, a repo skill that tails logs or queries
     metrics, a documented runnable CLI. Without this the agent knows telemetry
     exists but cannot use it, so liveness stays unverifiable (the meridian
     case). This is the rung that decides the score.

Boundary: this sees only what the *repo provides* toward agent-reachability; it
cannot know the agent's live environment. We score what the repo makes reachable
and say so.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from lib.doc_graph import EXCLUDE_DIRS

DEAD_CODE_TIMEOUT = 60  # seconds; a slow tool degrades rather than hangs the run
MAX_CANDIDATES = 50     # cap so a pathological repo can't bloat run-context.json

STATIC_REACHABILITY_CAVEAT = (
    "Static reachability proves nothing in THIS repo references the symbol; it "
    "cannot prove no external consumer (a mobile app, another service) calls it. "
    "Cross-boundary liveness needs telemetry or a named human."
)


# ── dead-code tier ─────────────────────────────────────────────────────────

def _has_ext(repo_root: Path, exts: set[str]) -> bool:
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in exts:
            continue
        rel = path.relative_to(repo_root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        return True
    return False


def _parse_vulture(stdout: str) -> list[dict]:
    """vulture: `path:line: unused function 'name' (60% confidence)`."""
    out: list[dict] = []
    rx = re.compile(r"^(.*?):(\d+): (unused \w[\w ]*?) '?([\w.]+)'?")
    for line in stdout.splitlines():
        m = rx.match(line.strip())
        if m:
            out.append({"path": m.group(1), "line": int(m.group(2)),
                        "kind": m.group(3), "symbol": m.group(4)})
    return out


def _parse_ts_prune(stdout: str) -> list[dict]:
    """ts-prune: `path:line - name` (suffix `(used in module)` is not dead)."""
    out: list[dict] = []
    rx = re.compile(r"^(.*?):(\d+) - (\S+)(.*)$")
    for line in stdout.splitlines():
        m = rx.match(line.strip())
        if m and "used in module" not in m.group(4):
            out.append({"path": m.group(1), "line": int(m.group(2)),
                        "kind": "unused export", "symbol": m.group(3)})
    return out


def _parse_staticcheck(stdout: str) -> list[dict]:
    """staticcheck U1000: `path:line:col: ... is unused (U1000)`."""
    out: list[dict] = []
    rx = re.compile(r"^(.*?):(\d+):\d+:\s*(.*?is unused.*)$")
    for line in stdout.splitlines():
        m = rx.match(line.strip())
        if m:
            out.append({"path": m.group(1), "line": int(m.group(2)),
                        "kind": "unused", "symbol": m.group(3)})
    return out


def _parse_deadcode(stdout: str) -> list[dict]:
    """x/tools deadcode: `path:line:col: unreachable func: name`."""
    out: list[dict] = []
    rx = re.compile(r"^(.*?):(\d+):\d+:\s*(unreachable func.*)$")
    for line in stdout.splitlines():
        m = rx.match(line.strip())
        if m:
            out.append({"path": m.group(1), "line": int(m.group(2)),
                        "kind": "unreachable", "symbol": m.group(3)})
    return out


def _parse_knip(stdout: str) -> list[dict]:
    """knip --reporter json: {files:[...], issues:[{file, exports:[...]}]}."""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return []
    out: list[dict] = []
    for f in data.get("files", []):
        out.append({"path": f, "line": 0, "kind": "unused file", "symbol": "(file)"})
    for issue in data.get("issues", []):
        path = issue.get("file", "")
        for exp in issue.get("exports", []):
            name = exp.get("name", exp) if isinstance(exp, dict) else exp
            out.append({"path": path, "line": 0, "kind": "unused export", "symbol": name})
    return out


# language -> ordered tool preference. Each tool: cmd builder + parser.
# Tools that need a full build (knip, staticcheck) are still attempted but
# degrade gracefully on timeout / non-zero / unparseable output.
_DEAD_CODE_TOOLS: list[dict] = [
    {"language": "python", "tool": "vulture", "exts": {".py"},
     "cmd": lambda root: ["vulture", str(root)], "parser": _parse_vulture},
    {"language": "typescript", "tool": "ts-prune", "exts": {".ts", ".tsx"},
     "cmd": lambda root: ["ts-prune"], "parser": _parse_ts_prune},
    {"language": "typescript", "tool": "knip", "exts": {".ts", ".tsx", ".js", ".jsx"},
     "cmd": lambda root: ["knip", "--reporter", "json"], "parser": _parse_knip},
    {"language": "go", "tool": "deadcode", "exts": {".go"},
     "cmd": lambda root: ["deadcode", "./..."], "parser": _parse_deadcode},
    {"language": "go", "tool": "staticcheck", "exts": {".go"},
     "cmd": lambda root: ["staticcheck", "-checks", "U1000", "./..."],
     "parser": _parse_staticcheck},
]


@dataclass
class DeadCodeResult:
    available: bool = False
    candidates: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)  # {language, tool, status, reason}
    caveat: str = STATIC_REACHABILITY_CAVEAT

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "candidate_count": len(self.candidates),
            "candidates": self.candidates[:MAX_CANDIDATES],
            "tools": self.tools,
            "caveat": self.caveat,
        }


def scan_dead_code(repo_root: Path, run: bool = True) -> DeadCodeResult:
    """Best-effort intra-repo dead-code scan. Never raises."""
    repo_root = repo_root.resolve()
    result = DeadCodeResult()
    seen_languages: set[str] = set()

    for spec in _DEAD_CODE_TOOLS:
        lang = spec["language"]
        if lang in seen_languages:  # one tool per language: first available wins
            continue
        if not _has_ext(repo_root, spec["exts"]):
            continue
        tool = spec["tool"]
        if shutil.which(tool) is None:
            result.tools.append({"language": lang, "tool": tool,
                                 "status": "tool_absent",
                                 "reason": f"{tool} not on PATH"})
            continue
        seen_languages.add(lang)
        if not run:
            result.tools.append({"language": lang, "tool": tool,
                                 "status": "available_not_run",
                                 "reason": "execution disabled"})
            continue
        try:
            proc = subprocess.run(
                spec["cmd"](repo_root), cwd=str(repo_root),
                capture_output=True, text=True, timeout=DEAD_CODE_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            result.tools.append({"language": lang, "tool": tool,
                                 "status": "timeout",
                                 "reason": f"exceeded {DEAD_CODE_TIMEOUT}s"})
            continue
        except (OSError, FileNotFoundError) as e:  # pragma: no cover
            result.tools.append({"language": lang, "tool": tool,
                                 "status": "error", "reason": str(e)})
            continue
        found = spec["parser"](proc.stdout)
        result.available = True
        result.candidates.extend(found)
        result.tools.append({"language": lang, "tool": tool, "status": "ran",
                             "reason": f"{len(found)} candidate(s)"})
    return result


# ── observability tier ───────────────────────────────────────────────────

_MANIFESTS = [
    "package.json", "pyproject.toml", "requirements.txt", "Pipfile",
    "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts",
    "Gemfile", "composer.json", "*.csproj",
]

# substring -> human signal name. Matched against manifest text (lowercased).
_INSTRUMENTED_SIGNALS = {
    "opentelemetry": "OpenTelemetry", "otel": "OpenTelemetry",
    "prom-client": "Prometheus", "prometheus": "Prometheus",
    "micrometer": "Micrometer/Prometheus",
    "dd-trace": "Datadog APM", "datadog": "Datadog",
    "newrelic": "New Relic", "elastic-apm": "Elastic APM", "sentry": "Sentry",
    "structlog": "structured logging (structlog)", "zerolog": "structured logging (zerolog)",
    "logrus": "structured logging (logrus)", "zap": "structured logging (zap)",
    "winston": "structured logging (winston)", "pino": "structured logging (pino)",
    "slog": "structured logging (slog)",
}

_RUNBOOK_NAME_RE = re.compile(
    r"(observability|runbook|on[- ]?call|oncall|dashboards?)", re.IGNORECASE)
_RUNBOOK_CONTENT_RE = re.compile(
    r"\b(runbook|observability|dashboard|grafana|slo\b|sli\b|data[- ]freshness|"
    r"alerting|on[- ]?call)\b", re.IGNORECASE)
# Runnable query *commands* (not product names in prose) signal rung-3
# reachability. Matched only against fenced code blocks so a runbook that merely
# mentions "dashboards live in Grafana" doesn't get mistaken for one the agent
# can actually execute -- that distinction is the whole meridian case.
_RUNNABLE_QUERY_RE = re.compile(
    r"\b(kubectl\s+logs|kubectl\s+get|stern\s|logcli\s|promtool\s|"
    r"journalctl|docker\s+logs|aws\s+logs|gcloud\s+logging|az\s+monitor|"
    r"datadog-ci|sumo\b|splunk\s|curl[^\n`]*/(metrics|api))",
    re.IGNORECASE)
_FENCE_RE = re.compile(r"```.*?\n(.*?)```", re.DOTALL)


def _fenced_code(text: str) -> str:
    """Concatenate the contents of fenced code blocks (```...```)."""
    return "\n".join(_FENCE_RE.findall(text))
# Observability-flavoured names for MCP servers / repo skills (rung 3).
_OBS_TOOL_RE = re.compile(
    r"(log|metric|trace|telemetr|observ|grafana|prometheus|datadog|loki|"
    r"tempo|otel|dashboard|tail)", re.IGNORECASE)


def _iter_files(repo_root: Path, exts: set[str] | None = None) -> list[Path]:
    out: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if exts is not None and path.suffix.lower() not in exts:
            continue
        out.append(path)
    return out


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _detect_instrumented(repo_root: Path) -> list[str]:
    signals: set[str] = set()
    manifests = [
        p for p in _iter_files(repo_root)
        if p.name in _MANIFESTS or p.suffix == ".csproj"
        or (p.name == "requirements.txt")
    ]
    for m in manifests:
        text = _read(m).lower()
        for needle, label in _INSTRUMENTED_SIGNALS.items():
            if needle in text:
                signals.add(label)
    # Config-file presence is also instrumentation evidence.
    for path in _iter_files(repo_root):
        name = path.name.lower()
        if name in {"otel-collector-config.yaml", "otel-collector-config.yml",
                    "prometheus.yml", "prometheus.yaml"}:
            signals.add("telemetry config present")
    return sorted(signals)


def _detect_discoverable(repo_root: Path) -> tuple[list[str], list[Path]]:
    signals: set[str] = set()
    runbooks: list[Path] = []
    for path in _iter_files(repo_root, {".md", ".mdx", ".markdown"}):
        rel = path.relative_to(repo_root)
        # Match the doc's own name or an *intra-repo* directory (runbooks/,
        # observability/) - never the repo-root dir name, which says nothing.
        dir_parts = rel.parts[:-1]
        if _RUNBOOK_NAME_RE.search(path.stem) or any(_RUNBOOK_NAME_RE.search(p) for p in dir_parts):
            signals.add(f"runbook/observability doc: {rel}")
            runbooks.append(path)
            continue
        # Body-level signal even when the filename is plain.
        text = _read(path)
        if _RUNBOOK_CONTENT_RE.search(text):
            signals.add(f"observability content: {path.relative_to(repo_root)}")
            runbooks.append(path)
    return sorted(signals), runbooks


def _detect_reachable(repo_root: Path, runbooks: list[Path]) -> list[str]:
    signals: set[str] = set()

    # 1. .mcp.json exposing an observability server.
    for mcp in _iter_files(repo_root, {".json"}):
        if mcp.name != ".mcp.json":
            continue
        if _OBS_TOOL_RE.search(_read(mcp)):
            signals.add(f"MCP server over telemetry: {mcp.relative_to(repo_root)}")

    # 2. Repo skills named for logs/metrics/traces. These live under `skills/`
    #    or `.claude/skills/` - the latter is in EXCLUDE_DIRS, so scan directly
    #    rather than via _iter_files (which would skip .claude).
    _skip = {"node_modules", ".venv", "venv", "dist", "build", ".git"}
    for path in repo_root.rglob("SKILL.md"):
        rel = path.relative_to(repo_root)
        parts = [p.lower() for p in rel.parts]
        if any(p in _skip for p in parts):
            continue
        if "skills" in parts and _OBS_TOOL_RE.search(path.parent.name):
            signals.add(f"repo skill for telemetry: {rel}")

    # 3. Runbooks whose fenced code blocks hold runnable query commands.
    for rb in runbooks:
        if _RUNNABLE_QUERY_RE.search(_fenced_code(_read(rb))):
            signals.add(f"runbook with runnable queries: {rb.relative_to(repo_root)}")

    return sorted(signals)


@dataclass
class ObservabilityResult:
    instrumented: list[str] = field(default_factory=list)
    discoverable: list[str] = field(default_factory=list)
    reachable: list[str] = field(default_factory=list)
    rung: int = 0

    def as_dict(self) -> dict:
        return {
            "rung": self.rung,
            "instrumented": {"present": bool(self.instrumented), "signals": self.instrumented},
            "discoverable": {"present": bool(self.discoverable), "signals": self.discoverable},
            "reachable": {"present": bool(self.reachable), "signals": self.reachable},
            "boundary": (
                "Scores what the repo makes agent-reachable; cannot observe the "
                "agent's live environment."
            ),
        }


def scan_observability(repo_root: Path) -> ObservabilityResult:
    repo_root = repo_root.resolve()
    instrumented = _detect_instrumented(repo_root)
    discoverable, runbooks = _detect_discoverable(repo_root)
    reachable = _detect_reachable(repo_root, runbooks)
    rung = 3 if reachable else 2 if discoverable else 1 if instrumented else 0
    return ObservabilityResult(
        instrumented=instrumented, discoverable=discoverable,
        reachable=reachable, rung=rung,
    )


def scan_liveness(repo_root: Path, run_dead_code: bool = True) -> dict:
    """Top-level Layer 1 scan: dead-code candidates + observability rungs."""
    return {
        "dead_code": scan_dead_code(repo_root, run=run_dead_code).as_dict(),
        "observability": scan_observability(repo_root).as_dict(),
    }
