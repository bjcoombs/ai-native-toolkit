# /assess Deterministic Wiki Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `/assess` so all data work runs in deterministic Python (heuristic CLAUDE.md grading, stats diffing across runs, template-driven MD generation), with the LLM responsible only for prose synthesis. Restructure `.assess/` as a compounding wiki (`index.md`, `log.md`, `hotspots/*.md`) so each run reads prior state and updates entity pages rather than overwriting a snapshot.

**Architecture:** New Python modules under `skills/assess/scripts/lib/` handle grading, diffing, and wiki writing - each independently testable with pytest. A new orchestrator `assess_core.py` runs the deterministic pipeline before the LLM does anything; it produces a structured JSON `run-context.json` the SKILL.md instructions consume. Templates under `skills/assess/templates/` produce all MD files except the prose-heavy report sections. Tests in `skills/assess/tests/` cover every deterministic function.

**Tech Stack:** Python 3.11+, pytest (via pyproject.toml + uv), stdlib only for data work (pathlib, json, re, subprocess, datetime, dataclasses). Existing deps unchanged (lizard, squarify, matplotlib via PEP 723).

**Non-goals (deferred to follow-up plans):**
- Coverage data ingestion (separate plan: codecov API + lcov reader)
- Git-history append-rate analysis (separate plan: git mining)
- Function-level complexity granularity (separate plan: lizard depth)
- Test↔code pairing automation (separate plan: pairs with coverage)

---

## File Structure

```
skills/assess/
├── SKILL.md                          [MODIFY: slim, point at scripts]
├── pyproject.toml                    [CREATE: pytest config + dev deps]
├── scripts/
│   ├── complexity-treemap.py         [unchanged]
│   ├── assess_core.py                [CREATE: orchestrates deterministic pipeline]
│   └── lib/
│       ├── __init__.py               [CREATE]
│       ├── claudemd_grader.py        [CREATE: heuristic CLAUDE.md scoring]
│       ├── stats_diff.py             [CREATE: prior-vs-current comparison]
│       └── wiki_writer.py            [CREATE: render templates → MD files]
├── templates/
│   ├── index.md.template             [CREATE: wiki catalog]
│   ├── log_entry.md.template         [CREATE: append-only run record]
│   └── hotspot.md.template           [CREATE: per-file persistent page]
└── tests/
    ├── __init__.py                   [CREATE]
    ├── conftest.py                   [CREATE: shared fixtures]
    ├── test_claudemd_grader.py       [CREATE]
    ├── test_stats_diff.py            [CREATE]
    ├── test_wiki_writer.py           [CREATE]
    ├── test_assess_core.py           [CREATE: end-to-end]
    └── fixtures/
        ├── good_claudemd.md          [CREATE]
        ├── bad_claudemd.md           [CREATE]
        ├── prior_stats.json          [CREATE]
        └── current_stats.json        [CREATE]

CLAUDE.md                             [MODIFY: document new architecture]
.claude-plugin/plugin.json            [MODIFY: bump to 1.4.0]
```

---

## Task 1: Set up Python module structure and pytest

**Files:**
- Create: `skills/assess/pyproject.toml`
- Create: `skills/assess/scripts/lib/__init__.py`
- Create: `skills/assess/tests/__init__.py`
- Create: `skills/assess/tests/conftest.py`
- Create: `skills/assess/tests/test_smoke.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
# skills/assess/pyproject.toml
[project]
name = "assess"
version = "0.1.0"
description = "Deterministic core for /assess skill"
requires-python = ">=3.11"

[dependency-groups]
dev = [
    "pytest>=8.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["scripts"]
addopts = "-v --tb=short"
```

- [ ] **Step 2: Create lib package init**

```python
# skills/assess/scripts/lib/__init__.py
"""Deterministic core modules for /assess.

Public surface:
    claudemd_grader: heuristic scoring of CLAUDE.md files
    stats_diff:      compare current vs prior complexity stats
    wiki_writer:     render wiki MD files from templates
"""

__version__ = "0.1.0"
```

- [ ] **Step 3: Create tests package init**

```python
# skills/assess/tests/__init__.py
```

(empty file - just marks the directory as a package)

- [ ] **Step 4: Create conftest with shared fixtures**

```python
# skills/assess/tests/conftest.py
"""Shared pytest fixtures."""
from __future__ import annotations
from pathlib import Path

import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to tests/fixtures/."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_assess_dir(tmp_path: Path) -> Path:
    """A clean .assess/ directory in a temp location."""
    assess_dir = tmp_path / ".assess"
    assess_dir.mkdir()
    (assess_dir / "hotspots").mkdir()
    return assess_dir
```

- [ ] **Step 5: Write smoke test**

```python
# skills/assess/tests/test_smoke.py
"""Smoke test: verify the test harness runs and lib is importable."""
from __future__ import annotations

from lib import __version__


def test_lib_importable() -> None:
    assert __version__ == "0.1.0"


def test_fixtures_dir_exists(fixtures_dir):
    # fixtures dir doesn't need contents yet; just verify the fixture works
    assert fixtures_dir.parent.name == "tests"
```

- [ ] **Step 6: Run smoke test**

Run: `cd skills/assess && uv run --with pytest pytest tests/test_smoke.py -v`

Expected: 2 passed.

- [ ] **Step 7: Create fixtures directory placeholder**

```bash
mkdir -p skills/assess/tests/fixtures
touch skills/assess/tests/fixtures/.gitkeep
```

- [ ] **Step 8: Commit**

```bash
git add skills/assess/pyproject.toml \
        skills/assess/scripts/lib/__init__.py \
        skills/assess/tests/__init__.py \
        skills/assess/tests/conftest.py \
        skills/assess/tests/test_smoke.py \
        skills/assess/tests/fixtures/.gitkeep
git commit -m "feat(assess): Set up pytest harness for deterministic core"
```

---

## Task 2: Heuristic CLAUDE.md grader

**Files:**
- Create: `skills/assess/tests/fixtures/good_claudemd.md`
- Create: `skills/assess/tests/fixtures/bad_claudemd.md`
- Create: `skills/assess/tests/test_claudemd_grader.py`
- Create: `skills/assess/scripts/lib/claudemd_grader.py`

- [ ] **Step 1: Create good_claudemd fixture (positive directives, tradeoffs, paths)**

```markdown
<!-- skills/assess/tests/fixtures/good_claudemd.md -->
# Project Guidelines

## Approach

- Use `bcrypt` for password hashing because timing-safe comparison matters.
- Prefer Postgres over SQLite for production: we need concurrent writers.
- Default to constructor injection in `src/auth/` over field injection.

## When editing `src/payments/processor.py`

- Match the existing transaction pattern in `src/payments/refund.py`.
- Add the corresponding test in `tests/payments/test_processor.py`.
- The reconciler runs every 5 minutes; idempotency is required.

## Working if

- Diffs touch only files mentioned in the task.
- New code follows the patterns in `src/auth/login.py`.
- Tests run green before opening a PR.
```

