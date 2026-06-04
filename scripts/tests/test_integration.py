"""
Integration tests: run the full standalone skill build and validate ZIP contents.

Catches the class of bug where plugin-internal content leaks into bundled
reference files that weren't processed by the transformer.
"""
import re
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent


def _build(skill_name: str, tmp_path: Path) -> zipfile.ZipFile:
    from standalone_skill_config import SKILLS
    from transform_skill import build_standalone_skill_zip

    cfg = SKILLS[skill_name]
    out_zip = tmp_path / f"{cfg['standalone_name']}.zip"
    bundle_files = {
        dest: REPO_ROOT / src for dest, src in cfg.get("bundle_files", {}).items()
    } or None
    issues = build_standalone_skill_zip(
        skill_source_dir=REPO_ROOT / cfg["source_dir"],
        out_zip=out_zip,
        standalone_name=cfg["standalone_name"],
        standalone_description=cfg["standalone_description"],
        replacements=cfg["replacements"],
        exclude_dirs=frozenset(cfg["exclude_dirs"]),
        bundle_files=bundle_files,
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


# ── ab-equivalence (vendored, no standalone ZIP of its own) ───────────────────
# ab-equivalence is a library skill composed by skill-forge and semantic-compress,
# never invoked directly, so it ships no standalone ZIP. The one file skill-forge's
# solo mode uses (the runner-prompt wrapper) is vendored into skill-forge's ZIP;
# the build's link localizer rewrites the `../ab-equivalence/.../runner-prompt.md`
# references to that local copy. These tests pin that vendoring.


class TestSkillForgeVendorsRunner:
    @pytest.fixture(scope="class")
    def forge_zip(self, tmp_path_factory):
        return _build("skill-forge", tmp_path_factory.mktemp("forge-vendor"))

    def test_runner_prompt_vendored(self, forge_zip):
        assert "skill-forge/references/runner-prompt.md" in forge_zip.namelist(), (
            "skill-forge composes ab-equivalence's runner; the wrapper must be "
            "vendored into the standalone ZIP so solo mode can fill it"
        )

    def test_runner_link_resolves_locally(self, forge_zip):
        skill_md = forge_zip.read("skill-forge/SKILL.md").decode("utf-8")
        assert "(references/runner-prompt.md)" in skill_md, (
            "the runner link must be rewritten to the vendored local copy"
        )

    def test_ab_equivalence_skill_link_degraded(self, forge_zip):
        # The capability mention `[ab-equivalence](../ab-equivalence/SKILL.md)`
        # has no local target, so it degrades to plain text, never a dead link.
        skill_md = forge_zip.read("skill-forge/SKILL.md").decode("utf-8")
        assert "](../ab-equivalence/SKILL.md)" not in skill_md


def _all_standalone_skill_names() -> list[str]:
    from standalone_skill_config import SKILLS

    return list(SKILLS)


# Plugin-time cross-skill references (`../<skill>/...`, possibly several `../`
# deep, or `skills/<skill>/...`) cannot resolve in a standalone ZIP, which ships
# one skill with no siblings. The build's localizer must rewrite or degrade every
# one. This guardrail is exactly the check that was missing when the ab-equivalence
# extraction left skill-forge's runner reference dangling.
_CROSS_SKILL_RE = re.compile(r"(?:\.\./|skills/)[a-z][a-z0-9-]+/[A-Za-z0-9._/-]*\.md")


@pytest.mark.parametrize("skill_name", _all_standalone_skill_names())
def test_no_dangling_cross_skill_references(skill_name, tmp_path):
    zf = _build(skill_name, tmp_path)
    offenders: list[str] = []
    for name, content in _md_contents(zf).items():
        for hit in _CROSS_SKILL_RE.findall(content):
            offenders.append(f"{name}: {hit}")
    assert not offenders, (
        f"{skill_name} ships dangling cross-skill references (each standalone ZIP "
        "must be self-contained; vendor the file or let the localizer degrade it):\n"
        + "\n".join(offenders)
    )


# ── semantic-compress ─────────────────────────────────────────────────────────
class TestSemanticCompressBuild:
    @pytest.fixture(scope="class")
    def sc_zip(self, tmp_path_factory):
        return _build("semantic-compress", tmp_path_factory.mktemp("semantic-compress"))

    def test_skill_md_present(self, sc_zip):
        assert "semantic-compress/SKILL.md" in sc_zip.namelist()

    def test_result_docs_absent(self, sc_zip):
        # Forge/acceptance result docs are run instances, not skill content: the
        # runner owns the report format (the template), never report instances.
        # They must not ship in the standalone ZIP.
        names = sc_zip.namelist()
        for ref in (
            "semantic-compress/references/forge-report.md",
            "semantic-compress/references/forge-report-v2.md",
            "semantic-compress/references/acceptance-directive-clarity.md",
            "semantic-compress/references/acceptance-distillation-report.md",
        ):
            assert ref not in names, f"{ref} should not ship (result doc, not skill content)"

    def test_distill_references_present(self, sc_zip):
        # Distill mode degrades to a CLI-only note in standalone, but the three
        # distill reference docs still ride along under references/ so the
        # bundled SKILL.md's relative links resolve.
        names = sc_zip.namelist()
        for ref in (
            "semantic-compress/references/distill-loop.md",
            "semantic-compress/references/transfer-set-design.md",
            "semantic-compress/references/distillation-report-template.md",
        ):
            assert ref in names, f"{ref} missing from ZIP"

    def test_no_team_infrastructure_leaked(self, sc_zip):
        # The distill engine docs describe spawning runner subagents (Claude
        # Code-only); those mechanics are wrapped in chat-skip. No Claude
        # Code-only tool name may survive into the standalone ZIP.
        for name, content in _md_contents(sc_zip).items():
            for tool in ("TeamCreate", "TeamDelete", "SendMessage", "TaskCreate"):
                assert tool not in content, f"{name}: {tool} leaked"
        # The A/B harness / skill-forge dependency must appear only in degraded
        # form: the SKILL.md states distill mode is unavailable standalone.
        skill_md = sc_zip.read("semantic-compress/SKILL.md").decode("utf-8")
        assert "Distill mode is not available in this standalone build" in skill_md
        assert "runner harness" in skill_md

    def test_frontmatter_name_correct(self, sc_zip):
        skill_md = sc_zip.read("semantic-compress/SKILL.md").decode("utf-8")
        assert "name: semantic-compress" in skill_md

    def test_standalone_description_applied(self, sc_zip):
        skill_md = sc_zip.read("semantic-compress/SKILL.md").decode("utf-8")
        assert "Standalone build v" in skill_md

    def test_no_orphan_markers(self, sc_zip):
        for name, content in _md_contents(sc_zip).items():
            assert "chat-skip" not in content, f"{name}: chat-skip marker leaked"
            assert "chat-replace" not in content, f"{name}: chat-replace marker leaked"

    def test_zip_is_deterministic(self, tmp_path):
        zf1 = _build("semantic-compress", tmp_path / "run1")
        zf2 = _build("semantic-compress", tmp_path / "run2")
        assert Path(zf1.filename).read_bytes() == Path(zf2.filename).read_bytes()
