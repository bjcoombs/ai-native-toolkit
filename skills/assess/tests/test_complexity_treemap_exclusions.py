"""complexity-treemap.py's exclusions: the .assess self-exclusion, --exclude and
config.toml merging, generated-file sniffing and generated test reports.

Split from test_complexity_treemap.py (#720); the stubbed loader lives there.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_complexity_treemap import _load_treemap


@pytest.fixture(scope="module")
def treemap():
    return _load_treemap()


def test_assess_dir_is_self_excluded_by_default(treemap):
    """A prior run's run-context.json must not be scored on the next run -
    the script's own output directory is in EXCLUDE_DIRS. Otherwise re-runs
    pollute the heatmap with their own past output (#50); so does mutants/."""
    assert {".assess", "mutants"} <= treemap.EXCLUDE_DIRS


def test_is_user_excluded_matches_dir_name(treemap):
    """A plain dir name in the user excludes filters every file under it,
    at any depth."""
    extra_dirs = {"regulatory-raw"}
    assert treemap._is_user_excluded(
        Path("regulatory-raw/2024-Q1/data.csv"), extra_dirs, []
    ) is True
    assert treemap._is_user_excluded(
        Path("src/data/sub/regulatory-raw/file.txt"), extra_dirs, []
    ) is True
    # A different directory must not be filtered.
    assert treemap._is_user_excluded(
        Path("src/data/file.txt"), extra_dirs, []
    ) is False


def test_is_user_excluded_matches_glob_pattern(treemap):
    """A glob pattern matches by basename, not by full path."""
    extra_patterns = ["*.csv", "seed-*.json"]
    assert treemap._is_user_excluded(
        Path("data/reference.csv"), set(), extra_patterns
    ) is True
    assert treemap._is_user_excluded(
        Path("fixtures/seed-orders.json"), set(), extra_patterns
    ) is True
    # A glob that doesn't match the basename must not filter.
    assert treemap._is_user_excluded(
        Path("src/main.py"), set(), extra_patterns
    ) is False
    # No globs at all => no excludes.
    assert treemap._is_user_excluded(Path("anything.txt"), set(), []) is False


def test_is_user_excluded_dir_and_pattern_combine(treemap):
    """Dir excludes and pattern excludes are independent - either match
    is enough to exclude. Mirrors how the built-in defaults already work."""
    extra_dirs = {"vetted-context"}
    extra_patterns = ["*.parquet"]
    # Dir hit
    assert treemap._is_user_excluded(
        Path("vetted-context/note.md"), extra_dirs, extra_patterns
    ) is True
    # Pattern hit
    assert treemap._is_user_excluded(
        Path("data/silver/events.parquet"), extra_dirs, extra_patterns
    ) is True


def test_cli_exclude_classifies_glob_vs_dir(treemap, monkeypatch, tmp_path):
    """The CLI's `--exclude X` argument routes globby patterns to
    extra_patterns and plain strings to extra_dirs, transparently to the
    caller. Verified by capturing what collect() receives."""
    captured = {}

    def fake_collect(*args, **kwargs):
        captured["extra_dirs"] = kwargs.get("extra_exclude_dirs")
        captured["extra_patterns"] = kwargs.get("extra_exclude_patterns")
        # Return an empty result so main bails out early but cleanly.
        return [], "complexity", None, None, {}

    monkeypatch.setattr(treemap, "collect", fake_collect)
    monkeypatch.setattr(
        sys, "argv",
        ["complexity-treemap.py", str(tmp_path),
         "--exclude", "regulatory-raw",
         "--exclude", "*.csv",
         "--exclude", "seed-data",
         "--exclude", "data-*.json"],
    )
    rc = treemap.main()
    assert rc == 1  # "no scoreable files" - expected with empty collect()
    assert captured["extra_dirs"] == {"regulatory-raw", "seed-data"}
    assert sorted(captured["extra_patterns"]) == ["*.csv", "data-*.json"]


def test_argparse_help_builds_on_current_python(treemap, monkeypatch, capsys):
    """Regression for the Python 3.14 crash: argparse now eagerly validates help
    strings and rejects a bare ``%`` (it must be escaped ``%%``). Building the
    parser via ``--help`` must raise SystemExit (help printed), never ValueError
    ('badly formed help string'). Runs under whatever Python the suite is on, so
    a 3.14 CI job catches a reintroduced bare ``%`` in any help text."""
    monkeypatch.setattr(sys, "argv", ["complexity-treemap.py", "--help"])
    with pytest.raises(SystemExit) as exc:
        treemap.main()
    assert exc.value.code == 0
    assert "--test-pressure" in capsys.readouterr().out


def test_cli_exclude_merges_with_config_toml(treemap, monkeypatch, tmp_path):
    """`.assess/config.toml` and `--exclude` both layer onto the defaults;
    neither replaces the other. Config-supplied dirs join CLI dirs, and
    glob patterns merge across both sources."""
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        'exclude_dirs = ["vetted-context", "regulatory-raw"]\n'
        'exclude_patterns = ["*.parquet"]\n',
        encoding="utf-8",
    )
    captured = {}

    def fake_collect(*args, **kwargs):
        captured["extra_dirs"] = kwargs.get("extra_exclude_dirs")
        captured["extra_patterns"] = kwargs.get("extra_exclude_patterns")
        return [], "complexity", None, None, {}

    monkeypatch.setattr(treemap, "collect", fake_collect)
    monkeypatch.setattr(
        sys, "argv",
        ["complexity-treemap.py", str(tmp_path),
         "--exclude", "seed-data",
         "--exclude", "*.csv"],
    )
    rc = treemap.main()
    assert rc == 1  # no scoreable files
    assert captured["extra_dirs"] == {
        "vetted-context", "regulatory-raw", "seed-data",
    }
    assert sorted(captured["extra_patterns"]) == ["*.csv", "*.parquet"]