- [ ] **Step 2: Create bad_claudemd fixture (generic, no specifics)**

```markdown
<!-- skills/assess/tests/fixtures/bad_claudemd.md -->
# Project Guidelines

Write clean code. Follow best practices. Be careful with security.

Don't write bad code. Don't break things. Don't make mistakes.

Use good libraries. Test your code. Be a good engineer.
```

- [ ] **Step 3: Write tests for grader**

```python
# skills/assess/tests/test_claudemd_grader.py
"""Tests for heuristic CLAUDE.md grader."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.claudemd_grader import (
    count_positive_directives,
    count_tradeoff_phrases,
    count_path_references,
    count_verifiable_outcomes,
    grade_claudemd,
)


@pytest.fixture
def good_text(fixtures_dir: Path) -> str:
    return (fixtures_dir / "good_claudemd.md").read_text()


@pytest.fixture
def bad_text(fixtures_dir: Path) -> str:
    return (fixtures_dir / "bad_claudemd.md").read_text()


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


def test_grade_returns_letter_grade(good_text: str, bad_text: str) -> None:
    good = grade_claudemd(good_text, freshness_days=10)
    bad = grade_claudemd(bad_text, freshness_days=10)

    assert good.grade in {"A", "A-", "B+", "B"}
    assert bad.grade in {"D", "F"}
    assert good.score > bad.score


def test_grade_penalizes_staleness(good_text: str) -> None:
    fresh = grade_claudemd(good_text, freshness_days=10)
    stale = grade_claudemd(good_text, freshness_days=400)
    assert stale.score < fresh.score


def test_grade_empty_string_is_F() -> None:
    empty = grade_claudemd("", freshness_days=0)
    assert empty.grade == "F"
    assert empty.score == 0


def test_subscores_in_result(good_text: str) -> None:
    result = grade_claudemd(good_text, freshness_days=10)
    assert result.subscores["positive_directives"] >= 5
    assert result.subscores["path_references"] >= 4
    assert "tradeoff_phrases" in result.subscores
    assert "verifiable_outcomes" in result.subscores
```

- [ ] **Step 4: Run tests to verify all fail**

Run: `cd skills/assess && uv run --with pytest pytest tests/test_claudemd_grader.py -v`

Expected: ImportError on `lib.claudemd_grader` for all tests.

- [ ] **Step 5: Implement the grader**

```python
# skills/assess/scripts/lib/claudemd_grader.py
"""Heuristic CLAUDE.md grader.

Scores a CLAUDE.md file on signals that correlate with usefulness to an
LLM contributor:

    positive_directives: "Use X", "Prefer Y", "Default to Z" (positive framing)
    tradeoff_phrases:    "because", "over X", "instead of", "rather than"
    path_references:     file paths like src/foo/bar.py, tests/..., etc.
    verifiable_outcomes: "Working if", "verify:", "success criteria"
    freshness:           days since last git modification

No LLM calls. Pure regex + arithmetic. Deterministic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


POSITIVE_DIRECTIVE_PATTERNS = [
    r"\bUse\b",
    r"\bPrefer\b",
    r"\bChoose\b",
    r"\bDefault to\b",
    r"\bMatch\b",
    r"\bAdd\b",
    r"\bRun\b",
    r"\bWrite\b",
    r"\bFollow\b",
]

TRADEOFF_PATTERNS = [
    r"\bbecause\b",
    r"\bover\s+\w+",
    r"\binstead of\b",
    r"\brather than\b",
    r"\btradeoff\b",
]

PATH_PATTERN = re.compile(
    r"`[^`]*[/\\][^`\s]+`"          # backtick-wrapped paths
    r"|(?:^|\s)[\w./-]+/[\w./-]+"   # bare paths with at least one slash
)

VERIFIABLE_PATTERNS = [
    r"\bworking if\b",
    r"\bverify:\b",
    r"\bsuccess criteria\b",
    r"\bacceptance criteria\b",
]


@dataclass
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


def grade_claudemd(text: str, freshness_days: int) -> Grade:
    """Score a CLAUDE.md and return a Grade.

    Scoring weights (max 100):
        positive_directives:  3 points each, capped at 30
        tradeoff_phrases:     5 points each, capped at 25
        path_references:      3 points each, capped at 20
        verifiable_outcomes:  10 points each, capped at 15
        freshness penalty:    -10 if > 365 days, -5 if > 180, 0 otherwise
                              +10 baseline if file has any content
    """
    if not text.strip():
        return Grade(score=0, grade="F", subscores={})

    sub = {
        "positive_directives": count_positive_directives(text),
        "tradeoff_phrases": count_tradeoff_phrases(text),
        "path_references": count_path_references(text),
        "verifiable_outcomes": count_verifiable_outcomes(text),
    }

    score = 10  # baseline for non-empty content
    score += min(sub["positive_directives"] * 3, 30)
    score += min(sub["tradeoff_phrases"] * 5, 25)
    score += min(sub["path_references"] * 3, 20)
    score += min(sub["verifiable_outcomes"] * 10, 15)

    if freshness_days > 365:
        score -= 10
    elif freshness_days > 180:
        score -= 5

    score = max(0, min(score, 100))
    return Grade(score=score, grade=_letter_grade(score), subscores=sub)
```

- [ ] **Step 6: Run tests to verify all pass**

Run: `cd skills/assess && uv run --with pytest pytest tests/test_claudemd_grader.py -v`

Expected: 7 passed.

- [ ] **Step 7: Commit**

```bash
git add skills/assess/scripts/lib/claudemd_grader.py \
        skills/assess/tests/test_claudemd_grader.py \
        skills/assess/tests/fixtures/good_claudemd.md \
        skills/assess/tests/fixtures/bad_claudemd.md
git commit -m "feat(assess): Add heuristic CLAUDE.md grader"
```

---

## Task 3: Stats sidecar diff (loop awareness)

**Files:**
- Create: `skills/assess/tests/fixtures/prior_stats.json`
- Create: `skills/assess/tests/fixtures/current_stats.json`
- Create: `skills/assess/tests/test_stats_diff.py`
- Create: `skills/assess/scripts/lib/stats_diff.py`

- [ ] **Step 1: Create prior stats fixture**

```json
{
  "files_scored": 100,
  "loc": {"p50": 50, "p95": 400, "max": 1200},
  "ccn": {"p50": 3, "p95": 12, "max": 45},
  "top_hotspots": [
    {"path": "src/legacy/parser.go", "loc": 800, "ccn": 35, "commits": 12},
    {"path": "src/api/handler.go", "loc": 600, "ccn": 28, "commits": 8},
    {"path": "src/util/helpers.go", "loc": 400, "ccn": 18, "commits": 5}
  ],
  "top_complex": [
    {"path": "src/legacy/parser.go", "ccn": 35},
    {"path": "src/api/handler.go", "ccn": 28}
  ],
  "top_large": [
    {"path": "src/legacy/parser.go", "loc": 800},
    {"path": "src/api/handler.go", "loc": 600}
  ]
}
```

