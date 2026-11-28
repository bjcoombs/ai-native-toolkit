"""Every `jq -r` capture in the skill docs strips a trailing CR (#1496).

On Windows, Git Bash can run a native `jq.exe` that ends each line of raw
output with CRLF. Command substitution removes the LF but keeps the CR, so a
captured SHA, slug or id carries a trailing `\\r` into the next URL or
comparison, and `read` with the default IFS keeps it too. The skill docs pipe
each capture through `tr -d '\\r'`.

This test scans each `skills/*/SKILL.md` and `skills/*/references/**/*.md` for
a `$(...)` or `<(...)` whose body runs `jq -r` (or `--raw-output`), and for a
`jq -r ... | while read` loop, and fails on any that does not run
`tr -d '\\r'` after the last raw `jq`. A scan catches a capture added later,
which a one-off CRLF fixture would not.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STRIP = "tr -d '\\r'"
JQ_WORD = re.compile(r"(?<![\w.-])jq(?![\w.-])")
RAW_FLAG = re.compile(r"-[a-qs-zA-Z]*r[a-zA-Z]*|--raw-output")
WHILE_READ = re.compile(r"\|\s*while\s+(?:IFS=\S*\s+)?read\b")


def _skill_docs() -> list[Path]:
    skills = REPO / "skills"
    docs = sorted(skills.glob("*/SKILL.md"))
    docs += sorted(skills.glob("*/references/**/*.md"))
    return docs


def _close(text: str, i: int) -> int | None:
    """Index just past the `)` closing the substitution whose body starts at `i`."""
    depth, dq, n = 1, False, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "'" and not dq:
            end = text.find("'", i + 1)
            if end < 0:
                return None
            i = end + 1
            continue
        if c == '"':
            dq = not dq
        elif text.startswith("$(", i) or (not dq and text.startswith("<(", i)):
            inner = _close(text, i + 2)
            if inner is None:
                return None
            i = inner
            continue
        elif not dq and c == "(":
            depth += 1
        elif not dq and c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _jq_command_end(text: str, i: int) -> tuple[int, bool]:
    """End of the `jq` command whose arguments start at `i`, and whether it is raw.

    Walks shell words up to an unquoted `|`, `)`, `;`, `&` or newline, skipping
    quoted spans, so a `-r` written after a filter that holds `|` or `)` counts.
    """
    raw, n = False, len(text)
    while i < n:
        while i < n and text[i] in " \t":
            i += 1
        if i >= n or text[i] in "|);&\n":
            break
        word, start = [], i
        while i < n and text[i] not in " \t|);&\n":
            c = text[i]
            if c in "'\"":
                end = text.find(c, i + 1)
                i = n if end < 0 else end + 1
                word.append("'")
                continue
            if c == "\\":
                i += 2
                continue
            word.append(c)
            i += 1
        if RAW_FLAG.fullmatch(text[start:i]) and "'" not in word:
            raw = True
    return i, raw


def _unstripped(body: str) -> bool:
    last = -1
    for m in JQ_WORD.finditer(body):
        end, raw = _jq_command_end(body, m.end())
        if raw:
            last = end
    return last >= 0 and STRIP not in body[last:]


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Lines joined across a trailing backslash or a leading pipe."""
    out: list[tuple[int, str]] = []
    lines = text.splitlines()
    k = 0
    while k < len(lines):
        start, cur = k + 1, lines[k]
        while k + 1 < len(lines) and (
            cur.rstrip().endswith("\\") or lines[k + 1].lstrip().startswith("|")
        ):
            k += 1
            cur = cur.rstrip().rstrip("\\") + " " + lines[k].strip()
        out.append((start, cur))
        k += 1
    return out


def find_unstripped(text: str) -> list[int]:
    """Line numbers of `jq -r` captures and read loops that keep a CR."""
    hits: set[int] = set()
    for m in re.finditer(r"[$<]\((?!\()", text):
        end = _close(text, m.end())
        if end is not None and _unstripped(text[m.end():end]):
            hits.add(text.count("\n", 0, m.start()) + 1)
    for line_no, line in _logical_lines(text):
        loop = WHILE_READ.search(line)
        if loop and _unstripped(line[: loop.start()]):
            hits.add(line_no)
    return sorted(hits)


@pytest.mark.parametrize(
    "snippet",
    [
        "SHA=$(gh pr view 1 --json headRefOid | jq -r '.headRefOid')",
        "X=$(gh api foo \\\n  | jq -r -s '[.[].a] | min // empty')",
        "for PR in $(jq -r '.tasks[] | .pr' \"$F\"); do",
        "echo \"$R\" | jq -r '.[].id' | while read ID; do",
        "done < <(jq -r '.[]' \"$F\")",
        "case \"$(jq -r '.op' <<<\"$w\")\" in",
        "V=$(jq --raw-output .a f)",
        "V=$(gh api x | jq '.a | .b' -r)",
        "V=$(jq 'select(.x)' -r f)",
        "L=$(gh api user | jq -r '.login' | tr '[:upper:]' '[:lower:]')",
    ],
)
def test_scan_flags_an_unstripped_capture(snippet: str) -> None:
    assert find_unstripped(snippet) == [1]


@pytest.mark.parametrize(
    "snippet",
    [
        "SHA=$(gh pr view 1 --json headRefOid | jq -r '.headRefOid' | tr -d '\\r')",
        "X=$(gh api foo \\\n  | jq -r -s '[.[].a] | min // empty' | tr -d '\\r')",
        "echo \"$R\" | jq -r '.[].id' | tr -d '\\r' | while read ID; do",
        "N=$(jq '.a | length' f)",
        "jq -r '.path' run-context.json",
        "echo $(( 1 + 2 ))",
        "V=$(jq '.a | \"-r\"' f)",
        "V=$(jq-tool -r f)",
        "L=$(gh api user | jq -r '.login' | tr -d '\\r' | tr '[:upper:]' '[:lower:]')",
    ],
)
def test_scan_passes_a_stripped_or_unrelated_line(snippet: str) -> None:
    assert find_unstripped(snippet) == []


def test_scan_covers_the_skill_docs() -> None:
    names = {p.relative_to(REPO).as_posix() for p in _skill_docs()}
    assert "skills/pr-review-merge/SKILL.md" in names
    assert "skills/assess-pr/references/offer-pr-checks.md" in names


def test_every_jq_r_capture_strips_cr() -> None:
    offenders = [
        f"{p.relative_to(REPO).as_posix()}:{n}"
        for p in _skill_docs()
        for n in find_unstripped(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "jq -r capture without tr -d '\\r' (a Windows jq.exe ends lines with CRLF; "
        "see the Shell Pitfalls in skills/pr-review-merge/SKILL.md): " + ", ".join(offenders)
    )
