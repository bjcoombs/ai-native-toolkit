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
  ``credential`` (a ``password=``/``token=``/``api_key=`` assignment with a concrete value;
  checked again here so a value that only refers to a secret held elsewhere, such as
  ``${{ secrets.X }}``, ``$TOKEN``, ``process.env.X`` or ``os.environ[...]``, is not a finding),
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
  number with a ``+`` country code, 8 to 15 digits and a space, dash or bracket between them (not
  a timezone offset before a date, a decimal or a bare count such as ``+1000000000``), or
  the ``(NNN) NNN-NNNN`` form.

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
from typing import NotRequired, TypedDict

from lib.assess_config import is_user_excluded
from lib.agent_instructions_grader import CREDENTIAL_PLACEHOLDERS, scan_sensitive_content
from lib.instruction_placement import skill_and_agent_files

GRADER_KINDS = ("private_key", "cloud_key", "credential", "ssh_or_host", "ip_address", "home_path")
KINDS = (*GRADER_KINDS, "email", "phone")
REFERENCE_SUFFIXES = frozenset({".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ""})
_READ_MAX_BYTES = 1_000_000
REASON_UNREAD = "skill_files_unread"

_EMAIL = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})(?![\w-])")
_RESERVED_DOMAIN = re.compile(r"(?:^|\.)(?:example\.(?:com|org|net)|example|test|invalid|localhost|local)$",
                              re.IGNORECASE)
_NOREPLY_DOMAIN = re.compile(r"(?:^|\.)(?:no-?reply|donotreply)(?:\.|$)", re.IGNORECASE)
_PLACEHOLDER_LOCAL = frozenset({"user", "username", "you", "your", "yourname", "name", "first.last",
                                "firstname.lastname", "me", "someone", "somebody", "email", "foo",
                                "bar", "test", "noreply", "no-reply", "donotreply", "git",
                                # role addresses: a team's published contact, not a person's
                                "support", "security", "help", "info", "contact", "privacy", "abuse",
                                "admin", "hello", "team", "sales", "legal", "press", "feedback",
                                "postmaster", "webmaster", "hostmaster", "careers", "jobs", "billing"})
# A home path whose user name holds a letter or digit and is not a placeholder: the grader's
# pattern also takes ``/Users/.../``, which a skill writes as an example, so it is checked again.
# The Windows branch takes the grader's class (any name up to the next separator, spaces and
# accented letters included), less a leading dot so ``C:\Users\...\`` stays a placeholder.
_HOME = re.compile(r"(?:/Users/|/home/)([A-Za-z0-9][A-Za-z0-9._-]*)/|[A-Za-z]:\\Users\\([^\\/\r\n.][^\\/\r\n]*)\\")
_PLACEHOLDER_HOME = frozenset({"user", "username", "you", "your", "yourname", "name", "me", "shared",
                               "public", "default", "runner", "someone", "example", "jdoe", "alice", "bob"})
# An IPv4-shaped number that follows a version word is a version, not an address (``v1.2.3.4``,
# ``version 10.0.19041.1``); the grader's IP check is decided again here without those, and with
# a lookahead that also takes an address ending a sentence (``at 10.20.30.40.``). A version pin
# before it (``foo==1.2.3.4``, ``~=``, ``>=``) is a version. A plain ``=`` or a
# ``user@`` before an address is not: ``DB_HOST=10.20.30.40`` and ``deploy@10.1.2.3`` are findings.
_IPV4 = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\w|\.\d)")
# A netmask (``255.255.255.0``) and a CIDR range (``10.0.0.0/8``) name no host, so neither counts.
# RFC 5737's documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24): example
# addresses, as ``example.com`` is for email.
_DOC_RANGE = re.compile(r"^(?:192\.0\.2|198\.51\.100|203\.0\.113)\.")
# An ``ssh git@host`` test of a Git host's auth is no login detail, nor is a login on a reserved
# domain (``deploy@example.com``, as for email); any other user and host is.
_SSH = re.compile(r"\bssh\s+([A-Za-z0-9._-]+)@([A-Za-z0-9._-]+)")
_ROOT_LOGIN = re.compile(r"\broot@([A-Za-z0-9._-]+)")
_VERSION_BEFORE = re.compile(r"(?:(?:\bv|\bversion|\brelease|\bbuild)\s*[:=]?|[=~!<>]=)\s*$", re.IGNORECASE)
# A credential assignment, as the grader reads it, re-checked here: a value that only refers to a
# secret held elsewhere (``${{ secrets.X }}``, ``$TOKEN``, ``process.env.X``, ``os.environ[...]``) is
# not a committed secret, and telling a team to rotate it would be wrong.
_CREDENTIAL = re.compile(r"\b(?:password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|token)\b"
                         r"\s*[:=]\s*(\"[^\"]*\"|'[^']*'|\S+)", re.IGNORECASE)
