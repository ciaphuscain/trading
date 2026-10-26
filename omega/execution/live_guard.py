"""Live-trading guard.

Live execution is NOT implemented in this version. This module exists so that the
conditions for ever enabling it are explicit and testable, and so that no single
environment variable or config flag can turn it on.

Required before a future version may relax ``assert_live_allowed`` (all of them):
 1. A separate live BrokerAdapter, reviewed and contract-tested against the broker sandbox.
 2. Verified broker permissions and regulatory requirements (SEBI algo-trading rules
    for retail API orders, broker static-IP / API registration as applicable).
 3. Credentials stored outside the repository with least privilege.
 4. Paper-trading acceptance tests passed for >= the documented period.
 5. Risk limits configured and reviewed.
 6. A signed human authorisation record naming the strategy version, capital cap and expiry.
 7. A controlled rollout plan (smallest size, single symbol, manual monitoring).
"""

from __future__ import annotations


class LiveTradingDisabled(PermissionError):
    pass


LIVE_IMPLEMENTED = False


def assert_live_allowed(*_args, **_kwargs) -> None:
    raise LiveTradingDisabled(
        "Live trading is not implemented in OMEGA v0.x. Only the PaperBroker exists. "
        "See omega/execution/live_guard.py for the readiness checklist."
    )