def test_config_loader_missing_file_is_empty(tmp_path):
    """A repo with no .assess/config.toml degrades silently - no warning,
    no error, just an empty config (the common case)."""
    from lib.assess_config import load_excludes

    dirs, pats = load_excludes(tmp_path)
    assert dirs == set()
    assert pats == []


def test_config_loader_malformed_toml_returns_empty(tmp_path, capsys):
    """A broken TOML file must never block the assessment - the loader
    returns empty excludes and prints a one-line warning."""
    from lib.assess_config import load_excludes

    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        "this is not valid = = toml\n", encoding="utf-8",
    )
    dirs, pats = load_excludes(tmp_path)
    assert dirs == set()
    assert pats == []
    captured = capsys.readouterr()
    assert "could not read" in captured.err


def test_config_loader_drops_non_string_entries(tmp_path):
    """A schema violation in one entry doesn't poison the rest - e.g.
    `exclude_dirs = ["regulatory-raw", 42]` keeps the string and drops
    the integer."""
    from lib.assess_config import load_excludes

    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        'exclude_dirs = ["regulatory-raw", 42, "vetted-context"]\n'
        'exclude_patterns = ["*.csv", true]\n',
        encoding="utf-8",
    )
    dirs, pats = load_excludes(tmp_path)
    assert dirs == {"regulatory-raw", "vetted-context"}
    assert pats == ["*.csv"]


def test_config_loader_no_legacy_section_needed(tmp_path):
    """The schema is top-level - no `[treemap]` or `[exclude]` wrapper.
    The file is already namespaced by living under `.assess/config.toml`."""
    from lib.assess_config import load_excludes

    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        'exclude_dirs = ["regulatory-raw"]\n'
        'exclude_patterns = ["*.csv"]\n',
        encoding="utf-8",
    )
    dirs, pats = load_excludes(tmp_path)
    assert dirs == {"regulatory-raw"}
    assert pats == ["*.csv"]