_REFERENCE = re.compile(r"^(?:\$\{?\{?|\$\w|%\w+%|\{\{|<|process\.env\b|os\.environ\b|os\.getenv\b|getenv\b|"
                        r"env\.|env\[|ENV\[|secrets\.|vars\.|System\.getenv\b|Environment\.|import\.meta\.env\b)",
                        re.IGNORECASE)
# A type annotation or schema field, not a value: ``token: str``, ``api_key: Optional[str] = None``,
# ``secret: true``, ``password: required``.
_SCHEMA_VALUE = re.compile(
    r"^(?:(?:str|string|bytes|int|integer|number|float|bool|boolean|true|false|yes|no|required|optional|"
    r"SecretStr|list|dict|object|any|unknown)(?![\w-])|"
    r"(?:Optional|Union|List|Dict|Annotated|Literal)\[)", re.IGNORECASE)
# A date inside an international-number match: a timezone offset then a date, not a phone number.
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# A file name, not a mail domain: a retina asset (``logo@2x.png``) or any name ending in a file
# extension (``icon@3x.webp``).
_ASSET_DOMAIN = re.compile(r"^\d+x\b|\.(?:png|jpe?g|gif|svg|webp|avif|ico|pdf)$", re.IGNORECASE)
# A number, not a phone: a decimal (``+0.5``) or a run of digits with no separator (``+1000000000``).
_DECIMAL = re.compile(r"\.\d")
_SEPARATOR = re.compile(r"[ (-]")
_PHONE_INTL = re.compile(r"(?<![\w+])\+\d[\d .()-]{6,20}\d(?![\w])")
_PHONE_NANP = re.compile(r"(?<!\w)\(\d{3}\)\s?\d{3}-\d{4}(?!\w)")


class PersonalDataFile(TypedDict):
    path: str
    kinds: list[str]  # from KINDS, in KINDS order


class SkillPersonalDataBlock(TypedDict):
    """``run-context.json`` ``skill_personal_data`` (see the module doc)."""

    available: bool  # false (skill_files_unread): a file could not be read and none read holds a kind
    reason: NotRequired[str]
    files: list[PersonalDataFile]
    files_considered: int
    files_unread: int
    files_with_personal_data: int
    kinds_detected: list[str]


def _has_email(text: str) -> bool:
    for m in _EMAIL.finditer(text):
        local, domain = m.group(1).lower(), m.group(2).lower()
        if _RESERVED_DOMAIN.search(domain) or _NOREPLY_DOMAIN.search(domain) or local in _PLACEHOLDER_LOCAL:
            continue
        if local.startswith(("noreply", "no-reply")) or _ASSET_DOMAIN.search(domain):
            continue
        return True
    return False


def _has_phone(text: str) -> bool:
    if _PHONE_NANP.search(text):
        return True
    return any(8 <= sum(c.isdigit() for c in m.group(0)) <= 15 and not _DATE.search(m.group(0))
               and not _DECIMAL.search(m.group(0)) and _SEPARATOR.search(m.group(0)[1:])
               for m in _PHONE_INTL.finditer(text))


