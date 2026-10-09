"""Tests for heuristic agent-instructions grader.

Grades any of: CLAUDE.md, AGENTS.md, GEMINI.md, .cursorrules,
.github/copilot-instructions.md. The grader operates on text + freshness,
so it's filename-agnostic - the file selection lives in assess_core.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lib.agent_instructions_grader import (
    compute_size_metrics,
    count_positive_directives,
    count_tradeoff_phrases,
    count_path_references,
    count_verifiable_outcomes,
    detect_alias,
    detect_skills_delegation,
    grade_instructions,
    scan_sensitive_content,
)
from lib.instruction_content import RepoContext, build_repo_context


@pytest.fixture
def good_text(fixtures_dir: Path) -> str:
    return (fixtures_dir / "good_instructions.md").read_text()


@pytest.fixture
def bad_text(fixtures_dir: Path) -> str:
    return (fixtures_dir / "bad_instructions.md").read_text()


def test_positive_directives_good_outscores_bad(good_text: str, bad_text: str) -> None:
    # Good fixture uses: Use, Prefer, Default to, Match, Add (positive)
    # Bad fixture uses mostly: Write, Follow, Be, Don't (negatives + generic verbs)
    assert count_positive_directives(good_text) >= 5
    assert count_positive_directives(bad_text) <= 2


def test_tradeoff_phrases_only_in_good(good_text: str, bad_text: str) -> None:
    # "because", "over X" are tradeoff signals
    assert count_tradeoff_phrases(good_text) >= 2
    assert count_tradeoff_phrases(bad_text) == 0


def test_path_references_only_in_good(good_text: str, bad_text: str) -> None:
    # Good fixture has src/auth/, src/payments/processor.py, etc.
    assert count_path_references(good_text) >= 4
    assert count_path_references(bad_text) == 0


def test_verifiable_outcomes_only_in_good(good_text: str, bad_text: str) -> None:
    # "Working if" is the signal phrase
    assert count_verifiable_outcomes(good_text) >= 1
    assert count_verifiable_outcomes(bad_text) == 0


# --- JVM / build-tool verifiable outcomes (issue #116) --------------------
# The detector was calibrated for JS/Python phrasing and credited zero
# verifiable outcomes to Maven/Gradle CLAUDE.md files that contain runnable
# verification (mvn/gradle/gradlew test, -Dtest=, rg recipes). Recognise
# those idioms while keeping JS/Python phrase recognition intact.

MAVEN_INSTRUCTIONS = """# Project Guidelines

## Verifying a change

- Run the focused test: `mvn test -Dtest=PaymentServiceTest#refundsAreIdempotent`.
- Run the full verification gate before opening a PR: `mvn verify`.
- Confirm no stray TODOs slipped in: `rg "TODO" src/main/java`.
"""

GRADLE_INSTRUCTIONS = """# Project Guidelines

## Verifying a change