- [ ] **Step 2: Create current stats fixture (some files graduated, some regressed, some new)**

```json
{
  "files_scored": 105,
  "loc": {"p50": 52, "p95": 410, "max": 1300},
  "ccn": {"p50": 3, "p95": 13, "max": 48},
  "top_hotspots": [
    {"path": "src/api/handler.go", "loc": 700, "ccn": 32, "commits": 15},
    {"path": "src/new/feature.go", "loc": 500, "ccn": 22, "commits": 6},
    {"path": "src/util/helpers.go", "loc": 400, "ccn": 18, "commits": 5}
  ],
  "top_complex": [
    {"path": "src/api/handler.go", "ccn": 32},
    {"path": "src/new/feature.go", "ccn": 22}
  ],
  "top_large": [
    {"path": "src/api/handler.go", "loc": 700},
    {"path": "src/new/feature.go", "loc": 500}
  ]
}
```

Note: `src/legacy/parser.go` graduated (was top hotspot, now absent). `src/api/handler.go` regressed (ccn 28 → 32, commits 8 → 15). `src/new/feature.go` is new. `src/util/helpers.go` persisted unchanged.

- [ ] **Step 3: Write tests for stats diff**

```python
# skills/assess/tests/test_stats_diff.py
"""Tests for stats sidecar diff."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.stats_diff import (
    HotspotTransition,
    diff_stats,
    load_stats,
)


@pytest.fixture
def prior_stats(fixtures_dir: Path) -> dict:
    return load_stats(fixtures_dir / "prior_stats.json")


@pytest.fixture
def current_stats(fixtures_dir: Path) -> dict:
    return load_stats(fixtures_dir / "current_stats.json")


def test_load_stats_returns_dict(fixtures_dir: Path) -> None:
    stats = load_stats(fixtures_dir / "prior_stats.json")
    assert stats["files_scored"] == 100


def test_load_stats_missing_returns_none(tmp_path: Path) -> None:
    assert load_stats(tmp_path / "nope.json") is None


def test_diff_identifies_graduated(prior_stats: dict, current_stats: dict) -> None:
    diff = diff_stats(prior=prior_stats, current=current_stats)
    graduated_paths = {h.path for h in diff.graduated}
    assert "src/legacy/parser.go" in graduated_paths


def test_diff_identifies_regressed(prior_stats: dict, current_stats: dict) -> None:
    diff = diff_stats(prior=prior_stats, current=current_stats)
    regressed_paths = {h.path for h in diff.regressed}
    assert "src/api/handler.go" in regressed_paths
    # Regression must capture the delta
    handler = next(h for h in diff.regressed if h.path == "src/api/handler.go")
    assert handler.ccn_delta == 4  # 32 - 28
    assert handler.commits_delta == 7  # 15 - 8


def test_diff_identifies_new(prior_stats: dict, current_stats: dict) -> None:
    diff = diff_stats(prior=prior_stats, current=current_stats)
    new_paths = {h.path for h in diff.new}
    assert "src/new/feature.go" in new_paths


def test_diff_identifies_persistent(prior_stats: dict, current_stats: dict) -> None:
    diff = diff_stats(prior=prior_stats, current=current_stats)
    persistent_paths = {h.path for h in diff.persistent}
    assert "src/util/helpers.go" in persistent_paths


def test_diff_no_prior_means_all_new(current_stats: dict) -> None:
    diff = diff_stats(prior=None, current=current_stats)
    assert len(diff.graduated) == 0
    assert len(diff.regressed) == 0
    assert len(diff.persistent) == 0
    assert len(diff.new) == len(current_stats["top_hotspots"])


def test_diff_summary_counts(prior_stats: dict, current_stats: dict) -> None:
    diff = diff_stats(prior=prior_stats, current=current_stats)
    summary = diff.summary()
    assert summary["graduated"] == 1
    assert summary["regressed"] == 1
    assert summary["new"] == 1
    assert summary["persistent"] == 1
```

- [ ] **Step 4: Run tests to verify all fail**

Run: `cd skills/assess && uv run --with pytest pytest tests/test_stats_diff.py -v`

Expected: ImportError for all tests.

- [ ] **Step 5: Implement stats diff**

```python
# skills/assess/scripts/lib/stats_diff.py
"""Compare current complexity stats against a prior run.

Identifies hotspot transitions:
    graduated:  was in prior top_hotspots, absent from current
    regressed:  in both, but ccn or commits got worse
    new:        in current top_hotspots, absent from prior
    persistent: in both, roughly unchanged

No LLM calls. Pure set operations + arithmetic.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HotspotTransition:
    path: str
    ccn_delta: int = 0
    commits_delta: int = 0
    loc_delta: int = 0


@dataclass
class StatsDiff:
    graduated: list[HotspotTransition] = field(default_factory=list)
    regressed: list[HotspotTransition] = field(default_factory=list)
    new: list[HotspotTransition] = field(default_factory=list)
    persistent: list[HotspotTransition] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "graduated": len(self.graduated),
            "regressed": len(self.regressed),
            "new": len(self.new),
            "persistent": len(self.persistent),
        }


def load_stats(path: Path) -> dict | None:
    """Load stats JSON from path, or None if file doesn't exist."""
    if not path.exists():
        return None
    return json.loads(path.read_text())


def diff_stats(*, prior: dict | None, current: dict) -> StatsDiff:
    """Compute hotspot transitions between two stats snapshots."""
    diff = StatsDiff()

    current_hotspots = {h["path"]: h for h in current.get("top_hotspots", [])}

    if prior is None:
        diff.new = [HotspotTransition(path=p) for p in current_hotspots]
        return diff

    prior_hotspots = {h["path"]: h for h in prior.get("top_hotspots", [])}

    for path in prior_hotspots:
        if path not in current_hotspots:
            diff.graduated.append(HotspotTransition(path=path))

    for path, current_h in current_hotspots.items():
        if path not in prior_hotspots:
            diff.new.append(HotspotTransition(path=path))
            continue

        prior_h = prior_hotspots[path]
        ccn_delta = current_h.get("ccn", 0) - prior_h.get("ccn", 0)
        commits_delta = current_h.get("commits", 0) - prior_h.get("commits", 0)
        loc_delta = current_h.get("loc", 0) - prior_h.get("loc", 0)

        transition = HotspotTransition(
            path=path,
            ccn_delta=ccn_delta,
            commits_delta=commits_delta,
            loc_delta=loc_delta,
        )

        if ccn_delta > 0 or (loc_delta > 50 and commits_delta > 2):
            diff.regressed.append(transition)
        else:
            diff.persistent.append(transition)

    return diff
```

- [ ] **Step 6: Run tests to verify all pass**

Run: `cd skills/assess && uv run --with pytest pytest tests/test_stats_diff.py -v`

Expected: 8 passed.

- [ ] **Step 7: Commit**

