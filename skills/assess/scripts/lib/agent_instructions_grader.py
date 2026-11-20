"""Heuristic agent-instructions grader.

Filename-agnostic. Scores any agent instruction file - CLAUDE.md, AGENTS.md,
GEMINI.md, .cursorrules, .github/copilot-instructions.md - on what outcome
studies say helps an agent, not on how much the file says:

    verified_commands:   commands (fenced shell block or inline code) that
                         resolve to a real script, target, recipe, tool config
                         or CI step in the repo (``lib.command_resolver``) -
                         the largest credit, because agents run the tools a
                         file names (Gloaguen et al., 2026); a standard CLI
                         such as ``gh`` or ``kubectl`` earns partial credit
    path_references_existing: backticked paths that are tracked
    positive_directives / tradeoff_phrases / verifiable_outcomes: credited up
                         to a small floor only; past it, more instructions
                         lower instruction-following accuracy (IFScale)
    always-on size and share: the file split on its headings
                         (``lib.instruction_placement.analyse_file``); a
                         penalty for always-on lines past 200, another for
                         the lines that
                         belong in a skill or a nested file but load on every
                         task, and a B cap past 400 always-on lines
    content penalties:   directory-tree blocks, repository-overview sections,
                         and lines repeated from the root README
    freshness:           days since last content change

Commands that name a missing target and backticked paths or symbols that do
not exist in the tracked files are returned as ``Grade.findings``, never a hard fail.
The repository-checked signals need ``repo`` (a
``lib.instruction_content.RepoContext``); without it, commands earn no credit
and path references are counted unverified.

No LLM calls. Pure regex + arithmetic over the text and the tracked files (the working tree, so a clean checkout is HEAD).
Deterministic.
"""
from __future__ import annotations

import math
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TypedDict

from lib.command_resolver import Command, Resolution, extract_commands, resolve
from lib.instruction_content import (
    RepoContext,
    check_paths,
    check_symbols,
    count_directory_trees,
    overview_headings,
    readme_overlap_pct,
)
from lib.instruction_placement import ALWAYS_ON_FLAG_LINES, PlacementFile, analyse_file
from lib.run_context_types import GradeFinding


class SensitiveFinding(TypedDict):
    """One REDACTED sensitive-content hit in an instruction file."""

    category: str
    evidence: str


POSITIVE_DIRECTIVE_PATTERNS = [
    r"\bUse\b",
    r"\bPrefer\b",
    r"\bChoose\b",
    r"\bDefault to\b",
    r"\bMatch\b",
    r"\bAdd\b",
    r"\bRun\b",
]

TRADEOFF_PATTERNS = [
    r"\bbecause\b",
    r"\bover\s+(?!the\b|a\b|an\b|time\b|all\b|here\b|there\b|to\b|in\b|on\b|with\b)\w+",
    r"\binstead of\b",
    r"\brather than\b",
    r"\btradeoff\b",
]

PATH_PATTERN = re.compile(
    r"`[^`]*[/\\][^`\s]+`"          # backtick-wrapped paths
    r"|(?:^|\s)[\w./-]+/[\w./-]+"   # bare paths with at least one slash
)

VERIFIABLE_PATTERNS = [
    # Phrase-based outcomes (JS/Python/general prose idioms).
    r"\bworking if\b",
    r"\bverify:\b",
    r"\bsuccess criteria\b",
    r"\bacceptance criteria\b",
    # Runnable verification commands (issue #116). A CLAUDE.md that hands the
    # agent an exact command to confirm an outcome is just as "verifiable" as
    # one that spells out "success criteria" in prose - the original idiom set
    # only credited JS/Python phrasing and scored Maven/Gradle/JVM files zero.
    # Kept high-precision (a tool name plus a real subcommand/flag) so prose
    # that merely mentions Maven or Gradle is not credited.
    r"\bmvn\s+(?:-\S+\s+)*(?:clean\s+)?(?:test|verify|install|integration-test)\b",  # mvn test / verify
    r"-Dtest=\S",                                                                     # mvn test -Dtest=Class#method
    r"(?:^|\s)\.?/?gradlew?\s+(?:-\S+\s+)*(?:test|build|check|clean|assemble)\b",     # gradle / ./gradlew test|build|check
    r"--tests\s+\S",                                                                  # gradle test --tests Foo
    # ripgrep verification recipes - require a flag or a quoted query so that
    # bare prose mentions ("use rg to find things", "rg or grep") are not
    # credited, only an actual runnable search command.
    r"\brg\s+(?:(?:-{1,2}[\w-]+\s+)+\S|(?:-{1,2}[\w-]+\s+)*['\"]\S)",
]

