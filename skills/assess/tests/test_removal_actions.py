"""A removal proposal end to end (#1084): an oversized always-on instruction file gets a
``remove`` action from the core, and finalize writes it to actions.json with its mode
and measured cost."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from lib.gap_actions import REMOVAL_FINDING, REMOVE_MODE
from lib.instruction_placement import ALWAYS_ON_FLAG_LINES
from test_headline import _commit, _run

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _repo(tmp_path: Path, lines: int) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    # one heading-free file: a single unclassified section, counted always-on (share 1.0)
    body = "".join(f"Remember point {n} about how this project is laid out.\n" for n in range(lines))
    (repo / "CLAUDE.md").write_text("Run `make test` before every commit.\n" + body, encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _commit(repo, "2026-11-01T10:00:00Z", "one")
    (repo / ".assess").mkdir()
    (repo / ".assess" / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 1, "loc": {}, "ccn": {}, "top_hotspots": [], "top_complex": [], "top_large": [],
    }), encoding="utf-8")
    return repo


def test_an_oversized_always_on_instruction_file_gets_a_remove_action(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ALWAYS_ON_FLAG_LINES + 60)
    ctx = _run(repo, "2026-11-21")
    removals = [g for g in ctx["gap_actions"] if g.get("mode") == REMOVE_MODE]
    assert len(removals) == 1
    gap = removals[0]
    assert gap["paths"] == ["CLAUDE.md"] and gap["finding"] == REMOVAL_FINDING
    assert gap["cost"]["metric"] == "always_on_lines" and gap["cost"]["value"] > ALWAYS_ON_FLAG_LINES
    # the writer transcribes it into the Top 3; finalize stamps the mode and keeps the cost
    cache = repo / ".assess" / ".cache"
    cache.mkdir(exist_ok=True)
    (cache / "finalize-input.json").write_text(json.dumps({
        "run_id": ctx["run_id"], "top_action": gap["action"],
        "actions": [{"rank": 1, "action": gap["action"], "done_when": "always_on_lines <= 200",
                     "scope_fence": "CLAUDE.md only", "finding": gap["finding"], "files": gap["paths"],
                     "cost": gap["cost"]}],
    }), encoding="utf-8")
    done = subprocess.run([sys.executable, str(SCRIPTS / "assess_finalize.py"), str(repo)],
                          capture_output=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stderr
    (action,) = json.loads((repo / ".assess" / "actions.json").read_text(encoding="utf-8"))["actions"]
    assert (action["mode"], action["cost"]) == (REMOVE_MODE, gap["cost"])


def test_a_file_within_the_flag_gets_no_remove_action(tmp_path: Path) -> None:
    ctx = _run(_repo(tmp_path, ALWAYS_ON_FLAG_LINES - 50), "2026-11-21")
    assert not [g for g in ctx["gap_actions"] if g.get("mode") == REMOVE_MODE]
