"""lib/pack_layering.py: company and team packs layered onto the model, each row naming its source (#1274)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import lib.fleet.fleet_method as fm
from lib.scoring.level_signals import signal_path_validator
from lib.scoring.level_tally import default_model
from lib.pack_checks import collect
from lib.pack_layering import layer_packs, pack_sources, row_source
from lib.packs import Pack, PackError, load_pack
from lib.profiles import ModelChoice, ProfileError, model_lines, run_level, single_repo_choice

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "run-context-baseline.json"


def _row(pack: str, row_id: str, check: str, title: str, requires: str | None = None) -> str:
    needs = f'\n  requires = ["{requires}"]' if requires else ""
    return f'''
[[rows]]
id = "{row_id}"
title = "{title}"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  truth = ["docs"]{needs}
  criteria = [{{ signal = "pack.{pack}.{check}", op = "eq", value = true }}]
'''


def _pack_text(name: str, check: str, path: str, rows: str) -> str:
    return f'''[pack]
name = "{name}"
version = "2026.11"

[[checks]]
id = "{check}"
title = "{check}"
why = "A practice of the {name}."
exists = "{path}"
{rows}'''


COMPANY = _pack_text("company", "glossary", "docs/glossary.md",
                     _row("company", "company-glossary", "glossary", "Domain glossary"))
TEAM = _pack_text("team", "owner", "OWNERS", _row("team", "team-owner", "owner", "Owner named",
                                                  requires="company-glossary.1"))


def _load(tmp_path: Path, name: str, text: str) -> Pack:
    path = tmp_path / f"{name}.pack.toml"
    path.write_text(text, encoding="utf-8")
    return load_pack(path)


@pytest.fixture
def packs(tmp_path: Path) -> tuple[Pack, ...]:
    return (_load(tmp_path, "company", COMPANY), _load(tmp_path, "team", TEAM))


def _choice(packs: tuple[Pack, ...], profile: str | None = None) -> ModelChoice:
    return single_repo_choice(profile, None, signal_path_validator(packs), packs)


def _ctx(tmp_path: Path, packs: tuple[Pack, ...]) -> dict[str, Any]:
    """The golden run context with the pack block a repository holding a glossary, no OWNERS, gives."""
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "glossary.md").write_text("# Words\n", encoding="utf-8")
    ctx: dict[str, Any] = json.loads(GOLDEN.read_text(encoding="utf-8"))
    ctx.update(collect(repo, (set(), []), packs))
    return ctx


def test_default_company_and_team_score_together_each_condition_naming_its_source(
        tmp_path: Path, packs: tuple[Pack, ...]) -> None:
    choice = _choice(packs)
    assert (choice.kind, choice.name) == ("default", "default")
    assert pack_sources(choice.model) == ["company", "team"]
    level = run_level(_ctx(tmp_path, packs), choice)
    by_id = {c["id"]: c for c in level["conditions"]}
    assert by_id["company-glossary.1"]["source"] == "company"
    assert by_id["company-glossary.1"]["state"] == "met"
    assert by_id["team-owner.1"]["source"] == "team"
    assert by_id["team-owner.1"]["state"] == "unmet"
    defaults = {f"{r['id']}.{lv['level']}" for r in default_model()["rows"] for lv in r["levels"]}
    assert defaults and all(by_id[cid]["source"] == "default" for cid in defaults)
    # the order: the base model's conditions, then each pack's, in pass order
    ids = [c["id"] for c in level["conditions"]]
    assert ids[-2:] == ["company-glossary.1", "team-owner.1"]


def test_a_profile_base_names_the_profile(packs: tuple[Pack, ...]) -> None:
    choice = _choice(packs, "handover")
    assert choice.kind == "profile"
    assert {row_source(choice.model, r["id"]) for r in choice.model["rows"]} == {"handover", "company", "team"}


def test_a_row_id_clash_between_two_packs_names_both(tmp_path: Path) -> None:
    company = _load(tmp_path, "company", COMPANY)
    clash = _load(tmp_path, "team", _pack_text("team", "owner", "OWNERS",
                                                _row("team", "company-glossary", "owner", "Owner named")))
    with pytest.raises(ProfileError) as info:
        _choice((company, clash))
    assert "'company'" in str(info.value) and "'team'" in str(info.value)
    assert "company-glossary" in str(info.value)


def test_a_pack_row_cannot_redefine_a_base_row(tmp_path: Path) -> None:
    base_id = default_model()["rows"][0]["id"]
    pack = _load(tmp_path, "team", _pack_text("team", "owner", "OWNERS", _row("team", base_id, "owner", "X")))
    with pytest.raises(PackError, match="'default' and pack 'team'"):
        layer_packs(default_model(), "default", [pack], signal_path_validator([pack]))


def test_a_pack_row_requiring_a_later_pack_is_refused(tmp_path: Path) -> None:
    team = _load(tmp_path, "team", TEAM)
    company = _load(tmp_path, "company", COMPANY)
    with pytest.raises(ProfileError) as info:
        _choice((team, company))  # team requires company-glossary.1, but company comes after it
    message = str(info.value)
    assert "later pack 'company'" in message and "team-owner.1" in message


def test_a_requires_no_source_defines_is_refused(tmp_path: Path) -> None:
    pack = _load(tmp_path, "team", _pack_text("team", "owner", "OWNERS",
                                               _row("team", "team-owner", "owner", "X", requires="nope.1")))
    with pytest.raises(PackError, match="unknown condition 'nope.1'"):
        layer_packs(default_model(), "default", [pack])


def test_a_pack_row_may_require_a_base_condition(tmp_path: Path) -> None:
    base = default_model()["rows"][0]
    need = f"{base['id']}.1"
    pack = _load(tmp_path, "team", _pack_text("team", "owner", "OWNERS",
                                               _row("team", "team-owner", "owner", "X", requires=need)))
    model = layer_packs(default_model(), "default", [pack])
    assert model["rows"][-1]["levels"][0]["requires"] == [need]


def test_a_pack_row_reading_an_unpassed_pack_signal_is_refused(tmp_path: Path) -> None:
    pack = _load(tmp_path, "team", _pack_text("team", "owner", "OWNERS",
                                               _row("other", "team-owner", "owner", "X")))
    with pytest.raises(PackError, match="pack 'team'"):
        layer_packs(default_model(), "default", [pack], signal_path_validator([pack]))


def test_a_pack_named_like_the_base_is_refused(tmp_path: Path) -> None:
    pack = _load(tmp_path, "default", _pack_text("default", "owner", "OWNERS",
                                                  _row("default", "team-owner", "owner", "X")))
    with pytest.raises(PackError, match="name of the base model"):
        layer_packs(default_model(), "default", [pack])


def test_the_summary_lines_name_company_and_team_beside_their_practices(
        tmp_path: Path, packs: tuple[Pack, ...]) -> None:
    choice = _choice(packs)
    lines = model_lines(run_level(_ctx(tmp_path, packs), choice), choice)
    assert lines == ["Practice packs company, team: Domain glossary [pack company] (in place); "
                     "Owner named [pack team] (not met)."]


def test_a_profile_run_tags_pack_practices_on_its_own_lines(tmp_path: Path, packs: tuple[Pack, ...]) -> None:
    choice = _choice(packs, "citizen")
    lines = model_lines(run_level(_ctx(tmp_path, packs), choice), choice)
    assert lines[0] == "Maturity model: profile citizen, not the default."
    assert "Missing: Owner named [pack team] (not met) [not in the default model]." in lines[1]
    assert lines[2].startswith("Not in the default model: ") and "Domain glossary [pack company]" in lines[2]
    assert lines[-1].startswith("Practice packs company, team: ")


# --- runs without a pack row keep their level and rules hash ---------------------------


def test_no_pack_or_packs_without_rows_keep_the_golden_level_and_rules_hash(tmp_path: Path) -> None:
    """The golden fixture scored as before #1274: no pack, and a pack of checks only."""
    checks_only = _load(tmp_path, "company", _pack_text("company", "glossary", "docs/glossary.md", ""))
    ctx = _ctx(tmp_path, (checks_only,))
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    before = run_level(golden, None)
    for packs in ((), (checks_only,)):
        choice = _choice(packs)
        assert choice.model == default_model()
        assert pack_sources(choice.model) == []
        assert run_level(ctx, choice) == before
        assert all("source" not in c for c in run_level(ctx, choice)["conditions"])
        assert fm.rules_sha256(choice.model) == fm.rules_sha256(default_model())
        assert model_lines(before, choice) == []
    base = default_model()
    assert layer_packs(base, "default", [checks_only]) is base


