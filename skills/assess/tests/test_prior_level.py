"""``prior-level.json`` and the run context's ``prior_level`` (``lib/prior_level.py``, #940)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lib.level_tally import default_model, level_result
from lib.since_last_run import baseline_record
from lib.prior_level import (
    PRIOR_LEVEL_FILE,
    PRIOR_LEVEL_VERSION,
    UNREADABLE,
    as_prior_level,
    build_prior_level,
    load_prior_level,
    same_model,
    write_prior_level,
)


def _level() -> dict[str, Any]:
    return dict(level_result(default_model(), {}))


def test_no_file_reads_none(tmp_path: Path) -> None:
    assert load_prior_level(tmp_path) is None


def test_write_then_load_round_trips_the_tags_and_model_version(tmp_path: Path) -> None:
    level = _level()
    path = write_prior_level(tmp_path, level)
    assert path == tmp_path / PRIOR_LEVEL_FILE
    prior = load_prior_level(tmp_path)
    assert prior == {"prior_level_version": PRIOR_LEVEL_VERSION, "levels_version": level["levels_version"],
                     "tags": level["tags"]}
    assert same_model(prior, level)
    assert not same_model(prior, {**level, "levels_version": "other"})
    assert not same_model(None, level)
    first = path.read_bytes()
    write_prior_level(tmp_path, level)
    assert path.read_bytes() == first and b"\r\n" not in first


@pytest.mark.parametrize("content", [
    "not json",
    "[]",
    json.dumps({"prior_level_version": 99, "levels_version": "m", "tags": []}),
    json.dumps({"prior_level_version": 1, "levels_version": 7, "tags": []}),
    json.dumps({"prior_level_version": 1, "levels_version": "m", "tags": [{"tag": "nope"}]}),
    json.dumps({"prior_level_version": 1, "levels_version": "m", "tags": [
        {"tag": "docs", "conditions": 1, "countable": 1, "set_aside": 0, "met": -1, "measured": 1, "not_measured": [], "countable_not_measured": []}]}),
    json.dumps({"prior_level_version": 1, "levels_version": "m", "tags": [
        {"tag": "docs", "conditions": 1, "countable": 1, "set_aside": 0, "met": True, "measured": 1, "not_measured": [], "countable_not_measured": []}]}),
    json.dumps({"prior_level_version": 1, "levels_version": "m", "tags": [
        {"tag": "docs", "conditions": 1, "countable": 1, "set_aside": 0, "met": 0, "measured": 1, "not_measured": [{"id": 1}], "countable_not_measured": []}]}),
    # counts that do not nest: a share above 1 would draw off the scale
    json.dumps({"prior_level_version": 1, "levels_version": "m", "tags": [
        {"tag": "docs", "conditions": 60, "countable": 60, "set_aside": 0, "met": 50, "measured": 2, "not_measured": [], "countable_not_measured": []}]}),
    json.dumps({"prior_level_version": 1, "levels_version": "m", "tags": [
        {"tag": "docs", "conditions": 1, "countable": 1, "set_aside": 0, "met": 0, "measured": 2, "not_measured": [], "countable_not_measured": []}]}),
    json.dumps({"prior_level_version": 1, "levels_version": "m", "tags": [
        {"tag": "docs", "conditions": 1, "countable": 2, "set_aside": 0, "met": 0, "measured": 0, "not_measured": [], "countable_not_measured": []}]}),
    json.dumps({"prior_level_version": 1, "levels_version": "m", "tags": [
        {"tag": "docs", "conditions": 2, "countable": 2, "set_aside": 1, "met": 0, "measured": 2, "not_measured": [], "countable_not_measured": []}]}),
])
def test_a_malformed_file_reads_unreadable_not_absent(tmp_path: Path, content: str) -> None:
    (tmp_path / PRIOR_LEVEL_FILE).write_text(content, encoding="utf-8")
    assert load_prior_level(tmp_path) == {"available": False, "reason": "unreadable"}
    assert as_prior_level(load_prior_level(tmp_path)) is None


def test_a_level_without_tags_writes_nothing_and_keeps_the_previous_good_file(tmp_path: Path) -> None:
    """#1158: a degraded run leaves the previous good run's file, with its baseline and ledger."""
    write_prior_level(tmp_path, _level())
    before = (tmp_path / PRIOR_LEVEL_FILE).read_bytes()
    assert write_prior_level(tmp_path, {"available": False, "reason": "x"}) is None
    assert (tmp_path / PRIOR_LEVEL_FILE).read_bytes() == before
    assert build_prior_level({**_level(), "levels_version": None}) is None
    assert build_prior_level(None) is None