def test_config_loader_scalar_string_degrades_to_empty(tmp_path):
    """`exclude_dirs = "regulatory-raw"` (string, not list) used to iterate
    character-by-character, silently producing single-char "dir names"
    that match unexpectedly. The loader now rejects non-list values."""
    from lib.assess_config import load_excludes

    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        'exclude_dirs = "regulatory-raw"\n',
        encoding="utf-8",
    )
    dirs, pats = load_excludes(tmp_path)
    assert dirs == set()
    assert pats == []


def test_config_loader_scalar_int_does_not_raise(tmp_path):
    """`exclude_dirs = 5` is valid TOML but the wrong type. It used to
    raise `TypeError` (int not iterable), propagate through `load_excludes`,
    and abort the whole assessment - the opposite of "degrade silently"."""
    from lib.assess_config import load_excludes

    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        'exclude_dirs = 5\nexclude_patterns = true\n',
        encoding="utf-8",
    )
    # The test passes if this call returns without raising.
    dirs, pats = load_excludes(tmp_path)
    assert dirs == set()
    assert pats == []


# --- generated-file exclusion (header sniff, long lines, filename globs) -----

@pytest.mark.parametrize("name", [
    "types.generated.ts", "schema.generated.sql", "client.gen.ts",
    "database.types.ts",
])
def test_generated_header_free_filename_globs_are_filtered(treemap, name):
    assert treemap._is_build_artifact(Path("src") / name) is True


def _fake_scorers(treemap, monkeypatch, paths):
    monkeypatch.setattr(
        treemap, "lizard_scores",
        lambda root, **kw: {p: (10, 3.0, [3.0]) for p in paths},
    )
    monkeypatch.setattr(treemap, "scc_scores", lambda root, **kw: {})


def test_collect_drops_generated_header_and_long_line_files(
        treemap, tmp_path, monkeypatch):
    root = tmp_path
    (root / "db").mkdir()
    (root / "src").mkdir()
    schema = root / "db" / "schema.sql"
    schema.write_text("-- GENERATED FILE - DO NOT EDIT\nCREATE TABLE t (id int);\n", encoding="utf-8")
    font = root / "src" / "font.ts"
    font.write_text('export const F = "' + "A" * 40000 + '";\n', encoding="utf-8")
    hand = root / "src" / "hand.py"
    hand.write_text("def f():\n    return 1\n" + "# x\n" * 196
                    + "# do not edit the table above\n", encoding="utf-8")
    _fake_scorers(treemap, monkeypatch, [schema, font, hand])

    excluded: list[dict] = []
    files, *_rest, fn_ccn = treemap.collect(
        root, by="complexity", excluded_generated=excluded)
    assert [f[0] for f in files] == [hand]
    assert set(fn_ccn) == {hand}
    assert excluded == [
        {"path": "db/schema.sql", "reason": "generated-header"},
        {"path": "src/font.ts", "reason": "long-lines"},
    ]


def test_collect_include_artifacts_keeps_generated_header_files(
        treemap, tmp_path, monkeypatch):
    schema = tmp_path / "schema.sql"
    schema.write_text("-- @generated\nCREATE TABLE t (id int);\n", encoding="utf-8")
    _fake_scorers(treemap, monkeypatch, [schema])
    excluded: list[dict] = []
    files, *_ = treemap.collect(tmp_path, by="complexity",
                                include_artifacts=True,
                                excluded_generated=excluded)
    assert [f[0] for f in files] == [schema]
    assert excluded == []


def test_write_stats_carries_generated_header_exclusions(treemap, tmp_path):
    root = tmp_path
    f = root / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    out = root / "stats.json"
    listed = [{"path": "db/schema.sql", "reason": "generated-header"}]
    treemap.write_stats([(f, 1, 1.0, "lizard")], None, None, root, out,
                        excluded_generated=listed)
    stats = json.loads(out.read_text(encoding="utf-8"))
    assert stats["excluded_generated"] == listed
    assert isinstance(stats["schema_version"], int) and stats["schema_version"] > 1
    treemap.write_stats([(f, 1, 1.0, "lizard")], None, None, root, out)
    assert json.loads(out.read_text(encoding="utf-8"))["excluded_generated"] == []


