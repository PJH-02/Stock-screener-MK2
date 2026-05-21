"""US daily-price collection for trading workflows."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from market_providers import USProvider  # noqa: E402
from trading.config import DEFAULT_CONFIG  # noqa: E402
from trading.kiwoom_client import TradingSecurity  # noqa: E402


class USDailyPriceCollector:
    """Collect S&P 500 + Nasdaq 100 universe and daily OHLCV through free US sources."""

    def __init__(self, provider: USProvider | None = None) -> None:
        self.provider = provider or USProvider()

    def load_universe(self, *, limit: int | None = None) -> list[TradingSecurity]:
        securities = self.provider.get_universe(limit=limit)
        return [
            TradingSecurity(
                ticker=security.ticker,
                name=security.name,
                market=security.market,
                sector=security.sector,
                common_ticker=security.ticker,
            )
            for security in securities
        ]

    def load_price_history(
        self,
        securities: list[TradingSecurity],
        *,
        bars: int = DEFAULT_CONFIG.price_lookback_bars,
    ) -> dict[str, pd.DataFrame]:
        by_ticker: dict[str, pd.DataFrame] = {}
        universe = {security.ticker: security for security in self.provider.get_universe()}
        for security in securities:
            provider_security = universe.get(security.ticker)
            if provider_security is None:
                continue
            by_ticker[security.ticker] = self.provider.get_ohlcv(provider_security, days=bars)
        return by_ticker

