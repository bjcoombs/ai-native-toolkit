"""Transform a Claude Code plugin skill into a standalone ZIP for Chat / Cowork."""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

_SKIP_PATTERN = re.compile(
    r"^[ \t]*<!-- chat-skip:start -->.*?^[ \t]*<!-- chat-skip:end -->\n?",
    re.DOTALL | re.MULTILINE,
)
_REPLACE_PATTERN = re.compile(r"<!-- chat-replace:(\S+?) -->")


def strip_chat_skip(text: str) -> str:
    """Remove blocks delimited by chat-skip:start / chat-skip:end."""
    return _SKIP_PATTERN.sub("", text)


def apply_chat_replace(text: str, replacements: dict[str, str]) -> str:
    """Replace each '<!-- chat-replace:key -->\\nNEXT_LINE' with replacements[key]."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _REPLACE_PATTERN.match(lines[i].strip())
        if m:
            key = m.group(1)
            if key in replacements:
                out.append(replacements[key])
                i += 2  # consume marker + following line
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def override_frontmatter(text: str, name: str, description: str) -> str:
    """Rewrite name: and description: fields in YAML frontmatter."""
    text = re.sub(r"^name:.*$", f"name: {name}", text, flags=re.MULTILINE)
    text = re.sub(
        r'^description:.*?(?=^\S|\Z)',
        f'description: "{description}"\n',
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    return text


def check_orphan_markers(text: str) -> list[str]:
    """Return marker strings that were not consumed by the transforms."""
    issues: list[str] = []
    if "<!-- chat-skip:start -->" in text:
        issues.append("orphan <!-- chat-skip:start -->")
    if "<!-- chat-skip:end -->" in text:
        issues.append("orphan <!-- chat-skip:end -->")
    for m in _REPLACE_PATTERN.finditer(text):
        issues.append(f"orphan {m.group(0)}")
    return issues


def _transform_md(text: str, replacements: dict[str, str]) -> str:
    text = strip_chat_skip(text)
    text = apply_chat_replace(text, replacements)
    return text


def build_standalone_skill_zip(
    skill_source_dir: Path,
    out_zip: Path,
    standalone_name: str,
    standalone_description: str,
    replacements: dict[str, str],
    exclude_dirs: frozenset[str] = frozenset({"tests", "__pycache__", ".pytest_cache", ".venv"}),
) -> list[str]:
    """
    Transform *skill_source_dir* and write a standalone ZIP to *out_zip*.

    Returns validation issues; empty list means success. ZIP is not written if
    there are issues.
    """
    staging = out_zip.parent / f"_staging_{standalone_name}"
    if staging.exists():
        shutil.rmtree(staging)

    target_root = staging / standalone_name
    target_root.mkdir(parents=True)

    for src in skill_source_dir.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(skill_source_dir)
        if any(part in exclude_dirs for part in rel.parts):
            continue
        dest = target_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".md":
            dest.write_text(_transform_md(src.read_text("utf-8"), replacements), "utf-8")
        else:
            shutil.copy2(src, dest)

    skill_md = target_root / "SKILL.md"
    if skill_md.exists():
        skill_md.write_text(
            override_frontmatter(
                skill_md.read_text("utf-8"), standalone_name, standalone_description
            ),
            "utf-8",
        )

    issues: list[str] = []
    for md in sorted(target_root.rglob("*.md")):
        for problem in check_orphan_markers(md.read_text("utf-8")):
            issues.append(f"{md.relative_to(staging)}: {problem}")

    if not issues:
        out_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(target_root.rglob("*")):
                if f.is_file():
                    zi = zipfile.ZipInfo(str(f.relative_to(staging)))
                    zi.date_time = (2026, 1, 1, 0, 0, 0)  # normalise mtime for determinism
                    zf.writestr(zi, f.read_bytes())

    shutil.rmtree(staging)
    return issues
