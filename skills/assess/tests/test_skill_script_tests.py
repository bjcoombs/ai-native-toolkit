"""lib/skill_script_tests.py and the skill-script-tests condition (#1045): presence, not coverage."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

import pytest

import lib.level_tally as lt
import lib.manifest.check_manifest as cm
import lib.skill_script_tests as sst
import lib.unit_readings as ur
from lib.skill_script_tests import is_test_file, scan_skill_script_tests
from test_unit_readings import _read, _write

CID = "skill-script-tests.1"
# The maintainer's approved why on #1045, byte for byte.
WHY = ("A skill's scripts run with an agent's permissions every time it triggers; tests beside them are "
       "the only check that a change still does what the SKILL.md promises.")
SKILL = "---\nname: {0}\ndescription: Does {0}.\n---\nBody.\n"


def _plugin(root: Path, files: dict[str, str]) -> Path:
    for rel, text in {".claude-plugin/plugin.json": '{"name": "p"}\n', **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _state(block: Any) -> tuple[str, str | None]:
    cond = next(c for c in lt.level_result(lt.default_model(), {"skill_script_tests": block})["conditions"]
                if c["id"] == CID)
    return cond["state"], cond.get("reason")


# --- what counts -----------------------------------------------------------------------


@pytest.mark.parametrize(("rel", "test"), [
    ("tests/test_run.py", True), ("test_run.py", True), ("run_test.py", True), ("scripts/x_test.go", True),
    ("web/app.test.ts", True), ("web/app.spec.js", True), ("t/x.bats", True), ("tests/helpers.py", True),
    ("tests/README.md", False), ("tests/fixtures/sample/run.py", False), ("tests/golden/out.json", False),
    ("scripts/run.py", False), ("README.md", False),
    ("tests/__init__.py", False), ("tests/conftest.py", False), ("tests/test_init.py", True),
])
def test_what_is_a_test_file(rel: str, test: bool) -> None:
    assert is_test_file(PurePosixPath(rel)) is test


def test_a_skill_with_scripts_and_tests_beside_them_reads_met(tmp_path: Path) -> None:
    repo = _plugin(tmp_path, {"skills/a/SKILL.md": SKILL.format("a"), "skills/a/scripts/run.py": "print(1)\n",
                              "skills/a/tests/test_run.py": "def test_x(): pass\n"})
    block = scan_skill_script_tests(repo)
    assert block["skills"] == [{"path": "skills/a", "scripts": 1, "tests": 1, "untested": False,
                                "tested_elsewhere": []}]
    assert (block["skills_with_scripts"], block["skills_untested"]) == (1, 0)
    assert _state(block) == ("met", None)


def test_an_init_or_conftest_alone_is_not_a_test(tmp_path: Path) -> None:
    repo = _plugin(tmp_path, {"skills/a/SKILL.md": SKILL.format("a"), "skills/a/scripts/run.py": "print(1)\n",
                              "skills/a/tests/__init__.py": "", "skills/a/tests/conftest.py": "import pytest\n"})
    assert scan_skill_script_tests(repo)["skills"][0]["untested"] is True


def test_the_walk_skips_vendored_and_excluded_folders(tmp_path: Path) -> None:
    repo = _plugin(tmp_path, {"skills/a/SKILL.md": SKILL.format("a"), "skills/a/scripts/run.py": "x\n",
                              "node_modules/pkg/tests/test_a.py": '"skills/a/scripts/run.py"\n',
                              "vendor/tests/test_a.py": '"skills/a/scripts/run.py"\n'})
    (skill,) = scan_skill_script_tests(repo, excludes=({"vendor"}, []))["skills"]
    assert skill["tested_elsewhere"] == []


def test_a_skill_with_scripts_and_no_tests_reads_unmet(tmp_path: Path) -> None:
    repo = _plugin(tmp_path, {"skills/a/SKILL.md": SKILL.format("a"), "skills/a/scripts/run.sh": "echo\n",
                              "skills/a/tests/README.md": "no tests yet\n"})
    block = scan_skill_script_tests(repo)
    assert block["skills"][0]["untested"] is True and block["skills"][0]["tests"] == 0
    assert _state(block) == ("unmet", None)


def test_a_skill_without_scripts_is_not_counted_and_no_scripts_reads_not_measured(tmp_path: Path) -> None:
    repo = _plugin(tmp_path, {"skills/a/SKILL.md": SKILL.format("a"), "skills/a/references/r.md": "# R\n",
                              "skills/a/tests/fixtures/sample.py": "x = 1\n"})
    block = scan_skill_script_tests(repo)
    assert block["skills"][0]["untested"] is None and block["skills_with_scripts"] == 0
    assert _state(block) == ("not_measured", "no_skill_scripts")
    assert _state(scan_skill_script_tests(_plugin(tmp_path / "none", {"README.md": "x"}))) == (
        "not_measured", "no_skill_scripts")


def test_tests_elsewhere_are_named_but_do_not_count(tmp_path: Path) -> None:
    repo = _plugin(tmp_path, {
        "skills/fleet/SKILL.md": SKILL.format("fleet"), "skills/fleet/scripts/fleet.py": "print(1)\n",
        "tests/test_fleet.py": 'SCRIPT = ROOT / "skills" / "fleet" / "scripts" / "fleet.py"\n',
        "tests/test_other.py": 'print("fleet.py")\n',       # the name without the folder segment
        "tests/test_split.py": 'X = "fleet"\nY = "fleet.py"\n',  # segment and name on different lines
        "tests/fixtures/notes.py": '"fleet/scripts/fleet.py"\n',  # test data, not a test
    })
    (skill,) = scan_skill_script_tests(repo)["skills"]
    assert skill["untested"] is True and skill["tested_elsewhere"] == ["tests/test_fleet.py"]
    assert _state(scan_skill_script_tests(repo)) == ("unmet", None)


def test_the_check_measures_presence_and_says_so() -> None:
    manifest = cm.core_manifest()
    cond = next(e for e in manifest["entries"] if e.get("key") == f"{CID}:skill_script_tests.skills_untested")
    assert cond["why"] == WHY  # type: ignore[typeddict-item]
    assert "presence, not coverage" in cond["title"]  # type: ignore[typeddict-item]
    assert not [c for c in lt.gate_conditions(lt.default_model()) if c.startswith("skill-script-tests.")]


# --- per skill unit --------------------------------------------------------------------


def test_each_skill_unit_reads_its_own_scripts(tmp_path: Path) -> None:
    repo = _write(tmp_path / "plugin", {
        ".claude-plugin/plugin.json": '{"name": "p"}\n', "agents/a.md": "---\nname: a\ndescription: A.\n---\n",
        "skills/good/SKILL.md": SKILL.format("good"), "skills/good/scripts/run.py": "print(1)\n",
        "skills/good/tests/test_run.py": "def test_x(): pass\n",
        "skills/bad/SKILL.md": SKILL.format("bad"), "skills/bad/scripts/run.py": "print(1)\n",
    })
    units = {u["path"]: u for u in _read(repo)["units"]}
    good, bad = units["skills/good"]["blocks"]["skill_script_tests"], units["skills/bad"]["blocks"]["skill_script_tests"]
    assert (good["skills_with_scripts"], good["skills_untested"]) == (1, 0)
    assert (bad["skills_with_scripts"], bad["skills_untested"]) == (1, 1)
    assert "skill_script_tests" not in units["agents"]["blocks"]
    assert ur.unit_kinds(cm.core_manifest())["skill_script_tests"] == frozenset({"skill"})


def test_a_scoped_read_lists_only_that_skill_and_still_searches_the_repository(tmp_path: Path) -> None:
    repo = _plugin(tmp_path, {
        "skills/a/SKILL.md": SKILL.format("a"), "skills/a/scripts/a.py": "x\n",
        "skills/b/SKILL.md": SKILL.format("b"), "skills/b/scripts/b.py": "x\n",
        "tests/test_a.py": '"skills/a/scripts/a.py"\n',
    })
    scoped = scan_skill_script_tests(repo, scope=repo / "skills" / "a")
    assert [s["path"] for s in scoped["skills"]] == ["skills/a"]
    assert scoped["skills"][0]["tested_elsewhere"] == ["tests/test_a.py"]


def _marketplace(root: Path, skills: int) -> Path:
    """``skills`` untested skill units; every other one is tested from a shared tests folder."""
    files = {".claude-plugin/plugin.json": '{"name": "p"}\n'}
    for i in range(skills):
        files[f"skills/s{i}/SKILL.md"] = SKILL.format(f"s{i}")
        files[f"skills/s{i}/scripts/run{i}.py"] = "print(1)\n"
        if i % 2:
            files[f"tests/test_s{i}.py"] = f'"skills/s{i}/scripts/run{i}.py"\n'
    return _write(root, files)


def test_the_cached_index_reads_the_same_as_the_uncached_path(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """#1189: sharing the test-file index across units changes no reading."""
    repo = _marketplace(tmp_path / "m", 6)
    sst._test_index.cache_clear()
    cached = _read(repo)
    monkeypatch.setattr(sst, "_outside_tests", lambda r, d, p: sst._test_index.__wrapped__(
        r, sst._head(r), frozenset(d), tuple(p)))
    assert _read(repo) == cached
    full = scan_skill_script_tests(repo)
    assert [s["path"] for s in full["skills"] if s["tested_elsewhere"]] == ["skills/s1", "skills/s3", "skills/s5"]


