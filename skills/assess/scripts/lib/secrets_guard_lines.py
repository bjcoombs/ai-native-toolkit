"""Line helpers for the secrets-guard readers (#1173, #1204): comments, YAML blocks and shell quoting.

``lib.secrets_guard``, ``lib.secrets_guard_hooks`` and ``lib.secrets_guard_gitlab`` read CI and
pre-commit configuration line by line, with no YAML parser. This module holds what they share:

- ``SECRET_TOOLS`` and ``tools_in(text)``: the secrets scanners a hook, an action or a command names.
- ``code_lines(text)``: the lines with comments removed (a whole-line comment as an empty line so
  line numbers hold) and a shell command continued with a trailing backslash joined onto its first
  line.
- ``block_end`` / ``list_block_end``: the index after the YAML block that opens on a line.
- ``shell_text(line)`` (#1204): a CI line as the shell reads it. A YAML-quoted value after
  ``run:``, ``script:`` or ``- `` (``- 'gitleaks detect'``) is unwrapped, and ``git secrets``,
  the git-subcommand form, is written ``git-secrets`` (the same length, so offsets hold).
- ``quoted_spans(text)`` / ``blank_quoted(text)`` (#1204): the shell's single- and double-quoted
  spans (and backslash escapes outside them), and the text with their insides blanked, so a
  ``|``, ``||`` or ``;`` inside quotes (``--log-opts="a|b"``, ``echo 'a; gitleaks detect'``) is
  not read as an operator. An unclosed quote blanks nothing after it, and a line that holds a YAML
  flow sequence (``script: ["a", "b | c"]``) is not blanked at all: its quotes are YAML's, not the
  shell's, so the operators inside still count.

Inward-only: no ``lib`` import. Tests: ``tests/test_secrets_guard.py``,
``tests/test_secrets_guard_resolved.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

# The secrets scanners a hook, an action or a command can name.
SECRET_TOOLS = ("gitleaks", "detect-secrets", "trufflehog", "ggshield", "talisman", "secretlint",
                "git-secrets", "ripsecrets")
_TOOL = re.compile(r"(?<![\w-])(" + "|".join(re.escape(t) for t in SECRET_TOOLS) + r")(?![\w-])", re.IGNORECASE)
COMMENT = re.compile(r"^\s*#")
# A trailing `` #`` comment outside quotes (``echo "build #1"`` keeps its ``#``).
TRAILING_COMMENT = re.compile(r"""^((?:[^'"#]|'[^']*'|"[^"]*"|(?<!\s)#)*?)\s+#.*$""")
STEP_START = re.compile(r"^(\s*)-\s")
# A YAML-quoted scalar as a whole value: ``- 'x'``, ``run: "x"``, ``script: 'x'``.
_YAML_QUOTED = re.compile(r"""^(\s*(?:-\s+)?(?:(?:run|script|command)\s*:\s*)?)(['"])(.*)\2\s*$""")
_FLOW = re.compile(r"""^\s*(?:-\s+)?(?:[\w-]+\s*:\s*)?\[""")
_GIT_SECRETS = re.compile(r"\bgit secrets\b")


def read_text(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def tools_in(text: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _TOOL.finditer(text)})


def code_lines(text: str) -> list[str]:
    """The lines with comments removed, a whole-line comment as an empty line so line numbers hold."""
    lines = ["" if COMMENT.match(line) else TRAILING_COMMENT.sub(r"\1", line) for line in text.splitlines()]
    # A shell command continued with a trailing backslash is judged whole: join it onto its first
    # line and leave the continued lines empty, so masks and line numbers hold.
    for i in range(len(lines)):
        j = i
        while lines[i].rstrip().endswith("\\") and j + 1 < len(lines):
            j += 1
            lines[i] = lines[i].rstrip()[:-1].rstrip() + " " + lines[j].strip()
            lines[j] = ""
    return lines


def list_block_end(lines: list[str], start: int, indent: int) -> int:
    """As ``block_end``, but a list item at the key's own indent (``after_script:`` then ``- x`` with
    no indent, valid YAML) stays in the block."""
    for i in range(start + 1, len(lines)):
        line = lines[i]
        ind = len(line) - len(line.lstrip())
        if line.strip() and (ind < indent or (ind == indent and not STEP_START.match(line))):
            return i
    return len(lines)


def block_end(lines: list[str], start: int, indent: int) -> int:
    """The index after the YAML block that opens at ``start`` with ``indent`` (its list item or key)."""
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            return i
    return len(lines)


def shell_text(line: str) -> str:
    """``line`` as the shell reads it: a whole YAML-quoted value unwrapped, ``git secrets`` as
    ``git-secrets``."""
    m = _YAML_QUOTED.match(line)
    if m:
        quote, body = m.group(2), m.group(3)
        if quote == "'" and "'" not in body.replace("''", ""):
            line = m.group(1) + body.replace("''", "'")
        elif quote == '"' and '"' not in body.replace('\\"', ""):
            line = m.group(1) + body.replace('\\"', '"')
    return _GIT_SECRETS.sub("git-secrets", line)


def quoted_spans(text: str) -> list[tuple[int, int]]:
    """The ``[start, end)`` offsets of each closed shell quote's inside and each escaped character."""
    if _FLOW.match(text):
        return []
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            spans.append((i + 1, i + 2))
            i += 2
        elif c in "'\"":
            j = i + 1
            while j < len(text) and text[j] != c:
                j += 2 if c == '"' and text[j] == "\\" else 1
            if j >= len(text):
                return spans  # unclosed: the rest reads as written
            spans.append((i + 1, j))
            i = j + 1
        else:
            i += 1
    return spans


def blank_quoted(text: str) -> str:
    """``text`` with each quoted span's inside replaced by ``_`` (offsets kept)."""
    chars = list(text)
    for start, end in quoted_spans(text):
        for k in range(start, min(end, len(chars))):
            chars[k] = "_"
    return "".join(chars)


def in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)
