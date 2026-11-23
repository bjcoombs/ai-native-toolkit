"""Command line: ``python -m tally add <category> <amount>`` or ``python -m tally report``."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tally.ledger import Ledger
from tally.report import render

STORE = Path("tally.json")


def load() -> Ledger:
    ledger = Ledger()
    if STORE.exists():
        for row in json.loads(STORE.read_text(encoding="utf-8")):
            ledger.add(row["category"], str(row["pence"] / 100))
    return ledger


def save(ledger: Ledger) -> None:
    rows = [{"category": e.category, "pence": e.pence} for e in ledger.entries]
    STORE.write_text(json.dumps(rows), encoding="utf-8")


def main(argv: list[str]) -> int:
    ledger = load()
    if argv[:1] == ["add"] and len(argv) == 3:
        ledger.add(argv[1], argv[2])
        save(ledger)
        return 0
    if argv == ["report"]:
        print(render(ledger))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