```bash
git add skills/assess/scripts/lib/stats_diff.py \
        skills/assess/tests/test_stats_diff.py \
        skills/assess/tests/fixtures/prior_stats.json \
        skills/assess/tests/fixtures/current_stats.json
git commit -m "feat(assess): Add stats diff for cross-run loop awareness"
```

---

## Task 4: MD templates for wiki files

**Files:**
- Create: `skills/assess/templates/index.md.template`
- Create: `skills/assess/templates/log_entry.md.template`
- Create: `skills/assess/templates/hotspot.md.template`

These use Python `str.format()` placeholders - no Jinja, no new dep.

- [ ] **Step 1: Create index.md.template**

```markdown
# Assess Wiki Index

_Last updated: {last_updated}_

Catalog of every hotspot ever flagged by `/assess` in this repo. Status reflects the most recent run.

| File | First Flagged | Last Seen | Status | Latest CCN | Latest LOC |
|------|---------------|-----------|--------|------------|------------|
{hotspot_rows}

## Legend

- **active** - in the latest top hotspots list
- **graduated** - was a hotspot, no longer is (good)
- **regressed** - still a hotspot, and getting worse
- **persistent** - still a hotspot, roughly unchanged

## How this gets updated

Each `/assess` run reads this file, the prior `complexity-stats.json`, and the latest run output, then rewrites this index. Per-file detail lives in `hotspots/<slug>.md`. Run history lives in `log.md`.
```

- [ ] **Step 2: Create log_entry.md.template**

```markdown
## {run_date}

- **Files scored:** {files_scored}
- **AI Readiness:** {readiness_score} / 7 ({maturity_label})
- **CLAUDE.md grade:** {claudemd_grade}
- **Hotspot transitions:** {graduated_count} graduated, {regressed_count} regressed, {new_count} new, {persistent_count} persistent
- **Top action:** {top_action}

[Full report]({report_link})

---
```

- [ ] **Step 3: Create hotspot.md.template**

```markdown
# Hotspot: `{path}`

_First flagged: {first_flagged}. Last seen: {last_seen}. Status: {status}._

## Current metrics

| Metric | Value |
|--------|-------|
| LOC | {loc} |
| Cyclomatic complexity (file max) | {ccn} |
| Commits in churn window | {commits} |
| Has test file | {has_tests} |

## History across runs

| Run date | LOC | CCN | Commits | Status |
|----------|-----|-----|---------|--------|
{history_rows}

## Briefing for editing this file

Use this briefing when about to modify `{path}`:

{briefing}

## Suggested actions

{actions}
```

- [ ] **Step 4: Smoke-test the templates by rendering them with sample data**

```bash
cd skills/assess
uv run --with pytest python -c "
from pathlib import Path
tpl = Path('templates/index.md.template').read_text()
out = tpl.format(last_updated='2026-05-22', hotspot_rows='| foo | bar | baz | active | 30 | 600 |')
assert '2026-05-22' in out
assert '| foo |' in out
print('index template OK')

tpl = Path('templates/log_entry.md.template').read_text()
out = tpl.format(run_date='2026-05-22', files_scored=100, readiness_score=4.5,
                 maturity_label='Solid', claudemd_grade='B+',
                 graduated_count=1, regressed_count=1, new_count=1, persistent_count=1,
                 top_action='Add complexity rules', report_link='./assess-report.md')
assert '2026-05-22' in out
print('log_entry template OK')

tpl = Path('templates/hotspot.md.template').read_text()
out = tpl.format(path='src/foo.go', first_flagged='2026-05-22', last_seen='2026-05-22',
                 status='active', loc=600, ccn=30, commits=15, has_tests='no',
                 history_rows='| 2026-05-22 | 600 | 30 | 15 | active |',
                 briefing='Go service module.',
                 actions='- Add tests')
assert 'src/foo.go' in out
print('hotspot template OK')
"
```

Expected: three "OK" lines printed.

- [ ] **Step 5: Commit**

```bash
git add skills/assess/templates/
git commit -m "feat(assess): Add MD templates for wiki files"
```

---

## Task 5: Wiki writer module

**Files:**
- Create: `skills/assess/tests/test_wiki_writer.py`
- Create: `skills/assess/scripts/lib/wiki_writer.py`

- [ ] **Step 1: Write tests for wiki writer**

```python
# skills/assess/tests/test_wiki_writer.py
"""Tests for wiki writer module."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from lib.wiki_writer import (
    HotspotEntry,
    LogEntry,
    append_log_entry,
    slug_for_path,
    write_hotspot_page,
    write_index,
)


def test_slug_for_path_basic() -> None:
    assert slug_for_path("src/foo/bar.go") == "src-foo-bar-go"
    assert slug_for_path("services/api/handler.ts") == "services-api-handler-ts"


def test_slug_for_path_handles_special_chars() -> None:
    assert slug_for_path("src/foo bar/baz.go") == "src-foo-bar-baz-go"


def test_write_index_creates_file(tmp_assess_dir: Path) -> None:
    entries = [
        HotspotEntry(
            path="src/foo.go", first_flagged="2026-01-01", last_seen="2026-05-22",
            status="active", ccn=30, loc=600,
        ),
    ]
    write_index(tmp_assess_dir, entries, last_updated="2026-05-22")
    index = tmp_assess_dir / "index.md"
    assert index.exists()
    content = index.read_text()
    assert "src/foo.go" in content
    assert "active" in content


def test_write_index_overwrites(tmp_assess_dir: Path) -> None:
    (tmp_assess_dir / "index.md").write_text("OLD")
    entries = [HotspotEntry(
        path="src/new.go", first_flagged="2026-05-22", last_seen="2026-05-22",
        status="active", ccn=20, loc=300,
    )]
    write_index(tmp_assess_dir, entries, last_updated="2026-05-22")
    content = (tmp_assess_dir / "index.md").read_text()
    assert "OLD" not in content
    assert "src/new.go" in content


def test_append_log_entry_creates_file_if_missing(tmp_assess_dir: Path) -> None:
    entry = LogEntry(
        run_date="2026-05-22", files_scored=100, readiness_score=4.5,
        maturity_label="Solid", claudemd_grade="B+",
        graduated_count=1, regressed_count=0, new_count=0, persistent_count=2,
        top_action="Add complexity rules to .golangci.yml",
    )
    append_log_entry(tmp_assess_dir, entry)
    log = tmp_assess_dir / "log.md"
    assert log.exists()
    assert "2026-05-22" in log.read_text()


def test_append_log_entry_appends(tmp_assess_dir: Path) -> None:
    (tmp_assess_dir / "log.md").write_text("# Assess Log\n\n## 2026-05-01\n\nOld entry.\n\n---\n")
    entry = LogEntry(
        run_date="2026-05-22", files_scored=100, readiness_score=4.5,
        maturity_label="Solid", claudemd_grade="B+",
        graduated_count=0, regressed_count=0, new_count=0, persistent_count=0,
        top_action="Action X",
    )
    append_log_entry(tmp_assess_dir, entry)
    content = (tmp_assess_dir / "log.md").read_text()
    assert "2026-05-01" in content  # old entry preserved
    assert "2026-05-22" in content  # new entry appended
    assert content.index("2026-05-01") < content.index("2026-05-22")


def test_write_hotspot_page_creates_file(tmp_assess_dir: Path) -> None:
    write_hotspot_page(
        tmp_assess_dir,
        path="src/foo.go",
        first_flagged="2026-01-01",
        last_seen="2026-05-22",
        status="regressed",
        loc=600,
        ccn=30,
        commits=15,
        has_tests=False,
        history_rows="| 2026-01-01 | 500 | 25 | 8 | active |\n| 2026-05-22 | 600 | 30 | 15 | regressed |",
        briefing="Go API handler. Pairs with handler_test.go (which is missing).",
        actions="- Add `handler_test.go`\n- Split into smaller functions",
    )
    page = tmp_assess_dir / "hotspots" / "src-foo-go.md"
    assert page.exists()
    content = page.read_text()
    assert "src/foo.go" in content
    assert "regressed" in content
    assert "handler_test.go" in content
```

