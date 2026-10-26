"""Order lifecycle, idempotency, ambiguous responses, crash recovery and reconciliation."""

import pytest

from omega.execution.broker import FailureConfig, PaperBroker
from omega.execution.live_guard import LiveTradingDisabled, assert_live_allowed
from omega.execution.manager import OrderManager
from omega.execution.orders import IllegalTransition, Order, OrderStatus, S, idempotency_key

DAY = "2025-03-03"


def test_state_machine_rejects_illegal_transitions():
    o = Order("x", "A", "BUY", 1, "s", "t")
    with pytest.raises(IllegalTransition):
        o.transition(S.FILLED)
    o.transition(S.VALIDATED)
    o.transition(S.SUBMITTED)
    o.transition(S.UNKNOWN)
    o.transition(S.FILLED)
    with pytest.raises(IllegalTransition):
        o.transition(S.CANCELLED)  # terminal


def test_idempotency_key_is_deterministic():
    a = idempotency_key("M1", DAY, "ABC", "BUY", 10)
    assert a == idempotency_key("M1", "2025-03-03 15:30", "ABC", "BUY", 10)
    assert a != idempotency_key("M1", DAY, "ABC", "BUY", 11)


def test_happy_path_fill():
    b = PaperBroker(10_000)
    om = OrderManager(b)
    r = om.submit("M1", DAY, "ABC", "BUY", 10)
    assert r.order.status == OrderStatus.ACKNOWLEDGED
    b.match({"ABC": 100.0})
    om.reconcile()
    o = om.get(r.order.client_order_id)
    assert o.status == S.FILLED and o.filled_qty == 10 and o.avg_price == 100.0
    assert om.local_positions() == {"ABC": 10} == b.positions()


def test_duplicate_decision_is_not_resubmitted():
    b = PaperBroker(10_000)
    om = OrderManager(b)
    om.submit("M1", DAY, "ABC", "BUY", 10)
    r2 = om.submit("M1", DAY, "ABC", "BUY", 10)
    assert not r2.submitted_now and b.submit_calls == 1


def test_timeout_after_accept_goes_unknown_then_reconciles_without_duplicate():
    b = PaperBroker(10_000, FailureConfig(timeout_after_accept=1.0))
    om = OrderManager(b)
    r = om.submit("M1", DAY, "ABC", "BUY", 10)
    assert r.order.status == S.UNKNOWN
    # A naive retry of the same decision must not create a second order.
    om.submit("M1", DAY, "ABC", "BUY", 10)
    assert len(b.orders) == 1
    b.match({"ABC": 50.0})
    rep = om.reconcile()
    assert om.get(r.order.client_order_id).status == S.FILLED
    assert rep["breaks"] == {} and b.positions() == {"ABC": 10}


def test_timeout_before_accept_resolves_to_rejected_after_repeated_not_found():
    b = PaperBroker(10_000, FailureConfig(timeout_before_accept=1.0))
    om = OrderManager(b, not_found_limit=2)
    r = om.submit("M1", DAY, "ABC", "BUY", 10)
    assert r.order.status == S.UNKNOWN
    om.reconcile()
    assert om.get(r.order.client_order_id).status == S.UNKNOWN  # one miss is not proof
    om.reconcile()
    assert om.get(r.order.client_order_id).status == S.REJECTED
    assert b.orders == {}


def test_crash_after_write_ahead_is_recovered_by_reconciliation(tmp_path):
    db = tmp_path / "orders.sqlite"
    b = PaperBroker(10_000)

    class CrashingBroker(PaperBroker):
        is_live = False

        def submit(self, *a, **k):
            b.submit(*a, **k)  # the real broker accepts the order...
            raise SystemExit("process killed before the response was processed")

    om = OrderManager(CrashingBroker(0), db)
    with pytest.raises(SystemExit):
        om.submit("M1", DAY, "ABC", "BUY", 5)
    om.db.close()
    b.match({"ABC": 200.0})
    om2 = OrderManager(b, db)  # restarted process, same database
    [o] = om2.orders()
    assert o.status == S.SUBMITTED
    om2.reconcile()
    assert om2.get(o.client_order_id).status == S.FILLED
    assert om2.local_positions() == {"ABC": 5}
    om2.submit("M1", DAY, "ABC", "BUY", 5)
    assert len(b.orders) == 1


def test_partial_fills_and_duplicate_fill_reports_apply_once():
    b = PaperBroker(10_000, FailureConfig(partial_fill=1.0))
    om = OrderManager(b)
    r = om.submit("M1", DAY, "ABC", "BUY", 10)
    b.match({"ABC": 10.0})
    om.reconcile()
    assert om.get(r.order.client_order_id).status == S.PARTIALLY_FILLED
    om.reconcile()  # same broker report again: no double count
    assert om.local_positions() == {"ABC": 5}
    b.match({"ABC": 10.0})
    b.match({"ABC": 10.0})
    om.reconcile()
    o = om.get(r.order.client_order_id)
    assert o.filled_qty == b.orders[o.client_order_id].filled_qty == om.local_positions()["ABC"]


def test_position_break_halts_new_orders():
    b = PaperBroker(10_000)
    om = OrderManager(b)
    om.submit("M1", DAY, "ABC", "BUY", 10)
    b.match({"ABC": 10.0})
    b._pos["XYZ"] = 3  # position appears at the broker that we never ordered
    rep = om.reconcile()
    assert "XYZ" in rep["breaks"] and om.halted
    r = om.submit("M1", "2025-03-04", "DEF", "BUY", 1)
    assert r.order.status == S.REJECTED and r.note == "halted"


def test_risk_check_can_reject():
    om = OrderManager(PaperBroker(1_000), risk_check=lambda o: (False, "kill_switch"))
    r = om.submit("M1", DAY, "ABC", "BUY", 1)
    assert r.order.status == S.REJECTED and r.note == "kill_switch"


def test_live_is_impossible():
    with pytest.raises(LiveTradingDisabled):
        assert_live_allowed()

    class FakeLive(PaperBroker):
        is_live = True

    with pytest.raises(PermissionError):
        OrderManager(FakeLive(0))