def test_as_prior_level_keeps_a_valid_value_only() -> None:
    prior = build_prior_level(_level())
    assert as_prior_level(prior) == prior
    assert as_prior_level(None) is None


def test_counts_only_keeps_every_count_and_token() -> None:
    from lib.counts_only.content_free import counts_only

    prior = build_prior_level(_level())
    assert counts_only({"prior_level": prior})["prior_level"] == prior  # fails if a field is dropped
    # the unreadable form keeps its fixed reason token too
    assert counts_only({"prior_level": {**UNREADABLE}})["prior_level"] == UNREADABLE


# --- through a real run ------------------------------------------------------

REPO = {
    "src/a.py": "def f():\n    return 1\n",
    "CLAUDE.md": "# Project\n\nRun `make test` before every commit.\n",
}


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-11-01T10:00:00Z", "GIT_COMMITTER_DATE": "2026-11-01T10:00:00Z"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for rel, text in REPO.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8", newline="\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "one")
    return root


def test_a_run_reads_the_earlier_file_before_writing_its_own(repo: Path) -> None:
    from assess_core import build_and_publish_run

    first = build_and_publish_run(repo_root=repo, run_date="2026-11-22", non_interactive=True)
    assert first["prior_level"] is None
    stored = json.loads((repo / ".assess" / PRIOR_LEVEL_FILE).read_text(encoding="utf-8"))
    assert stored["tags"] == first["level"]["tags"]
    strip = (repo / ".assess" / "tag-profile.svg").read_text(encoding="utf-8")
    assert 'data-mark="prior"' not in strip and "No earlier run is recorded here" in strip

    # A hand-edited earlier file: the second run draws it, so it must have read it before writing.
    stored["tags"] = [{**row, "met": 0, "measured": 1} if row["tag"] == "agent_context" else row
                      for row in stored["tags"]]
    (repo / ".assess" / PRIOR_LEVEL_FILE).write_text(json.dumps(stored), encoding="utf-8")
    second = build_and_publish_run(repo_root=repo, run_date="2026-11-23", non_interactive=True)
    assert second["prior_level"] == stored
    strip = (repo / ".assess" / "tag-profile.svg").read_text(encoding="utf-8")
    assert strip.count('data-mark="prior"') == sum(row["measured"] > 0 for row in stored["tags"])
    assert '<g data-tag="agent_context"' in strip
    agent_row = strip.split('<g data-tag="agent_context"', 1)[1].split("</g>", 1)[0]
    assert 'data-mark="prior"' in agent_row
    after = json.loads((repo / ".assess" / PRIOR_LEVEL_FILE).read_text(encoding="utf-8"))
    assert after["tags"] == second["level"]["tags"]
    ctx = json.loads((repo / ".assess" / "run-context.json").read_text(encoding="utf-8"))
    assert ctx["prior_level"] == stored