def test_counts_only_keeps_each_condition_source(tmp_path: Path, packs: tuple[Pack, ...]) -> None:
    """``level.conditions.source`` is a token in the privacy table: the counts-only copy keeps it."""
    from lib.counts_only.content_free import counts_only
    ctx = _ctx(tmp_path, packs)
    ctx["level"] = run_level(ctx, _choice(packs))
    kept = {c["id"]: c.get("source") for c in counts_only(ctx)["level"]["conditions"]}
    assert kept["company-glossary.1"] == "company" and kept["team-owner.1"] == "team"


def test_a_mutation_refresh_without_the_runs_packs_is_refused(
        tmp_path: Path, packs: tuple[Pack, ...], capsys: pytest.CaptureFixture[str]) -> None:
    """--opt-in-mutation recounts the level: without the run's --pack it would drop the pack
    practices, so it returns 2 naming them before any mutation runs."""
    import assess_core
    repo = tmp_path / "target"
    (repo / ".assess").mkdir(parents=True)
    ctx = _ctx(tmp_path, packs)
    ctx["level"] = run_level(ctx, _choice(packs))
    (repo / ".assess" / "run-context.json").write_text(json.dumps(ctx), encoding="utf-8")
    assert assess_core.run_opt_in_mutation(repo) == 2
    assert "company, team" in capsys.readouterr().err
    assert assess_core.run_opt_in_mutation(repo, model=_choice(packs[:1])) == 2
    same_order = layer_packs(default_model(), "default", [packs[0], packs[1]])
    assert assess_core._recorded_packs(ctx["level"]) == pack_sources(same_order)


def test_a_mutation_refresh_of_a_run_without_pack_rows_reads_no_packs() -> None:
    import assess_core
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert assess_core._recorded_packs(run_level(golden, None)) == []
    assert assess_core._recorded_packs(None) == []


def test_a_mutation_refresh_with_an_edited_pack_is_refused(
        tmp_path: Path, packs: tuple[Pack, ...], capsys: pytest.CaptureFixture[str]) -> None:
    """The packs block records each pack's sha256: a pack edited since the run is refused."""
    import assess_core
    from lib.packs import pack_identity
    repo = tmp_path / "target"
    (repo / ".assess").mkdir(parents=True)
    ctx = _ctx(tmp_path, packs)
    ctx["level"] = run_level(ctx, _choice(packs))
    (repo / ".assess" / "run-context.json").write_text(json.dumps(ctx), encoding="utf-8")
    assert ctx["packs"] == [pack_identity(p) for p in packs]
    edited = _load(tmp_path, "team", TEAM.replace('title = "Owner named"', 'title = "Owner set"'))
    both = (packs[0], edited)
    assert assess_core.run_opt_in_mutation(repo, model=_choice(both), packs=both) == 2
    assert "unedited" in capsys.readouterr().err
