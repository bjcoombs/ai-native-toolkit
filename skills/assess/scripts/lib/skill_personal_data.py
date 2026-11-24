"""Personal data in skill and agent text: the file and the kind found, never the data (#1045).

Skill text is loaded into every session that triggers it and copied wherever the skill is
published, so a name, an address or a key in it leaks to every user. This module reads each
``SKILL.md``, each agent definition and the files in each skill's ``references/`` folder (the
files ``instruction_placement.skill_and_agent_files`` reads, plus their references) and records,
per file, the **kinds** of personal or sensitive data found. No model is involved.

The tendency it compensates for is accretion: a maintainer's own address or a pasted login ends
up in a skill once and ships in every copy after.

**The kinds detected** (``KINDS``). Precision first: each is a high-precision pattern, so a
"met" reading means none of these kinds was found, not that the text holds no personal data of
any kind. Personal names are not detected: a name pattern would flag every author credit and
example, and the noise would bury real findings.

- From ``agent_instructions_grader.scan_sensitive_content`` (the instruction-file scan):
  ``private_key`` (a PEM private key block), ``cloud_key`` (an AWS-style access key id),
  ``credential`` (a ``password=``/``token=``/``api_key=`` assignment with a concrete value),
  ``ssh_or_host`` (``root@host`` or ``ssh user@host``), ``ip_address`` (a routable or private
  IPv4 literal, loopback left out, and none that follows a version word such as ``v`` or
  ``version``) and ``home_path`` (a personal ``/Users/<name>/``,
  ``/home/<name>/`` or ``C:\\Users\\<name>\\`` path; checked again here so a placeholder such
  as ``/Users/.../`` or ``/home/user/`` is not a finding).
- Added here: ``email``, an address whose domain is not a reserved example, test or local
  domain (``example.com``, ``.example``, ``.test``, ``.invalid``, ``localhost``), not a
  no-reply address (``noreply@``, ``no-reply@``, or a ``noreply`` label in the domain, as in
  ``users.noreply.github.com``) and whose local
  part is not a placeholder (``user``, ``you``, ``name``, ``someone``, ...); and ``phone``, a
  number with a ``+`` country code and 8 to 15 digits, or the ``(NNN) NNN-NNNN`` form.

A finding names the file and the kind, never the data: no matched text, not even redacted,
reaches the block. Counts-only output drops the path, or hashes it under ``--hash-paths``, and
keeps the kinds as tokens.

Block (``skill_personal_data`` in run-context.json)::

    {"available": True,
     "files": [{"path": str, "kinds": [kind, ...]}],  # only the files with a finding
     "files_considered": int, "files_with_personal_data": int,
     "kinds_detected": [kind, ...]}                    # KINDS, so a reader sees what "met" covers

With ``scope`` (a skill unit's folder in the unit pass) only the files under it are read. With no
skill or agent file, ``files_considered`` is 0 and the condition reads not measured
(``no_skill_files``), never met.

Skill personal data (#1045), no model. `scan_skill_personal_data(repo_root, scope, excludes)` reads
each skill and agent file and its skill's `references/` files and records per file the `KINDS`
found: `scan_sensitive_content`'s six plus `email` (not reserved, no-reply or placeholder) and
`phone` (`+` country code, or `(NNN) NNN-NNNN`); the file and kind only, never the text. Personal
names are not detected. Tests: `tests/test_skill_personal_data.py`.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

from lib.agent_instructions_grader import scan_sensitive_content
from lib.instruction_placement import skill_and_agent_files

GRADER_KINDS = ("private_key", "cloud_key", "credential", "ssh_or_host", "ip_address", "home_path")
KINDS = (*GRADER_KINDS, "email", "phone")
REFERENCE_SUFFIXES = frozenset({".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ""})
_READ_MAX_BYTES = 1_000_000

_EMAIL = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})(?![\w-])")
_RESERVED_DOMAIN = re.compile(r"(?:^|\.)(?:example\.(?:com|org|net)|example|test|invalid|localhost|local)$",
                              re.IGNORECASE)
_NOREPLY_DOMAIN = re.compile(r"(?:^|\.)(?:no-?reply|donotreply)(?:\.|$)", re.IGNORECASE)
_PLACEHOLDER_LOCAL = frozenset({"user", "username", "you", "your", "yourname", "name", "first.last",
                                "firstname.lastname", "me", "someone", "somebody", "email", "foo",
                                "bar", "test", "noreply", "no-reply", "donotreply", "git"})
# A home path whose user name holds a letter or digit and is not a placeholder: the grader's
# pattern also takes ``/Users/.../``, which a skill writes as an example, so it is checked again.
_HOME = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\Users\\)([A-Za-z0-9][A-Za-z0-9._-]*)[/\\]")
_PLACEHOLDER_HOME = frozenset({"user", "username", "you", "your", "yourname", "name", "me", "shared",
                               "public", "default", "runner", "someone", "example", "jdoe", "alice", "bob"})
# An IPv4-shaped number that follows a version word is a version, not an address (``v1.2.3.4``,
# ``version 10.0.19041.1``); the grader's IP check is re-run here without those.
_IPV4 = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w.])")
_VERSION_BEFORE = re.compile(r"(?:\bv|\bversion|\brelease|\bbuild)\s*[:=]?\s*$", re.IGNORECASE)
_PHONE_INTL = re.compile(r"(?<![\w+])\+\d[\d .()-]{6,20}\d(?![\w])")
_PHONE_NANP = re.compile(r"(?<!\w)\(\d{3}\)\s?\d{3}-\d{4}(?!\w)")


class PersonalDataFile(TypedDict):
    path: str
    kinds: list[str]  # from KINDS, in KINDS order


class SkillPersonalDataBlock(TypedDict):
    """``run-context.json`` ``skill_personal_data`` (see the module doc)."""

    available: bool
    files: list[PersonalDataFile]
    files_considered: int
    files_with_personal_data: int
    kinds_detected: list[str]


def _has_email(text: str) -> bool:
    for m in _EMAIL.finditer(text):
        local, domain = m.group(1).lower(), m.group(2).lower()
        if _RESERVED_DOMAIN.search(domain) or _NOREPLY_DOMAIN.search(domain) or local in _PLACEHOLDER_LOCAL:
            continue
        if local.startswith(("noreply", "no-reply")) or local.endswith("[bot]"):
            continue
        return True
    return False


def _has_phone(text: str) -> bool:
    if _PHONE_NANP.search(text):
        return True
    return any(8 <= sum(c.isdigit() for c in m.group(0)) <= 15 for m in _PHONE_INTL.finditer(text))


def kinds_in(text: str) -> list[str]:
    """The ``KINDS`` found in ``text``, in ``KINDS`` order; the data itself is never returned."""
    found = {f["category"] for f in scan_sensitive_content(text)}
    if "home_path" in found and not any(m.group(1).lower() not in _PLACEHOLDER_HOME for m in _HOME.finditer(text)):
        found.discard("home_path")
    if "ip_address" in found and all(_VERSION_BEFORE.search(text[max(0, m.start() - 12):m.start()])
                                     for m in _IPV4.finditer(text) if all(int(g) <= 255 for g in m.groups())
                                     and m.group(1) != "127" and m.group(0) != "0.0.0.0"):
        found.discard("ip_address")
    if _has_email(text):
        found.add("email")
    if _has_phone(text):
        found.add("phone")
    return [k for k in KINDS if k in found]


def _files(repo: Path, scope: Path | None, excludes: tuple[set[str], list[str]] | None) -> list[Path]:
    folder = scope.resolve() if scope is not None else repo
    out: set[Path] = set()
    for path, kind in skill_and_agent_files(repo, None, excludes):
        path = path.resolve()
        if not path.is_relative_to(folder):
            continue
        out.add(path)
        refs = path.parent / "references"
        if kind == "skill" and refs.is_dir():
            out.update(p.resolve() for p in refs.rglob("*")
                       if p.is_file() and not p.is_symlink() and p.suffix.lower() in REFERENCE_SUFFIXES)
    return sorted(out)


def scan_skill_personal_data(
    repo_root: Path, scope: Path | None = None, excludes: tuple[set[str], list[str]] | None = None,
) -> SkillPersonalDataBlock:
    """Build the ``skill_personal_data`` block (see the module doc)."""
    repo = repo_root.resolve()
    considered = 0
    flagged: list[PersonalDataFile] = []
    for path in _files(repo, scope, excludes):
        try:
            if path.stat().st_size > _READ_MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        considered += 1
        kinds = kinds_in(text)
        if kinds:
            flagged.append(PersonalDataFile(path=path.relative_to(repo).as_posix(), kinds=kinds))
    return SkillPersonalDataBlock(available=True, files=flagged, files_considered=considered,
                                  files_with_personal_data=len(flagged), kinds_detected=list(KINDS))
