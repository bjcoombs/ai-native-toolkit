"""lib/levels_starter.py and scripts/levels_new.py: a starter model that loads with --levels (#1151)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import levels_new
from lib.level_signals import signal_path_validator
from lib.levels_starter import FIELD_GUIDE, StarterError, source_path, starter_text, write_starter
from lib.profiles import profile_names, single_repo_choice
from lib.signal_grades import DEFAULT_LEVELS


@pytest.mark.parametrize("profile", [None, *profile_names()])
def test_each_copy_loads_with_levels_and_keeps_every_row(tmp_path: Path, profile: str | None) -> None:
    out = write_starter(tmp_path / "my-team.toml", profile)
    choice = single_repo_choice(None, out, signal_path_validator())
    source = single_repo_choice(profile, None) if profile else None
    assert choice.kind == "file" and choice.model["version"] == "my-team-1"
    if source is not None:
        assert choice.model["rows"] == source.model["rows"]
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Your own maturity model")
    assert "Run it with: /assess --levels my-team.toml" in text


def test_the_field_guide_names_every_field() -> None:
    for field in ("version", "[[rows]]", "id", "title", "scope", "aggregation", "share", "[[rows.levels]]",
                  "truth", "criteria", "signal", "op", "value", "requires", "polarity", "[[steps]]"):
        assert f"{field}" in FIELD_GUIDE
    assert all(line.startswith("#") for line in FIELD_GUIDE.strip().splitlines())


def test_the_version_can_be_named(tmp_path: Path) -> None:
    out = write_starter(tmp_path / "x.toml", version="team-2026.11")
    assert 'version = "team-2026.11"' in out.read_text(encoding="utf-8")


@pytest.mark.parametrize("version", ["", '"', "profile-x", "default"])
def test_a_bad_version_is_refused_and_leaves_no_file(tmp_path: Path, version: str) -> None:
    with pytest.raises(StarterError):
        write_starter(tmp_path / "x.toml", version=version)
    assert not (tmp_path / "x.toml").exists()


def test_an_existing_file_is_kept_unless_forced(tmp_path: Path) -> None:
    out = tmp_path / "x.toml"
    out.write_text("mine", encoding="utf-8")
    with pytest.raises(StarterError, match="--force"):
        write_starter(out)
    assert out.read_text(encoding="utf-8") == "mine"
    write_starter(out, force=True)
    assert out.read_text(encoding="utf-8") != "mine"


def test_an_unknown_profile_names_the_profiles(tmp_path: Path) -> None:
    with pytest.raises(StarterError, match="handover"):
        source_path("nope")


def test_a_source_without_a_version_line_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "plain.toml"
    source.write_text("[[rows]]\n", encoding="utf-8")
    with pytest.raises(StarterError, match="no version line"):
        starter_text(source, "x.toml", "x-1")


def test_the_copy_names_its_source_relative_to_the_skill() -> None:
    assert "# The notes below come from levels/default.toml." in starter_text(DEFAULT_LEVELS, "x.toml", "x-1")


def test_the_command_prints_the_next_step_and_exits_2_on_error(tmp_path: Path,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "t.toml"
    assert levels_new.main([str(out), "--profile", "citizen"]) == 0
    assert capsys.readouterr().out == f"Wrote {out}. Run it with: /assess --levels {out}\n"
    assert levels_new.main([str(out)]) == 2
    assert "already exists" in capsys.readouterr().err


@pytest.mark.parametrize("version", ["profile-x", "default", "handover"])
def test_a_forced_copy_that_does_not_load_keeps_the_existing_file(tmp_path: Path, version: str) -> None:
    out = tmp_path / "x.toml"
    out.write_text("mine", encoding="utf-8")
    with pytest.raises(StarterError, match="does not load"):
        write_starter(out, version=version, force=True)
    assert out.read_text(encoding="utf-8") == "mine"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.toml"]  # no temporary file is left


@pytest.mark.parametrize(("name", "version"), [("default.toml", "custom-default-1"),
                                               ("profile-team.toml", "custom-profile-team-1"),
                                               ("handover.toml", "handover-1")])
def test_a_reserved_file_name_still_gets_a_version_that_loads(tmp_path: Path, name: str, version: str) -> None:
    out = write_starter(tmp_path / name)
    assert single_repo_choice(None, out, signal_path_validator()).model["version"] == version


def test_a_folder_that_does_not_exist_is_a_starter_error_and_exit_2(tmp_path: Path,
                                                                   capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "no" / "x.toml"
    with pytest.raises(StarterError, match="cannot write"):
        write_starter(out)
    assert levels_new.main([str(out)]) == 2
    assert "cannot write" in capsys.readouterr().err


def test_a_refused_copy_names_the_users_file_not_the_temporary_one(tmp_path: Path) -> None:
    with pytest.raises(StarterError) as caught:
        write_starter(tmp_path / "x.toml", version="profile-x")
    assert "x.toml has version 'profile-x'" in str(caught.value)
    assert "levels-new" not in str(caught.value)


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX")
def test_the_copy_keeps_the_usual_file_mode(tmp_path: Path) -> None:
    mask = os.umask(0o022)
    try:
        out = write_starter(tmp_path / "x.toml")
    finally:
        os.umask(mask)
    assert out.stat().st_mode & 0o777 == 0o644
