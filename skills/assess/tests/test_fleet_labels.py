"""Tests for the optional fleet labels step, lib/fleet_labels.py and its CLI (issue #572).

The model choice is the one unverifiable step; everything around it is pinned
here: the closed label set, the labels.jsonl schema and validation, the join to
repos.jsonl, and that labels never touch repos.jsonl or any maturity model score.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
import pytest

from lib import fleet_labels as fl
from lib.fleet import REPOS_FILE, FleetRow, dumps_rows
from lib.levels import Group, dumps_result, evaluate, load_levels

LIB = Path(__file__).resolve().parents[1] / "scripts" / "lib"
SKILLS = Path(__file__).resolve().parents[2]
LABELS_SCRIPT = SKILLS / "fleet" / "scripts" / "fleet_labels.py"
LEVELS_FIXTURES = Path(__file__).parent / "fixtures" / "levels"


def _load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fleet_labels_cli", LABELS_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


def _row(repo: str, scans: dict[str, Any] | None = None, *, failed: bool = False) -> FleetRow:
    return {
        "fleet_row_version": 2,
        "repo": repo,
        "group": repo.rsplit("/", 1)[0],
        "status": "failed" if failed else "ok",
        "reason": "missing_checkout" if failed else None,
        "signals_schema_version": None if failed else 1,
        "scans": None if failed else (scans or {}),
        "active": None if failed else True,
    }


ROWS: list[FleetRow] = [
    _row("grpa/svc1", {
        "agent_environment": {
            "available": True,
            "pinning": {"all_locked": True, "manifests": [
                {"ecosystem": "python", "locked": True, "manifest": "pyproject.toml"},
                {"ecosystem": "node", "locked": False, "manifest": "web/package.json"},
            ]},
            "notes": "x" * 200,
        },
        "archetype": {"available": False, "reason": "not_registered"},
        "ai_usage": {"available": True, "total_commits": 40, "window": "6mo", "ratio": None},
        "deep": {"a": {"b": {"c": {"d": 1}}}},
        "tags": {"list": ["a", "b"], "long": list(range(20)), "empty": []},
    }),
    _row("grpa/svc2"),
    _row("grpb/lib1"),
    _row("grpb/gone", failed=True),
]
IDS = sorted(r["repo"] for r in ROWS)


@pytest.fixture
def out(tmp_path: Path) -> Path:
    d = tmp_path / "fleet-out"
    d.mkdir()
    (d / REPOS_FILE).write_text(dumps_rows(sorted(ROWS, key=lambda r: r["repo"])), encoding="utf-8")
    return d


# --- the closed set -----------------------------------------------------------


def test_label_set_is_the_confirmed_closed_set():
    assert fl.LABELS == ("service", "library", "infrastructure", "mirror", "docs", "other")
    assert set(fl.LABEL_DEFINITIONS) == set(fl.LABELS)


@pytest.mark.parametrize("raw, want", [
    ("service", "service"), (" Library ", "library"), ("DOCS", "docs"), ("other", "other"),
    ("application", None), ("", None), (None, None), (3, None), (["service"], None),
])
def test_normalise_label(raw: Any, want: str | None):
    assert fl.normalise_label(raw) == want


@pytest.mark.parametrize("raw, want", [
    (0, 0.0), (1, 1.0), (0.876, 0.88), (True, None), (False, None), (-0.1, None),
    (1.01, None), (float("nan"), None), (float("inf"), None), ("0.9", None), (None, None),
])
def test_normalise_confidence(raw: Any, want: float | None):
    assert fl.normalise_confidence(raw) == want


# --- model input --------------------------------------------------------------


def test_digest_keeps_identity_and_short_facts_only():
    d = fl.digest(ROWS[0])
    assert d["path"] == "grpa/svc1" and d["group"] == "grpa" and d["status"] == "ok"
    facts = d["facts"]
    assert "archetype" not in facts  # unavailable block dropped
    env = facts["agent_environment"]
    assert env["pinning.all_locked"] is True
    assert env["pinning.manifests#len"] == 2
    assert env["pinning.manifests#kinds"] == ["node", "python"]
    assert "notes" not in env  # long free text dropped
    assert "available" not in env
    assert facts["ai_usage"] == {"total_commits": 40, "window": "6mo"}  # None leaves dropped
    assert facts["deep"] == {"a.b.c#keys": 1}
    assert facts["tags"] == {"list": ["a", "b"], "long#len": 20}
    failed = fl.digest(ROWS[3])
    assert failed == {"path": "grpb/gone", "group": "grpb", "status": "failed", "facts": {}}


def test_batches_are_sorted_and_cut():
    parts = fl.batches(ROWS, 3)
    assert [[r["repo"] for r in p] for p in parts] == [IDS[:3], IDS[3:]]
    with pytest.raises(ValueError):
        fl.batches(ROWS, 0)


def test_prompt_lists_every_label_and_one_digest_line_per_row():
    text = fl.build_prompt(fl.batches(ROWS, 10)[0])
    for label in fl.LABELS:
        assert f"- {label}: " in text
    digests = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
    assert [d["path"] for d in digests] == IDS
    assert fl.build_prompt(ROWS) == fl.build_prompt(ROWS)  # deterministic


# --- model output -------------------------------------------------------------


def test_parse_answers_tolerates_fences_and_prose():
    reply = (
        "Here are the labels:\n```json\n"
        '{"path": "grpa/svc1", "label": "service", "confidence": 0.9},\n'
        "{not json}\n"
        '["not", "an", "object"]\n'
        '{"path": "grpb/lib1", "label": "library", "confidence": 0.8}\n'
        "```\nDone."
    )
    assert [a["path"] for a in fl.parse_answers(reply)] == ["grpa/svc1", "grpb/lib1"]


def test_parse_answers_reads_a_json_array_or_single_object():
    assert len(fl.parse_answers('[{"path": "a"}, 3, {"path": "b"}]')) == 2
    assert fl.parse_answers('{"path": "a"}') == [{"path": "a"}]
    assert fl.parse_answers("") == []


def test_parse_answers_reads_a_one_line_array_amid_other_batches():
    # Batch replies concatenated into one answers file: one batch answered as an array.
    text = '[{"path": "a", "label": "docs"}, 3]\n{"path": "b", "label": "docs"}\n'
    assert [a["path"] for a in fl.parse_answers(text)] == ["a", "b"]


def test_build_label_rows_one_row_per_repo_and_coercions():
    answers = [
        {"path": "grpa/svc1", "label": "Service", "confidence": 0.93},
        {"path": "grpa/svc1", "label": "library", "confidence": 1.0},  # duplicate: first wins
        {"path": "grpb/lib1", "label": "sdk", "confidence": 0.7},  # outside the set
        {"path": "grpb/gone", "label": "mirror", "confidence": "high"},  # bad confidence
        {"path": "elsewhere/x", "label": "docs", "confidence": 0.5},  # unknown path
        {"label": "docs"},  # no path
    ]
    rows, report = fl.build_label_rows(ROWS, answers, "claude-haiku-test")
    assert rows == [
        {"path": "grpa/svc1", "label": "service", "confidence": 0.93, "model": "claude-haiku-test"},
        {"path": "grpa/svc2", "label": "other", "confidence": 0.0, "model": "claude-haiku-test"},
        {"path": "grpb/gone", "label": "mirror", "confidence": 0.0, "model": "claude-haiku-test"},
        {"path": "grpb/lib1", "label": "other", "confidence": 0.7, "model": "claude-haiku-test"},
    ]
    assert report == {
        "labelled": 3, "missing": ["grpa/svc2"], "unknown": ["elsewhere/x"],
        "duplicates": ["grpa/svc1"], "coerced": ["grpb/lib1"],
    }
    with pytest.raises(ValueError):
        fl.build_label_rows(ROWS, [], "  ")


def test_model_comes_from_the_caller_not_the_answer():
    rows, _ = fl.build_label_rows(ROWS, [{"path": "grpa/svc1", "label": "service",
                                          "confidence": 1, "model": "gpt-x"}], "haiku")
    assert {r["model"] for r in rows} == {"haiku"}


# --- schema, writing and validation ---------------------------------------------


def test_checked_in_schema_matches_the_generator():
    assert fl.SCHEMA_PATH.read_text(encoding="utf-8") == fl.render_schema()
    jsonschema.Draft202012Validator.check_schema(fl.build_schema())


def test_written_rows_validate_against_schema_and_check(out: Path):
    rows, _ = fl.build_label_rows(ROWS, [{"path": p, "label": "service", "confidence": 0.5}
                                         for p in IDS], "haiku")
    path = fl.write_labels(out, reversed(rows))
    text = path.read_text(encoding="utf-8")
    validator = jsonschema.Draft202012Validator(fl.build_schema())
    lines = text.splitlines()
    assert [json.loads(line)["path"] for line in lines] == IDS
    for line in lines:
        validator.validate(json.loads(line))
    assert fl.check_labels(text, IDS) == []
    assert fl.write_labels(out, rows).read_text(encoding="utf-8") == text  # byte-identical
    assert not list(out.glob(".labels.jsonl.*"))


def test_schema_rejects_what_check_rejects():
    validator = jsonschema.Draft202012Validator(fl.build_schema())
    good = {"path": "a/b", "label": "docs", "confidence": 0.5, "model": "m"}
    assert not list(validator.iter_errors(good))
    for bad in ({**good, "label": "sdk"}, {**good, "confidence": 1.5}, {**good, "model": ""},
                {**good, "extra": 1}, {k: v for k, v in good.items() if k != "model"},
                {**good, "path": ""}):
        assert list(validator.iter_errors(bad))
        assert fl.check_labels(json.dumps(bad), ["a/b"])
    empty = fl.check_labels(json.dumps({**good, "path": ""}), [""])
    assert any("path is not a non-empty string" in p for p in empty)


def test_check_labels_names_every_problem():
    good = {"path": "a/x", "label": "docs", "confidence": 0.5, "model": "m"}
    text = "\n".join([
        json.dumps({**good, "path": "b/y"}),
        json.dumps(good),
        json.dumps(good),
        "not json",
        json.dumps([1]),
        json.dumps({**good, "path": "z/z", "label": "sdk", "confidence": True, "model": " "}),
        json.dumps({**good, "path": "a/w", "confidence": 1}),  # an int: valid, as in the schema
    ])
    problems = fl.check_labels(text, ["a/x", "b/y", "c/missing"])
    joined = "\n".join(problems)
    for needle in ("line 4: not JSON", "line 5: not a JSON object", "label 'sdk'",
                   "confidence True", "model is not",
                   "not sorted", "a/x: more than one row", "z/z: not in", "c/missing: no row"):
        assert needle in joined, needle
    assert "line 7" not in joined


@pytest.mark.parametrize("confidence", [0, 1, 0.5, 1.0])
def test_schema_and_check_agree_on_valid_confidence(confidence: Any):
    row = {"path": "a/b", "label": "docs", "confidence": confidence, "model": "m"}
    assert not list(jsonschema.Draft202012Validator(fl.build_schema()).iter_errors(row))
    assert fl.check_labels(json.dumps(row), ["a/b"]) == []


# --- join: display and filter only ------------------------------------------------


def test_labels_by_path_and_filter_never_change_rows(out: Path):
    labels: list[fl.LabelRow] = [
        {"path": "grpa/svc1", "label": "service", "confidence": 0.9, "model": "m"},
        {"path": "grpb/lib1", "label": "library", "confidence": 0.9, "model": "m"},
        {"path": "grpb/lib1", "label": "docs", "confidence": 0.9, "model": "m"},  # first wins
        {"path": "orphan/x", "label": "docs", "confidence": 0.9, "model": "m"},  # ignored
    ]
    before = json.dumps(ROWS, sort_keys=True)
    assert fl.labels_by_path(ROWS, labels) == {
        "grpa/svc1": "service", "grpa/svc2": None, "grpb/lib1": "library", "grpb/gone": None,
    }
    assert [r["repo"] for r in fl.filter_rows(ROWS, labels, {"service", "library"})] == [
        "grpa/svc1", "grpb/lib1",
    ]
    assert fl.filter_rows(ROWS, labels, {"other"}) == []  # unlabelled is not "other"
    assert json.dumps(ROWS, sort_keys=True) == before
    assert all("label" not in r for r in ROWS)


def test_read_labels_skips_invalid_rows(tmp_path: Path):
    good = {"path": "a/x", "label": "docs", "confidence": 0.5, "model": "m"}
    path = tmp_path / fl.LABELS_FILE
    path.write_text("\n".join([json.dumps(good), "nope", json.dumps({**good, "label": "sdk"})]),
                    encoding="utf-8")
    assert fl.read_labels(path) == [good]


def test_read_repos_errors(tmp_path: Path):
    with pytest.raises(fl.LabelsError, match="cannot read"):
        fl.read_repos(tmp_path / "missing.jsonl")
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{oops\n", encoding="utf-8")
    with pytest.raises(fl.LabelsError, match="not JSON"):
        fl.read_repos(bad)
    bad.write_text('\n{"status": "ok"}\n', encoding="utf-8")
    with pytest.raises(fl.LabelsError, match="no repo id"):
        fl.read_repos(bad)


# --- never scored ---------------------------------------------------------------


def _tree_digest(d: Path) -> dict[str, str]:
    return {
        str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(d.rglob("*")) if p.is_file() and p.name != fl.LABELS_FILE
    }


def test_label_step_leaves_the_run_output_byte_identical(out: Path, tmp_path: Path, capsys):
    before = _tree_digest(out)
    answers = tmp_path / "answers.txt"
    answers.write_text('{"path": "grpa/svc1", "label": "service", "confidence": 0.9}\n',
                       encoding="utf-8")
    assert cli.main(["write", str(out), "--model", "haiku", "--answers", str(answers)]) == 0
    assert (out / fl.LABELS_FILE).is_file()
    assert _tree_digest(out) == before


def test_levels_results_are_the_same_with_and_without_labels(out: Path):
    maturity_model = load_levels(LEVELS_FIXTURES / "levels.toml")
    signals = {p.stem: json.loads(p.read_text(encoding="utf-8"))
               for p in sorted((LEVELS_FIXTURES / "repos").glob("*.json"))}
    settings = json.loads((LEVELS_FIXTURES / "settings.json").read_text(encoding="utf-8"))
    group: Group = {"name": "g", "settings": settings, "repos": signals}
    before = dumps_result(evaluate(maturity_model, group))
    rows = [_row(f"g/{name}", data) for name, data in signals.items()]
    labels, _ = fl.build_label_rows(rows, [{"path": r["repo"], "label": "docs", "confidence": 1}
                                           for r in rows], "haiku")
    fl.write_labels(out, labels)
    fl.labels_by_path(rows, labels)
    assert dumps_result(evaluate(maturity_model, group)) == before
    assert "label" not in before


def _imports(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"^\s*(?:from|import)\s+([\w.]+)", text, re.MULTILINE))


def test_no_gathering_or_scoring_module_imports_labels():
    allowed = {LIB / "fleet_labels.py", LABELS_SCRIPT}
    sources = [*LIB.glob("*.py"), *(SKILLS / "fleet" / "scripts").glob("*.py"),
               *(SKILLS / "assess" / "scripts").glob("*.py")]
    offenders = [p.name for p in sources
                 if p not in allowed and any("fleet_labels" in m for m in _imports(p))]
    assert offenders == []


def test_labels_module_has_no_model_or_network_dependency():
    banned = {"anthropic", "openai", "requests", "httpx", "urllib", "http", "socket", "subprocess"}
    for path in (LIB / "fleet_labels.py", LABELS_SCRIPT):
        roots = {m.split(".")[0] for m in _imports(path)}
        assert roots.isdisjoint(banned), (path.name, roots & banned)


# --- CLI ----------------------------------------------------------------------------


def test_cli_prompt_batches(out: Path, capsys):
    assert cli.main(["prompt", str(out), "--batch-size", "3", "--batch", "1"]) == 0
    captured = capsys.readouterr()
    assert "batch 1 of 2 (1 repositories)" in captured.err
    assert '"path":"grpb/lib1"' in captured.out and '"path":"grpa/svc1"' not in captured.out
    assert cli.main(["prompt", str(out), "--batch-size", "3", "--count"]) == 0
    assert capsys.readouterr().out == "2\n"
    assert cli.main(["prompt", str(out), "--batch", "5"]) == 2
    assert cli.main(["prompt", str(out), "--batch-size", "0"]) == 2
    assert "out of range" in capsys.readouterr().err


def test_cli_write_then_check(out: Path, monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(
        '```\n{"path": "grpa/svc1", "label": "service", "confidence": 0.9}\n'
        '{"path": "grpb/lib1", "label": "sdk", "confidence": 0.4}\n```\n'))
    assert cli.main(["write", str(out), "--model", "claude-haiku-test"]) == 0
    err = capsys.readouterr().err
    assert "4 repositories, 2 answered" in err
    assert "warning: 2 of 4 repositories had no answer" in err
    assert "missing (2): grpa/svc2, grpb/gone" in err and "coerced (1): grpb/lib1" in err
    assert cli.main(["check", str(out)]) == 0
    with (out / fl.LABELS_FILE).open("a", encoding="utf-8") as handle:
        handle.write('{"path": "zz/zz", "label": "x", "confidence": 2, "model": ""}\n')
    assert cli.main(["check", str(out)]) == 1
    assert "invalid" in capsys.readouterr().err


def test_cli_usage_errors(out: Path, tmp_path: Path, capsys):
    assert cli.main(["write", str(out), "--model", " ", "--answers", "x"]) == 2
    assert cli.main(["write", str(out), "--model", "m", "--answers", str(tmp_path / "nope")]) == 2
    assert cli.main(["check", str(out)]) == 2  # no labels.jsonl yet
    assert cli.main(["prompt", str(tmp_path)]) == 2  # no repos.jsonl
    assert "error:" in capsys.readouterr().err
