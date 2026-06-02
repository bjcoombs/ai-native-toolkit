"""
Integration tests: run the full standalone skill build and validate ZIP contents.

Catches the class of bug where plugin-internal content leaks into bundled
reference files that weren't processed by the transformer.
"""
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent


def _build(skill_name: str, tmp_path: Path) -> zipfile.ZipFile:
    from standalone_skill_config import SKILLS
    from transform_skill import build_standalone_skill_zip

    cfg = SKILLS[skill_name]
    out_zip = tmp_path / f"{cfg['standalone_name']}.zip"
    issues = build_standalone_skill_zip(
        skill_source_dir=REPO_ROOT / cfg["source_dir"],
        out_zip=out_zip,
        standalone_name=cfg["standalone_name"],
        standalone_description=cfg["standalone_description"],
        replacements=cfg["replacements"],
        exclude_dirs=frozenset(cfg["exclude_dirs"]),
    )
    assert not issues, "Build produced issues:\n" + "\n".join(issues)
    assert out_zip.exists(), "ZIP not created despite no issues reported"
    return zipfile.ZipFile(out_zip)


def _md_contents(zf: zipfile.ZipFile) -> dict[str, str]:
    return {
        name: zf.read(name).decode("utf-8")
        for name in zf.namelist()
        if name.endswith(".md")
    }


# ── assess ───────────────────────────────────────────────────────────────────

class TestAssessBuild:
    @pytest.fixture(scope="class")
    def assess_zip(self, tmp_path_factory):
        return _build("assess", tmp_path_factory.mktemp("assess"))

    def test_skill_md_present(self, assess_zip):
        assert "assess/SKILL.md" in assess_zip.namelist()

    def test_scripts_present(self, assess_zip):
        names = assess_zip.namelist()
        assert any(n.startswith("assess/scripts/") for n in names)

    def test_tests_excluded(self, assess_zip):
        names = assess_zip.namelist()
        assert not any("tests/" in n for n in names), "tests/ directory leaked into ZIP"

    def test_no_skill_dir_reference(self, assess_zip):
        for name, content in _md_contents(assess_zip).items():
            assert "SKILL_DIR" not in content, f"{name}: SKILL_DIR leaked"

    def test_no_plugin_root_reference(self, assess_zip):
        # The plugin-only path resolution ($CLAUDE_PLUGIN_ROOT) must be stripped;
        # standalone uses bare scripts/ paths.
        for name, content in _md_contents(assess_zip).items():
            assert "CLAUDE_PLUGIN_ROOT" not in content, f"{name}: CLAUDE_PLUGIN_ROOT leaked"

    def test_no_dollar_arguments(self, assess_zip):
        for name, content in _md_contents(assess_zip).items():
            assert "$ARGUMENTS" not in content, f"{name}: $ARGUMENTS leaked"

    def test_no_namespaced_slash_command(self, assess_zip):
        for name, content in _md_contents(assess_zip).items():
            assert "ai-native-toolkit:assess" not in content, (
                f"{name}: namespaced slash command leaked"
            )

    def test_no_plugin_install_instructions(self, assess_zip):
        for name, content in _md_contents(assess_zip).items():
            assert "/plugin marketplace add" not in content, (
                f"{name}: plugin install instructions leaked"
            )

    def test_frontmatter_name_correct(self, assess_zip):
        skill_md = assess_zip.read("assess/SKILL.md").decode("utf-8")
        assert "name: assess" in skill_md

    def test_zip_is_deterministic(self, tmp_path):
        zf1 = _build("assess", tmp_path / "run1")
        zf2 = _build("assess", tmp_path / "run2")
        assert Path(zf1.filename).read_bytes() == Path(zf2.filename).read_bytes()


# ── huddle ───────────────────────────────────────────────────────────────────

class TestHuddleBuild:
    @pytest.fixture(scope="class")
    def huddle_zip(self, tmp_path_factory):
        return _build("huddle", tmp_path_factory.mktemp("huddle"))

    def test_skill_md_present(self, huddle_zip):
        assert "huddle/SKILL.md" in huddle_zip.namelist()

    def test_no_team_create(self, huddle_zip):
        for name, content in _md_contents(huddle_zip).items():
            assert "TeamCreate" not in content, f"{name}: TeamCreate leaked"

    def test_no_send_message(self, huddle_zip):
        for name, content in _md_contents(huddle_zip).items():
            assert "SendMessage" not in content, f"{name}: SendMessage leaked"

    def test_no_team_delete(self, huddle_zip):
        for name, content in _md_contents(huddle_zip).items():
            assert "TeamDelete" not in content, f"{name}: TeamDelete leaked"

    def test_no_agent_teams_env_var(self, huddle_zip):
        for name, content in _md_contents(huddle_zip).items():
            assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in content, (
                f"{name}: capability flag env var leaked"
            )

    def test_solo_mode_preserved(self, huddle_zip):
        skill_md = huddle_zip.read("huddle/SKILL.md").decode("utf-8")
        assert "Solo flat-parallel" in skill_md

    def test_frontmatter_name_correct(self, huddle_zip):
        skill_md = huddle_zip.read("huddle/SKILL.md").decode("utf-8")
        assert "name: huddle" in skill_md


