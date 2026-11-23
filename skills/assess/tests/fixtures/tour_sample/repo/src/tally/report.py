"""Totals per category, largest first, formatted for the terminal."""
from __future__ import annotations

from tally.ledger import Ledger


def by_category(ledger: Ledger) -> dict[str, int]:
    totals: dict[str, int] = {}
    for entry in ledger.entries:
        totals[entry.category] = totals.get(entry.category, 0) + entry.pence
    return dict(sorted(totals.items(), key=lambda item: (-item[1], item[0])))


def render(ledger: Ledger) -> str:
    lines = [f"{name:<12} {pence / 100:>8.2f}" for name, pence in by_category(ledger).items()]
    lines.append(f"{'total':<12} {ledger.total() / 100:>8.2f}")
    return "\n".join(lines)