# Always-on size and share (issue #560, replacing the #517 whole-file curve).
# Every line of an always-loaded file is paid on every task: Gloaguen et al.
# (2026, https://arxiv.org/html/2602.11988v1) measured context files raising
# agent cost 19-23% while lifting success about 4% at best. The file is split
# on its headings (``lib.instruction_placement``) and two kinds of line cost
# points, one point per ``*_LINES_PER_POINT`` lines, each capped:
#
# - always-on lines past ``ALWAYS_ON_FLAG_LINES`` (200, Claude Code's own
#   "target under 200 lines per CLAUDE.md file",
#   https://code.claude.com/docs/en/memory). Pointers, rules and identity are
#   what the layer is for; past 200 lines even they cost. Not gated on the
#   block's ``flagged`` (which also needs more than half the file): with that
#   gate, adding reference lines to a file could drop it under half and lower
#   its penalty, so the score would reward the wrong action. ``flagged`` stays
#   the block's advice flag. Lines are counted as line-equivalents: the
#   larger of the always-on lines and always-on words / ``WORDS_PER_LINE``
#   (about 12 words a line, the ratio the #517 word curve used), so a file of
#   very long lines cannot dodge the term or the B cap.
# - fetch-on-need lines: sections that belong in a skill, a nested file or an
#   existing SKILL.md, which a two-line pointer would replace. This is the
#   share term: a file that is mostly reference pays for it at any size, and a
#   large file that is mostly always-on content pays nothing for the share.
#
# Both terms only grow as lines are added outside a section: a section's
# classification reads its own text, the skills and the count of headed
# sections (which only grows), never the file's total length, so lines added
# elsewhere cannot declassify it. Past ``ALWAYS_ON_GRADE_CAP_LINES`` always-on
# lines no amount of other credit
# makes the file lean: the grade is capped at B (``SIZE_GRADE_CAP_SCORE``).
# Skills beside the file no longer halve anything: the placement split names
# what to move, so delegation is credited by moving it.
ALWAYS_ON_LINES_PER_POINT = 10
ALWAYS_ON_MAX_PENALTY = 30
WORDS_PER_LINE = 12
RELOCATABLE_LINES_PER_POINT = 10
RELOCATABLE_MAX_PENALTY = 30
ALWAYS_ON_GRADE_CAP_LINES = 2 * ALWAYS_ON_FLAG_LINES
SIZE_GRADE_CAP_SCORE = 59

# Scoring weights (points each, cap). Verified commands carry the most credit;
# counts of directives, tradeoff phrases and paths are a small floor, because
# instruction-following accuracy falls as instruction count rises
# (IFScale, https://arxiv.org/abs/2507.11538).
BASELINE_POINTS = 10
COMMAND_POINTS, COMMAND_CAP = 12, 40
# A standard CLI (``lib.command_resolver.EXTERNAL_TOOLS``) is a real command
# with no repo target to verify: partial credit, inside COMMAND_CAP. A package
# manager's built-in (``npm ci``, ``uv sync``) stays at full credit because
# its precondition is checked against the repo (the manifest it reads exists),
# so the resolver has confirmed it will run here; ``gh pr checks`` gets no
# such check.
EXTERNAL_POINTS, EXTERNAL_CAP = 4, 16
DIRECTIVE_POINTS, DIRECTIVE_CAP = 2, 10
TRADEOFF_POINTS, TRADEOFF_CAP = 3, 10
PATH_POINTS, PATH_CAP = 3, 15
VERIFIABLE_POINTS, VERIFIABLE_CAP = 5, 10

# Content the evidence says does not help (Gloaguen et al.: repository
# overviews did not improve success). README overlap is the share of the
# file's substantive lines repeated from the root README.
TREE_PENALTY = 10
OVERVIEW_PENALTY = 5
README_OVERLAP_STEPS = ((50, 15), (25, 8))  # (overlap % at least, penalty)

# --- Sensitive-content scan (issue #56) -----------------------------------
# Before /assess recommends committing ANY instruction file - especially to a
# public repo - it must scan the candidate text for content that should not be
# published: infrastructure recon (IPs, SSH/host details), credentials, and
# home-directory / PII paths. Conservative by design: high-precision signals so
# a legitimate instruction file is not flagged. Every finding's evidence is
# REDACTED before it leaves this module - the scan must not itself copy the
# secret it is warning about into run-context.json (which ships in the wiki).

