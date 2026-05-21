"""Korean daily-price collection for trading workflows."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pandas as pd

from trading.config import DATA_CACHE_DIR, DEFAULT_CONFIG
from trading.kiwoom_client import KiwoomRESTClient, TradingSecurity


class KRDailyPriceCollector:
    """Collect KOSPI/KOSDAQ universe and daily OHLCV through Kiwoom REST."""

    def __init__(self, client: KiwoomRESTClient | None = None, cache_dir: Path = DATA_CACHE_DIR / "prices" / "kiwoom_kr") -> None:
        self.client = client or KiwoomRESTClient()
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load_universe(self, *, limit: int | None = None) -> list[TradingSecurity]:
        securities = self.client.load_universe()
        return securities[:limit] if limit else securities

    def load_price_history(
        self,
        securities: list[TradingSecurity],
        *,
        end_date: str,
        bars: int = DEFAULT_CONFIG.price_lookback_bars,
    ) -> dict[str, pd.DataFrame]:
        history: dict[str, pd.DataFrame] = {}
        for security in securities:
            cached = self._read_price_cache(security.ticker, end_date=end_date, bars=bars)
            if cached is not None:
                history[security.ticker] = cached
                continue
            df = self.client.load_daily_ohlcv(security.ticker, end_date=end_date, bars=bars)
            self._write_price_cache(security.ticker, df)
            history[security.ticker] = df
        return history

    def load_inputs(
        self,
        *,
        end_date: str,
        limit: int | None = None,
        bars: int = DEFAULT_CONFIG.price_lookback_bars,
    ) -> tuple[list[TradingSecurity], dict[str, Any]]:
        securities = self.load_universe(limit=limit)
        self._write_universe_cache(securities)
        return securities, self.load_price_history(securities, end_date=end_date, bars=bars)

    def _price_cache_path(self, ticker: str) -> Path:
        return self.cache_dir / f"{ticker}.json"

    def _read_price_cache(self, ticker: str, *, end_date: str, bars: int) -> pd.DataFrame | None:
        path = self._price_cache_path(ticker)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("data", payload if isinstance(payload, list) else [])
            df = pd.DataFrame(rows)
            if df.empty or "date" not in df.columns:
                return None
            df = df.sort_values("date").reset_index(drop=True)
            max_date = str(df["date"].max())
            if max_date < end_date:
                return None
            return df.tail(bars).reset_index(drop=True)
        except Exception:
            return None

    def _write_price_cache(self, ticker: str, df: pd.DataFrame) -> None:
        path = self._price_cache_path(ticker)
        payload = {
            "created_at": time.time(),
            "ticker": ticker,
            "data": json.loads(df.to_json(orient="records", date_format="iso", force_ascii=False)),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_universe_cache(self, securities: list[TradingSecurity]) -> None:
        path = DATA_CACHE_DIR / "kr_universe.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([security.to_dict() for security in securities], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
