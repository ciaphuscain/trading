import pandas as pd
import pytest

from omega.data.market import MarketData
from omega.data.sources import load_directory, read_bhavcopy, synthetic_market


def test_csv_round_trip_preserves_dataset_version(tmp_path):
    md = synthetic_market(n_symbols=5, n_days=300, seed=2)
    md = MarketData(md.raw, md.adj, md.actions, None, md.source)
    long = pd.concat({f: md.raw.field(f).stack() for f in ("open", "high", "low", "close", "volume")}, axis=1)
    long = long.reset_index()
    long.columns = ["date", "symbol", "open", "high", "low", "close", "volume"]
    long.to_csv(tmp_path / "bars.csv", index=False)
    md.actions.to_csv(tmp_path / "actions.csv", index=False)
    assert load_directory(tmp_path).version() == md.version()


def test_bhavcopy_legacy_and_udiff_layouts(tmp_path):
    legacy = pd.DataFrame({"SYMBOL": ["ABC", "ABC", "XYZ"], "SERIES": ["EQ", "BE", "EQ"],
                           "OPEN": [10, 11, 20], "HIGH": [11, 12, 21], "LOW": [9, 10, 19], "CLOSE": [10.5, 11, 20.5],
                           "LAST": [10.5, 11, 20.5], "PREVCLOSE": [10, 11, 20], "TOTTRDQTY": [100, 5, 300],
                           "TOTTRDVAL": [1, 1, 1], "TIMESTAMP": ["03-JAN-2023"] * 3})
    legacy.to_csv(tmp_path / "legacy.csv", index=False)
    a = read_bhavcopy(tmp_path / "legacy.csv")
    assert a.symbol.tolist() == ["ABC", "XYZ"] and a.date.iloc[0] == pd.Timestamp("2023-01-03")
    udiff = pd.DataFrame({"TradDt": ["2024-07-08"] * 2, "TckrSymb": ["ABC", "XYZ"], "SctySrs": ["EQ", "EQ"],
                          "OpnPric": [1, 2], "HghPric": [1, 2], "LwPric": [1, 2], "ClsPric": [1, 2],
                          "TtlTradgVol": [7, 8]})
    udiff.to_csv(tmp_path / "udiff.csv", index=False)
    b = read_bhavcopy(tmp_path / "udiff.csv")
    assert b.volume.tolist() == [7, 8]
    pd.DataFrame({"foo": [1]}).to_csv(tmp_path / "bad.csv", index=False)
    with pytest.raises(ValueError):
        read_bhavcopy(tmp_path / "bad.csv")
