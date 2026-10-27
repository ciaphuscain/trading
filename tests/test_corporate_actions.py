import pandas as pd
import pytest

from omega.data.corporate_actions import parse_nse_actions, parse_purpose
from omega.data.validation import validate

from .helpers import make_bars


@pytest.mark.parametrize("purpose,expected", [
    ("Dividend - Rs 5 Per Share", [("dividend", 5.0)]),
    ("Interim Dividend - Rs. 2.50 Per Share", [("dividend", 2.5)]),
    ("Annual General Meeting/Dividend - Re 1 Per Share", [("dividend", 1.0)]),
    ("Final Dividend - Rs 8 Per Share/Special Dividend - Rs 2 Per Share", [("dividend", 10.0)]),
    ("Bonus 1:1", [("split", 2.0)]),
    ("Bonus 3:2", [("split", 2.5)]),
    ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share", [("split", 5.0)]),
    ("Face Value Split (Sub-Division) - From Re 1/- Per Share To Re 0.50/- Per Share", [("split", 2.0)]),
    ("Consolidation of Shares - From Rs 1/- Per Share To Rs 10/- Per Share", [("split", 0.1)]),
])
def test_parse_purpose(purpose, expected):
    acts, problem = parse_purpose(purpose)
    assert problem is None
    assert [k for k, _ in acts] == [k for k, _ in expected]
    assert [v for _, v in acts] == pytest.approx([v for _, v in expected])


@pytest.mark.parametrize("purpose", ["Rights 1:5 @ Premium Rs 100/-", "Demerger", "Something odd"])
def test_unsupported_or_unknown_purposes_are_reported_not_guessed(purpose):
    acts, problem = parse_purpose(purpose)
    assert problem is not None and acts == []


def test_parse_nse_export_file(tmp_path):
    pd.DataFrame({"SYMBOL": ["ABC", "ABC", "XYZ", "OLD", "ABC"], "COMPANY NAME": ["a"] * 5,
                  "SERIES": ["EQ", "EQ", "EQ", "BE", "EQ"],
                  "PURPOSE": ["Bonus 1:1", "Dividend - Rs 3 Per Share", "Rights 1:4", "Bonus 1:1", "Bonus 1:1"],
                  "FACE VALUE": [10] * 5,
                  "EX-DATE": ["15-Jan-2024", "20-Feb-2024", "01-Mar-2024", "01-Mar-2024", "15-Jan-2024"]}
                 ).to_csv(tmp_path / "ca.csv", index=False)
    acts, bad = parse_nse_actions(tmp_path / "ca.csv")
    assert acts.to_dict("records") == [
        {"symbol": "ABC", "ex_date": pd.Timestamp("2024-01-15"), "kind": "split", "value": 2.0},  # duplicate dropped
        {"symbol": "ABC", "ex_date": pd.Timestamp("2024-02-20"), "kind": "dividend", "value": 3.0}]
    assert bad.symbol.tolist() == ["XYZ"]


def test_validation_flags_recorded_split_not_visible_in_prices():
    bars = [(100, 100, 100, 100, 1e5)] * 6  # no price drop at all
    acts = pd.DataFrame({"symbol": ["A"], "ex_date": [pd.bdate_range("2024-12-30", periods=11)[8]],
                         "kind": ["split"], "value": [2.0]})
    rep = validate(make_bars({"A": bars}, actions=acts))
    assert any(i.check == "split_not_visible" for i in rep.warnings)
    ok = [(100, 100, 100, 100, 1e5)] * 3 + [(50, 50, 50, 50, 2e5)] * 3
    assert not any(i.check == "split_not_visible" for i in validate(make_bars({"A": ok}, actions=acts)).issues)
