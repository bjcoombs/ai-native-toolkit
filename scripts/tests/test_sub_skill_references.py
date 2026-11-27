"""The assess ZIP bundles its sub-skills' references beside their bodies (#1408).

All of them share the ZIP's one references/ folder, so a name clash would overwrite a
file without a word; the config refuses one at load.
"""
from pathlib import Path

import pytest

import standalone_skill_config as cfg


def _ref(root: Path, skill: str, name: str) -> None:
    folder = root / "skills" / skill / "references"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("# ref\n", encoding="utf-8")


def test_each_sub_skill_reference_lands_in_the_hosts_references(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "_REPO", tmp_path)
    _ref(tmp_path, "host", "own.md")
    _ref(tmp_path, "a", "report-x.md")
    _ref(tmp_path, "b", "offer-y.md")
    assert cfg._sub_skill_references(("a", "b"), host="host") == {
        "references/report-x.md": "skills/a/references/report-x.md",
        "references/offer-y.md": "skills/b/references/offer-y.md",
    }


@pytest.mark.parametrize("clash_with", ["host", "a"])
def test_a_name_clash_is_refused(tmp_path, monkeypatch, clash_with):
    monkeypatch.setattr(cfg, "_REPO", tmp_path)
    _ref(tmp_path, "host", "own.md")
    _ref(tmp_path, "a", "shared.md" if clash_with == "a" else "own.md")
    _ref(tmp_path, "b", "shared.md")
    with pytest.raises(ValueError, match="clashes"):
        cfg._sub_skill_references(("a", "b"), host="host")


def test_the_assess_bundle_carries_every_sub_skill_reference():
    bundle = cfg.SKILLS["assess"]["bundle_files"]
    for sub in ("assess-findings", "assess-pr"):
        for ref in (cfg._REPO / "skills" / sub / "references").glob("*.md"):
            assert bundle[f"references/{ref.name}"] == f"skills/{sub}/references/{ref.name}"
