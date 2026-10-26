import pandas as pd

from omega.risk.engine import OrderCheck, RiskEngine, RiskLimits

NOW = pd.Timestamp("2025-03-05")


def chk(oid="o1", side="BUY", qty=10, held=0, stop=None, last=NOW, vol=1e5):
    return OrderCheck(oid, "ABC", side, qty, 100.0, 100.0, last, vol, held, stop)


def run(eng, o, equity=5000.0, cash=5000.0, gross=0.0, pv=0.0):
    return eng.check(o, equity=equity, cash=cash, gross_exposure=gross, position_value=pv, decision_time=NOW)


def test_basic_approval_and_duplicate():
    e = RiskEngine(RiskLimits(), 5000)
    e.start_day(NOW, 5000)
    assert run(e, chk()).approved
    assert run(e, chk()).reason == "duplicate_order_id"


def test_stale_data_and_price_sanity():
    e = RiskEngine(RiskLimits(), 5000)
    e.start_day(NOW, 5000)
    assert run(e, chk(last=NOW - pd.Timedelta(days=10))).reason == "stale_data"
    bad = OrderCheck("o9", "ABC", "BUY", 1, 150.0, 100.0, NOW, 1e5, 0)
    assert run(e, bad).reason == "price_sanity"


def test_kill_switch_blocks_buys_not_sells(tmp_path):
    ks = tmp_path / "KILL_SWITCH"
    e = RiskEngine(RiskLimits(), 5000, audit_path=tmp_path / "audit.jsonl", kill_switch_file=ks)
    e.start_day(NOW, 5000)
    ks.write_text("stop")
    assert run(e, chk("b1")).reason == "kill_switch"
    assert run(e, chk("s1", side="SELL", held=10)).approved
    ks.unlink()
    e.set_kill_switch(True, who="operator", why="test")
    assert run(e, chk("b2")).reason == "kill_switch"
    assert "kill_switch" in (tmp_path / "audit.jsonl").read_text()


def test_drawdown_halt_cools_down_and_rearms():
    e = RiskEngine(RiskLimits(max_drawdown_fraction=0.2, drawdown_cooldown=3), 5000)
    e.start_day(NOW, 5000)
    assert run(e, chk("a"), equity=3900).reason == "max_drawdown_breached"
    for k in range(2):
        e.start_day(NOW + pd.Timedelta(days=k + 1), 3900)
        assert run(e, chk(f"h{k}"), equity=3900).reason == "drawdown_halt"
    e.start_day(NOW + pd.Timedelta(days=3), 3900)  # cooldown over: peak reset to 3900
    assert run(e, chk("ok"), equity=3900, cash=3900).approved


def test_position_exposure_liquidity_and_loss_limits():
    e = RiskEngine(RiskLimits(), 5000)
    e.start_day(NOW, 5000)
    assert run(e, chk("p", qty=31)).reason == "max_position_fraction"  # 3100 > 60% of 5000
    assert run(e, chk("g", qty=20), gross=4500).reason == "max_gross_exposure"
    assert run(e, chk("l", qty=20, vol=500)).reason == "liquidity"
    assert run(e, chk("s", qty=25, stop=0.25)).reason == "max_loss_per_trade"
    assert run(e, chk("c", qty=20), cash=1000).reason == "insufficient_cash"
