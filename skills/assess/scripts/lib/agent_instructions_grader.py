"""Heuristic agent-instructions grader.

Filename-agnostic. Scores any agent instruction file - CLAUDE.md, AGENTS.md,
GEMINI.md, .cursorrules, .github/copilot-instructions.md - on signals that
correlate with usefulness to an LLM contributor:

    positive_directives: "Use X", "Prefer Y", "Default to Z" (positive framing)
    tradeoff_phrases:    "because", "over X", "instead of", "rather than"
    path_references:     file paths like src/foo/bar.py, tests/..., etc.
    verifiable_outcomes: "Working if", "verify:", "success criteria", or a
                         runnable verification command (mvn/gradle/rg)
    freshness:           days since last git modification

No LLM calls. Pure regex + arithmetic. Deterministic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


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

# Size/bloat metrics with CONSERVATIVE thresholds.
# Conservative threshold rationale: small/legitimate instruction files must
# never be penalized. 500 lines / 3000 words covers the common case of a
# well-structured CLAUDE.md with sections, examples, and patterns - only
# genuinely bloated files cross this threshold.
SIZE_THRESHOLD_LINES = 500
SIZE_THRESHOLD_WORDS = 3000

# Skills delegation detection - text patterns that indicate progressive
# disclosure (guidance factored into on-demand skills rather than inlined).
SKILL_DELEGATION_PATTERNS = [
    r"\.claude/skills/",
    r"skills/\w+/SKILL\.md",
    r"skill\s+\(.*?loaded\s+on\s+demand",
    r"via\s+the\s+`?\w+-?\w*`?\s+skill",
    r"load(?:s|ed)?\s+on\s+demand",
    r"progressive\s+disclosure",
]

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


def scan_sensitive_content(text: str) -> list[dict]:
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
    findings: list[dict] = []
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


def detect_alias(text: str) -> dict:
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


def compute_size_metrics(text: str) -> dict:
    """Return line_count, word_count, and threshold-exceeded flags."""
    lines = text.splitlines()
    words = len(text.split())
    return {
        "line_count": len(lines),
        "word_count": words,
        "exceeds_line_threshold": len(lines) > SIZE_THRESHOLD_LINES,
        "exceeds_word_threshold": words > SIZE_THRESHOLD_WORDS,
    }


def detect_skills_delegation(text: str) -> dict:
    """Detect if an instruction file delegates to skills (progressive-disclosure
    pointers). Presence means the repo factors guidance into on-demand skills
    rather than inlining everything into one monolithic file."""
    matches: list[str] = []
    for p in SKILL_DELEGATION_PATTERNS:
        found = re.findall(p, text, re.IGNORECASE)
        matches.extend(found)
    return {
        "delegates_to_skills": len(matches) > 0,
        "delegation_pointers": len(matches),
        "delegation_samples": matches[:5],  # first 5 for evidence
    }


def detect_skills_dir(repo_root: Path) -> dict:
    """Check for the presence of skills directories in the repo.

    Looks for `.claude/skills/` and `skills/` and counts the `*/SKILL.md`
    files within. A repo with skills is using progressive disclosure, so a
    large instruction file is not necessarily bloat.
    """
    skills_paths = [
        repo_root / ".claude" / "skills",
        repo_root / "skills",
    ]
    found_dirs: list[str] = []
    skill_files: list[str] = []
    for sp in skills_paths:
        if sp.is_dir():
            found_dirs.append(str(sp.relative_to(repo_root)))
            for skill_md in sp.glob("*/SKILL.md"):
                skill_files.append(str(skill_md.relative_to(repo_root)))
    return {
        "skills_dirs_present": len(found_dirs) > 0,
        "skills_dirs": found_dirs,
        "skills_count": len(skill_files),
        "skill_files": skill_files,
    }


def compute_bloat_penalty(
    size_metrics: dict,
    skills_present: bool,
    delegates_to_skills: bool,
) -> tuple[int, str | None]:
    """Compute the point penalty for an oversized monolithic instruction file.

    Returns: (penalty_points, remediation_message)

    Asymmetric scoring:
    - Lean file (not oversized) -> no penalty
    - Oversized file + skills factoring (dir present or delegation pointers)
      -> no penalty; the repo uses progressive disclosure
    - Oversized file + NO skills -> PENALTY scaled by overage

    Penalty scale (conservative - only clear bloat is penalized):
    - 500-750 lines: -5, 750-1000: -10, 1000+: -15
    - Word count applies the same tiers at 3000/4500/6000 words
    - Take the higher penalty of the two metrics
    """
    is_oversized = (
        size_metrics["exceeds_line_threshold"]
        or size_metrics["exceeds_word_threshold"]
    )

    if not is_oversized:
        return 0, None

    if skills_present or delegates_to_skills:
        # Repo uses progressive disclosure - no penalty even if the
        # instruction file is large (it may be a hub that points to skills).
        return 0, None

    lines = size_metrics["line_count"]
    line_penalty = 0
    if lines > 1000:
        line_penalty = 15
    elif lines > 750:
        line_penalty = 10
    elif lines > SIZE_THRESHOLD_LINES:
        line_penalty = 5

    words = size_metrics["word_count"]
    word_penalty = 0
    if words > 6000:
        word_penalty = 15
    elif words > 4500:
        word_penalty = 10
    elif words > SIZE_THRESHOLD_WORDS:
        word_penalty = 5

    penalty = max(line_penalty, word_penalty)

    remediation = (
        f"Instruction file exceeds size threshold ({lines} lines, {words} words) "
        "without factoring guidance into on-demand skills. Remediation: factor "
        "guidance into on-demand skills - extract topic-specific guidance into "
        "`.claude/skills/*/SKILL.md` files loaded when relevant, keeping the root "
        "instruction file lean."
    )

    return penalty, remediation


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
    if score >= 25:
        return "D"
    return "F"


def grade_instructions(
    text: str,
    freshness_days: int,
    *,
    skills_present: bool = False,
    delegates_to_skills: bool | None = None,
) -> Grade:
    """Score an agent instruction file (CLAUDE.md / AGENTS.md / GEMINI.md / etc.) and return a Grade.

    Scoring weights (max 100):
        positive_directives:  3 points each, capped at 30
        tradeoff_phrases:     5 points each, capped at 25
        path_references:      3 points each, capped at 20
        verifiable_outcomes:  10 points each, capped at 15
        freshness penalty:    -10 if > 365 days, -5 if > 180, 0 otherwise
                              +10 baseline if file has any content
        bloat penalty:        -5/-10/-15 for an oversized monolithic file that
                              does NOT factor guidance into on-demand skills

    Args:
        skills_present: whether the repo has a skills directory (auto-detected
            by the caller via ``detect_skills_dir``).
        delegates_to_skills: whether the text itself contains progressive-
            disclosure pointers. ``None`` (the default) auto-detects from text.

    The bloat penalty makes an oversized monolith score STRICTLY BELOW an
    equivalent lean-file-plus-skills repo - the monolith is penalized, not
    merely annotated. Conservative thresholds (500 lines / 3000 words) ensure
    small/legitimate instruction files are never penalized.
    """
    if not text.strip():
        return Grade(score=0, grade="F", subscores={})

    # Auto-detect delegation from text when not explicitly provided.
    if delegates_to_skills is None:
        delegates_to_skills = detect_skills_delegation(text)["delegates_to_skills"]

    sub = {
        "positive_directives": count_positive_directives(text),
        "tradeoff_phrases": count_tradeoff_phrases(text),
        "path_references": count_path_references(text),
        "verifiable_outcomes": count_verifiable_outcomes(text),
    }

    size_metrics = compute_size_metrics(text)
    sub["line_count"] = size_metrics["line_count"]
    sub["word_count"] = size_metrics["word_count"]

    score = 10  # baseline for non-empty content
    score += min(sub["positive_directives"] * 3, 30)
    score += min(sub["tradeoff_phrases"] * 5, 25)
    score += min(sub["path_references"] * 3, 20)
    score += min(sub["verifiable_outcomes"] * 10, 15)

    if freshness_days > 365:
        score -= 10
    elif freshness_days > 180:
        score -= 5

    # Bloat penalty - the core change. Oversized monolithic files with no
    # skills factoring lose points, scoring strictly below lean-file-plus-skills.
    bloat_penalty, _bloat_remediation = compute_bloat_penalty(
        size_metrics, skills_present, delegates_to_skills
    )
    score -= bloat_penalty
    sub["bloat_penalty"] = bloat_penalty

    score = max(0, min(score, 100))
    return Grade(score=score, grade=_letter_grade(score), subscores=sub)
