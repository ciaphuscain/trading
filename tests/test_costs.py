import math

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from omega.costs.model import CostModel, SlippageModel

D = pd.Timestamp("2025-01-15")


def test_hand_calculated_delivery_buy_2500():
    c = CostModel("discount_zero_delivery").charges("BUY", 2500.0, D)
    # STT 0.1% = 2.50 -> rounded half-up to 3; exch 2500*0.00297% = 0.074 -> 0.07;
    # SEBI 0.0025 -> 0.00; stamp 0.015% = 0.375 -> 0.38; GST 18% of (0.07425+0.0025) = 0.0138 -> 0.01
    assert c.as_dict() == {"brokerage": 0.0, "stt": 3.0, "exchange_txn": 0.07, "sebi_fee": 0.0, "stamp": 0.38,
                           "gst": 0.01, "dp": 0.0, "total": 3.46}


def test_hand_calculated_delivery_sell_2500_includes_dp():
    c = CostModel("discount_zero_delivery").charges("SELL", 2500.0, D)
    # DP 13.50 per scrip per sell day; GST on (0.07425 + 0.0025 + 13.5) = 2.4438 -> 2.44
    assert c.dp == 13.5 and c.gst == 2.44 and c.stamp == 0.0
    assert c.total == pytest.approx(19.01)


def test_dp_charged_once_per_scrip_per_day():
    m = CostModel()
    assert m.charges("SELL", 1000, D, first_sell_of_scrip_today=False).dp == 0.0


def test_round_trip_on_small_ticket_is_material():
    # The economic fact driving OMEGA's design: ~0.9% round trip on a INR 2,500 ticket.
    f = CostModel().round_trip_fraction(2500.0, D)
    assert 0.0085 < f < 0.0095


def test_flat_brokerage_profile_is_min_of_pct_and_flat():
    m = CostModel("flat_20_or_0p1pct")
    assert m.charges("BUY", 2500.0, D).brokerage == 2.5
    assert m.charges("BUY", 100000.0, D).brokerage == 20.0


def test_schedule_is_dated():
    m = CostModel()
    old = m.charges("BUY", 1_000_000.0, pd.Timestamp("2024-09-30")).exchange_txn
    new = m.charges("BUY", 1_000_000.0, pd.Timestamp("2024-10-01")).exchange_txn
    assert old == pytest.approx(32.2) and new == pytest.approx(29.7)
    with pytest.raises(ValueError):
        m.charges("BUY", 1000.0, pd.Timestamp("2001-01-01"))


def test_unknown_broker_rejected():
    with pytest.raises(KeyError):
        CostModel("no_such_broker")


@given(st.floats(min_value=1.0, max_value=5e6), st.sampled_from(["BUY", "SELL"]))
def test_charges_non_negative_and_monotone(value, side):
    m = CostModel()
    a = m.charges(side, value, D).total
    b = m.charges(side, value * 1.5, D).total
    assert a >= 0 and b >= a - 0.011  # paise rounding


def test_slippage_is_adverse():
    s = SlippageModel(half_spread_bps=10, impact_coef_bps=0)
    assert s.price(100.0, "BUY", 1, 1e6) == pytest.approx(100.10)
    assert s.price(100.0, "SELL", 1, 1e6) == pytest.approx(99.90)
    imp = SlippageModel(half_spread_bps=10, impact_coef_bps=10)
    assert imp.price(100.0, "BUY", 10_000, 10_000) > imp.price(100.0, "BUY", 1, 10_000)
    assert math.isfinite(s.price(100.0, "BUY", 5, 0))
