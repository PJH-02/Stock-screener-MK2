"""Backward-compatible Korean data manager wrapper.

The main screener now uses market_providers directly. This class remains for
older imports and delegates to KRProvider so the old broken OpenDART code path
is no longer used.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from market_providers import KRProvider, Security
from utils import setup_logger

logger = setup_logger("data_manager")


class DataManager:
    """Compatibility wrapper around KRProvider."""

    def __init__(self):
        self.provider = KRProvider()
        self.today = datetime.now().strftime("%Y%m%d")
        self.start_date = (datetime.now() - timedelta(days=730)).strftime("%Y%m%d")

    def get_universe(self):
        return [security.ticker for security in self.provider.get_universe()]

    def get_ohlcv(self, ticker, days=400):
        return self.provider.get_ohlcv(self._security(ticker), days=days)

    def get_company_name(self, ticker):
        return self._security(ticker).name

    def get_financial_statements(self, ticker):
        return self.provider.get_financials(self._security(ticker))

    def get_market_data(self, ticker):
        security = self._security(ticker)
        ohlcv = self.provider.get_ohlcv(security)
        if ohlcv.empty:
            return None
        return {
            "ticker": ticker,
            "name": security.name,
            "ohlcv": ohlcv,
            "close_price": ohlcv["close"].iloc[-1],
        }

    def _security(self, ticker):
        seed = {security.ticker: security for security in self.provider._seed_universe()}
        if ticker in seed:
            return seed[ticker]
        return Security(market="KR", ticker=str(ticker).zfill(6), name=str(ticker).zfill(6), currency="KRW")
