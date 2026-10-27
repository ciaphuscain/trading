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


def _zip_csv(df: pd.DataFrame, name: str) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, df.to_csv(index=False))
    return buf.getvalue()


def test_bhavcopy_downloader_caches_rate_limits_and_records_holidays(tmp_path):
    from omega.data.nse_archive import build_bars, download_range

    calls, sleeps = [], []

    def fake_fetch(url):
        calls.append(url)
        if "20250102" in url or "02JAN2025" in url:  # pretend 2 Jan is a holiday: both URLs 404
            return None
        day = "2025-01-01" if ("20250101" in url) else "2025-01-03"
        df = pd.DataFrame({"TradDt": [day] * 2, "TckrSymb": ["ABC", "XYZ"], "SctySrs": ["EQ", "BE"],
                           "OpnPric": [1, 2], "HghPric": [1, 2], "LwPric": [1, 2], "ClsPric": [1, 2],
                           "TtlTradgVol": [10, 20]})
        return _zip_csv(df, "bhav.csv")

    s1 = download_range("2025-01-01", "2025-01-03", tmp_path, fetch=fake_fetch, min_interval=1.0,
                        sleep=sleeps.append)
    assert s1 == {"fetched": 2, "cached": 0, "no_file": 1, "requests": 4}
    assert sleeps and all(0 < w <= 1.0 for w in sleeps)  # rate limiter engaged between requests
    n = len(calls)
    s2 = download_range("2025-01-01", "2025-01-03", tmp_path, fetch=fake_fetch, sleep=sleeps.append)
    assert len(calls) == n and s2["cached"] == 2 and s2["no_file"] == 1  # nothing re-requested
    bars = build_bars(tmp_path)
    assert bars.symbol.tolist() == ["ABC", "ABC"] and bars.date.nunique() == 2


def test_downloader_rejects_non_zip_response(tmp_path):
    from omega.data.nse_archive import download_range

    with pytest.raises(ValueError):
        download_range("2025-01-01", "2025-01-01", tmp_path, fetch=lambda u: b"<html>blocked</html>", sleep=lambda s: None)


def test_isin_unification_bridges_renames_and_never_merges_reused_tickers():
    from omega.data.nse_archive import unify_symbols_by_isin

    d = pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-06"])
    bars = pd.DataFrame({
        "date": [d[0], d[1], d[2], d[3], d[0], d[1]],
        # INE1 traded as OLDCO, renamed NEWCO; INE2 traded as NEWCO earlier (ticker later reused)
        "symbol": ["OLDCO", "OLDCO", "NEWCO", "NEWCO", "NEWCO", "NEWCO"],
        "isin": ["INE1", "INE1", "INE1", "INE1", "INE2", "INE2"],
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0})
    out = unify_symbols_by_isin(bars)
    assert set(out[out["isin"] == "INE1"].symbol) == {"NEWCO"}
    assert set(out[out["isin"] == "INE2"].symbol) == {"NEWCO~INE2"}