def test_the_mutation_recount_rewrites_the_file_and_keeps_the_earlier_run(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``run_opt_in_mutation`` writes the recounted tags and never re-reads the file it replaces."""
    import assess_core
    from assess_core import build_and_publish_run

    build_and_publish_run(repo_root=repo, run_date="2026-11-22", non_interactive=True)
    earlier = load_prior_level(repo / ".assess")
    build_and_publish_run(repo_root=repo, run_date="2026-11-23", non_interactive=True)
    ctx_path = repo / ".assess" / "run-context.json"
    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert ctx["prior_level"] == earlier
    (repo / ".assess" / PRIOR_LEVEL_FILE).write_text("stale", encoding="utf-8")
    monkeypatch.setattr(assess_core, "mutation_scope", lambda focus: ["src/a.py"])
    monkeypatch.setattr(assess_core, "scan_test_pressure", lambda *a, **k: ctx["test_pressure"])
    assert assess_core.run_opt_in_mutation(repo) == 0
    after = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert after["prior_level"] == earlier
    assert load_prior_level(repo / ".assess") == build_prior_level(after["level"], baseline_record(after))
    strip = (repo / ".assess" / "tag-profile.svg").read_text(encoding="utf-8")
    assert "Hollow dots: the earlier run" in strip


def test_a_run_over_an_unreadable_earlier_file_says_so_and_replaces_it(repo: Path) -> None:
    from assess_core import build_and_publish_run

    (repo / ".assess").mkdir()
    (repo / ".assess" / PRIOR_LEVEL_FILE).write_text("{not json", encoding="utf-8")
    ctx = build_and_publish_run(repo_root=repo, run_date="2026-11-22", non_interactive=True)
    assert ctx["prior_level"] == {"available": False, "reason": "unreadable"}
    strip = (repo / ".assess" / "tag-profile.svg").read_text(encoding="utf-8")
    assert "record could not be read" in strip and 'data-mark="prior"' not in strip
    assert load_prior_level(repo / ".assess") == build_prior_level(ctx["level"], baseline_record(ctx))


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlink_in_the_file_place_is_replaced_never_written_through(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("keep", encoding="utf-8")
    assess = tmp_path / "assess"
    assess.mkdir()
    (assess / PRIOR_LEVEL_FILE).symlink_to(outside)
    path = write_prior_level(assess, _level())
    assert path is not None and not path.is_symlink()
    assert outside.read_text(encoding="utf-8") == "keep"
    assert load_prior_level(assess) == build_prior_level(_level())


def test_a_file_that_cannot_be_written_is_named_and_skipped(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "no-such-dir"
    assert write_prior_level(missing, _level()) is None
    assert f"{PRIOR_LEVEL_FILE} not written" in capsys.readouterr().err


def test_version_2_adds_the_committed_baseline_and_version_1_still_reads(tmp_path: Path) -> None:
    """#1122: the baseline sits beside the tags; a version-1 file still loads, without one."""
    from lib.prior_level import Baseline, load_baseline
    level = _level()
    baseline: Baseline = {"run_id": "r1", "head_sha": "a" * 40, "held_gates": ["instructions"]}
    write_prior_level(tmp_path, level, baseline)
    assert load_baseline(tmp_path) == baseline
    (tmp_path / PRIOR_LEVEL_FILE).write_text(json.dumps(
        {"prior_level_version": 1, "levels_version": level["levels_version"], "tags": level["tags"]}), encoding="utf-8")
    assert load_prior_level(tmp_path) is not None and load_baseline(tmp_path) is None
    (tmp_path / PRIOR_LEVEL_FILE).write_text(json.dumps(
        {"prior_level_version": 2, "levels_version": "m", "tags": [], "baseline": {"head_sha": 7}}), encoding="utf-8")
    # a malformed baseline is dropped alone; the tags still read
    assert load_prior_level(tmp_path) == {"prior_level_version": 2, "levels_version": "m", "tags": []}
    assert load_baseline(tmp_path) is None


def test_a_malformed_ledger_is_dropped_alone(tmp_path: Path) -> None:
    """#1084: a bad pruning entry drops the ledger; the tags and the baseline still read."""
    from lib.prior_level import load_baseline, load_pruning
    level = _level()
    (tmp_path / PRIOR_LEVEL_FILE).write_text(json.dumps({
        "prior_level_version": 2, "levels_version": level["levels_version"], "tags": level["tags"],
        "baseline": {"run_id": "r", "head_sha": "b" * 40, "held_gates": []},
        "pruning": [{"path": "CLAUDE.md", "completed_sha": "a" * 40, "cost_before": 300, "runs_held": -1,
                     "credited": False, "saved": None, "gates": None, "credited_at": None}]}), encoding="utf-8")
    prior = load_prior_level(tmp_path)
    assert isinstance(prior, dict) and prior["tags"] == level["tags"] and "pruning" not in prior
    assert load_pruning(tmp_path) == [] and load_baseline(tmp_path) is not None


# --- one record per model (#1184) ----------------------------------------------------------

def test_model_key_names_the_record_a_level_reads() -> None:
    from lib.prior_level import model_key

    assert model_key({"levels_version": "default-2026.11.10"}) == "default"
    assert model_key({"available": False}) == "default"  # a degraded level writes nothing anyway
    assert model_key({"levels_version": "profile-citizen-2026.11.23"}) == "citizen"
    assert model_key({"levels_version": "team-1"}) is None
    assert model_key({"levels_version": "profile-"}) is None


def test_a_version_three_file_keeps_readable_profile_records_and_drops_the_rest() -> None:
    from lib.prior_level import as_prior_level, record_for

    tags = _level()["tags"]
    good = {"levels_version": "profile-citizen-2026.11.23", "tags": tags,
            "baseline": {"run_id": None, "head_sha": "c" * 40, "held_gates": ["instructions"]}}
    raw = {"prior_level_version": 3, "models": {
        "citizen": good,
        "handover": {"levels_version": "profile-handover-2026.11.23", "tags": [{"tag": "nope"}]},
        "broken": {**good, "baseline": {"head_sha": ""}}}}
    prior = as_prior_level(raw)
    assert prior is not None and set(prior["models"]) == {"citizen", "broken"}
    assert "baseline" not in prior["models"]["broken"]  # a malformed baseline is dropped alone
    citizen = {"levels_version": "profile-citizen-2026.11.23"}
    assert record_for(raw, citizen) == {"prior_level_version": 3, **good}
    assert record_for(raw, {"levels_version": "default-2026.11.10"}) is None  # no default record
    assert as_prior_level({"prior_level_version": 3}) is None  # neither record
    assert as_prior_level({"prior_level_version": 2, "models": {"citizen": good}}) is None  # v2 has no models
    v2 = {"prior_level_version": 2, "levels_version": "default-2026.11.23", "tags": tags,
          "models": {"citizen": good, "junk": "junk"}}
    read = as_prior_level(v2)
    assert read is not None and "models" not in read  # a v2 file's models is dropped, never read
    assert record_for(v2, citizen) is None
    assert record_for({**v2, "models": {"citizen": "junk"}}, citizen) is None


def test_an_older_file_still_reads_as_the_default_record(tmp_path: Path) -> None:
    from lib.prior_level import load_baseline, record_for

    baseline = {"run_id": "r", "head_sha": "a" * 40, "held_gates": ["instructions"]}
    old = {"prior_level_version": 2, "levels_version": "default-2026.11.10", "tags": _level()["tags"],
           "baseline": baseline}
    (tmp_path / "prior-level.json").write_text(json.dumps(old), encoding="utf-8")
    assert load_baseline(tmp_path) == baseline
    assert load_baseline(tmp_path, {"levels_version": "profile-citizen-2026.11.23"}) is None
    assert record_for(old, {"levels_version": "team-1"}) is None


# --- one record per base model and pack set (#1276, carried from #1301) --------------------

def _packed(*packs: str) -> dict[str, Any]:
    """A default-model level with pack rows: each condition names its source, the base first."""
    level = _level()
    conditions = [{**c, "source": "default"} for c in level["conditions"]]
    conditions += [{"id": f"{p}-row.1", "row": f"{p}-row", "level": 1, "grade": None, "counted": False,
                    "state": "met", "source": p} for p in packs]
    return {**level, "conditions": conditions}


def test_a_run_with_packs_keys_its_own_record() -> None:
    from lib.prior_level import base_model_key, level_packs, model_key

    assert level_packs(_packed("company", "team")) == ["company", "team"]
    assert level_packs(_level()) == []
    assert model_key(_packed("company", "team")) == "default--company--team"
    assert model_key(_packed()) == "default"  # every row the base's: no pack row, the base record
    assert base_model_key(_packed("team")) == "default"
    citizen = {**_packed("team"), "levels_version": "profile-citizen-2026.11.23"}
    assert model_key(citizen) == "citizen--team"
    assert model_key({**_packed("team"), "levels_version": "team-1"}) is None  # a custom model keeps none


def test_runs_with_and_without_packs_never_share_a_record(tmp_path: Path) -> None:
    """A run with packs neither reads nor replaces the record of a run without them, so "since
    last run" never reads a pack coming or going as gates gained or lost."""
    from lib.prior_level import record_for

    write_prior_level(tmp_path, _level())
    plain = json.loads((tmp_path / PRIOR_LEVEL_FILE).read_text(encoding="utf-8"))
    assert record_for(plain, _packed("team")) is None  # the first run with the pack: no earlier record
    write_prior_level(tmp_path, _packed("team"))
    both = json.loads((tmp_path / PRIOR_LEVEL_FILE).read_text(encoding="utf-8"))
    assert {k: v for k, v in both.items() if k != "models"} == plain  # the default record kept as it was
    assert set(both["models"]) == {"default--team"}
    default_view = record_for(both, _level())
    assert default_view is not None and default_view["tags"] == plain["tags"]
    assert record_for(both, _packed("team")) is not None
    assert record_for(both, _packed("company")) is None  # other packs, another record
