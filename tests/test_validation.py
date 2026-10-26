import pandas as pd

from omega.data.market import from_long
from omega.data.sources import synthetic_market
from omega.data.validation import validate, validate_long_bars

from .helpers import make_bars


def _bars():
    return pd.DataFrame({"date": pd.bdate_range("2025-01-06", periods=4).repeat(1), "symbol": "A",
                         "open": [10, 11, 12, 13.0], "high": [11, 12, 13, 14.0], "low": [9, 10, 11, 12.0],
                         "close": [10.5, 11.5, 12.5, 13.5], "volume": [100, 100, 100, 100.0]})


def test_clean_synthetic_data_has_no_errors():
    rep = validate(synthetic_market(n_symbols=10, n_days=400, seed=1))
    assert rep.ok, rep.errors[:5]


def test_detects_corrupted_candle():
    b = _bars()
    b.loc[2, "high"] = 5.0  # high below low/open/close
    rep = validate(from_long(b))
    assert any(i.check == "ohlc_inconsistent" for i in rep.errors)


def test_detects_duplicates_before_pivot():
    b = pd.concat([_bars(), _bars().iloc[[1]].assign(close=99.0)])
    rep = validate_long_bars(b)
    assert any(i.check == "duplicate_bar" and "conflicting" in i.detail for i in rep.errors)


def test_detects_unadjusted_split():
    bars = [(100, 100, 100, 100, 1e5)] * 3 + [(50, 50, 50, 50, 2e5)] * 3
    rep = validate(make_bars({"A": bars}))
    assert any(i.check == "unexplained_jump" for i in rep.errors)
    acts = pd.DataFrame({"symbol": ["A"], "ex_date": [pd.bdate_range("2024-12-30", periods=11)[8]],
                         "kind": ["split"], "value": [2.0]})
    assert validate(make_bars({"A": bars}, actions=acts)).ok


def test_detects_non_positive_price_and_future_dates():
    b = _bars()
    b.loc[1, ["open", "low"]] = [0.0, 0.0]
    rep = validate(from_long(b), as_of=pd.Timestamp("2025-01-07"))
    checks = {i.check for i in rep.errors}
    assert {"non_positive_price", "future_date"} <= checks


def test_dataset_version_changes_with_content():
    a = from_long(_bars())
    b2 = _bars()
    b2.loc[3, "close"] = 13.6
    assert a.version() == from_long(_bars()).version()
    assert a.version() != from_long(b2).version()
