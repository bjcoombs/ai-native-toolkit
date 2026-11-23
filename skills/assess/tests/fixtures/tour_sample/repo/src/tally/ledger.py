"""An in-memory ledger of expenses, each with a category and an amount in pence."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Entry:
    category: str
    pence: int


@dataclass
class Ledger:
    entries: list[Entry] = field(default_factory=list)

    def add(self, category: str, amount: str) -> Entry:
        """Record an expense. ``amount`` is in pounds, such as "12.50"."""
        name = category.strip().lower()
        if not name:
            raise ValueError("category must not be empty")
        pence = round(float(amount) * 100)
        if pence <= 0:
            raise ValueError("amount must be positive")
        entry = Entry(name, pence)
        self.entries.append(entry)
        return entry

    def total(self) -> int:
        return sum(entry.pence for entry in self.entries)