- [ ] **Step 2: Run tests to verify all fail**

Run: `cd skills/assess && uv run --with pytest pytest tests/test_wiki_writer.py -v`

Expected: ImportError for all tests.

- [ ] **Step 3: Implement wiki writer**

```python
# skills/assess/scripts/lib/wiki_writer.py
"""Render and write the .assess/ wiki files from templates.

No LLM calls. Pure string formatting + file IO. Deterministic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


# Templates live alongside the scripts/lib/ package, one directory up under templates/
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


@dataclass
class HotspotEntry:
    path: str
    first_flagged: str
    last_seen: str
    status: str   # active | graduated | regressed | persistent
    ccn: int
    loc: int


@dataclass
class LogEntry:
    run_date: str
    files_scored: int
    readiness_score: float
    maturity_label: str
    claudemd_grade: str
    graduated_count: int
    regressed_count: int
    new_count: int
    persistent_count: int
    top_action: str
    report_link: str = "./assess-report.md"


def slug_for_path(path: str) -> str:
    """Convert a file path into a safe filename slug."""
    safe = re.sub(r"[^a-zA-Z0-9]+", "-", path)
    return safe.strip("-").lower()


def _load_template(name: str) -> str:
    return (_TEMPLATES_DIR / name).read_text()


def write_index(assess_dir: Path, entries: list[HotspotEntry], *, last_updated: str) -> None:
    """(Re)write index.md from the current set of hotspot entries."""
    rows = []
    for e in entries:
        rows.append(
            f"| `{e.path}` | {e.first_flagged} | {e.last_seen} | {e.status} | {e.ccn} | {e.loc} |"
        )
    content = _load_template("index.md.template").format(
        last_updated=last_updated,
        hotspot_rows="\n".join(rows) if rows else "| _no hotspots tracked yet_ | | | | | |",
    )
    (assess_dir / "index.md").write_text(content)


def append_log_entry(assess_dir: Path, entry: LogEntry) -> None:
    """Append a dated entry to log.md (create the file if absent)."""
    log_path = assess_dir / "log.md"
    snippet = _load_template("log_entry.md.template").format(
        run_date=entry.run_date,
        files_scored=entry.files_scored,
        readiness_score=entry.readiness_score,
        maturity_label=entry.maturity_label,
        claudemd_grade=entry.claudemd_grade,
        graduated_count=entry.graduated_count,
        regressed_count=entry.regressed_count,
        new_count=entry.new_count,
        persistent_count=entry.persistent_count,
        top_action=entry.top_action,
        report_link=entry.report_link,
    )
    if log_path.exists():
        log_path.write_text(log_path.read_text() + snippet)
    else:
        log_path.write_text("# Assess Log\n\n" + snippet)


def write_hotspot_page(
    assess_dir: Path,
    *,
    path: str,
    first_flagged: str,
    last_seen: str,
    status: str,
    loc: int,
    ccn: int,
    commits: int,
    has_tests: bool,
    history_rows: str,
    briefing: str,
    actions: str,
) -> None:
    """(Re)write hotspots/<slug>.md."""
    hotspots_dir = assess_dir / "hotspots"
    hotspots_dir.mkdir(exist_ok=True)
    content = _load_template("hotspot.md.template").format(
        path=path,
        first_flagged=first_flagged,
        last_seen=last_seen,
        status=status,
        loc=loc,
        ccn=ccn,
        commits=commits,
        has_tests="yes" if has_tests else "no",
        history_rows=history_rows,
        briefing=briefing,
        actions=actions,
    )
    (hotspots_dir / f"{slug_for_path(path)}.md").write_text(content)
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `cd skills/assess && uv run --with pytest pytest tests/test_wiki_writer.py -v`

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add skills/assess/scripts/lib/wiki_writer.py \
        skills/assess/tests/test_wiki_writer.py
git commit -m "feat(assess): Add wiki writer for index, log, and hotspot pages"
```

---

## Task 6: Orchestration script (assess_core.py)

**Files:**
- Create: `skills/assess/tests/test_assess_core.py`
- Create: `skills/assess/scripts/assess_core.py`

The orchestrator wires the deterministic modules together. It reads prior state, runs the treemap, grades CLAUDE.md, computes the diff, updates wiki files, and writes a `run-context.json` summarizing everything the LLM needs to produce the prose sections of the report.

- [ ] **Step 1: Write end-to-end test against a tiny fixture repo**

