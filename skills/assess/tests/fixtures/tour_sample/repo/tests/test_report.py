from tally.ledger import Ledger
from tally.report import by_category, render


def test_by_category_puts_the_largest_first():
    ledger = Ledger()
    ledger.add("coffee", "2.80")
    ledger.add("rent", "500")
    ledger.add("coffee", "3.20")
    assert list(by_category(ledger)) == ["rent", "coffee"]


def test_render_ends_with_the_total():
    ledger = Ledger()
    ledger.add("rent", "500")
    assert render(ledger).splitlines()[-1].split() == ["total", "500.00"]