def _literal_secret(value: str) -> bool:
    """Whether a credential value is a literal secret: not empty, not a placeholder the grader skips,
    and not a reference to a secret held elsewhere. Decided per value, so a placeholder beside a
    reference reads clean, as each does alone."""
    return (bool(value) and not CREDENTIAL_PLACEHOLDERS.match(value) and not _REFERENCE.match(value)
            and not _SCHEMA_VALUE.match(value))


def kinds_in(text: str) -> list[str]:
    """The ``KINDS`` found in ``text``, in ``KINDS`` order; the data itself is never returned."""
    found = {f["category"] for f in scan_sensitive_content(text)}
    if "home_path" in found and not any((m.group(1) or m.group(2)).lower() not in _PLACEHOLDER_HOME
                                         for m in _HOME.finditer(text)):
        found.discard("home_path")
    if "credential" in found and not any(_literal_secret(m.group(1).strip("\"'")) for m in _CREDENTIAL.finditer(text)):
        found.discard("credential")
    found.discard("ip_address")
    if any(not _VERSION_BEFORE.search(text[max(0, m.start() - 12):m.start()])
           for m in _IPV4.finditer(text) if all(int(g) <= 255 for g in m.groups())
           and m.group(1) != "127" and m.group(0) != "0.0.0.0" and not _DOC_RANGE.match(m.group(0))
           and m.group(1) != "255" and not text.startswith("/", m.end())):  # a netmask or a CIDR range
        found.add("ip_address")
    if "ssh_or_host" in found and not any(not _RESERVED_DOMAIN.search(m.group(1).lower())
                                          for m in _ROOT_LOGIN.finditer(text)) \
            and all(m.group(1).lower() == "git" or _RESERVED_DOMAIN.search(m.group(2).lower())
                    for m in _SSH.finditer(text)):
        found.discard("ssh_or_host")
    if _has_email(text):
        found.add("email")
    if _has_phone(text):
        found.add("phone")
    return [k for k in KINDS if k in found]


def _files(repo: Path, scope: Path | None, excludes: tuple[set[str], list[str]] | None) -> list[Path]:
    folder = scope.resolve() if scope is not None else repo
    out: set[Path] = set()
    dirs, pats = excludes or (set(), [])
    for path, kind in skill_and_agent_files(repo, None, excludes):
        path = path.resolve()
        if not path.is_relative_to(folder):
            continue
        out.add(path)
        refs = path.parent / "references"
        if kind == "skill" and refs.is_dir():
            # A symlinked references/ folder can point outside the repository: keep only what resolves
            # inside it, as for the skill file above.
            # A target outside the scope (another skill's references/) or a user exclusion is left out.
            out.update(r for r in (p.resolve() for p in refs.rglob("*")
                                   if p.is_file() and not p.is_symlink() and p.suffix.lower() in REFERENCE_SUFFIXES)
                       if r.is_relative_to(repo) and r.is_relative_to(folder)
                       and not is_user_excluded(r.relative_to(repo), dirs, pats))
    return sorted(out)


def scan_skill_personal_data(
    repo_root: Path, scope: Path | None = None, excludes: tuple[set[str], list[str]] | None = None,
) -> SkillPersonalDataBlock:
    """Build the ``skill_personal_data`` block (see the module doc)."""
    repo = repo_root.resolve()
    considered = unread = 0
    flagged: list[PersonalDataFile] = []
    for path in _files(repo, scope, excludes):
        try:  # a file over the limit is read up to it, not skipped
            with path.open("rb") as handle:
                text = handle.read(_READ_MAX_BYTES).decode("utf-8", errors="replace")
        except OSError:
            unread += 1
            continue
        considered += 1
        kinds = kinds_in(text)
        if kinds:
            flagged.append(PersonalDataFile(path=path.relative_to(repo).as_posix(), kinds=kinds))
    block = SkillPersonalDataBlock(available=True, files=flagged, files_considered=considered,
                                   files_unread=unread, files_with_personal_data=len(flagged),
                                   kinds_detected=list(KINDS))
    if unread and not flagged:  # a clean reading would claim files it never read
        block["available"] = False
        block["reason"] = (f"{REASON_UNREAD}: {unread} skill or agent file(s) could not be read, and none "
                           "of those read holds a detected kind")
    return block