# Placeholder values that mean "fill this in", not a real secret. A credential
# assignment whose value matches one of these is not flagged.
_CREDENTIAL_PLACEHOLDERS = re.compile(
    r"^(?:x{2,}|\*{2,}|\.{3,}|-{2,}|_+|"
    r"your[_-]?\w*|my[_-]?\w*|some[_-]?\w*|example\w*|placeholder\w*|"
    r"change[_-]?me|todo|tbd|none|null|env|secret|password|token|"
    r"\$\{?\w+\}?|<[^>]+>|\{\{[^}]+\}\})$",
    re.IGNORECASE,
)


def _redact(token: str, *, keep: int = 0) -> str:
    """Mask the bulk of a token so the warning never republishes the secret."""
    token = token.strip()
    if keep <= 0 or len(token) <= keep:
        return "***"
    return f"{token[:keep]}***"


def _scan_ip_addresses(text: str) -> list[str]:
    findings: list[str] = []
    for m in re.finditer(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w.])", text):
        octets = [int(g) for g in m.groups()]
        if any(o > 255 for o in octets):
            continue  # not a valid IPv4 - likely a version string
        # Loopback / unspecified are harmless and noisy; skip them.
        if octets[0] == 127 or octets == [0, 0, 0, 0]:
            continue
        findings.append(f"{octets[0]}.x.x.x")
    return findings


