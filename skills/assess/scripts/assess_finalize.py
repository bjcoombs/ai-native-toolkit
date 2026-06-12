"""LLM write-back for /assess: fill placeholders left by the deterministic core.

The deterministic core writes log.md and hotspots/*.md with placeholders for
LLM-derived content (score, maturity label, top action, per-hotspot actions).
After the LLM writes assess-report.md, it also writes finalize-input.json with
its derived values and invokes this script to update the wiki files in place.

Reads (in order; first hit wins):
    {repo_root}/.assess/.cache/finalize-input.json  (preferred - transient cache)
    {repo_root}/.assess/finalize-input.json         (legacy - written to working tree)

The input file is **consumed and deleted** on success. It carries no future
utility past the run that produced it, and leaving it in the working tree
caused noisy diffs when users committed `.assess/` (issue #39).

Updates:
    {repo_root}/.assess/log.md           (last entry's placeholders)
    {repo_root}/.assess/hotspots/*.md    (Suggested actions sections)

Writes (when the input carries an ``actions`` array):
    {repo_root}/.assess/actions.json     (durable machine-readable Top 3
                                          action contract for executor agents)

Run:
    uv run assess_finalize.py <repo_root>
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


# Make sibling lib package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.wiki_writer import slug_for_path


def _finalize_log(assess_dir: Path, *, score: float, maturity_label: str, top_action: str) -> None:
    """Replace placeholders in the latest log.md entry.

    Only the most recent entry (top of file after the heading) is updated.
    Prior entries are immutable historical records.
    """
    log_path = assess_dir / "log.md"
    if not log_path.exists():
        return

    text = log_path.read_text(encoding="utf-8")
    # Replace the LAST occurrence of each placeholder - log entries are appended
    # (newest at the bottom of the file), and we want to finalize the latest run.
    # An unfinalized older entry stays untouched as historical evidence.
    text = _replace_last(
        text,
        pattern=r"\*\*AI Readiness:\*\* [\d.]+ / 8 \(\(LLM fills in\)\)",
        replacement=f"**AI Readiness:** {score} / 8 ({maturity_label})",
    )
    text = _replace_last(
        text,
        pattern=r"\*\*Top action:\*\* Deterministic ranker not yet wired \(LLM picks Top 3\)",
        replacement=f"**Top action:** {top_action}",
    )
    log_path.write_text(text, encoding="utf-8")


def _replace_last(text: str, *, pattern: str, replacement: str) -> str:
    """Replace the LAST occurrence of pattern in text with replacement (literal).

    Uses slicing rather than re.sub so the replacement is a literal string,
    not subject to backreference interpretation.
    """
    matches = list(re.finditer(pattern, text))
    if not matches:
        return text
    last = matches[-1]
    return text[:last.start()] + replacement + text[last.end():]


def _finalize_hotspot_actions(assess_dir: Path, *, hotspot_actions: dict[str, list[str]]) -> None:
    """Rewrite the 'Suggested actions' section of each hotspot page.

    Pages whose paths are absent from hotspot_actions are left as-is. Paths in
    hotspot_actions whose pages don't exist are silently skipped (lifecycle:
    a hotspot might have graduated between LLM read and finalize).
    """
    hotspots_dir = assess_dir / "hotspots"
    if not hotspots_dir.exists():
        return

    for path, actions in hotspot_actions.items():
        slug = slug_for_path(path)
        page_path = hotspots_dir / f"{slug}.md"
        if not page_path.exists():
            continue

        page_text = page_path.read_text(encoding="utf-8")
        actions_block = "\n".join(f"- {a}" for a in actions) if actions else "- (no actions)"
        # Replace the section content between "## Suggested actions" and end-of-file (or next ## heading)
        new_text = re.sub(
            r"(## Suggested actions\s*\n\s*\n).*?(\n##\s|$)",
            lambda m: m.group(1) + actions_block + "\n" + m.group(2),
            page_text,
            count=1,
            flags=re.DOTALL,
        )
        page_path.write_text(new_text, encoding="utf-8")


# Keys every action contract entry must carry. The executor-critical pair is
# done_when (the exit criterion - without it a weak model doesn't know when to
# stop) and scope_fence (what NOT to touch - without it a weak model
# over-extends). Entries missing required keys are dropped with a warning
# rather than failing the run: a partial contract beats none, but a malformed
# entry must not reach an executor as if it were complete.
ACTION_REQUIRED_KEYS = {"rank", "action", "done_when", "scope_fence"}


def _write_actions_contract(assess_dir: Path, actions: list[dict]) -> None:
    """Write the durable machine-readable Top 3 contract to actions.json.

    Unlike finalize-input.json (consumed and deleted - transient by design),
    actions.json persists: it is the artifact an executing agent reads to know
    what to do, how to verify it, and where to stop, without parsing the
    report's markdown table.
    """
    valid = []
    for a in actions:
        if not isinstance(a, dict) or not ACTION_REQUIRED_KEYS <= set(a):
            missing = ACTION_REQUIRED_KEYS - set(a) if isinstance(a, dict) else ACTION_REQUIRED_KEYS
            print(
                f"actions.json: dropping malformed entry (missing {sorted(missing)})",
                file=sys.stderr,
            )
            continue
        valid.append(a)
    if not valid:
        return
    payload = {"schema": 1, "actions": sorted(valid, key=lambda a: a["rank"])}
    (assess_dir / "actions.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _locate_input(assess_dir: Path) -> Path:
    """Find finalize-input.json. Prefer the transient cache location; fall back
    to the legacy in-tree path for backwards compatibility.
    """
    cache_path = assess_dir / ".cache" / "finalize-input.json"
    if cache_path.exists():
        return cache_path
    legacy = assess_dir / "finalize-input.json"
    if legacy.exists():
        return legacy
    raise FileNotFoundError(
        f"finalize-input.json not found at {cache_path} or {legacy}"
    )


def finalize_run(*, assess_dir: Path) -> None:
    """Read finalize-input.json, apply it to log.md and hotspot pages, then
    delete the input file (one-off, no future utility).
    """
    input_path = _locate_input(assess_dir)
    data = json.loads(input_path.read_text(encoding="utf-8"))
    _finalize_log(
        assess_dir,
        score=data["score"],
        maturity_label=data["maturity_label"],
        top_action=data["top_action"],
    )
    _finalize_hotspot_actions(assess_dir, hotspot_actions=data.get("hotspot_actions", {}))
    actions = data.get("actions")
    if isinstance(actions, list) and actions:
        _write_actions_contract(assess_dir, actions)
    # Clean up: the input file is consumed - delete from both the cache and
    # the legacy in-tree location so a stale copy can't leak into a commit.
    try:
        input_path.unlink()
    except OSError:
        pass
    legacy = assess_dir / "finalize-input.json"
    if legacy != input_path and legacy.exists():
        try:
            legacy.unlink()
        except OSError:
            pass


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: assess_finalize.py <repo_root>", file=sys.stderr)
        return 2
    repo_root = Path(sys.argv[1]).resolve()
    assess_dir = repo_root / ".assess"
    finalize_run(assess_dir=assess_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
