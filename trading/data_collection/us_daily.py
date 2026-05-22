"""US daily-price collection for trading workflows."""

from __future__ import annotations

import sys
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from market_providers import USProvider, get_yahoo_chart_ohlcv  # noqa: E402
from trading.config import DATA_CACHE_DIR, DEFAULT_CONFIG  # noqa: E402
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
        start_date: str | None = None,
        end_date: str | None = None,
        bars: int = DEFAULT_CONFIG.price_lookback_bars,
    ) -> dict[str, pd.DataFrame]:
        by_ticker: dict[str, pd.DataFrame] = {}
        universe = {security.ticker: security for security in self.provider.get_universe()}
        requested_days = days_for_date_range(start_date, end_date, minimum=bars)
        for security in securities:
            provider_security = universe.get(security.ticker)
            if provider_security is None:
                continue
            df = self.provider.get_ohlcv(provider_security, days=requested_days)
            by_ticker[security.ticker] = slice_by_date(df, start_date=start_date, end_date=end_date).tail(requested_days).reset_index(drop=True)
        return by_ticker


class USIndexPriceCollector:
    """Collect US benchmark index daily OHLCV through Yahoo chart."""

    symbols = {"S&P500": "^GSPC", "NASDAQ": "^IXIC"}

    def __init__(self, cache_dir: Path = DATA_CACHE_DIR / "indexes" / "yahoo_us") -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load_index_history(
        self,
        *,
        start_date: str | None,
        end_date: str | None,
        bars: int = DEFAULT_CONFIG.price_lookback_bars,
    ) -> dict[str, pd.DataFrame]:
        requested_days = days_for_date_range(start_date, end_date, minimum=bars)
        history: dict[str, pd.DataFrame] = {}
        for name, symbol in self.symbols.items():
            cached = self._read_index_cache(name, start_date=start_date, end_date=end_date, bars=requested_days)
            if cached is None:
                df = get_yahoo_chart_ohlcv(symbol, requested_days)
                df = df.rename(columns={"Date": "date", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
                if "currency" not in df.columns:
                    df["currency"] = "USD"
                self._write_index_cache(name, df)
                cached = slice_by_date(df, start_date=start_date, end_date=end_date).tail(requested_days).reset_index(drop=True)
            history[name] = cached
        return history

    def _index_cache_path(self, name: str) -> Path:
        return self.cache_dir / f"{name}.json"

    def _read_index_cache(self, name: str, *, start_date: str | None, end_date: str | None, bars: int) -> pd.DataFrame | None:
        path = self._index_cache_path(name)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("data", payload if isinstance(payload, list) else [])
            df = pd.DataFrame(rows)
            if df.empty or "date" not in df.columns:
                return None
            df = df.sort_values("date").reset_index(drop=True)
            if end_date is not None and str(df["date"].max()) < end_date:
                return None
            if start_date is not None and str(df["date"].min()) > start_date:
                return None
            return slice_by_date(df, start_date=start_date, end_date=end_date).tail(bars).reset_index(drop=True)
        except Exception:
            return None

    def _write_index_cache(self, name: str, df: pd.DataFrame) -> None:
        payload = {
            "created_at": time.time(),
            "name": name,
            "data": json.loads(df.to_json(orient="records", date_format="iso", force_ascii=False)),
        }
        self._index_cache_path(name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def days_for_date_range(start_date: str | None, end_date: str | None, *, minimum: int) -> int:
    if start_date is None or end_date is None:
        return minimum
    calendar_days = max((date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 10, 1)
    return max(minimum, calendar_days)


def slice_by_date(df: pd.DataFrame, *, start_date: str | None, end_date: str | None) -> pd.DataFrame:
    frame = df.copy()
    if "date" not in frame.columns:
        return frame
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    frame = frame.dropna(subset=["date"])
    if start_date is not None:
        frame = frame[frame["date"].astype(str) >= start_date]
    if end_date is not None:
        frame = frame[frame["date"].astype(str) <= end_date]
    return frame.sort_values("date").reset_index(drop=True)
