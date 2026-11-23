"""Tests for the fleet settings file, lib/fleet_config.py and --fleet-config (issue #706)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import assess_signals
from lib import fleet_config as fcfg
import lib.fleet.fleet_history as fh
import lib.fleet.fleet_trends as fleet_trends
from lib import run_setup
from lib import scan_registry as reg
from lib.fleet.runner import FLEET_ROW_VERSION, ScanOptions, signals_command
from lib.fleet.fleet_compare import FleetRun, comparability, compare
from lib.fleet.fleet_groups import roll_up
from lib.fleet.fleet_stamp import FleetStamp, build_stamp, write_stamp
from lib.scan_registry import ScanSpec
from lib.signals import build_document
from test_fleet import make_repo
from test_fleet_cli import fleet_cli
from test_fleet_groups import LEVELS

SENTINEL = "zz-sentinel-inhouse-agent"

# A schema with keys, standing in for the ones the consumer issues add.
SCHEMA: dict[str, fcfg.SectionKeys] = {
    "ai_usage": {"agent_identities": fcfg.string_list,
                 "rules": fcfg.table_list({"pattern": fcfg.string, "field": fcfg.string},
                                          frozenset({"pattern"}))},
    "review": {"components": fcfg.string_list, "enabled": fcfg.boolean},
    "platform": {"sample": fcfg.positive_int, "host": fcfg.string},
}

FULL = f"""\
# comment
[ai_usage]
agent_identities = ["^{SENTINEL}", "^bot"]
rules = [{{ pattern = "^x" }}, {{ pattern = "^y", field = "name" }}]

[review]
components = ["gitlab.example/ai-review"]
enabled = true

