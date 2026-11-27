"""A skill's text in reading order, with each reference expanded at its pointer (#1408).

A SKILL.md names a reference as `references/<name>.md` (relative to this skill dir)
on the line that replaced the moved text. Tests that read a section of the skill read
it through this helper, so a sentence moved into a reference stays under test in the
place an agent meets it.
"""
from __future__ import annotations

import re
from pathlib import Path

_POINTER = re.compile(r"`references/([A-Za-z0-9_.-]+\.md)` \(relative to this skill dir\)")


def read_skill(skill_md: Path) -> str:
    """SKILL.md with the text of every reference it points at inserted after the pointer line."""
    out: list[str] = []
    refs = skill_md.parent / "references"
    for line in skill_md.read_text(encoding="utf-8").splitlines():
        out.append(line)
        for name in _POINTER.findall(line):
            ref = refs / name
            if ref.is_file():
                out.extend(["", ref.read_text(encoding="utf-8").rstrip("\n"), ""])
    return "\n".join(out) + "\n"