- Run the focused test: `./gradlew test --tests com.example.PaymentServiceTest`.
- Build and check everything: `./gradlew build check`.
- Confirm logging uses the wrapper: `rg "System.out" src`.
"""


def test_maven_instructions_score_nonzero_verifiable_outcomes() -> None:
    # Success criterion for issue #116: a Maven CLAUDE.md with runnable
    # mvn/rg verification must score a NON-ZERO verifiable_outcomes.
    assert count_verifiable_outcomes(MAVEN_INSTRUCTIONS) >= 1
    grade = grade_instructions(MAVEN_INSTRUCTIONS, freshness_days=10)
    assert grade.subscores["verifiable_outcomes"] >= 1


def test_gradle_instructions_score_nonzero_verifiable_outcomes() -> None:
    assert count_verifiable_outcomes(GRADLE_INSTRUCTIONS) >= 1
    grade = grade_instructions(GRADLE_INSTRUCTIONS, freshness_days=10)
    assert grade.subscores["verifiable_outcomes"] >= 1


def test_jvm_idioms_do_not_regress_js_python_recognition(good_text: str, bad_text: str) -> None:
    # Existing phrase-based recognition stays intact: the good JS/Python
    # fixture still credits a verifiable outcome, the bad one still none.
    assert count_verifiable_outcomes(good_text) >= 1
    assert count_verifiable_outcomes(bad_text) == 0


def test_jvm_patterns_do_not_false_match_prose() -> None:
    # High precision: prose that merely mentions Maven/Gradle without a
    # runnable command must not be credited as a verifiable outcome.
    prose = (
        "# Guidelines\n\n"
        "This is a Maven project that uses Gradle elsewhere. "
        "We care about testing and verifying our work generally.\n"
    )
    assert count_verifiable_outcomes(prose) == 0


def test_rg_prose_mention_is_not_credited() -> None:
    # A bare reference to ripgrep without a runnable recipe (no flag, no
    # quoted query) must not count as a verifiable outcome.
    prose = (
        "# Guidelines\n\n"
        "You can use rg to find things, and rg or grep are both useful. "
        "The rg tool is fast.\n"
    )
    assert count_verifiable_outcomes(prose) == 0


def test_rg_recipe_with_flag_is_credited() -> None:
    # A real ripgrep recipe (flag-driven, unquoted query) is verifiable.
    text = "# Guidelines\n\nCheck for leftovers: `rg -n TODO src/main/java`.\n"
    assert count_verifiable_outcomes(text) >= 1


def test_grade_returns_letter_grade(good_text: str, bad_text: str) -> None:
    good = grade_instructions(good_text, freshness_days=10)
    bad = grade_instructions(bad_text, freshness_days=10)

    # Graded as text alone (no repo), no command can be verified, so the
    # largest credit is out of reach: a directive-rich file tops out at B/C
    # since the #512 rescoring. Verified-command credit is tested with a repo
    # in the fixture-pair tests below.
    assert good.grade in {"B", "C"}
    assert bad.grade in {"D", "F"}
    assert good.score > bad.score


def test_grade_penalizes_staleness(good_text: str) -> None:
    fresh = grade_instructions(good_text, freshness_days=10)
    stale = grade_instructions(good_text, freshness_days=400)
    assert stale.score < fresh.score


def test_grade_empty_string_is_F() -> None:
    empty = grade_instructions("", freshness_days=0)
    assert empty.grade == "F"
    assert empty.score == 0


def test_subscores_in_result(good_text: str) -> None:
    result = grade_instructions(good_text, freshness_days=10)
    assert result.subscores["positive_directives"] >= 5
    assert result.subscores["path_references"] >= 4
    assert "tradeoff_phrases" in result.subscores
    assert "verifiable_outcomes" in result.subscores


def test_size_metrics_accurate() -> None:
    text = "line1\nline2\nline3"
    m = compute_size_metrics(text)
    assert m["line_count"] == 3
    assert m["word_count"] == 3
    assert m["exceeds_line_threshold"] is False
    assert m["exceeds_word_threshold"] is False


def test_skills_delegation_detection() -> None:
    text = "Load Java conventions via the `java-conventions` skill."
    d = detect_skills_delegation(text)
    assert d["delegates_to_skills"] is True
    assert d["delegation_pointers"] >= 1
    assert len(d["delegation_samples"]) >= 1


def test_skills_delegation_detection_dir_pointer() -> None:
    text = "Topic guidance lives under .claude/skills/ and loads on demand."
    d = detect_skills_delegation(text)
    assert d["delegates_to_skills"] is True


def test_no_skills_delegation_in_generic_text() -> None:
    text = "Write clean code. Follow best practices."
    d = detect_skills_delegation(text)
    assert d["delegates_to_skills"] is False
    assert d["delegation_pointers"] == 0


def test_size_subscores_in_grade(good_text: str) -> None:
    result = grade_instructions(good_text, freshness_days=10)
    assert "line_count" in result.subscores
    assert "word_count" in result.subscores
    assert "bloat_penalty" in result.subscores
    assert result.subscores["bloat_penalty"] == 0  # good_instructions is small


# --- Sensitive-content scan (issue #56) -----------------------------------

def _categories(findings: list[dict]) -> set[str]:
    return {f["category"] for f in findings}


def test_scan_flags_public_ip() -> None:
    findings = scan_sensitive_content("Demo server: 203.0.113.42 runs the stack.")
    assert "ip_address" in _categories(findings)
    # Evidence is redacted - the full IP must not survive into the finding.
    assert all("203.0.113.42" not in f["evidence"] for f in findings)


def test_scan_ignores_loopback_and_version_strings() -> None:
    assert scan_sensitive_content("bind to 127.0.0.1 for local dev") == []
    # 999 is not a valid octet -> a version-like string, not an IP.
    assert scan_sensitive_content("upgrade to release 1.2.999.4") == []


def test_scan_flags_ssh_root_login() -> None:
    findings = scan_sensitive_content("Connect with `ssh root@demo.example.com`.")
    cats = _categories(findings)
    assert "ssh_or_host" in cats
    assert all("demo.example.com" not in f["evidence"] for f in findings)


def test_scan_flags_private_key_and_cloud_key() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    assert "private_key" in _categories(scan_sensitive_content(pem))
    assert "cloud_key" in _categories(scan_sensitive_content("AWS: AKIAIOSFODNN7EXAMPLE"))


def test_scan_flags_real_credential_but_not_placeholder() -> None:
    real = scan_sensitive_content('password = "hunter2correcthorse"')
    assert "credential" in _categories(real)
    assert all("hunter2" not in f["evidence"] for f in real)
    # Placeholders / env refs are not flagged.
    assert scan_sensitive_content("API_KEY=your_key_here") == []
    assert scan_sensitive_content("token = ${GH_TOKEN}") == []
    assert scan_sensitive_content("password: <your-password>") == []


def test_scan_flags_home_directory_path_but_not_placeholder() -> None:
    findings = scan_sensitive_content("Config lives at /Users/ben/.config/app.yaml")
    assert "home_path" in _categories(findings)
    assert all("ben" not in f["evidence"] for f in findings)
    # Generic placeholder home dirs are not a leak.
    assert scan_sensitive_content("clone into /home/user/project") == []


def test_scan_clean_instruction_file_has_no_findings(good_text: str) -> None:
    assert scan_sensitive_content(good_text) == []


# --- Alias detection (issue #57) ------------------------------------------

def test_detect_alias_thin_stub_points_at_claude_md() -> None:
    stub = "# AGENTS.md\n\nSee [CLAUDE.md](./CLAUDE.md) for all project instructions."
    result = detect_alias(stub)
    assert result["is_alias"] is True
    assert result["alias_target"] == "CLAUDE.md"


def test_detect_alias_rejects_full_standalone_doc(good_text: str) -> None:
    # A real instruction file is not a thin alias even if it mentions CLAUDE.md.
    assert detect_alias(good_text)["is_alias"] is False


def test_detect_alias_rejects_stub_with_no_canonical_reference() -> None:
    assert detect_alias("# Notes\n\nThis project is great.")["is_alias"] is False


# --- Rescoring on verified commands and size (issue #512) -------------------

@pytest.fixture
def grading_repo(tmp_path: Path, fixtures_dir: Path) -> RepoContext:
    """The instruction_grading fixture repo, copied out of the git checkout."""
    root = tmp_path / "repo"
    shutil.copytree(fixtures_dir / "instruction_grading" / "repo", root)
    return build_repo_context(root)


def _fixture(fixtures_dir: Path, name: str) -> str:
    return (fixtures_dir / "instruction_grading" / name).read_text()


def test_verified_commands_outgrade_a_directive_monolith(
    fixtures_dir: Path, grading_repo: RepoContext,
) -> None:
    """Success criterion: a 60-line file of verified commands outgrades a
    400-line file of directives, overview and directory tree."""
    lean_text = _fixture(fixtures_dir, "verified_commands.md")
    mono_text = _fixture(fixtures_dir, "directive_monolith.md")
    assert len(lean_text.splitlines()) <= 60
    assert len(mono_text.splitlines()) == 400

    lean = grade_instructions(lean_text, freshness_days=10, repo=grading_repo, path="CLAUDE.md")
    mono = grade_instructions(mono_text, freshness_days=10, repo=grading_repo, path="CLAUDE.md")

    assert lean.grade in {"A", "A-"}
    assert mono.grade == "F"
    assert lean.score - mono.score >= 40
    assert lean.subscores["verified_commands"] >= 6
    assert lean.subscores["unresolved_commands"] == 0
    assert lean.subscores["bloat_penalty"] == 0
    assert lean.findings == []
    # The monolith out-counts the lean file on every old signal...
    assert mono.subscores["positive_directives"] > 10 * lean.subscores["positive_directives"]
    assert mono.subscores["tradeoff_phrases"] > lean.subscores["tradeoff_phrases"]
    # ...and pays for its size, its tree and its overview sections.
    assert mono.subscores["bloat_penalty"] >= 20
    assert mono.subscores["directory_trees"] == 1
    assert mono.subscores["overview_sections"] >= 2
    assert mono.subscores["content_penalty"] >= 15
    # Before the penalties the monolith's capped floor credit (10 + 10 + 10)
    # cleared the F cutoff, so the anomaly detector reads its F as explained;
    # the lean file had no penalties to subtract.
    assert mono.subscores["score_before_penalties"] == 30
    assert lean.subscores["score_before_penalties"] == lean.score


def test_missing_script_and_stale_path_are_findings(grading_repo: RepoContext) -> None:
    """Success criterion: a command naming a missing script and a backticked
    path that does not exist each produce a finding."""
    text = (
        "# CLAUDE.md\n\n"
        "Run `npm test`, then `npm run deploy`.\n"
        "Handlers live in `src/routes/index.ts`; the old `src/legacy/router.ts` is gone.\n"
    )
    grade = grade_instructions(text, freshness_days=1, repo=grading_repo, path="CLAUDE.md")
    assert grade.findings == [
        {"kind": "unresolved_command", "line": 3, "reference": "npm run deploy",
         "reason": "no script `deploy` in package.json"},
        {"kind": "stale_path", "line": 4, "reference": "src/legacy/router.ts",
         "reason": "no tracked file or directory at this path"},
    ]
    assert grade.subscores["verified_commands"] == 1
    assert grade.subscores["unresolved_commands"] == 1
    assert grade.subscores["path_references_existing"] == 1
    assert grade.subscores["stale_references"] == 1


def test_duplicate_commands_count_once(grading_repo: RepoContext) -> None:
    text = "`npm test` and `npm  test` and `npm run nope` twice: `npm run nope`\n"
    grade = grade_instructions(text, freshness_days=1, repo=grading_repo)
    assert grade.subscores["verified_commands"] == 1
    assert [f["reference"] for f in grade.findings] == ["npm run nope"]


def test_unknown_runner_is_neither_credit_nor_finding(grading_repo: RepoContext) -> None:
    grade = grade_instructions("```bash\nrg -n TODO src\n```\n", freshness_days=1, repo=grading_repo)
    assert grade.subscores["unknown_commands"] == 1
    assert grade.subscores["verified_commands"] == 0
    assert grade.findings == []


def test_text_only_grading_counts_paths_unverified(good_text: str) -> None:
    grade = grade_instructions(good_text, freshness_days=10)
    assert grade.subscores["verified_commands"] == 0
    assert grade.subscores["path_references_existing"] == grade.subscores["path_references"]
    assert grade.findings == []


def test_command_credit_is_capped(grading_repo: RepoContext) -> None:
    # No "run" in the commands, so no directive credit muddies the sum.
    few = grade_instructions("`npm test` `make check` `make fmt`\n", 1, repo=grading_repo)
    many = grade_instructions(
        "`npm test` `make check` `make fmt` `make lint` `make test` `npm t`\n",
        1, repo=grading_repo,
    )
    assert many.subscores["verified_commands"] == 6
    assert few.score == 10 + 3 * 12
    assert many.score == 10 + 40  # COMMAND_CAP


def test_readme_overlap_penalty(tmp_path: Path) -> None:
    lines = [f"This sentence number {i} explains the orders service in some detail." for i in range(10)]
    root = tmp_path / "r"
    root.mkdir()
    (root / "README.md").write_text("\n".join(lines), encoding="utf-8")
    ctx = build_repo_context(root)
    half = "\n".join(lines[:5] + [f"Agent-only guidance line {i} that the README lacks." for i in range(5)])
    quarter = "\n".join(lines[:3] + [f"Agent-only guidance line {i} that the README lacks." for i in range(9)])
    assert grade_instructions(half, 1, repo=ctx).subscores["content_penalty"] == 15
    assert grade_instructions(quarter, 1, repo=ctx).subscores["content_penalty"] == 8