```python
# skills/assess/tests/test_assess_core.py
"""End-to-end test for the assess_core orchestrator.

We don't run lizard/scc here - we drive assess_core via its public functions
to exercise the deterministic plumbing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from assess_core import build_run_context


def test_build_run_context_first_run(tmp_path: Path) -> None:
    """No prior .assess/, no CLAUDE.md - everything is 'new' or 'missing'."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()

    current_stats = {
        "files_scored": 50,
        "loc": {"p50": 30, "p95": 200, "max": 500},
        "ccn": {"p50": 2, "p95": 8, "max": 20},
        "top_hotspots": [
            {"path": "src/a.go", "loc": 500, "ccn": 20, "commits": 5},
        ],
        "top_complex": [{"path": "src/a.go", "ccn": 20}],
        "top_large": [{"path": "src/a.go", "loc": 500}],
    }
    (assess_dir / "complexity-stats.json").write_text(json.dumps(current_stats))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")

    assert ctx["run_date"] == "2026-05-22"
    assert ctx["stats_summary"]["files_scored"] == 50
    assert ctx["claudemd"]["grade"] == "F"  # no CLAUDE.md
    assert ctx["diff"]["new"] == 1
    assert ctx["diff"]["graduated"] == 0
    assert (assess_dir / "log.md").exists()
    assert (assess_dir / "index.md").exists()


def test_build_run_context_with_claudemd(tmp_path: Path, fixtures_dir: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text((fixtures_dir / "good_claudemd.md").read_text())
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 10, "loc": {"p50": 10, "p95": 30, "max": 50},
        "ccn": {"p50": 1, "p95": 3, "max": 5},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["claudemd"]["grade"] in {"A", "A-", "B+", "B"}
    assert ctx["claudemd"]["subscores"]["positive_directives"] >= 5


def test_build_run_context_second_run_sees_diff(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()

    # First run state - persist prior stats
    prior_stats = {
        "files_scored": 50, "loc": {"p50": 30, "p95": 200, "max": 500},
        "ccn": {"p50": 2, "p95": 8, "max": 20},
        "top_hotspots": [
            {"path": "src/legacy.go", "loc": 500, "ccn": 20, "commits": 5},
        ],
        "top_complex": [{"path": "src/legacy.go", "ccn": 20}],
        "top_large": [{"path": "src/legacy.go", "loc": 500}],
    }
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps(prior_stats))

    current_stats = {
        "files_scored": 55, "loc": {"p50": 30, "p95": 220, "max": 550},
        "ccn": {"p50": 2, "p95": 9, "max": 22},
        "top_hotspots": [
            {"path": "src/new.go", "loc": 400, "ccn": 18, "commits": 4},
        ],
        "top_complex": [{"path": "src/new.go", "ccn": 18}],
        "top_large": [{"path": "src/new.go", "loc": 400}],
    }
    (assess_dir / "complexity-stats.json").write_text(json.dumps(current_stats))

    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["diff"]["graduated"] == 1
    assert ctx["diff"]["new"] == 1


def test_build_run_context_writes_hotspot_pages(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 10, "loc": {"p50": 10, "p95": 30, "max": 50},
        "ccn": {"p50": 1, "p95": 3, "max": 5},
        "top_hotspots": [
            {"path": "src/foo.go", "loc": 500, "ccn": 20, "commits": 5},
        ],
        "top_complex": [{"path": "src/foo.go", "ccn": 20}],
        "top_large": [{"path": "src/foo.go", "loc": 500}],
    }))

    build_run_context(repo_root=repo, run_date="2026-05-22")
    hotspots = list((assess_dir / "hotspots").iterdir())
    assert len(hotspots) == 1
    assert hotspots[0].name == "src-foo-go.md"
```

- [ ] **Step 2: Run tests to verify all fail**

Run: `cd skills/assess && uv run --with pytest pytest tests/test_assess_core.py -v`

Expected: ModuleNotFoundError for `assess_core`.

- [ ] **Step 3: Implement assess_core.py**

```python
# skills/assess/scripts/assess_core.py
"""Orchestrator for the deterministic core of /assess.

Reads:
    {repo_root}/.assess/complexity-stats.json       (current run)
    {repo_root}/.assess/complexity-stats.prior.json (if it exists)
    {repo_root}/CLAUDE.md                           (if it exists)

Writes:
    {repo_root}/.assess/run-context.json   (everything the LLM needs)
    {repo_root}/.assess/index.md           (regenerated each run)
    {repo_root}/.assess/log.md             (appended each run)
    {repo_root}/.assess/hotspots/*.md      (one per top hotspot)

Run:
    uv run assess_core.py <repo_root>

The LLM still writes assess-report.md (the prose-heavy summary).
The LLM reads run-context.json to ground that prose in deterministic data.
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Make sibling lib package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.claudemd_grader import grade_claudemd
from lib.stats_diff import diff_stats, load_stats
from lib.wiki_writer import (
    HotspotEntry,
    LogEntry,
    append_log_entry,
    write_hotspot_page,
    write_index,
)


def _claudemd_freshness_days(claudemd_path: Path) -> int:
    """Days since the CLAUDE.md was last touched in git."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", str(claudemd_path)],
            cwd=claudemd_path.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        ts = int(out.stdout.strip()) if out.stdout.strip() else 0
        if ts == 0:
            return 0
        delta = datetime.now().timestamp() - ts
        return max(0, int(delta // 86400))
    except (ValueError, FileNotFoundError):
        return 0


def _grade_claudemd_if_present(repo_root: Path) -> dict:
    candidate = repo_root / "CLAUDE.md"
    if not candidate.exists():
        return {"grade": "F", "score": 0, "subscores": {}, "present": False}
    text = candidate.read_text()
    freshness = _claudemd_freshness_days(candidate)
    grade = grade_claudemd(text, freshness_days=freshness)
    return {
        "grade": grade.grade,
        "score": grade.score,
        "subscores": grade.subscores,
        "freshness_days": freshness,
        "present": True,
    }


def _status_for_path(path: str, diff_summary: dict, current_paths: set[str]) -> str:
    """Map a hotspot path to its transition status."""
    if path not in current_paths:
        return "graduated"
    return diff_summary.get(path, "new")


def build_run_context(*, repo_root: Path, run_date: str) -> dict:
    """Run the deterministic pipeline and return the structured context dict.

    Side effects: writes index.md, log.md, hotspots/*.md, run-context.json.
    """
    assess_dir = repo_root / ".assess"
    current = load_stats(assess_dir / "complexity-stats.json") or {
        "files_scored": 0, "top_hotspots": [], "top_complex": [], "top_large": [],
        "loc": {}, "ccn": {},
    }
    prior = load_stats(assess_dir / "complexity-stats.prior.json")

    diff = diff_stats(prior=prior, current=current)
    claudemd = _grade_claudemd_if_present(repo_root)

    # Build status map: which paths are graduated, new, regressed, persistent
    status_map: dict[str, str] = {}
    for h in diff.graduated:
        status_map[h.path] = "graduated"
    for h in diff.new:
        status_map[h.path] = "new"
    for h in diff.regressed:
        status_map[h.path] = "regressed"
    for h in diff.persistent:
        status_map[h.path] = "persistent"

    # Wiki: hotspot pages for current top hotspots
    hotspot_entries: list[HotspotEntry] = []
    for h in current.get("top_hotspots", []):
        status = status_map.get(h["path"], "active")
        hotspot_entries.append(HotspotEntry(
            path=h["path"],
            first_flagged=run_date,    # refined in a later plan via log scan
            last_seen=run_date,
            status=status,
            ccn=h.get("ccn", 0),
            loc=h.get("loc", 0),
        ))
        write_hotspot_page(
            assess_dir,
            path=h["path"],
            first_flagged=run_date,
            last_seen=run_date,
            status=status,
            loc=h.get("loc", 0),
            ccn=h.get("ccn", 0),
            commits=h.get("commits", 0),
            has_tests=False,  # filled in by a follow-up plan (test pairing)
            history_rows=f"| {run_date} | {h.get('loc', 0)} | {h.get('ccn', 0)} | {h.get('commits', 0)} | {status} |",
            briefing=f"Hot file in this repo. CCN {h.get('ccn', 0)}, {h.get('loc', 0)} LOC.",
            actions="- Pending LLM-generated suggestions",
        )

    # Also surface graduated hotspots in the index
    for h in diff.graduated:
        hotspot_entries.append(HotspotEntry(
            path=h.path,
            first_flagged=run_date,
            last_seen=run_date,
            status="graduated",
            ccn=0,
            loc=0,
        ))

    write_index(assess_dir, hotspot_entries, last_updated=run_date)

    top_action = "Deterministic ranker not yet wired (LLM picks Top 3)"
    log_entry = LogEntry(
        run_date=run_date,
        files_scored=current.get("files_scored", 0),
        readiness_score=0.0,  # LLM produces the layered score
        maturity_label="(LLM fills in)",
        claudemd_grade=claudemd["grade"],
        graduated_count=len(diff.graduated),
        regressed_count=len(diff.regressed),
        new_count=len(diff.new),
        persistent_count=len(diff.persistent),
        top_action=top_action,
    )
    append_log_entry(assess_dir, log_entry)

    ctx = {
        "run_date": run_date,
        "repo_root": str(repo_root),
        "stats_summary": {
            "files_scored": current.get("files_scored", 0),
            "loc": current.get("loc", {}),
            "ccn": current.get("ccn", {}),
            "top_hotspots": current.get("top_hotspots", []),
        },
        "claudemd": claudemd,
        "diff": diff.summary(),
        "diff_detail": {
            "graduated": [h.__dict__ for h in diff.graduated],
            "regressed": [h.__dict__ for h in diff.regressed],
            "new": [h.__dict__ for h in diff.new],
            "persistent": [h.__dict__ for h in diff.persistent],
        },
    }
    (assess_dir / "run-context.json").write_text(json.dumps(ctx, indent=2))
    return ctx


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: assess_core.py <repo_root>", file=sys.stderr)
        return 2
    repo_root = Path(sys.argv[1]).resolve()
    run_date = datetime.now().strftime("%Y-%m-%d")
    ctx = build_run_context(repo_root=repo_root, run_date=run_date)
    print(json.dumps(ctx["diff"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `cd skills/assess && uv run --with pytest pytest tests/test_assess_core.py -v`

Expected: 4 passed.

- [ ] **Step 5: Run full test suite as regression check**

Run: `cd skills/assess && uv run --with pytest pytest -v`

Expected: All tests pass (smoke + grader + diff + wiki_writer + assess_core = ~27 tests).

- [ ] **Step 6: Commit**

```bash
git add skills/assess/scripts/assess_core.py \
        skills/assess/tests/test_assess_core.py
