"""LLM write-back for /assess: fill placeholders left by the deterministic core.

The deterministic core writes log.md and hotspots/*.md with placeholders for
LLM-derived content (score, maturity label, top action, per-hotspot actions).
After the LLM writes assess-report.md, it also writes finalize-input.json with
its derived values and invokes this script to update the wiki files in place.

Reads:
    {repo_root}/.assess/finalize-input.json

Updates:
    {repo_root}/.assess/log.md           (last entry's placeholders)
    {repo_root}/.assess/hotspots/*.md    (Suggested actions sections)

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
    # Replace the AI Readiness placeholder - only the first occurrence (latest entry)
    text = re.sub(
        r"\*\*AI Readiness:\*\* [\d.]+ / 7 \(\(LLM fills in\)\)",
        lambda _m: f"**AI Readiness:** {score} / 7 ({maturity_label})",
        text,
        count=1,
    )
    text = re.sub(
        r"\*\*Top action:\*\* Deterministic ranker not yet wired \(LLM picks Top 3\)",
        lambda _m: f"**Top action:** {top_action}",
        text,
        count=1,
    )
    log_path.write_text(text, encoding="utf-8")


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


def finalize_run(*, assess_dir: Path) -> None:
    """Read finalize-input.json and apply it to log.md and hotspot pages."""
    input_path = assess_dir / "finalize-input.json"
    if not input_path.exists():
        raise FileNotFoundError(f"finalize-input.json not found at {input_path}")

    data = json.loads(input_path.read_text(encoding="utf-8"))
    _finalize_log(
        assess_dir,
        score=data["score"],
        maturity_label=data["maturity_label"],
        top_action=data["top_action"],
    )
    _finalize_hotspot_actions(assess_dir, hotspot_actions=data.get("hotspot_actions", {}))


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
