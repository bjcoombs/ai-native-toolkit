import pytest

from tally.ledger import Ledger


def test_add_records_the_amount_in_pence():
    ledger = Ledger()
    entry = ledger.add("Groceries ", "12.50")
    assert (entry.category, entry.pence) == ("groceries", 1250)


def test_total_sums_every_entry():
    ledger = Ledger()
    ledger.add("rent", "500")
    ledger.add("coffee", "2.80")
    assert ledger.total() == 50280


@pytest.mark.parametrize("category, amount", [("", "1"), ("rent", "0"), ("rent", "-3")])
def test_add_refuses_bad_input(category, amount):
    with pytest.raises(ValueError):
        Ledger().add(category, amount)