git commit -m "feat(assess): Add assess_core orchestrator with run-context.json output"
```

---

## Task 7: Update SKILL.md to use deterministic core

**Files:**
- Modify: `skills/assess/SKILL.md`

The SKILL.md currently embeds heuristics inline (`ls -la CLAUDE.md`, grading via prose). Replace that with a single call to `assess_core.py` and instruct the LLM to read `run-context.json` to produce the prose sections.

- [ ] **Step 1: Read current SKILL.md to identify sections to replace**

Run: `wc -l skills/assess/SKILL.md && grep -n '^##' skills/assess/SKILL.md`

Expected output: line count + numbered section headers. Note line numbers for Step 1 (output dir), Step 2 (treemap), Step 3 (layer scans), Step 4 (report), Step 5 (PR).

- [ ] **Step 2: Modify Step 2 of SKILL.md to chain assess_core after treemap**

Find the Step 2 block in SKILL.md (the treemap section). Append a new sub-step:

```markdown
After the treemap completes, run the deterministic core to produce the wiki files and the run context:

\`\`\`bash
# Rotate the prior stats sidecar so the diff has something to compare against next run
if [ -f "$REPO_ROOT/.assess/complexity-stats.json" ]; then
  cp "$REPO_ROOT/.assess/complexity-stats.json" "$REPO_ROOT/.assess/complexity-stats.prior.json" 2>/dev/null || true
fi

# Run the treemap (produces fresh complexity-stats.json)
uv run "$SKILL_DIR/scripts/complexity-treemap.py" "$REPO_ROOT" \
    -o "$REPO_ROOT/.assess/complexity-heatmap.svg" \
    --stats "$REPO_ROOT/.assess/complexity-stats.json"

# Run the deterministic core (CLAUDE.md grade, stats diff, wiki files, run-context.json)
uv run "$SKILL_DIR/scripts/assess_core.py" "$REPO_ROOT"
\`\`\`

Now `$REPO_ROOT/.assess/run-context.json` contains the structured data you need for the prose sections. Read it before writing the report.
```