def test_fifty_untested_skill_units_read_the_test_files_once(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """#1189: the unit pass reads the repository's test files once, not once per untested unit."""
    repo = _marketplace(tmp_path / "m", 50)
    reads: list[str] = []
    real = Path.read_text

    def counting(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.parent.name == "tests" and self.name.startswith("test_s"):
            reads.append(self.name)
        return real(self, *args, **kwargs)

    sst._test_index.cache_clear()
    monkeypatch.setattr(Path, "read_text", counting)
    units = [u for u in _read(repo)["units"] if u["path"].startswith("skills/")]
    assert len(units) == 50 and all(u["blocks"]["skill_script_tests"]["skills_untested"] == 1 for u in units)
    assert sorted(reads) == sorted(f"test_s{i}.py" for i in range(1, 50, 2))  # each file once


def test_one_script_tested_elsewhere_uses_the_same_matcher(tmp_path: Path) -> None:
    """#1233: the per-script question the sibling-test probe asks, answered by the
    ``tested_elsewhere`` matcher, script by script."""
    repo = _plugin(tmp_path, {
        "skills/ghsync/SKILL.md": SKILL.format("ghsync"), "skills/ghsync/scripts/ghsync.sh": "echo\n",
        "skills/ghsync/scripts/other.sh": "echo\n", "skills/ghsync/tests/helper.sh": "echo\n",
        "tools/loose.sh": "echo\n",
        "tests/test_ghsync.py": 'SCRIPT = ROOT / "skills/ghsync/scripts/ghsync.sh"\nX = "loose.sh"\n',
    })
    assert sst.script_tested_elsewhere(repo, "skills/ghsync/scripts/ghsync.sh") is True
    assert sst.script_tested_elsewhere(repo, "skills/ghsync/scripts/other.sh") is False  # not named
    assert sst.script_tested_elsewhere(repo, "skills/ghsync/tests/helper.sh") is False  # under tests/
    assert sst.script_tested_elsewhere(repo, "skills/ghsync/SKILL.md") is False  # not a script
    assert sst.script_tested_elsewhere(repo, "tools/loose.sh") is False  # in no skill folder


def test_a_skill_md_above_the_repository_is_never_its_skill_folder(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text(SKILL.format("parent"), encoding="utf-8")
    (tmp_path / "outside.sh").write_text("echo\n", encoding="utf-8")
    repo = _plugin(tmp_path / "repo", {"tests/test_x.py": 'X = "repo/../outside.sh"\n'})
    assert sst.script_tested_elsewhere(repo, "../outside.sh") is False
    assert sst.script_tested_elsewhere(repo, str(tmp_path / "outside.sh")) is False
