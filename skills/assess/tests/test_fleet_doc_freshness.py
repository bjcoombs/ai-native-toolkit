"""Cross-repo doc freshness for a docs repository's declared ``[[describes]]`` mappings (#664)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import jsonschema
import pytest

from lib import fleet_doc_freshness as fdf
from lib.assess_config import load_describes
from lib.doc_staleness import analyze_doc_staleness
from lib.fleet_doc_freshness import Checkout, freshness_rows, measure
from lib.fleet_history import persist
from lib.fleet_schema import FRESHNESS_SCHEMA_PATH, build_freshness_schema, render_freshness
from test_fleet_cli import fleet_cli

DOC_EDIT = 1_700_000_000
DAY = 86_400

DESCRIBES = """\
[[describes]]
path = "services/billing.md"
repo = "grp/billing"
scope = ["api/"]
"""


def _git(repo: Path, *args: str, at: int | None = None, committed: int | None = None,
         author: str = "Dev <dev@example.com>") -> None:
    env = {**os.environ}
    name, _, email = author.partition(" <")
    env.update(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email.rstrip(">"),
               GIT_COMMITTER_NAME="ci", GIT_COMMITTER_EMAIL="ci@example.com")
    if at is not None:
        env["GIT_AUTHOR_DATE"] = f"@{at} +0000"
        env["GIT_COMMITTER_DATE"] = f"@{committed or at} +0000"
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _commit(repo: Path, files: dict[str, str], msg: str, at: int, **kw: object) -> None:
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg, at=at, **kw)  # type: ignore[arg-type]


def _checkout(root: Path, slug: str) -> Path:
    path = root / slug / f"{slug.rsplit('/', 1)[-1]}-main"
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    return path


def _fleet(tmp_path: Path, config: str = DESCRIBES) -> tuple[Path, Path, Path]:
    """root, docs checkout (doc last edited at DOC_EDIT), billing checkout (api/ before it)."""
    root = tmp_path / "froot"
    docs = _checkout(root, "grp/docs")
    _commit(docs, {"services/billing.md": "# Billing\n", "README.md": "x\n"}, "docs: add", DOC_EDIT - 50 * DAY)
    _commit(docs, {"services/billing.md": "# Billing\nv2\n"}, "docs: billing", DOC_EDIT)
    _commit(docs, {".assess/config.toml": config}, "chore: describes", DOC_EDIT + DAY)
    svc = _checkout(root, "grp/billing")
    files = {f"src/f{i}.py": "x\n" for i in range(30)}
    _commit(svc, {**files, "api/routes.py": "r0\n", "api/package.json": "{}\n"}, "init", DOC_EDIT - 90 * DAY)
    return root, docs, svc


def _api_commits(svc: Path, n: int, start: int = 1, **kw: object) -> None:
    for i in range(start, start + n):
        _commit(svc, {"api/routes.py": f"r{i}\n"}, f"feat: api change {i}", DOC_EDIT + i * DAY, **kw)


def _targets(root: Path, *slugs: str) -> list[Checkout]:
    return [Checkout(s, root / s / f"{s.rsplit('/', 1)[-1]}-main") for s in slugs]


def _only(root: Path, *, full: bool = True, slugs: tuple[str, ...] = ("grp/docs", "grp/billing"),
          salt: str | None = None) -> dict[str, object]:
    result = freshness_rows(_targets(root, *slugs), full=full, hash_salt=salt)
    assert len(result.rows) == 1, result
    return dict(result.rows[0])


def test_scope_counts_only_commits_to_the_scope_after_the_doc_edit(tmp_path):
    root, _docs, svc = _fleet(tmp_path)
    _api_commits(svc, 5)
    for i in range(20):  # outside api/: never counted
        _commit(svc, {f"src/f{i}.py": f"y{i}\n"}, f"refactor: src {i}", DOC_EDIT + (10 + i) * DAY)

    row = _only(root)

    assert row["status"] == fdf.BEHIND
    assert row["commits_since_doc_change"] == 5
    assert row["method"] == "cross-repo-explicit"
    assert row["confidence"] == "high"
    assert row["doc_last_change"] == "2023-11-14"
    commits = row["commits"]
    assert isinstance(commits, list) and len(commits) == fdf.SUBJECTS_CAP
    assert [c["subject"] for c in commits] == [f"feat: api change {i}" for i in (5, 4, 3, 2, 1)]


def test_noise_bots_and_lockfile_only_commits_do_not_count(tmp_path):
    root, _docs, svc = _fleet(tmp_path)
    for i in range(5):
        _commit(svc, {"api/routes.py": f"bot{i}\n"}, f"chore(deps): bump {i}", DOC_EDIT + (i + 1) * DAY,
                author="dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>")
    for i in range(3):
        _commit(svc, {"api/routes.py": f"ren{i}\n"}, f"Update dep {i}", DOC_EDIT + (i + 10) * DAY,
                author="Renovate Bot <bot@renovateapp.com>")
    for i in range(2):  # a human dependency bump touching only the manifest
        _commit(svc, {"api/package.json": f'{{"v": {i}}}\n'}, f"bump {i}", DOC_EDIT + (i + 20) * DAY)

    row = _only(root)

    assert row["status"] == fdf.CURRENT
    assert row["commits_since_doc_change"] == 0


def test_bulk_mechanical_commit_uses_git_churn_rule(tmp_path):
    root, _docs, svc = _fleet(tmp_path)
    sweep = {f"src/f{i}.py": "formatted\n" for i in range(30)}
    _commit(svc, {**sweep, "api/routes.py": "formatted\n"}, "style: format", DOC_EDIT + DAY)

    assert _only(root)["status"] == fdf.CURRENT


def test_author_time_not_merge_time(tmp_path):
    root, _docs, svc = _fleet(tmp_path)
    # Authored before the doc edit, committed (merged) after it: does not count.
    _commit(svc, {"api/routes.py": "old\n"}, "feat: old work", DOC_EDIT - DAY, committed=DOC_EDIT + 5 * DAY)

    assert _only(root)["commits_since_doc_change"] == 0


def test_threshold_two_commits_is_minor_drift_not_a_finding(tmp_path):
    root, _docs, svc = _fleet(tmp_path)
    _api_commits(svc, 2)

    row = _only(root)

    assert row["status"] == fdf.MINOR_DRIFT
    assert row["behind_threshold"] == 3


def test_rot_renamed_scope_is_broken_not_fresh(tmp_path):
    root, _docs, svc = _fleet(tmp_path)
    _git(svc, "mv", "api", "http")
    _git(svc, "commit", "-q", "-m", "move api", at=DOC_EDIT + DAY)

    row = _only(root)

    assert row["status"] == fdf.BROKEN
    assert row["reason"] == fdf.SCOPE_PATH_MISSING
    assert row["commits_since_doc_change"] is None


def test_rot_repo_not_in_fleet_is_unknown_with_reason(tmp_path):
    root, _docs, _svc = _fleet(tmp_path)

    row = _only(root, slugs=("grp/docs",))

    assert row["status"] == fdf.UNKNOWN
    assert row["reason"] == fdf.REPO_NOT_IN_FLEET


def test_rot_missing_doc_path_is_broken(tmp_path):
    root, docs, _svc = _fleet(tmp_path)
    _git(docs, "rm", "-q", "services/billing.md")
    _git(docs, "commit", "-q", "-m", "drop", at=DOC_EDIT + 2 * DAY)

    row = _only(root)

    assert (row["status"], row["reason"]) == (fdf.BROKEN, fdf.DOC_PATH_MISSING)


def test_missing_scope_fails_validation_with_remediation(tmp_path):
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        '[[describes]]\npath = "services/billing.md"\nrepo = "grp/billing"\n')

    cfg = load_describes(tmp_path)

    assert cfg.mappings == []
    assert len(cfg.errors) == 1
    assert "has no scope" in cfg.errors[0]
    assert 'scope = ["api/"]' in cfg.errors[0] and "openapi.yaml" in cfg.errors[0]


@pytest.mark.parametrize("bad", ['"/etc"', '"../x"', '":(glob)**"', "[]", "5", '["api", 3]'])
def test_invalid_scopes_are_rejected(tmp_path, bad):
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        f'[[describes]]\npath = "a.md"\nrepo = "g/r"\nscope = {bad}\n')

    cfg = load_describes(tmp_path)

    assert cfg.mappings == [] and "scope" in cfg.errors[0]


def test_valid_entries_normalise_and_bad_entries_report(tmp_path):
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        '[[describes]]\npath = "./docs/svc/"\nrepo = "g/r/"\nscope = "./api/"\n'
        '[[describes]]\nrepo = "g/r"\nscope = ["."]\n'
        '[[describes]]\npath = "a.md"\nscope = ["."]\n')

    cfg = load_describes(tmp_path)

    assert cfg.mappings == [{"path": "docs/svc", "repo": "g/r", "scope": ["api"]}]
    assert [e.split(" needs ")[1][:4] for e in cfg.errors] == ["path", "repo"]


def test_describes_not_an_array_is_reported(tmp_path):
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text('describes = "x"\n')

    assert load_describes(tmp_path).errors


def test_invalid_mapping_becomes_a_row(tmp_path):
    root, _docs, _svc = _fleet(tmp_path, config='[[describes]]\npath = "services/billing.md"\n'
                                                 'repo = "grp/billing"\n')

    result = freshness_rows(_targets(root, "grp/docs", "grp/billing"), full=False, hash_salt=None)

    assert [r["status"] for r in result.rows] == [fdf.INVALID]
    assert result.errors[0].startswith("grp/docs: ")


def test_whole_repo_scope_is_low_confidence(tmp_path):
    root, _docs, svc = _fleet(tmp_path, config=DESCRIBES.replace('["api/"]', '["."]'))
    _api_commits(svc, 3)

    row = _only(root)

    assert (row["status"], row["confidence"]) == (fdf.BEHIND, "low")


def test_folder_path_uses_its_newest_doc(tmp_path):
    root, docs, svc = _fleet(tmp_path, config=DESCRIBES.replace("services/billing.md", "services"))
    _api_commits(svc, 4)
    _commit(docs, {"services/other.md": "o\n"}, "docs: other", DOC_EDIT + 3 * DAY)

    assert _only(root)["commits_since_doc_change"] == 1


def test_shallow_service_checkout_newer_than_doc_is_unknown(tmp_path):
    root, _docs, svc = _fleet(tmp_path)
    _api_commits(svc, 4)
    shallow = root / "grp" / "shallow" / "shallow-main"
    shallow.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{svc}", str(shallow)],
                   check=True, capture_output=True)
    config = DESCRIBES.replace("grp/billing", "grp/shallow")
    (root / "grp/docs/docs-main/.assess/config.toml").write_text(config)

    row = _only(root, slugs=("grp/docs", "grp/shallow"))

    assert (row["status"], row["reason"]) == (fdf.UNKNOWN, fdf.SERVICE_HISTORY_TRUNCATED)


def test_counts_only_rows_carry_no_text_and_hash_paths(tmp_path):
    root, _docs, svc = _fleet(tmp_path)
    _api_commits(svc, 3)

    row = _only(root, full=False, salt="s")

    assert row["commits"] is None
    assert row["commits_since_doc_change"] == 3
    assert str(row["path"]).startswith("h:")
    assert [str(s)[:2] for s in row["scope"]] == ["h:"]
    assert row["repo"] == "grp/billing"


def test_cost_one_git_log_per_mapping_on_the_service(tmp_path, monkeypatch):
    root, _docs, svc = _fleet(tmp_path, config=DESCRIBES + DESCRIBES.replace("api/", "src/"))
    _api_commits(svc, 3)
    calls: list[list[str]] = []
    real = subprocess.run

    def spy(cmd, *a, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        return real(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    fdf._doc_clock.cache_clear()

    result = freshness_rows(_targets(root, "grp/docs", "grp/billing"), full=False, hash_salt=None)

    assert len(result.rows) == 2
    # History walks only: the content clock's one-commit HEAD read (``log -1``,
    # the anchor its day counts are measured to, #773) walks no history.
    logs = [c for c in calls if "log" in c and "-1" not in c]
    service_logs = [c for c in logs if str(svc) in c]
    assert len(service_logs) == 2  # one per mapping
    assert len(logs) - len(service_logs) <= 1  # the docs repository's content clock


def test_no_describes_writes_nothing(tmp_path):
    root = tmp_path / "froot"
    svc = _checkout(root, "grp/billing")
    _commit(svc, {"a.py": "x\n"}, "init", DOC_EDIT)
    stale = tmp_path / "out"
    stale.mkdir()
    (stale / fdf.FRESHNESS_FILE).write_text("{}\n")

    result = freshness_rows(_targets(root, "grp/billing"), full=False, hash_salt=None)

    assert result.rows == [] and result.errors == []
    assert fdf.write_freshness(stale, result.rows) is None
    assert not (stale / fdf.FRESHNESS_FILE).exists()


def test_standalone_assess_lists_declared_mappings_unmeasured(tmp_path):
    _root, docs, _svc = _fleet(tmp_path)

    block = analyze_doc_staleness(docs)

    assert block["declared_cross_repo"] == [{
        "path": "services/billing.md", "repo": "grp/billing", "scope": ["api"],
        "status": "declared_not_measured", "reason": None}]


def test_standalone_assess_omits_the_block_without_describes(tmp_path):
    root = tmp_path / "r"
    _checkout(tmp_path, "r/r")
    assert "declared_cross_repo" not in analyze_doc_staleness(root / "r" / "r-main")


def test_measure_reports_unreadable_checkout(tmp_path):
    root, docs, _svc = _fleet(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    mapping = load_describes(docs).mappings[0]

    row = measure(Checkout("grp/docs", docs), mapping, Checkout("grp/billing", empty), full=False)

    assert (row["status"], row["reason"]) == (fdf.UNKNOWN, fdf.REPO_CHECKOUT_UNREADABLE)


def test_fleet_cli_writes_doc_freshness_before_delete_after(tmp_path, capsys):
    root, _docs, svc = _fleet(tmp_path)
    _api_commits(svc, 5)
    for checkout in (_docs, svc):  # pushed to a remote, so --delete-after may remove them
        bare = tmp_path / "remotes" / checkout.name
        _git(tmp_path, "init", "-q", "--bare", str(bare))
        _git(checkout, "remote", "add", "origin", str(bare))
        _git(checkout, "push", "-q", "origin", "main")
        _git(checkout, "fetch", "-q", "origin")
    out = tmp_path / "out"

    hist = tmp_path / "hist"
    code = fleet_cli.main([str(root), "--out", str(out), "--scans", "shared_tooling",
                           "--history-dir", str(hist), "--jobs", "1", "--delete-after"])

    assert code == 0
    rows = [json.loads(ln) for ln in (out / fdf.FRESHNESS_FILE).read_text().splitlines()]
    assert [(r["repo"], r["status"], r["commits_since_doc_change"], r["commits"]) for r in rows] == [
        ("grp/billing", fdf.BEHIND, 5, None)]
    assert "doc freshness: 1 declared mappings: 1 behind" in capsys.readouterr().err
    assert not svc.exists() and not _docs.exists()  # deleted after the join read them
    kept = list(hist.glob(f"*/*/{fdf.FRESHNESS_FILE}"))
    assert len(kept) == 1 and kept[0].read_text() == (out / fdf.FRESHNESS_FILE).read_text()


def test_summary_line_orders_statuses():
    row = fdf._row("d", None, fdf.INVALID, fdf.INVALID_MAPPING)
    assert fdf.summary_line([row, {**row, "status": fdf.BEHIND}]) == \
        "2 declared mappings: 1 behind, 1 invalid"


def test_report_discloses_declared_mappings_and_is_silent_without():
    from assess_report import render_declared_cross_repo

    rows = [{"status": "declared_not_measured"}, {"status": "invalid"}]
    text = render_declared_cross_repo({"doc_staleness": {"declared_cross_repo": rows}})

    assert "1 declared, not measured outside fleet mode" in text
    assert "1 rejected" in text
    assert render_declared_cross_repo({"doc_staleness": {}}) == ""


def test_shallow_service_checkout_older_than_doc_is_measured(tmp_path):
    root, _docs, svc = _fleet(tmp_path)
    _commit(svc, {"src/f1.py": "pre\n"}, "pre-doc work", DOC_EDIT - 10 * DAY)
    _api_commits(svc, 3)
    shallow = root / "grp" / "shallow" / "shallow-main"
    shallow.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", "--depth", "4", f"file://{svc}", str(shallow)],
                   check=True, capture_output=True)
    (root / "grp/docs/docs-main/.assess/config.toml").write_text(
        DESCRIBES.replace("grp/billing", "grp/shallow"))

    row = _only(root, slugs=("grp/docs", "grp/shallow"))

    assert (row["status"], row["commits_since_doc_change"]) == (fdf.BEHIND, 3)


def test_shallow_docs_checkout_cannot_vouch_for_current(tmp_path):
    root, docs, svc = _fleet(tmp_path)
    _api_commits(svc, 1)
    shallow = root / "grp" / "sdocs" / "sdocs-main"
    shallow.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{docs}", str(shallow)],
                   check=True, capture_output=True)

    row = _only(root, slugs=("grp/sdocs", "grp/billing"))

    assert (row["status"], row["reason"]) == (fdf.UNKNOWN, fdf.DOC_HISTORY_TRUNCATED)


def test_doc_without_history_is_unknown(tmp_path):
    root, docs, _svc = _fleet(tmp_path, config=DESCRIBES.replace("billing.md", "new.md"))
    (docs / "services" / "new.md").write_text("draft\n")  # on disk, never committed

    row = _only(root)

    assert (row["status"], row["reason"]) == (fdf.UNKNOWN, fdf.DOC_HISTORY_UNKNOWN)


def test_manifest_named_as_scope_counts_its_own_changes(tmp_path):
    root, _docs, svc = _fleet(tmp_path, config=DESCRIBES.replace('["api/"]', '["api/package.json"]'))
    for i in range(3):
        _commit(svc, {"api/package.json": f'{{"v": {i}}}\n'}, f"api: contract {i}", DOC_EDIT + (i + 1) * DAY)

    assert _only(root)["status"] == fdf.BEHIND


def test_counts_only_rules_keep_declared_mapping_fields():
    from lib.assess_config import DeclaredMapping
    from lib.counts_only import counts_only

    declared: list[DeclaredMapping] = [
        {"path": "services/billing.md", "repo": "grp/billing", "scope": ["api"],
         "status": "declared_not_measured", "reason": None},
        {"path": None, "repo": None, "scope": [], "status": "invalid", "reason": "invalid_mapping"},
    ]
    ctx = {"doc_staleness": {"declared_cross_repo": declared}}

    plain = counts_only(ctx)["doc_staleness"]["declared_cross_repo"]
    hashed = counts_only(ctx, hash_paths=True, salt="s")["doc_staleness"]["declared_cross_repo"]

    assert plain == declared
    assert hashed[0]["status"] == "declared_not_measured"
    assert hashed[1]["reason"] == "invalid_mapping"
    assert all(str(v).startswith("h:") for v in (hashed[0]["path"], hashed[0]["repo"], *hashed[0]["scope"]))


def test_freshness_schema_file_matches_the_type():
    assert FRESHNESS_SCHEMA_PATH.read_text(encoding="utf-8") == render_freshness()


def test_every_row_shape_validates_against_the_schema(tmp_path):
    root, _docs, svc = _fleet(tmp_path, config=DESCRIBES + DESCRIBES.replace("api/", "gone/")
                              + '[[describes]]\npath = "README.md"\nrepo = "grp/absent"\nscope = ["."]\n'
                              + '[[describes]]\npath = "README.md"\nrepo = "grp/billing"\n')
    _api_commits(svc, 6)
    schema = build_freshness_schema()
    targets = _targets(root, "grp/docs", "grp/billing")
    seen: set[str] = set()
    for full, salt in ((True, None), (False, None), (False, "s")):
        for row in freshness_rows(targets, full=full, hash_salt=salt).rows:
            jsonschema.validate(json.loads(json.dumps(row)), schema)
            seen.add(row["status"])
    assert seen == {fdf.BEHIND, fdf.BROKEN, fdf.UNKNOWN, fdf.INVALID}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**row, "status": "stale"}, schema)


def _run_files(out: Path, freshness: bool) -> None:
    out.mkdir(exist_ok=True)
    (out / "repos.jsonl").write_text('{"repo": "org/a"}\n')
    (out / "groups.jsonl").write_text("{}\n")
    (out / "run.json").write_text(json.dumps(
        {"fleet_stamp_version": 1, "levels_version": None, "window": "6mo", "counts_only": True,
         "scans": None, "paths_hashed": False}))
    path = out / fdf.FRESHNESS_FILE
    if freshness:
        path.write_text('{"status": "behind"}\n')
    else:
        path.unlink(missing_ok=True)


def test_history_keeps_doc_freshness_and_drops_a_stale_same_day_copy(tmp_path):
    stamp = json.loads(json.dumps({"fleet_stamp_version": 1, "levels_version": None, "window": "6mo",
                                   "counts_only": True, "scans": None, "paths_hashed": False}))
    out, entry = tmp_path / "out", tmp_path / "hist" / "org" / "2026-11-20"
    _run_files(out, freshness=True)
    persist(out, entry, stamp, frozenset({"org/a"}))
    assert (entry / fdf.FRESHNESS_FILE).read_text() == '{"status": "behind"}\n'

    _run_files(out, freshness=False)  # a same-day rerun with no mappings
    persist(out, entry, stamp, frozenset({"org/a"}))
    assert not (entry / fdf.FRESHNESS_FILE).exists()
    assert (entry / "run.json").exists()


def test_history_without_doc_freshness_is_unchanged(tmp_path):
    stamp = json.loads(json.dumps({"fleet_stamp_version": 1, "levels_version": None, "window": "6mo",
                                   "counts_only": True, "scans": None, "paths_hashed": False}))
    out, entry = tmp_path / "out", tmp_path / "hist" / "org" / "2026-11-20"
    _run_files(out, freshness=False)
    persist(out, entry, stamp, frozenset({"org/a"}))
    assert sorted(p.name for p in entry.iterdir()) == ["groups.jsonl", "repos.jsonl", "run.json"]


def test_hashing_keeps_paths_with_spaces(tmp_path):
    config = DESCRIBES.replace("services/billing.md", "services/Billing Notes.md").replace(
        '["api/"]', '["api/", "api v2"]')
    root, docs, svc = _fleet(tmp_path, config=config)
    _commit(docs, {"services/Billing Notes.md": "# B\n"}, "docs: spaced", DOC_EDIT)
    _commit(svc, {"api v2/x.py": "x\n"}, "init v2", DOC_EDIT - DAY)

    row = _only(root, full=False, salt="s")

    assert str(row["path"]).startswith("h:")
    assert len(row["scope"]) == 2 and all(str(s).startswith("h:") for s in row["scope"])
