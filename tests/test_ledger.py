import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from omega.portfolio.ledger import InsufficientCash, Ledger, LedgerError, UnownedSale

D = pd.Timestamp("2025-01-06")


def test_buy_sell_round_trip_exact():
    lg = Ledger(5000.0)
    lg.buy(D, "ABC", 10, 200.0, 3.46, "o1")
    assert lg.cash == pytest.approx(5000 - 2000 - 3.46)
    lg.sell(D, "ABC", 10, 210.0, 19.01, "o2")
    assert lg.cash == pytest.approx(5000 - 2003.46 + 2100 - 19.01)
    assert lg.realized_pnl == pytest.approx(2100 - 19.01 - 2003.46)
    assert lg.positions == {}
    lg.check_invariants()


def test_rejects_fractional_unowned_duplicate_and_overspend():
    lg = Ledger(1000.0)
    with pytest.raises(LedgerError):
        lg.buy(D, "A", 1.5, 10.0, 0.0, "x")  # type: ignore[arg-type]
    with pytest.raises(UnownedSale):
        lg.sell(D, "A", 1, 10.0, 0.0, "y")
    with pytest.raises(InsufficientCash):
        lg.buy(D, "A", 101, 10.0, 0.0, "z")
    lg.buy(D, "A", 10, 10.0, 0.0, "dup")
    with pytest.raises(LedgerError):
        lg.buy(D, "A", 1, 10.0, 0.0, "dup")
    lg.check_invariants()


def test_split_with_cash_in_lieu():
    lg = Ledger(10_000.0)
    lg.buy(D, "A", 3, 300.0, 0.0, "b")
    lg.apply_split(D, "A", 1.5, 200.0)  # 3 * 1.5 = 4.5 -> 4 shares + 0.5 share in cash
    assert lg.positions["A"].qty == 4
    assert lg.cash == pytest.approx(10_000 - 900 + 100)
    lg.check_invariants()


def test_dividend_and_write_off():
    lg = Ledger(1000.0)
    lg.buy(D, "A", 5, 100.0, 0.0, "b")
    assert lg.apply_dividend(D, "A", 2.0) == 10.0
    lg.write_off(D, "A", 250.0)
    assert lg.positions == {} and lg.cash == pytest.approx(1000 - 500 + 10 + 250)
    lg.check_invariants()


ops = st.lists(
    st.tuples(st.sampled_from(["BUY", "SELL", "SPLIT", "DIV"]), st.sampled_from(["A", "B", "C"]),
              st.integers(min_value=-3, max_value=40), st.floats(min_value=0.5, max_value=900.0),
              st.floats(min_value=0.0, max_value=25.0)),
    max_size=60,
)


@settings(max_examples=300, deadline=None)
@given(ops, st.floats(min_value=0.0, max_value=20_000.0))
def test_property_invariants_hold_under_random_operations(seq, cash):
    """Whatever sequence of (possibly invalid) operations is attempted, the ledger never
    goes negative, never holds fractional/negative shares and never creates cash."""
    lg = Ledger(cash)
    for n, (op, sym, qty, price, fee) in enumerate(seq):
        try:
            if op == "BUY":
                lg.buy(D, sym, qty, price, fee, f"o{n}")
            elif op == "SELL":
                lg.sell(D, sym, qty, price, fee, f"o{n}")
            elif op == "SPLIT":
                lg.apply_split(D, sym, [2.0, 1.5, 5.0][n % 3], price)
            else:
                lg.apply_dividend(D, sym, fee)
        except LedgerError:
            pass
        lg.check_invariants()
        assert lg.cash >= -1e-6
        assert all(isinstance(p.qty, int) and p.qty > 0 for p in lg.positions.values())
    # conservation: final cash = initial - buys + sells + dividends + cash-in-lieu
    assert sum(j.cash_delta for j in lg.journal) == pytest.approx(lg.cash, abs=1e-6)
