import pytest

from omega.execution.manager import OrderManager


@pytest.fixture(autouse=True)
def _close_order_managers(monkeypatch):
    """Close every OrderManager a test creates (open SQLite handles lock files on Windows)."""
    created = []
    real_init = OrderManager.__init__

    def tracking_init(self, *a, **k):
        real_init(self, *a, **k)
        created.append(self)

    monkeypatch.setattr(OrderManager, "__init__", tracking_init)
    yield
    for om in created:
        om.close()
