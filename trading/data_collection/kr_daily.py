"""Korean daily-price collection for trading workflows."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from trading.config import DATA_CACHE_DIR, DEFAULT_CONFIG
from trading.kiwoom_client import KiwoomRESTClient, TradingSecurity


def bars_for_date_range(start_date: str | None, end_date: str, *, minimum: int = DEFAULT_CONFIG.price_lookback_bars) -> int:
    if start_date is None:
        return minimum
    calendar_days = max((date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1, 1)
    return max(minimum, int(calendar_days * 1.6))


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
        start_date: str | None = None,
        bars: int = DEFAULT_CONFIG.price_lookback_bars,
    ) -> dict[str, pd.DataFrame]:
        history: dict[str, pd.DataFrame] = {}
        requested_bars = bars_for_date_range(start_date, end_date, minimum=bars)
        for security in securities:
            cached = self._read_price_cache(security.ticker, start_date=start_date, end_date=end_date, bars=requested_bars)
            if cached is not None:
                history[security.ticker] = cached
                continue
            df = self.client.load_daily_ohlcv(security.ticker, end_date=end_date, bars=requested_bars)
            self._write_price_cache(security.ticker, df)
            history[security.ticker] = self._slice_by_date(df, start_date=start_date, end_date=end_date)
        return history

    def load_inputs(
        self,
        *,
        end_date: str,
        start_date: str | None = None,
        limit: int | None = None,
        bars: int = DEFAULT_CONFIG.price_lookback_bars,
    ) -> tuple[list[TradingSecurity], dict[str, Any]]:
        securities = self.load_universe(limit=limit)
        self._write_universe_cache(securities)
        return securities, self.load_price_history(securities, start_date=start_date, end_date=end_date, bars=bars)

    def _price_cache_path(self, ticker: str) -> Path:
        return self.cache_dir / f"{ticker}.json"

    def _read_price_cache(self, ticker: str, *, start_date: str | None, end_date: str, bars: int) -> pd.DataFrame | None:
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
            min_date = str(df["date"].min())
            if max_date < end_date:
                return None
            if start_date is not None and min_date > start_date:
                return None
            return self._slice_by_date(df, start_date=start_date, end_date=end_date).tail(bars).reset_index(drop=True)
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

    @staticmethod
    def _slice_by_date(df: pd.DataFrame, *, start_date: str | None, end_date: str) -> pd.DataFrame:
        frame = df.copy()
        if "date" not in frame.columns:
            return frame
        dates = frame["date"].astype(str)
        if start_date is not None:
            frame = frame[dates >= start_date]
            dates = frame["date"].astype(str)
        frame = frame[dates <= end_date]
        return frame.sort_values("date").reset_index(drop=True)


class KRInstitutionalFlowCollector:
    """Collect daily institutional supply/demand through Kiwoom REST."""

    def __init__(self, client: KiwoomRESTClient | None = None, cache_dir: Path = DATA_CACHE_DIR / "flows" / "kiwoom_kr") -> None:
        self.client = client or KiwoomRESTClient()
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.errors: list[dict[str, Any]] = []

    def load_flows(
        self,
        securities: list[TradingSecurity],
        *,
        start_date: str | None,
        end_date: str,
        bars: int = DEFAULT_CONFIG.price_lookback_bars,
    ) -> dict[str, list[dict[str, Any]]]:
        flows: dict[str, list[dict[str, Any]]] = {}
        requested_bars = bars_for_date_range(start_date, end_date, minimum=bars)
        for security in securities:
            cached = self._read_flow_cache(security.ticker, start_date=start_date, end_date=end_date, bars=requested_bars)
            if cached is None:
                try:
                    df = self.client.load_institutional_flow(security.ticker, end_date=end_date, bars=requested_bars)
                except Exception as exc:
                    self.errors.append({"ticker": security.ticker, "name": security.name, "error": str(exc)})
                    flows[security.ticker] = []
                    continue
                self._write_flow_cache(security.ticker, df)
                cached = self._slice_by_date(df, start_date=start_date, end_date=end_date)
            flows[security.ticker] = json.loads(cached.to_json(orient="records", date_format="iso", force_ascii=False))
        return flows

    def _flow_cache_path(self, ticker: str) -> Path:
        return self.cache_dir / f"{ticker}.json"

    def _read_flow_cache(self, ticker: str, *, start_date: str | None, end_date: str, bars: int) -> pd.DataFrame | None:
        path = self._flow_cache_path(ticker)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("data", payload if isinstance(payload, list) else [])
            df = pd.DataFrame(rows)
            if df.empty or "date" not in df.columns:
                return None
            df = df.sort_values("date").reset_index(drop=True)
            if str(df["date"].max()) < end_date:
                return None
            if start_date is not None and str(df["date"].min()) > start_date:
                return None
            return self._slice_by_date(df, start_date=start_date, end_date=end_date).tail(bars).reset_index(drop=True)
        except Exception:
            return None

    def _write_flow_cache(self, ticker: str, df: pd.DataFrame) -> None:
        payload = {
            "created_at": time.time(),
            "ticker": ticker,
            "data": json.loads(df.to_json(orient="records", date_format="iso", force_ascii=False)),
        }
        self._flow_cache_path(ticker).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _slice_by_date(df: pd.DataFrame, *, start_date: str | None, end_date: str) -> pd.DataFrame:
        return KRDailyPriceCollector._slice_by_date(df, start_date=start_date, end_date=end_date)


class KRIndexPriceCollector:
    """Collect KOSPI/KOSDAQ index daily OHLCV through Kiwoom REST."""

    def __init__(self, client: KiwoomRESTClient | None = None, cache_dir: Path = DATA_CACHE_DIR / "indexes" / "kiwoom_kr") -> None:
        self.client = client or KiwoomRESTClient()
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.errors: list[dict[str, Any]] = []

    def load_index_history(
        self,
        *,
        start_date: str | None,
        end_date: str,
        bars: int = DEFAULT_CONFIG.price_lookback_bars,
    ) -> dict[str, pd.DataFrame]:
        requested_bars = bars_for_date_range(start_date, end_date, minimum=bars)
        history: dict[str, pd.DataFrame] = {}
        for market in ("KOSPI", "KOSDAQ"):
            cached = self._read_index_cache(market, start_date=start_date, end_date=end_date, bars=requested_bars)
            if cached is None:
                try:
                    df = self.client.load_index_ohlcv(market, end_date=end_date, bars=requested_bars)
                except Exception as exc:
                    self.errors.append({"market": market, "error": str(exc)})
                    history[market] = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
                    continue
                self._write_index_cache(market, df)
                cached = KRDailyPriceCollector._slice_by_date(df, start_date=start_date, end_date=end_date)
            history[market] = cached
        return history

    def _index_cache_path(self, market: str) -> Path:
        return self.cache_dir / f"{market}.json"

    def _read_index_cache(self, market: str, *, start_date: str | None, end_date: str, bars: int) -> pd.DataFrame | None:
        path = self._index_cache_path(market)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("data", payload if isinstance(payload, list) else [])
            df = pd.DataFrame(rows)
            if df.empty or "date" not in df.columns:
                return None
            df = df.sort_values("date").reset_index(drop=True)
            if str(df["date"].max()) < end_date:
                return None
            if start_date is not None and str(df["date"].min()) > start_date:
                return None
            return KRDailyPriceCollector._slice_by_date(df, start_date=start_date, end_date=end_date).tail(bars).reset_index(drop=True)
        except Exception:
            return None

    def _write_index_cache(self, market: str, df: pd.DataFrame) -> None:
        payload = {
            "created_at": time.time(),
            "market": market,
            "data": json.loads(df.to_json(orient="records", date_format="iso", force_ascii=False)),
        }
        self._index_cache_path(market).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