def scan_sensitive_content(text: str) -> list[SensitiveFinding]:
    """Scan an instruction file for content unsafe to commit (issue #56).

    Returns a list of ``{"category": str, "evidence": str}`` findings with the
    evidence REDACTED. Categories:

        private_key   - an embedded PEM private key block
        cloud_key     - an AWS-style access key id
        credential    - a ``password=``/``token=``/``api_key=`` assignment with
                        a concrete (non-placeholder) value
        ssh_or_host   - root@host / ssh user@host login details
        ip_address    - a routable/private IPv4 literal (loopback excluded)
        home_path     - a personal home-directory path (/Users/<name>/, ...)

    Conservative: high-precision signals only. An empty list means "nothing
    obviously sensitive found" - not a guarantee, so the prose still advises a
    human glance before committing to a public repo.
    """
    findings: list[SensitiveFinding] = []
    seen: set[tuple[str, str]] = set()

    def add(category: str, evidence: str) -> None:
        key = (category, evidence)
        if key not in seen:
            seen.add(key)
            findings.append({"category": category, "evidence": evidence})

    if re.search(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----", text):
        add("private_key", "-----BEGIN PRIVATE KEY-----")

    for m in re.finditer(r"\b(AKIA[0-9A-Z]{16})\b", text):
        add("cloud_key", _redact(m.group(1), keep=4))

    cred = re.compile(
        r"\b(password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|token)\b"
        r"\s*[:=]\s*(\"[^\"]+\"|'[^']+'|\S+)",
        re.IGNORECASE,
    )
    for m in cred.finditer(text):
        value = m.group(2).strip("\"'")
        if not value or _CREDENTIAL_PLACEHOLDERS.match(value):
            continue
        add("credential", f"{m.group(1).lower()}=***")

    for m in re.finditer(r"\broot@[A-Za-z0-9._-]+", text):
        add("ssh_or_host", "root@***")
    for m in re.finditer(r"\bssh\s+[A-Za-z0-9._-]+@[A-Za-z0-9._-]+", text):
        add("ssh_or_host", "ssh ***@***")

    for ip in _scan_ip_addresses(text):
        add("ip_address", ip)

    for m in re.finditer(r"(?:/Users/|/home/)([A-Za-z0-9._-]+)/", text):
        name = m.group(1)
        if name.lower() in {"user", "username", "you", "name", "shared", "public"}:
            continue  # generic placeholder, not a person's home dir
        add("home_path", "/Users/***/" if "/Users/" in m.group(0) else "/home/***/")
    for m in re.finditer(r"[A-Za-z]:\\Users\\([^\\/]+)\\", text):
        if m.group(1).lower() not in {"user", "username", "public", "default"}:
            add("home_path", "C:\\Users\\***\\")

    return findings


# --- Alias detection (issue #57) ------------------------------------------
# Claude Code reads a single canonical CLAUDE.md. A repo that also wants an
# AGENTS.md (for Codex) or GEMINI.md (for Gemini CLI) should point it AT the
# canonical file - a thin stub or symlink - not maintain a second standalone
# document. Detect that thin-stub shape so the grader can treat it as an alias
# (inheriting the canonical grade) rather than a low-scoring standalone doc.

# Canonical instruction filenames an alias might point at.
_CANONICAL_BASENAMES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md")

# A stub is "thin" when it carries essentially no instruction content of its own.
ALIAS_MAX_NONBLANK_LINES = 12
ALIAS_MAX_WORDS = 80


class AliasResult(TypedDict):
    """``detect_alias``'s verdict: whether the file is a thin alias, and of what."""

    is_alias: bool
    alias_target: str | None


def detect_alias(text: str) -> AliasResult:
    """Detect a thin alias/stub that points at a canonical instruction file.

    A thin alias is a short file whose only real content is a reference to a
    canonical instruction file (e.g. an ``AGENTS.md`` that says "see CLAUDE.md").
    Treating it as an alias avoids grading it as a bespoke standalone doc and
    avoids recommending it be rewritten into a duplicate routing document.

    Returns ``{"is_alias": bool, "alias_target": str | None}`` where
    ``alias_target`` is the referenced canonical basename (e.g. ``CLAUDE.md``).
    """
    stripped = text.strip()
    nonblank = [ln for ln in stripped.splitlines() if ln.strip()]
    words = len(stripped.split())
    if not nonblank or len(nonblank) > ALIAS_MAX_NONBLANK_LINES or words > ALIAS_MAX_WORDS:
        return {"is_alias": False, "alias_target": None}

    for basename in _CANONICAL_BASENAMES:
        if re.search(rf"\b{re.escape(basename)}\b", stripped):
            return {"is_alias": True, "alias_target": basename}
    return {"is_alias": False, "alias_target": None}


@dataclass(frozen=True)
class Grade:
    score: int
    grade: str
    subscores: dict[str, int] = field(default_factory=dict)
    findings: list[GradeFinding] = field(default_factory=list)


def _count(text: str, patterns: list[str]) -> int:
    total = 0
    for p in patterns:
        total += len(re.findall(p, text, re.IGNORECASE))
    return total


def count_positive_directives(text: str) -> int:
    return _count(text, POSITIVE_DIRECTIVE_PATTERNS)


def count_tradeoff_phrases(text: str) -> int:
    return _count(text, TRADEOFF_PATTERNS)


def count_path_references(text: str) -> int:
    return len(PATH_PATTERN.findall(text))


def count_verifiable_outcomes(text: str) -> int:
    return _count(text, VERIFIABLE_PATTERNS)


class PlacementPenalty(TypedDict):
    """The always-on size and share penalty for one file (see the constants)."""

    always_on_penalty: int
    relocatable_penalty: int
    capped: bool


def always_on_equivalent_lines(record: PlacementFile) -> int:
    """Always-on lines, or always-on words / ``WORDS_PER_LINE`` when larger."""
    return max(record["always_on_lines"],
               math.ceil(record["always_on_words"] / WORDS_PER_LINE))


def placement_penalty(record: PlacementFile) -> PlacementPenalty:
    """Score one ``instruction_placement`` record on always-on size and share.

    ``always_on_penalty``: one point per ``ALWAYS_ON_LINES_PER_POINT``
    always-on line-equivalents (``always_on_equivalent_lines``) past
    ``ALWAYS_ON_FLAG_LINES``, capped at
    ``ALWAYS_ON_MAX_PENALTY``. ``relocatable_penalty``: one point per
    ``RELOCATABLE_LINES_PER_POINT`` fetch-on-need lines, capped at
    ``RELOCATABLE_MAX_PENALTY``. ``capped``: a file past
    ``ALWAYS_ON_GRADE_CAP_LINES`` always-on line-equivalents grades at most
    B. Neither
    reads ``flagged``, so for a fixed split each grows with the lines added.
    """
    always_on = always_on_equivalent_lines(record)
    over = max(0, always_on - ALWAYS_ON_FLAG_LINES)
    return {
        "always_on_penalty": min(math.ceil(over / ALWAYS_ON_LINES_PER_POINT),
                                 ALWAYS_ON_MAX_PENALTY),
        "relocatable_penalty": min(
            math.ceil(record["fetch_on_need_lines"] / RELOCATABLE_LINES_PER_POINT),
            RELOCATABLE_MAX_PENALTY),
        "capped": always_on > ALWAYS_ON_GRADE_CAP_LINES,
    }


# Scores below this are an F (see ``_letter_grade``).
F_GRADE_CUTOFF = 25


def _letter_grade(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 70:
        return "A-"
    if score >= 60:
        return "B+"
    if score >= 50:
        return "B"
    if score >= 40:
        return "C"
    if score >= F_GRADE_CUTOFF:
        return "D"
    return "F"


def _capped(count: int, points: int, cap: int) -> int:
    return min(count * points, cap)


def _resolve_for_file(cmd: Command, repo: RepoContext, path: str) -> Resolution:
    """Resolve from the repo root, then, for a nested file, from its directory.

    A nested ``packages/api/AGENTS.md`` may mean ``./scripts/dev.sh`` relative
    to itself or to the root; a ``missing`` from the root is retried from the
    file's directory so neither reading produces a false finding.
    """
    res = resolve(cmd, index=repo.index)
    self_dir = posixpath.dirname(path)
    if res.verdict != "missing" or not self_dir:
        return res
    local = resolve(replace(cmd, cwd=posixpath.join(self_dir, cmd.cwd)), index=repo.index)
    return res if local.verdict == "missing" else local


def _repo_signals(text: str, repo: RepoContext, path: str) -> tuple[dict[str, int], list[GradeFinding]]:
    """Subscores and findings that need the repository: commands and references."""
    findings: list[GradeFinding] = []
    verified: set[str] = set()
    external: set[str] = set()
    missing: set[str] = set()
    unknown: set[str] = set()
    for cmd in extract_commands(text):
        res = _resolve_for_file(cmd, repo, path)
        key = " ".join(cmd.text.split())
        if res.verdict == "resolved":
            verified.add(key)
        elif res.verdict == "external":
            external.add(key)
        elif res.verdict == "unknown":
            unknown.add(key)
        elif key not in missing:
            missing.add(key)
            findings.append({"kind": "unresolved_command", "line": cmd.line,
                             "reference": key, "reason": res.reason})
    existing, stale_paths = check_paths(text, repo, path)
    stale_symbols = check_symbols(text, repo, path)
    findings.extend({"kind": r["kind"], "line": r["line"], "reference": r["reference"],
                     "reason": r["reason"]} for r in [*stale_paths, *stale_symbols])
    sub = {
        "verified_commands": len(verified),
        "external_commands": len(external),
        "unresolved_commands": len(missing),
        "unknown_commands": len(unknown),
        "path_references_existing": len({r.text for r in existing}),
        "stale_references": len(stale_paths) + len(stale_symbols),
        "readme_overlap_pct": readme_overlap_pct(text, repo.readme_text),
    }
    return sub, sorted(findings, key=lambda f: (f["line"], f["kind"], f["reference"]))


def _content_penalty(sub: dict[str, int]) -> int:
    penalty = TREE_PENALTY if sub["directory_trees"] else 0
    penalty += OVERVIEW_PENALTY if sub["overview_sections"] else 0
    for floor, points in README_OVERLAP_STEPS:
        if sub["readme_overlap_pct"] >= floor:
            penalty += points
            break
    return penalty


def _freshness_penalty(freshness_days: int) -> int:
    if freshness_days > 365:
        return 10
    return 5 if freshness_days > 180 else 0


def grade_instructions(
    text: str,
    freshness_days: int,
    *,
    skills: Mapping[str, str] | None = None,
    repo: RepoContext | None,
    path: str = "",
) -> Grade:
    """Score an agent instruction file (CLAUDE.md / AGENTS.md / GEMINI.md / etc.) and return a Grade.

    Scoring (max 95, clamped to 0-100):
        baseline:                 +10 for any content
        verified_commands:        12 points each, capped at 40
        external_commands:        4 points each, capped at 16; verified and
                                  external together capped at 40
        positive_directives:      2 points each, capped at 10
        tradeoff_phrases:         3 points each, capped at 10
        path_references_existing: 3 points each, capped at 15
        verifiable_outcomes:      5 points each, capped at 10
        freshness penalty:        -10 if > 365 days, -5 if > 180
        bloat_penalty:            always-on size and share
                                  (``placement_penalty``): -1 per 10 always-on
                                  lines past 200 (cap 30), counting 12 words
                                  as a line when that is more, plus -1 per 10
                                  fetch-on-need lines (cap 30)
        size grade cap:           over 400 always-on lines (or 4800 always-on
                                  words) the score is capped at 59 (a B)
        content_penalty:          -10 for a directory-tree block, -5 for a
                                  repository-overview section, -8 / -15 when
                                  25% / 50% of the file's lines repeat the README

    Args:
        skills: every ``SKILL.md`` text by repo-relative path
            (``lib.instruction_placement.skill_texts``), so a section a skill
            already holds counts as fetch-on-need, as in the
            ``instruction_placement`` block. ``None`` compares against none.
        repo: the repository context, required so a caller cannot drop it
            by accident. ``None`` is the explicit text-only opt-out: no
            command earns credit, path references are counted unverified, and
            no finding is produced (a file graded this way tops out at 55, a B).
        path: the file's repo-relative path, so a relative path reference
            resolves from its directory and its own text is not searched for
            the symbols it names.

    Subscore keys read downstream (``positive_directives``,
    ``tradeoff_phrases``, ``path_references``, ``verifiable_outcomes``,
    ``line_count``, ``word_count``) keep their meaning, and ``bloat_penalty``
    stays the total size penalty (now ``always_on_penalty`` plus
    ``relocatable_penalty``):
    ``path_references`` stays the raw count, and the credited count is
    ``path_references_existing``.
    """
    if not text.strip():
        return Grade(score=0, grade="F", subscores={})

    sub = {
        "positive_directives": count_positive_directives(text),
        "tradeoff_phrases": count_tradeoff_phrases(text),
        "path_references": count_path_references(text),
        "verifiable_outcomes": count_verifiable_outcomes(text),
    }
    sub["line_count"] = len(text.splitlines())
    sub["word_count"] = len(text.split())
    placement = analyse_file(text, path or "CLAUDE.md", "instruction", skills or {})
    sub["always_on_lines"] = placement["always_on_lines"]
    sub["always_on_words"] = placement["always_on_words"]
    sub["always_on_pct"] = round(placement["always_on_share"] * 100)
    sub["fetch_on_need_lines"] = placement["fetch_on_need_lines"]
    sub["always_on_flagged"] = int(placement["flagged"])

    findings: list[GradeFinding] = []
    if repo is not None:
        repo_sub, findings = _repo_signals(text, repo, path)
        sub.update(repo_sub)
    else:
        sub.update({"verified_commands": 0, "external_commands": 0,
                    "unresolved_commands": 0, "unknown_commands": 0,
                    "path_references_existing": sub["path_references"],
                    "stale_references": 0, "readme_overlap_pct": 0})
    sub["directory_trees"] = count_directory_trees(text)
    sub["overview_sections"] = len(overview_headings(text))

    score = BASELINE_POINTS
    score += min(
        _capped(sub["verified_commands"], COMMAND_POINTS, COMMAND_CAP)
        + _capped(sub["external_commands"], EXTERNAL_POINTS, EXTERNAL_CAP),
        COMMAND_CAP,
    )
    score += _capped(sub["positive_directives"], DIRECTIVE_POINTS, DIRECTIVE_CAP)
    score += _capped(sub["tradeoff_phrases"], TRADEOFF_POINTS, TRADEOFF_CAP)
    score += _capped(sub["path_references_existing"], PATH_POINTS, PATH_CAP)
    score += _capped(sub["verifiable_outcomes"], VERIFIABLE_POINTS, VERIFIABLE_CAP)
    score -= _freshness_penalty(freshness_days)

    size = placement_penalty(placement)
    sub["always_on_penalty"] = size["always_on_penalty"]
    sub["relocatable_penalty"] = size["relocatable_penalty"]
    bloat_penalty = size["always_on_penalty"] + size["relocatable_penalty"]
    sub["bloat_penalty"] = bloat_penalty
    sub["content_penalty"] = _content_penalty(sub)
    # Before the size and content penalties and before clamping, so a reader
    # can tell an F the penalties caused from one the content earned.
    sub["score_before_penalties"] = score
    score -= bloat_penalty + sub["content_penalty"]

    score = max(0, min(score, 100))
    sub["size_grade_capped"] = int(size["capped"])
    if sub["size_grade_capped"]:
        score = min(score, SIZE_GRADE_CAP_SCORE)
    return Grade(score=score, grade=_letter_grade(score), subscores=sub, findings=findings)
