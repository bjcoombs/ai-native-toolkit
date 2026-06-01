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
_FRONTMATTER_PATTERN = re.compile(r"\A(---\n)(.*?)(\n---\n)", re.DOTALL)
# Permissive variant used only by strip_frontmatter: tolerates CRLF line
# endings and an EOF close fence (closing `---` without a trailing newline).
_FRONTMATTER_STRIP_PATTERN = re.compile(
    r"\A---\r?\n(.*?)\r?\n---(\r?\n|\Z)",
    re.DOTALL,
)

# Always excluded from the ZIP regardless of per-skill config — these are tooling
# artefacts that should never ship.
REQUIRED_EXCLUDES: frozenset[str] = frozenset({"__pycache__", ".pytest_cache", ".venv"})


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
    """Rewrite name: and description: fields in the YAML frontmatter only.

    Body content is left untouched even if a line begins with `name:` or
    `description:` (e.g. a fenced YAML example showing skill manifest syntax).
    """
    m = _FRONTMATTER_PATTERN.match(text)
    if not m:
        return text
    fm_open, fm_body, fm_close = m.groups()
    rest = text[m.end():]

    fm_body = re.sub(r"^name:.*$", f"name: {name}", fm_body, flags=re.MULTILINE)
    fm_body = re.sub(
        r"^description:.*?(?=^\S|\Z)",
        f'description: "{description}"\n',
        fm_body,
        flags=re.MULTILINE | re.DOTALL,
    )
    # Strip the trailing newline left by the description replacement so the
    # rebuilt document doesn't gain a blank line before the closing fence.
    fm_body = fm_body.rstrip("\n")

    return f"{fm_open}{fm_body}{fm_close}{rest}"


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


def strip_frontmatter(text: str) -> str:
    """Return *text* with any leading YAML frontmatter (--- ... ---) removed.

    Tolerates CRLF line endings and a closing ``---`` at EOF without a
    trailing newline — both are valid frontmatter variants and should be
    stripped from bundled methodology files.
    """
    m = _FRONTMATTER_STRIP_PATTERN.match(text)
    if not m:
        return text
    return text[m.end():].lstrip("\n")


def build_standalone_skill_zip(  # noqa: C901  # marker-transform + zip assembly; ccn 17, ratchet target
    skill_source_dir: Path,
    out_zip: Path,
    standalone_name: str,
    standalone_description: str,
    replacements: dict[str, str],
    exclude_dirs: frozenset[str] = frozenset({"tests"}),
    bundle_files: dict[str, Path] | None = None,
) -> list[str]:
    """
    Transform *skill_source_dir* and write a standalone ZIP to *out_zip*.

    Returns validation issues; empty list means success. ZIP is not written if
    there are issues.

    ``REQUIRED_EXCLUDES`` (``__pycache__``, ``.pytest_cache``, ``.venv``) are
    always excluded in addition to *exclude_dirs* — callers cannot opt out of
    these tooling artefacts.

    ``bundle_files`` maps destination paths (relative to the skill root inside
    the ZIP) to source ``Path`` objects somewhere on disk. Markdown bundled
    files have their YAML frontmatter stripped so the body reads as plain
    methodology rather than Claude-Code-agent metadata.
    """
    effective_excludes = exclude_dirs | REQUIRED_EXCLUDES
    staging = out_zip.parent / f"_staging_{standalone_name}"
    if staging.exists():
        shutil.rmtree(staging)

    target_root = staging / standalone_name
    target_root.mkdir(parents=True)

    for src in skill_source_dir.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(skill_source_dir)
        if any(part in effective_excludes for part in rel.parts):
            continue
        dest = target_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".md":
            dest.write_text(_transform_md(src.read_text("utf-8"), replacements), "utf-8")
        else:
            shutil.copy2(src, dest)

    if bundle_files:
        for rel_dest, src_path in bundle_files.items():
            # Guard against config errors that would write outside the staging
            # skill root: reject absolute paths and any '..' segments.
            dest_path = Path(rel_dest)
            if dest_path.is_absolute() or ".." in dest_path.parts:
                raise ValueError(
                    f"bundle_files dest must be a relative path within the skill "
                    f"root (got {rel_dest!r}); absolute paths and '..' segments are rejected"
                )
            if not src_path.exists():
                raise FileNotFoundError(
                    f"bundle_files source missing: {src_path} (for {rel_dest})"
                )
            dest = target_root / dest_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src_path.suffix == ".md":
                # Strip frontmatter, then apply the same marker transform the
                # skill's own .md files get. A bundled sub-skill can carry
                # chat-skip / chat-replace markers (e.g. the assess sub-skills'
                # plugin-only script paths); without transforming them here they
                # would survive into the ZIP and trip check_orphan_markers.
                body = strip_frontmatter(src_path.read_text("utf-8"))
                dest.write_text(_transform_md(body, replacements), "utf-8")
            else:
                shutil.copy2(src_path, dest)

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