def test_generated_header_all_excluded_error_names_the_exclusion(
        treemap, tmp_path, monkeypatch, capsys):
    schema = tmp_path / "schema.sql"
    schema.write_text("-- GENERATED FILE - DO NOT EDIT\nCREATE TABLE t (id int);\n", encoding="utf-8")
    _fake_scorers(treemap, monkeypatch, [schema])
    monkeypatch.setattr(sys, "argv", ["complexity-treemap.py", str(tmp_path)])
    assert treemap.main() == 1
    err = capsys.readouterr().err
    assert "no scoreable files found" in err
    assert "1 excluded as generated" in err
    assert "--include-artifacts" in err


def test_write_stats_paths_match_generated_header_list_separator(treemap, tmp_path):
    """Row paths use forward slashes on every host, the same form as
    excluded_generated, so assess_core can intersect the two sets on Windows
    (where str() of a relative path would use backslashes)."""
    (tmp_path / "db").mkdir()
    f = tmp_path / "db" / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    out = tmp_path / "stats.json"
    treemap.write_stats([(f, 1, 1.0, "lizard")], None, None, tmp_path, out)
    paths = [r["path"] for r in json.loads(out.read_text(encoding="utf-8"))["top_large"]]
    assert paths == ["db/a.py"]
    src = (Path(treemap.__file__)).read_text(encoding="utf-8")
    rel_body = src[src.index("def _rel_path(p: Path, root: Path) -> str:"):][:400]
    assert "as_posix()" in rel_body and "str(p" not in rel_body


# --- generated test reports, code/data maxima, scc-only hint -----------------

@pytest.mark.parametrize("rel", [
    "web/tests/html-report/index.html",
    "e2e/playwright-report/index.html",
    "web/accessibility/lighthouse-report.html",
    "web/accessibility/lighthouse-results.json",
    "security/zap-report.html",
    "security/zap-report.json",
    "security/zap_report.html",
    "mcp/test/fixtures/big/lines.jsonl",
    "a/fixtures/lines.jsonl",
])
def test_report_default_excludes_drop_generated_reports(treemap, rel):
    path = Path(rel)
    in_dir = any(part in treemap.EXCLUDE_DIRS for part in path.parts)
    assert in_dir or treemap._is_build_artifact(path)


@pytest.mark.parametrize("rel", [
    "data/events.jsonl",           # .jsonl outside any fixtures/ directory
    "fixtures/lines.jsonl",        # bare top-level fixtures/ stays scored
    "fixtures/taxonomy/concepts.json",
    "mcp/test/fixtures/big/case.json",  # only .jsonl leaves nested fixtures/
    "src/report.html",
    "security/zap_report.py",  # the script that runs ZAP, not its output
])
def test_report_default_excludes_keep_hand_kept_files(treemap, rel):
    path = Path(rel)
    assert not any(part in treemap.EXCLUDE_DIRS for part in path.parts)
    assert treemap._is_build_artifact(path) is False


def test_report_default_excludes_bypassed_by_include_artifacts(
        treemap, tmp_path, monkeypatch):
    """The nested-fixture .jsonl rule is a filename default like the globs, so
    --include-artifacts scores it."""
    import subprocess as sp

    nested = tmp_path / "mcp" / "fixtures" / "lines.jsonl"
    nested.parent.mkdir(parents=True)
    nested.write_text('{"a": 1}\n', encoding="utf-8")
    payload = json.dumps([{"Name": "JSONL", "Files": [
        {"Location": str(nested), "Code": 1, "Complexity": 0}]}])
    monkeypatch.setattr(treemap.shutil, "which", lambda _: "/usr/bin/scc")
    monkeypatch.setattr(treemap.subprocess, "run", lambda *a, **k:
                        sp.CompletedProcess(a, 0, stdout=payload))
    assert treemap.scc_scores(tmp_path) == {}
    langs: dict = {}
    assert treemap.scc_scores(tmp_path, include_artifacts=True,
                              languages=langs) == {nested.resolve(): (1, 0.0)}
    assert langs == {nested.resolve(): "JSONL"}