(The `\` escapes shown above are for the plan document - in the actual file the code block uses plain triple backticks.)

- [ ] **Step 3: Modify the Layer 0 (Breadcrumbs) section to consume the grade from run-context.json**

Find the Layer 0 section. Replace the manual `ls CLAUDE.md` instructions with:

```markdown
### Layer 0: Breadcrumbs (Behavioral Contracts)

Read the CLAUDE.md grade from `run-context.json`:

\`\`\`bash
jq '.claudemd' "$REPO_ROOT/.assess/run-context.json"
\`\`\`

Use this directly for the report:
- `grade: "A" | "B+" | ...` - report verbatim
- `subscores.positive_directives` - count of positive directives found
- `subscores.tradeoff_phrases` - count of reasoning phrases
- `subscores.path_references` - count of file path references
- `freshness_days` - days since last CLAUDE.md edit

**Scoring rule:** trust the grade. The heuristic is deterministic and tested.
- A/A-/B+ → **Present**
- B/C → **Partial**
- D/F → **Missing** (or **Partial** if file exists but scores low - note the grade)

This replaces the prior subjective "is it generic?" check. The grader rewards positive directives and tradeoff reasoning; it penalizes pure-negative framing and staleness.
```

- [ ] **Step 4: Add a new section between Step 3 and Step 4 for cross-run context**

```markdown
### Step 3.5: Read Cross-Run Context

Before scoring, check what changed since the last run:

\`\`\`bash
jq '.diff, .diff_detail' "$REPO_ROOT/.assess/run-context.json"
\`\`\`

If `prior` was None (first run), skip this section in the report. Otherwise, populate a "What Changed Since Last Run" section in the report:

- **Graduated** (good): list paths from `diff_detail.graduated` - hotspots that left the top list
- **Regressed** (bad): list paths from `diff_detail.regressed` with their `ccn_delta` / `commits_delta`
- **New** (watch): list paths from `diff_detail.new`
- **Persistent** (structural debt if N runs in a row): list paths from `diff_detail.persistent`

The wiki files at `.assess/index.md` and `.assess/hotspots/*.md` are already updated by `assess_core.py` - you don't need to write them. You only write the prose summary in `assess-report.md`.
```

- [ ] **Step 5: Modify Step 4 (report) to require positive-framed action language**

In Step 4, find the "Good actions look like" and "Generic actions to avoid" example block. Add a new bullet:

```markdown
**Frame actions positively.** "Add `cyclop` rule (threshold 15) to `.golangci.yml`" beats "Stop letting complex code through CI." Positive directives are easier for the next contributor (human or LLM) to act on - they say what to do, not what to avoid. If you find yourself writing "Don't X" or "Never Y", convert to "Use X (because Z)" instead.
```

- [ ] **Step 6: Update the report template's footer to point at the wiki structure**

In Step 4, find the closing footer line:

```markdown
_Report generated by [`/ai-native-toolkit:assess`](https://github.com/bjcoombs/ai-native-toolkit)..._
```

Add immediately above it:

```markdown
**Wiki:** see `.assess/index.md` for the full hotspot catalog across all runs, `.assess/log.md` for run history, and `.assess/hotspots/<file>.md` for per-file briefings.
```

- [ ] **Step 7: Sanity-check the modified SKILL.md by rendering it**

Run: `cat skills/assess/SKILL.md | head -50 && echo '...' && tail -30 skills/assess/SKILL.md`

Expected: header intact, new sections present, footer intact.

- [ ] **Step 8: Commit**

```bash
git add skills/assess/SKILL.md
git commit -m "feat(assess): Wire SKILL.md to deterministic core and wiki"
```

---

## Task 8: Documentation and version bump

**Files:**
- Modify: `CLAUDE.md`
- Modify: `.claude-plugin/plugin.json`

- [ ] **Step 1: Update CLAUDE.md to document the new architecture**

Add a new section after the existing "## CI" section:

```markdown
## /assess architecture

Deterministic core in `skills/assess/scripts/lib/` does all data work; the LLM only writes prose.

- `lib/claudemd_grader.py` - heuristic CLAUDE.md scoring (regex + arithmetic, no AI)
- `lib/stats_diff.py` - cross-run comparison (graduated/regressed/new/persistent hotspots)
- `lib/wiki_writer.py` - renders `index.md`, `log.md`, `hotspots/*.md` from templates
- `scripts/assess_core.py` - orchestrator; writes `run-context.json` for the LLM to read

Tests live in `skills/assess/tests/` and run via `uv run --with pytest pytest`. Add a test alongside any change to a deterministic module - that's the contract that lets us trust the output regardless of which LLM is driving.

The `.assess/` directory in a target repo is a compounding wiki:

- `assess-report.md` - latest prose-heavy summary (LLM-written)
- `complexity-stats.json` + `.prior.json` - current and previous run sidecars
- `complexity-heatmap.svg` - current treemap
- `run-context.json` - structured data the LLM uses for prose
- `index.md` - catalog of every hotspot ever flagged (deterministic)
- `log.md` - append-only run history (deterministic)
- `hotspots/<slug>.md` - per-file persistent page (deterministic)

Each `/assess` run reads the prior state from this directory and adds to it. Hotspots that leave the top list graduate. The wiki is the value, not any single snapshot.
```

- [ ] **Step 2: Bump version in plugin.json**

```bash
# Read current version, bump minor (new feature: deterministic core + wiki)
# 1.3.0 → 1.4.0
```

Modify `.claude-plugin/plugin.json`:

```json
{
  "name": "ai-native-toolkit",
  "version": "1.4.0",
  ...
}
```

Use Edit with old `"version": "1.3.0"` → new `"version": "1.4.0"` (single occurrence).

- [ ] **Step 3: Update CLAUDE.md Versioning section to note the bump rationale**

Find the existing Versioning table. The current rules describe semver - no change needed; just confirm 1.4.0 is correct for "new feature, no breaking changes."

- [ ] **Step 4: Run full test suite as final check**

Run: `cd skills/assess && uv run --with pytest pytest -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md .claude-plugin/plugin.json
git commit -m "docs(assess): Document deterministic core architecture and bump to 1.4.0"
```

- [ ] **Step 6: Push and open PR**

```bash
git push -u origin assess-deterministic-wiki
gh pr create --title "feat(assess): Add deterministic core + compounding wiki" --body "$(cat <<'EOF'
## Summary

- Refactor /assess so all data work runs in deterministic Python (heuristic CLAUDE.md grading, stats diffing, template-driven MD generation)
- LLM responsible only for prose synthesis
- `.assess/` becomes a compounding wiki: `index.md`, `log.md`, `hotspots/<file>.md`
- Each run reads prior state and updates entity pages rather than overwriting

## Architecture

New Python modules under `skills/assess/scripts/lib/`:
- `claudemd_grader.py` - heuristic CLAUDE.md scoring (positive directives, tradeoff phrases, path refs, verifiable outcomes, freshness)
- `stats_diff.py` - cross-run comparison (graduated/regressed/new/persistent)
- `wiki_writer.py` - render templates → MD files

Orchestrator `assess_core.py` wires the modules together, writes `run-context.json` for the LLM, and updates the wiki files. All deterministic, tested via pytest.

SKILL.md now points at the scripts and instructs the LLM to read `run-context.json` for the data it needs to write prose.

## Test plan

- [ ] `uv run --with pytest pytest skills/assess/` - all green
- [ ] Run `/assess` against a fresh repo (no prior `.assess/`) - wiki is created, no errors
- [ ] Run `/assess` a second time - "What Changed Since Last Run" section appears
- [ ] Run `/assess` against a repo with CLAUDE.md - grade reflects content quality

## Out of scope (follow-up plans)

- Coverage data ingestion (codecov API + lcov reader)
- Git-history append-rate analysis
- Function-level complexity granularity
- Test↔code pairing automation
EOF
)"
```

- [ ] **Step 7: Watch CI and fix any failures**

```bash
PR=$(gh pr view --json number --jq '.number')
gh pr checks $PR --watch --fail-fast
```

If CI fails: fix, commit, push. Repeat until green.

---

## Self-Review Notes

**Spec coverage:**
- Heuristic CLAUDE.md grading (Task 2) - covers user's "grade content, not existence" request
- Standard tooling over AI (Tasks 2, 3, 5, 6) - deterministic Python throughout
- Positive-framing reward in grader (Task 2) - directly addresses the user's catch about my own negative framing
- Loop awareness via prior-stats read (Tasks 3, 6) - matches user's "the loop happens naturally" insight
- Wiki structure from Karpathy gist (Tasks 4, 5, 6) - index.md, log.md, hotspots/*.md
- Documentation updated (Task 8) - so future contributors understand the split
- Version bump (Task 8) - matches the existing repo convention

**Out of scope confirmed:**
- Coverage data, append-rate detection, function-level granularity, test pairing - explicit in plan header

**Type consistency check:**
- `HotspotEntry`, `LogEntry`, `HotspotTransition`, `Grade`, `StatsDiff` are the dataclasses; each used identically across modules
- `grade_claudemd(text, freshness_days=int)` signature consistent across grader, assess_core, tests
- `diff_stats(*, prior, current)` keyword-only, consistent

**Placeholder scan:** none found - every code block has full implementation.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-22-assess-deterministic-wiki.md`.

Two execution options:

**1. Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration. Best for plans this long (8 tasks, ~50 steps).

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints. Faster if I stay focused, but heavier on context.

Which approach?