# ── skill-forge ───────────────────────────────────────────────────────────────

class TestSkillForge:
    @pytest.fixture(scope="class")
    def forge_zip(self, tmp_path_factory):
        return _build("skill-forge", tmp_path_factory.mktemp("skill-forge"))

    def test_skill_md_present(self, forge_zip):
        assert "skill-forge/SKILL.md" in forge_zip.namelist()

    def test_tests_fixture_excluded(self, forge_zip):
        names = forge_zip.namelist()
        assert not any("/tests/" in n for n in names), "tests/ fixture leaked into ZIP"

    def test_no_team_create(self, forge_zip):
        for name, content in _md_contents(forge_zip).items():
            assert "TeamCreate" not in content, f"{name}: TeamCreate leaked"

    def test_no_send_message(self, forge_zip):
        for name, content in _md_contents(forge_zip).items():
            assert "SendMessage" not in content, f"{name}: SendMessage leaked"

    def test_no_team_delete(self, forge_zip):
        for name, content in _md_contents(forge_zip).items():
            assert "TeamDelete" not in content, f"{name}: TeamDelete leaked"

    def test_no_plugin_root_reference(self, forge_zip):
        for name, content in _md_contents(forge_zip).items():
            assert "$CLAUDE_PLUGIN_ROOT" not in content, (
                f"{name}: $CLAUDE_PLUGIN_ROOT leaked"
            )

    def test_no_dollar_arguments(self, forge_zip):
        for name, content in _md_contents(forge_zip).items():
            assert "$ARGUMENTS" not in content, f"{name}: $ARGUMENTS leaked"

    def test_no_namespaced_slash_command(self, forge_zip):
        for name, content in _md_contents(forge_zip).items():
            assert "ai-native-toolkit:skill-forge" not in content, (
                f"{name}: namespaced slash command leaked"
            )

    def test_no_agent_teams_env_var(self, forge_zip):
        for name, content in _md_contents(forge_zip).items():
            assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in content, (
                f"{name}: capability flag env var leaked"
            )

    def test_chat_replace_marker_consumed(self, forge_zip):
        # The marker itself is consumed by the transform; only its standalone
        # replacement text survives.
        for name, content in _md_contents(forge_zip).items():
            assert "chat-replace:execution-mode-rule" not in content, (
                f"{name}: chat-replace marker leaked"
            )

    def test_solo_replacement_applied(self, forge_zip):
        skill_md = forge_zip.read("skill-forge/SKILL.md").decode("utf-8")
        assert "Run in solo mode" in skill_md

    def test_frontmatter_name_correct(self, forge_zip):
        skill_md = forge_zip.read("skill-forge/SKILL.md").decode("utf-8")
        assert "name: skill-forge" in skill_md

    def test_zip_is_deterministic(self, tmp_path):
        zf1 = _build("skill-forge", tmp_path / "run1")
        zf2 = _build("skill-forge", tmp_path / "run2")
        assert Path(zf1.filename).read_bytes() == Path(zf2.filename).read_bytes()


# ── deslop ──────────────────────────────────────────────────────────────────

class TestDeslopBuild:
    @pytest.fixture(scope="class")
    def deslop_zip(self, tmp_path_factory):
        return _build("deslop", tmp_path_factory.mktemp("deslop"))

    def test_skill_md_present(self, deslop_zip):
        assert "deslop/SKILL.md" in deslop_zip.namelist()

    def test_reference_checklist_present(self, deslop_zip):
        assert "deslop/references/full-checklist.md" in deslop_zip.namelist()

    def test_frontmatter_name_correct(self, deslop_zip):
        skill_md = deslop_zip.read("deslop/SKILL.md").decode("utf-8")
        assert "name: deslop" in skill_md

    def test_standalone_description_applied(self, deslop_zip):
        # The build replaces the source description (with its plugin TRIGGER
        # phrasing) with the standalone one carrying the version suffix.
        skill_md = deslop_zip.read("deslop/SKILL.md").decode("utf-8")
        assert "Standalone build v" in skill_md

    def test_no_orphan_markers(self, deslop_zip):
        for name, content in _md_contents(deslop_zip).items():
            assert "chat-skip" not in content, f"{name}: chat-skip marker leaked"
            assert "chat-replace" not in content, f"{name}: chat-replace marker leaked"

    def test_zip_is_deterministic(self, tmp_path):
        zf1 = _build("deslop", tmp_path / "run1")
        zf2 = _build("deslop", tmp_path / "run2")
        assert Path(zf1.filename).read_bytes() == Path(zf2.filename).read_bytes()