[platform]
sample = 25
"""


def _parse(text: str) -> fcfg.FleetConfig:
    return fcfg.parse_fleet_config(text, SCHEMA)


# --- loading and validation ------------------------------------------------


def test_the_known_sections_are_defined_and_empty_allowed():
    assert set(fcfg.SECTION_KEYS) == {"ai_usage", "review", "platform", "identities"}
    assert fcfg.parse_fleet_config("[ai_usage]\n[review]\n[platform]\n[identities]\n") == fcfg.empty_config()
    assert fcfg.parse_fleet_config("") == fcfg.empty_config()


def test_a_valid_file_loads_with_every_section_and_typed_accessors(tmp_path):
    path = tmp_path / "fleet.toml"
    path.write_text(FULL, encoding="utf-8")
    config = fcfg.load_fleet_config(path, SCHEMA)
    assert fcfg.ai_usage_section(config) == {
        "agent_identities": [f"^{SENTINEL}", "^bot"],
        "rules": [{"pattern": "^x"}, {"pattern": "^y", "field": "name"}]}
    assert fcfg.review_section(config) == {"components": ["gitlab.example/ai-review"], "enabled": True}
    assert fcfg.platform_section(config) == {"sample": 25}
    assert fcfg.section_entries(config) == {"ai_usage": 4, "review": 2, "platform": 1, "identities": 0}


def test_a_section_left_out_is_empty():
    config = _parse("[review]\ncomponents = []\n")
    assert config["ai_usage"] == {} and config["platform"] == {}
    assert fcfg.section_entries(config) == {"ai_usage": 0, "review": 0, "platform": 0, "identities": 0}


@pytest.mark.parametrize(("text", "named"), [
    ("[reviews]\n", "reviews: unknown section"),
    ("top = 1\n", "top: unknown section"),
    ("review = 1\n", "review: expected a table"),
    ("[review]\nfoo = 1\n", "review.foo: unknown key"),
    ("[review]\ncomponents = \"one\"\n", "review.components: expected a list of strings"),
    ("[review]\ncomponents = [1]\n", "review.components: expected a list of strings"),
    ("[review]\nenabled = 1\n", "review.enabled: expected true or false"),
    ("[platform]\nsample = 0\n", "platform.sample: expected a whole number"),
    ("[platform]\nsample = true\n", "platform.sample: expected a whole number"),
    ("[platform]\nhost = 3\n", "platform.host: expected a string"),
    ("[ai_usage]\nrules = [1]\n", "ai_usage.rules: expected a list of tables"),
    ("[ai_usage]\nrules = [{ pattern = \"a\", kind = \"b\" }]\n", "ai_usage.rules[0].kind: unknown field"),
    ("[ai_usage]\nrules = [{ field = \"name\" }]\n", "ai_usage.rules[0].pattern: required field missing"),
    ("[ai_usage]\nrules = [{ pattern = 7 }]\n", "ai_usage.rules[0].pattern: expected a string"),
    ("[review\n", "not valid TOML"),
])
def test_validation_errors_name_the_key(text, named):
    with pytest.raises(fcfg.FleetConfigError) as exc:
        _parse(text)
    assert named in str(exc.value)


def test_a_key_a_real_section_does_not_define_is_unknown():
    with pytest.raises(fcfg.FleetConfigError,
                       match=r"ai_usage.agents: unknown key \(known: agent_identities, token_users_as_agents\)"):
        fcfg.parse_fleet_config(f'[ai_usage]\nagents = ["{SENTINEL}"]\n')


def test_ai_usage_keys_validate():
    config = fcfg.parse_fleet_config(
        '[ai_usage]\ntoken_users_as_agents = true\n'
        '[[ai_usage.agent_identities]]\npattern = "^a"\nfield = "name"\n')
    assert fcfg.ai_usage_section(config) == {
        "token_users_as_agents": True, "agent_identities": [{"pattern": "^a", "field": "name"}]}
    assert fcfg.section_entries(config)["ai_usage"] == 2


def test_an_error_message_never_carries_the_value():
    with pytest.raises(fcfg.FleetConfigError) as exc:
        _parse(f'[review]\ncomponents = "{SENTINEL}"\n')
    assert SENTINEL not in str(exc.value)


def test_an_unreadable_file_is_a_config_error(tmp_path):
    with pytest.raises(fcfg.FleetConfigError, match="cannot read"):
        fcfg.load_fleet_config(tmp_path / "absent.toml")
    (tmp_path / "bin.toml").write_bytes(b"\xff\xfe")
    with pytest.raises(fcfg.FleetConfigError, match="cannot read"):
        fcfg.load_fleet_config(tmp_path / "bin.toml")


# --- hash ------------------------------------------------------------------


def test_the_hash_ignores_comments_whitespace_and_key_order_but_not_list_order():
    base = fcfg.config_sha256(_parse(FULL))
    reordered = "[platform]\nsample=25\n[review]\nenabled = true\ncomponents = [\"gitlab.example/ai-review\"]\n" \
                f"[ai_usage]\nrules = [{{ pattern = \"^x\" }}, {{ field = \"name\", pattern = \"^y\" }}]\n" \
                f"agent_identities = [\"^{SENTINEL}\", \"^bot\"]\n"
    assert fcfg.config_sha256(_parse(reordered)) == base
    swapped = FULL.replace(f'["^{SENTINEL}", "^bot"]', f'["^bot", "^{SENTINEL}"]')
    assert fcfg.config_sha256(_parse(swapped)) != base
    assert len(base) == 64


# --- merge -----------------------------------------------------------------


def test_repository_lists_extend_the_fleet_lists_and_keep_fleet_indices():
    fleet = _parse(FULL)
    repo = {"ai_usage": {"agent_identities": ["^bot", "^local"], "rules": [{"pattern": "^x"}, {"pattern": "^z"}]},
            "exclude_dirs": ["vendor"]}
    merged = fcfg.merge_repo_sections(fleet, repo, SCHEMA)
    assert merged.warnings == []
    assert merged.config["ai_usage"] == {
        "agent_identities": [f"^{SENTINEL}", "^bot", "^local"],
        "rules": [{"pattern": "^x"}, {"pattern": "^y", "field": "name"}, {"pattern": "^z"}]}
    assert fleet["ai_usage"]["agent_identities"] == [f"^{SENTINEL}", "^bot"]  # type: ignore[typeddict-item]


def test_a_fleet_scalar_is_never_replaced_silently_and_an_unset_one_takes_the_repo_value():
    fleet = _parse(FULL)
    merged = fcfg.merge_repo_sections(
        fleet, {"review": {"enabled": False}, "platform": {"sample": 25, "host": "gl.example"}}, SCHEMA)
    assert merged.config["review"]["enabled"] is True  # type: ignore[typeddict-item]
    assert merged.config["platform"] == {"sample": 25, "host": "gl.example"}
    assert merged.warnings == ["review.enabled: set in the fleet config; the repository value is ignored"]


def test_bad_repository_input_is_skipped_with_a_warning_never_raised():
    merged = fcfg.merge_repo_sections(
        fcfg.empty_config(),
        {"review": {"components": 3, "nope": 1}, "platform": "x", "ai_usage": {"agent_identities": ["a"]}},
        SCHEMA)
    assert merged.config == {"ai_usage": {"agent_identities": ["a"]}, "review": {}, "platform": {}}
    assert merged.warnings == [
        "review.components: expected a list of strings; ignored",
        "review.nope: unknown key; ignored",
        "platform: expected a table; ignored",
    ]


def test_repo_fleet_config_reads_the_repository_file_and_warns_on_stderr(tmp_path, capsys):
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text("[review]\nfoo = 1\n", encoding="utf-8")
    assert fcfg.repo_fleet_config(tmp_path) == fcfg.empty_config()
    assert "review.foo: unknown key; ignored" in capsys.readouterr().err
    assert run_setup.fleet_config(tmp_path) == fcfg.empty_config()


# --- registry input --------------------------------------------------------


def test_the_registry_declares_fleet_config_as_a_private_setup_input():
    (spec,) = [s for s in reg.SCANS if s.key == "fleet_config"]
    assert spec.private and spec.stage == reg.STAGE_SETUP and spec.reads == ("repo_root",)


def test_a_scan_reading_fleet_config_gets_the_merged_config(tmp_path, monkeypatch):
    monkeypatch.setattr(fcfg, "SECTION_KEYS", SCHEMA)
    repo = make_repo(tmp_path, "grp/svc", files={".assess/config.toml": '[review]\ncomponents = ["local"]\n'})
    seen: list[Any] = []
    spec = ScanSpec("probe", lambda config: seen.append(config) or {"available": True},
                    ("fleet_config",), reg.STAGE_READ_SIDE)
    document = build_document(repo, ["probe"], specs=(spec,), fleet_config=_parse(FULL))
    assert document["scans"]["probe"] == {"available": True}
    assert seen[0]["review"]["components"] == ["gitlab.example/ai-review", "local"]
    build_document(repo, ["probe"], specs=(spec,))  # no fleet file: the repository's sections alone
    assert seen[1]["review"] == {"components": ["local"]}


# --- stamp, comparability, history -----------------------------------------


def _row(repo: str) -> Any:
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": "grp", "status": "ok",
            "reason": None, "signals_schema_version": 1, "scans": {}, "active": True}


def _stamp(config: fcfg.FleetConfig | None) -> FleetStamp:
    return build_stamp([_row("grp/a")], toolkit_commit="abc", plugin_version="2.0.0", today=date(2026, 11, 20),
                       window="6mo", levels_version="fixture-1", counts_only=True, paths_hashed=False,
                       scans=None, fleet_config=config)


def test_the_stamp_records_the_hash_and_entry_counts_never_a_value():
    config = _parse(FULL)
    stamp = _stamp(config)
    assert stamp["fleet_config_sha256"] == fcfg.config_sha256(config)
    assert stamp["fleet_config_entries"] == {"ai_usage": 4, "review": 2, "platform": 1, "identities": 0}
    assert SENTINEL not in json.dumps(stamp) and "ai-review" not in json.dumps(stamp)
    assert _stamp(None)["fleet_config_sha256"] is None and _stamp(None)["fleet_config_entries"] is None


def test_runs_with_different_configs_are_not_comparable_and_say_why():
    rows = [_row("grp/a")]
    earlier = FleetRun(_stamp(_parse(FULL)), rows, roll_up(rows, LEVELS, {}))
    later = FleetRun(_stamp(_parse("[review]\nenabled = false\n")), rows, roll_up(rows, LEVELS, {}))
    comparison = compare(earlier, later)
    assert not comparison["comparable"]
    assert any(r.startswith("fleet config changed") for r in comparison["reasons"])
    no_config = FleetRun(_stamp(None), rows, roll_up(rows, LEVELS, {}))
    assert any(r.startswith("fleet config changed None->") for r in compare(no_config, later)["reasons"])
    assert compare(earlier, FleetRun(_stamp(_parse(FULL)), rows, roll_up(rows, LEVELS, {})))["comparable"]


def test_a_stamp_from_before_706_compares_as_no_fleet_config():
    old: Any = {k: v for k, v in _stamp(None).items() if not k.startswith("fleet_config")}
    assert comparability(old, _stamp(None)) == ([], [])
    assert comparability(old, _stamp(fcfg.empty_config()))[0] != []


def test_history_and_trends_match_on_the_config_hash(tmp_path):
    assert "fleet_config_sha256" in fh._MATCH_KEYS and "fleet_config_sha256" in fleet_trends.MATCH_KEYS
    entry = tmp_path / "org" / "2026-11-20"
    entry.mkdir(parents=True)
    write_stamp(entry, _stamp(_parse(FULL)))
    other: dict[str, Any] = dict(_stamp(None))
    conflict = fh.entry_conflict(entry, other)
    assert conflict is not None and "fleet_config_sha256" in conflict
    assert fh.entry_conflict(entry, dict(_stamp(_parse(FULL)))) is None


# --- CLIs ------------------------------------------------------------------


def test_signals_command_passes_the_config_path():
    options = ScanOptions(fleet_config=Path("/abs/fleet.toml"))
    command = signals_command(Path("/repo"), Path("/out.json"), options)
    assert command[-2:] == ["--fleet-config", str(Path("/abs/fleet.toml"))]
    assert "--fleet-config" not in signals_command(Path("/repo"), Path("/out.json"), ScanOptions())


def test_assess_signals_exits_2_naming_the_key(tmp_path, capsys):
    repo = make_repo(tmp_path, "grp/svc")
    bad = tmp_path / "bad.toml"
    bad.write_text(f'[review]\ncomponents = ["{SENTINEL}"]\n', encoding="utf-8")
    out = tmp_path / "out.json"
    assert assess_signals.main([str(repo), "--out", str(out), "--fleet-config", str(bad)]) == 2
    err = capsys.readouterr().err
    assert "review.components: unknown key" in err and SENTINEL not in err
    assert not out.exists()


def test_assess_signals_accepts_a_valid_config(tmp_path):
    repo = make_repo(tmp_path, "grp/svc")
    good = tmp_path / "good.toml"
    good.write_text("[ai_usage]\n[review]\n", encoding="utf-8")
    out = tmp_path / "out.json"
    assert assess_signals.main([str(repo), "--out", str(out), "--scans", "agent_ops",
                                "--counts-only", "--fleet-config", str(good)]) == 0
    assert out.is_file()


def test_fleet_exits_2_naming_the_key_before_any_scan(tmp_path, capsys):
    make_repo(tmp_path / "root", "grp/svc")
    bad = tmp_path / "bad.toml"
    bad.write_text(f'[platform]\nhost = "{SENTINEL}"\n', encoding="utf-8")
    out = tmp_path / "out"
    assert fleet_cli.main([str(tmp_path / "root"), "--out", str(out), "--no-history",
                           "--fleet-config", str(bad)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: --fleet-config") and "platform.host: unknown key" in err
    assert SENTINEL not in err and not out.exists()


def test_fleet_gives_every_scan_the_same_copy_and_stamps_the_hash(tmp_path, monkeypatch):
    root = tmp_path / "root"
    for slug in ("grp/a", "grp/b"):
        make_repo(root, slug)
    text = f"# {SENTINEL}\n[ai_usage]\n[review]\n[platform]\n"
    config_path = tmp_path / "fleet.toml"
    config_path.write_text(text, encoding="utf-8")
    seen: list[tuple[Path | None, str]] = []
    real = fleet_cli.run_fleet

    def spy(targets, options, jobs, **kwargs):  # type: ignore[no-untyped-def]
        path = options.fleet_config
        seen.append((path, path.read_text(encoding="utf-8") if path else ""))
        return real(targets, options, jobs, **kwargs)

    monkeypatch.setattr(fleet_cli, "run_fleet", spy)
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "out"
    assert fleet_cli.main([str(root), "--out", str(out), "--no-history", "--no-diagrams", "--scans", "agent_ops",
                           "--fleet-config", "fleet.toml"]) == 0
    ((copy, copied),) = seen
    assert copy is not None and copy.is_absolute() and copied == text and copy != config_path.resolve()
    assert not copy.exists()  # removed once the scans are done
    stamp = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert stamp["fleet_config_sha256"] == fcfg.config_sha256(fcfg.empty_config())
    assert stamp["fleet_config_entries"] == {"ai_usage": 0, "review": 0, "platform": 0, "identities": 0}
    rows = [json.loads(line) for line in (out / "repos.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["status"] for r in rows] == ["ok", "ok"]
    for path in out.rglob("*"):
        if path.is_file():
            assert SENTINEL not in path.read_text(encoding="utf-8", errors="replace")


def test_a_new_empty_section_leaves_every_stored_hash_unchanged():
    config = _parse(FULL)
    widened: Any = {**config, "later": {}}
    assert fcfg.config_sha256(widened) == fcfg.config_sha256(config)
    assert fcfg.config_sha256(fcfg.empty_config()) == fcfg.config_sha256(_parse("[review]\n"))


def test_a_history_entry_from_before_706_still_matches_and_loads(tmp_path):
    from lib.fleet.runner import write_rows
    from lib.fleet.fleet_compare import load_run
    from lib.fleet.fleet_groups import write_groups

    rows = [_row("grp/a")]
    entry = tmp_path / "org" / "2026-11-19"
    entry.mkdir(parents=True)
    write_rows(entry, rows)
    write_groups(entry, roll_up(rows, LEVELS, {}))
    old = {k: v for k, v in _stamp(None).items() if not k.startswith("fleet_config")}
    (entry / "run.json").write_text(json.dumps(old), encoding="utf-8")
    assert fh.entry_conflict(entry, dict(_stamp(None))) is None
    assert fh.latest_matching(entry.parent, dict(_stamp(None))) == entry
    assert load_run(entry).stamp.get("fleet_config_sha256") is None


# --- [platform] (#714) -----------------------------------------------------


def test_platform_jobs_and_timeout_validate_and_leave_the_hash_alone():
    config = fcfg.parse_fleet_config("[platform]\njobs = 2\ntimeout = 90\n")
    assert fcfg.platform_section(config) == {"jobs": 2, "timeout": 90}
    assert fcfg.config_sha256(config) == fcfg.config_sha256(fcfg.empty_config())
    assert fcfg.section_entries(config)["platform"] == 2
    for bad, message in (("jobs = 0", "platform.jobs: expected a whole number"),
                         ("timeout = \"x\"", "platform.timeout: expected a whole number")):
        with pytest.raises(fcfg.FleetConfigError, match=message):
            fcfg.parse_fleet_config(f"[platform]\n{bad}\n")


def test_signals_command_passes_platform_only_when_set():
    assert "--platform" not in signals_command(Path("/c"), Path("/o.json"), ScanOptions())
    assert "--platform" in signals_command(Path("/c"), Path("/o.json"), ScanOptions(platform=True))
