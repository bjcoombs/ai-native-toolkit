"""JVM/Maven capability-driven analysis offers (issue #113, v1 bounded).

Generalises /assess's tool mapping from a hardcoded per-language allowlist
(``vulture`` for Python, ``ts-prune`` for TS, ``staticcheck`` for Go) to a
*capability-driven detect-or-propose* model, proven on ONE capability (liveness)
in ONE build system (Maven). The defect this fixes is the **non-enumeration
architecture**: when a repo's language isn't in the allowlist, every analysis
capability silently degrades to "unavailable" - the report reads "this layer is
absent here" rather than "a tool could serve this - install one?". JVM is simply
the first ecosystem to expose it.

For each JVM analysis capability the scan reports a STATE the report and the
offer-layer (SKILL.md Step 2) act on:

  * ``served``         - liveness, when ``mvn dependency:analyze`` has been run
                         (run-consent) and its coarse module-level candidates
                         are fed into the dead-code signal.
  * ``offer``          - liveness, when Maven is detected but the analyze goal
                         has not been run. The agent should offer to resolve it
                         inside the run. ``consent`` distinguishes the shape:
                         ``run`` when ``mvn`` is already on PATH (a RUN-consent -
                         ``dependency:analyze`` needs a *compiling build*, not
                         just an install), ``install`` when ``mvn`` is absent.
  * ``credited``       - a capability already served by a configured pom.xml
                         plugin (Checkstyle / SpotBugs / PMD / error-prone /
                         OpenRewrite); detected and NOT re-offered.
  * ``honest_degrade`` - a capability nothing serves yet; the report NAMES the
                         capability and an ecosystem-appropriate candidate tool.
                         A deliverable distinct from both "Present" and a silent
                         "Missing" - module graph, linting, modernization, and
                         every capability under Gradle take this state in v1.

Boundary: the candidate tool named here is the deterministic DEFAULT. The
assessing agent has latitude to propose a different ecosystem-appropriate tool
at runtime (SKILL.md Step 2); that choice is human-judged, not CI-tested. CI
tests SIGNAL CONSUMPTION - given a tool's output, the scorecard feeds correctly.

Out of scope for v1 (fast-follow): healing the module graph (``jdeps``), linting,
and modernization; Gradle plugin reading and a served Gradle path; non-JVM
languages benefiting from the same general flow.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from lib.doc_graph import is_excluded_path

# Capabilities a JVM project exposes, in report order. Only ``liveness`` is
# healed in v1; the rest honest-degrade (or are credited when a plugin serves
# them).
JVM_CAPABILITIES = ("liveness", "module_graph", "linting", "modernization")

# Ecosystem-appropriate DEFAULT candidate per capability. The agent may propose
# an alternative at runtime (reasoned latitude); this is what the deterministic
# report names so honest-degrade is never silent.
_CANDIDATE_TOOLS = {
    "liveness": "mvn dependency:analyze",
    "module_graph": "jdeps",
    "linting": "Checkstyle / SpotBugs / error-prone",
    "modernization": "OpenRewrite",
}

_CAPABILITY_GLOSS = {
    "liveness": "coarse module-level dead-dependency detection",
    "module_graph": "static inter-module dependency graph",
    "linting": "style and bug-pattern static analysis",
    "modernization": "automated API / idiom migration",
}

# pom.xml needles (lowercased) that mean a capability is already served, so the
# offer-flow CREDITS it rather than re-offering. Coarse on purpose: a substring
# match against the pom text. error-prone is configured as a compiler
# annotation-processor (``error_prone_core``), not a standalone plugin, so it is
# matched by its artifact stem as well as the hyphenated name.
_PLUGIN_CREDITS = [
    ("maven-checkstyle-plugin", "linting", "Checkstyle"),
    ("spotbugs-maven-plugin", "linting", "SpotBugs"),
    ("findbugs-maven-plugin", "linting", "FindBugs"),
    ("maven-pmd-plugin", "linting", "PMD"),
    ("error_prone_core", "linting", "error-prone"),
    ("error-prone", "linting", "error-prone"),
    ("rewrite-maven-plugin", "modernization", "OpenRewrite"),
    ("modernizer-maven-plugin", "modernization", "Modernizer"),
]

# Cap parsed liveness candidates so a pathological multi-module reactor can't
# bloat run-context.json (mirrors liveness_scan.MAX_CANDIDATES intent).
MAX_JVM_CANDIDATES = 50

# Section headers in ``mvn dependency:analyze`` output. Only "unused declared"
# is surfaced as liveness dead-weight (a declared dependency nothing in the
# module references - coarse module-level dead code). "Used undeclared" is a
# build-hygiene signal, counted in a note but not a liveness candidate.
_UNUSED_DECLARED_HEADER = "unused declared dependencies"
_USED_UNDECLARED_HEADER = "used undeclared dependencies"

# A Maven coordinate line, after the optional ``[WARNING]``/``[INFO]`` log
# prefix is stripped: ``group:artifact:type:version:scope`` (>= 4 colons). The
# leading whitespace under a section header is what Maven uses to nest entries.
_XML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_LOG_PREFIX_RE = re.compile(r"^\s*\[[A-Z]+\]\s*")
_COORD_RE = re.compile(
    r"^([\w.\-]+):([\w.\-]+):[\w.\-]+:[\w.\-]+(?::[\w.\-]+)?\s*$"
)


def _iter_build_files(repo_root: Path, names: set[str],
                      extra_exclude_dirs: set[str] | None = None,
                      extra_exclude_patterns: list[str] | None = None,
                      ) -> list[Path]:
    """Build files matching ``names``, skipping vendored / build / fixture dirs
    and any user-supplied exclude (so a fixture ``pom.xml`` under
    ``tests/fixtures/`` never makes a Python repo look like a Maven project)."""
    from lib.assess_config import is_user_excluded
    extra_dirs = extra_exclude_dirs or set()
    extra_pats = extra_exclude_patterns or []
    out: list[Path] = []
    for name in names:
        for path in repo_root.rglob(name):
            if not path.is_file():
                continue
            rel = path.relative_to(repo_root)
            if is_excluded_path(rel):
                continue
            if is_user_excluded(rel, extra_dirs, extra_pats):
                continue
            out.append(path)
    return out


def detect_build_system(repo_root: Path,
                        extra_exclude_dirs: set[str] | None = None,
                        extra_exclude_patterns: list[str] | None = None,
                        ) -> tuple[str | None, list[str]]:
    """Return ``(build_system, sorted_relative_build_files)``.

    Maven wins when both are present - it is the served path in v1, so a
    polyglot repo with a ``pom.xml`` still gets the liveness offer. Gradle is
    detected (so it honest-degrades with a named candidate) but has no served
    path in v1.
    """
    repo_root = repo_root.resolve()
    poms = _iter_build_files(
        repo_root, {"pom.xml"},
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
    )
    if poms:
        return "maven", sorted(str(p.relative_to(repo_root)) for p in poms)
    gradles = _iter_build_files(
        repo_root, {"build.gradle", "build.gradle.kts"},
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
    )
    if gradles:
        return "gradle", sorted(str(p.relative_to(repo_root)) for p in gradles)
    return None, []


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def detect_configured_plugins(repo_root: Path, build_files: list[str]) -> dict[str, list[str]]:
    """Map ``capability -> [served_by, ...]`` for plugins already configured in
    the given Maven ``build_files``.

    Coarse: a lowercased substring match against each pom's text. Crediting an
    already-configured tool (so it isn't re-offered) is low-harm if over-eager,
    and a precise XML parse would add a dependency for marginal gain in v1.
    """
    served: dict[str, list[str]] = {}
    for rel in build_files:
        # Strip XML comments first: an explanatory comment ("no error-prone yet")
        # must not credit a tool the build doesn't actually configure.
        text = _XML_COMMENT_RE.sub(" ", _read((repo_root / rel))).lower()
        if not text:
            continue
        for needle, capability, label in _PLUGIN_CREDITS:
            if needle in text:
                served.setdefault(capability, [])
                if label not in served[capability]:
                    served[capability].append(label)
    return served


def parse_dependency_analyze(stdout: str, pom_path: str = "pom.xml") -> list[dict]:
    """Parse ``mvn dependency:analyze`` output into coarse liveness candidates.

    Surfaces *unused declared dependencies* - a dependency a module declares but
    nothing in it references, i.e. dead weight at module granularity. Returns
    the same ``{path, line, kind, symbol}`` shape as the per-symbol dead-code
    tier so the existing ``dead_code`` consumers need no change. ``path`` is the
    pom that declared it; ``symbol`` is the ``group:artifact`` coordinate.
    """
    out: list[dict] = []
    in_unused = False
    for raw in stdout.splitlines():
        lowered = raw.lower()
        if _UNUSED_DECLARED_HEADER in lowered:
            in_unused = True
            continue
        if _USED_UNDECLARED_HEADER in lowered:
            in_unused = False
            continue
        if not in_unused:
            continue
        stripped = _LOG_PREFIX_RE.sub("", raw).strip()
        m = _COORD_RE.match(stripped)
        if not m:
            # A non-coordinate line ends the unused-declared block.
            if stripped:
                in_unused = False
            continue
        out.append({
            "path": pom_path,
            "line": 0,
            "kind": "unused declared dependency",
            "symbol": f"{m.group(1)}:{m.group(2)}",
        })
    return out


def count_used_undeclared(stdout: str) -> int:
    """Count *used undeclared dependencies* - a build-hygiene signal noted
    alongside the liveness candidates but not itself dead weight."""
    count = 0
    in_block = False
    for raw in stdout.splitlines():
        lowered = raw.lower()
        if _USED_UNDECLARED_HEADER in lowered:
            in_block = True
            continue
        if _UNUSED_DECLARED_HEADER in lowered:
            in_block = False
            continue
        if not in_block:
            continue
        stripped = _LOG_PREFIX_RE.sub("", raw).strip()
        if _COORD_RE.match(stripped):
            count += 1
        elif stripped:
            in_block = False
    return count


def _liveness_capability(build_system: str, mvn_on_path: bool,
                         analyze_output: str | None,
                         pom_path: str) -> dict:
    """Build the ``liveness`` capability entry, the only one healed in v1."""
    cap: dict[str, Any] = {
        "candidate_tool": _CANDIDATE_TOOLS["liveness"],
        "gloss": _CAPABILITY_GLOSS["liveness"],
    }
    # Gradle has no served liveness path in v1 - honest-degrade with a named
    # candidate rather than a silent miss.
    if build_system != "maven":
        cap.update({
            "state": "honest_degrade",
            "consent": None,
            "candidate_count": 0,
            "candidates": [],
            "note": ("Gradle liveness is deferred to a fast-follow; Maven is the "
                     "served path in v1. A candidate tool is named so the "
                     "capability degrades honestly rather than silently."),
        })
        return cap
    if analyze_output is not None:
        candidates = parse_dependency_analyze(analyze_output, pom_path=pom_path)[:MAX_JVM_CANDIDATES]
        used_undeclared = count_used_undeclared(analyze_output)
        cap.update({
            "state": "served",
            "consent": "run",
            "candidate_count": len(candidates),
            "candidates": candidates,
            "used_undeclared_count": used_undeclared,
            "note": ("Coarse module-level liveness from `mvn dependency:analyze`: "
                     f"{len(candidates)} unused declared dependency(ies), "
                     f"{used_undeclared} used-undeclared. Dependency granularity, "
                     "not per-symbol - it flags dead weight a module declares but "
                     "never references."),
        })
        return cap
    # Maven detected, analyze not run: OFFER. Consent shape depends on whether
    # mvn is already invokable (run-consent) or must be installed first.
    cap.update({
        "state": "offer",
        "consent": "run" if mvn_on_path else "install",
        "candidate_count": 0,
        "candidates": [],
        "note": (
            "`mvn dependency:analyze` needs a compiling build (resolves deps, "
            "may hit the network), so a read-only assessment does not run it by "
            "default. " + (
                "Offer to RUN it against the project (run-consent)."
                if mvn_on_path else
                "Maven is not on PATH; offer to INSTALL it, then run the analyze "
                "goal (install-consent)."
            )
        ),
    })
    return cap


def _credited_or_degraded(capability: str, served: dict[str, list[str]]) -> dict:
    """Build a non-liveness capability entry: credited when a configured plugin
    serves it, otherwise honest-degrade with a named candidate."""
    cap: dict[str, Any] = {
        "candidate_tool": _CANDIDATE_TOOLS[capability],
        "gloss": _CAPABILITY_GLOSS[capability],
    }
    if capability in served:
        cap.update({
            "state": "credited",
            "served_by": served[capability],
            "note": (f"Already served by configured {', '.join(served[capability])} "
                     "in pom.xml - detected and credited, not re-offered."),
        })
    else:
        cap.update({
            "state": "honest_degrade",
            "note": (f"No configured tool serves {capability} "
                     f"({_CAPABILITY_GLOSS[capability]}); v1 names a candidate "
                     "rather than healing it. Honest-degrade is a deliverable, "
                     "not a silent miss."),
        })
    return cap


def scan_jvm_capabilities(repo_root: Path, *,
                          run_build_tools: bool = False,
                          analyze_output: str | None = None,
                          mvn_on_path: bool | None = None,
                          extra_exclude_dirs: set[str] | None = None,
                          extra_exclude_patterns: list[str] | None = None,
                          ) -> dict:
    """Capability-driven JVM scan. Never raises - degrades to ``available: False``.

    ``analyze_output`` lets a caller (and CI's consumption test) feed canned
    ``mvn dependency:analyze`` output without invoking Maven, so the SERVED path
    is exercised deterministically. With ``run_build_tools=True`` and ``mvn`` on
    PATH and no ``analyze_output`` supplied, the analyze goal is run for real
    (opt-in run-consent). The default scan stays read-only: liveness reports the
    ``offer`` state instead.
    """
    repo_root = repo_root.resolve()
    build_system, build_files = detect_build_system(
        repo_root,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
    )
    if build_system is None:
        return {"available": False, "build_system": None, "build_files": []}

    if mvn_on_path is None:
        mvn_on_path = shutil.which("mvn") is not None
    pom_path = build_files[0] if build_files else "pom.xml"

    # Opt-in run-consent: actually run the analyze goal when asked and possible.
    if (build_system == "maven" and analyze_output is None
            and run_build_tools and mvn_on_path):
        analyze_output = _run_dependency_analyze(repo_root)

    served = (detect_configured_plugins(repo_root, build_files)
              if build_system == "maven" else {})

    capabilities = {
        "liveness": _liveness_capability(
            build_system, mvn_on_path, analyze_output, pom_path),
        "module_graph": _credited_or_degraded("module_graph", served),
        "linting": _credited_or_degraded("linting", served),
        "modernization": _credited_or_degraded("modernization", served),
    }
    return {
        "available": True,
        "build_system": build_system,
        "build_files": build_files,
        "capabilities": capabilities,
    }


def _run_dependency_analyze(repo_root: Path) -> str | None:
    """Run ``mvn dependency:analyze`` (opt-in run-consent). Returns combined
    stdout/stderr, or ``None`` on any failure - the scan then falls back to the
    offer state rather than crashing the assessment."""
    import subprocess
    try:
        proc = subprocess.run(
            ["mvn", "-q", "dependency:analyze"],
            cwd=str(repo_root), capture_output=True, text=True,
            timeout=300, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (proc.stdout or "") + (proc.stderr or "")
