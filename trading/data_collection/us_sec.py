"""US SEC financial data collection for trading workflows."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from market_providers import USProvider  # noqa: E402
from trading.kiwoom_client import TradingSecurity  # noqa: E402


class USSECFinancialCollector:
    """Collect SEC companyfacts financials for a US universe."""

    def __init__(self, provider: USProvider | None = None) -> None:
        self.provider = provider or USProvider()

    def load_financials(self, securities: list[TradingSecurity]) -> dict[str, dict[str, Any]]:
        provider_universe = {security.ticker: security for security in self.provider.get_universe()}
        financials: dict[str, dict[str, Any]] = {}
        for security in securities:
            provider_security = provider_universe.get(security.ticker)
            if provider_security is None:
                continue
            financials[security.ticker] = self.provider.get_financials(provider_security)
        return financials

