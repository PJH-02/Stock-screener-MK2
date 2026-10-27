from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import PROCESSED_DIR, UNIVERSE_CACHE


DAILY_FILES = {
    "open": "stock_open.parquet",
    "high": "stock_high.parquet",
    "low": "stock_low.parquet",
    "close": "stock_close.parquet",
    "volume": "stock_volume.parquet",
    "institution": "stock_institution.parquet",
    "free_float": "stock_free_float.parquet",
    "listed_shares": "stock_listed_shares.parquet",
    "market_cap": "stock_market_cap.parquet",
}


@dataclass
class MarketData:
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    institution_krw: pd.DataFrame
    free_float: pd.DataFrame
    listed_shares: pd.DataFrame
    market_cap_krw: pd.DataFrame
    earnings_krw: pd.DataFrame
    index: pd.DataFrame
    metadata: pd.DataFrame
    universe: pd.DataFrame
    market: pd.Series
    sector: pd.Series

    @property
    def tickers(self) -> list[str]:
        return list(self.close.columns)

    @property
    def dates(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.close.index)


def load_env(path: Path | str = ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _read_wide(name: str) -> pd.DataFrame:
    df = pd.read_parquet(PROCESSED_DIR / DAILY_FILES[name])
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return df.astype("float32")


def load_universe(path: Path = UNIVERSE_CACHE) -> pd.DataFrame:
    rows = json.loads(path.read_text(encoding="utf-8"))
    df = pd.DataFrame(rows)
    df["code"] = df["ticker"].astype(str).str.zfill(6)
    df["ticker"] = "A" + df["code"]
    return df[["ticker", "code", "name", "market", "sector"]].drop_duplicates("ticker")


def load_market_data() -> MarketData:
    metadata = pd.read_parquet(PROCESSED_DIR / "stock_metadata.parquet")
    universe = load_universe()
    metadata = metadata.merge(
        universe[["ticker", "market", "sector"]],
        on="ticker",
        how="inner",
    )

    tickers = metadata["ticker"].tolist()
    frames = {name: _read_wide(name) for name in DAILY_FILES}

    # Use the intersection of all daily stock dates. The current raw files have
    # 2026-06-02 in some datasets but not close/volume/mcap, so it is excluded.
    common_dates = None
    for frame in frames.values():
        common_dates = set(frame.index) if common_dates is None else common_dates & set(frame.index)
    dates = pd.DatetimeIndex(sorted(common_dates))

    common_tickers = set(tickers)
    for frame in frames.values():
        common_tickers &= set(frame.columns)
    ordered_tickers = [ticker for ticker in tickers if ticker in common_tickers]

    frames = {
        name: frame.loc[dates, ordered_tickers].sort_index(axis=1)
        for name, frame in frames.items()
    }
    ordered_tickers = list(frames["close"].columns)
    metadata = metadata.set_index("ticker").loc[ordered_tickers].reset_index()

    raw_close_proxy = (frames["market_cap"] * 1_000_000.0) / frames["listed_shares"]
    adjustment = (frames["close"] / raw_close_proxy).replace([np.inf, -np.inf], np.nan)
    adjustment = adjustment.ffill().bfill()
    for key in ["open", "high", "low"]:
        frames[key] = frames[key] * adjustment
    frames["high"] = pd.concat([frames["high"].stack(), frames["open"].stack(), frames["close"].stack()], axis=1).max(axis=1).unstack()
    frames["low"] = pd.concat([frames["low"].stack(), frames["open"].stack(), frames["close"].stack()], axis=1).min(axis=1).unstack()

    earnings = pd.read_parquet(PROCESSED_DIR / "stock_earnings_quarterly.parquet")
    earnings.index = pd.to_datetime(earnings.index)
    earnings = earnings.sort_index()
    earnings = earnings.reindex(columns=ordered_tickers).astype("float32") * 1_000.0

    index = pd.read_parquet(PROCESSED_DIR / "index.parquet")
    index.index = pd.to_datetime(index.index)
    index = index.sort_index()
    index = index.reindex(dates).ffill()

    market = metadata.set_index("ticker")["market"]
    sector = metadata.set_index("ticker")["sector"]

    return MarketData(
        open=frames["open"],
        high=frames["high"],
        low=frames["low"],
        close=frames["close"],
        volume=frames["volume"],
        institution_krw=frames["institution"] * 10_000.0,
        free_float=frames["free_float"],
        listed_shares=frames["listed_shares"],
        market_cap_krw=frames["market_cap"] * 1_000_000.0,
        earnings_krw=earnings,
        index=index,
        metadata=metadata,
        universe=universe,
        market=market,
        sector=sector,
    )


def next_trading_date(dates: pd.DatetimeIndex, target: pd.Timestamp) -> pd.Timestamp | None:
    pos = dates.searchsorted(pd.Timestamp(target), side="left")
    if pos >= len(dates):
        return None
    return dates[pos]


def first_backtest_date(dates: pd.DatetimeIndex, warmup_years: int) -> pd.Timestamp:
    target = dates[0] + pd.DateOffset(years=warmup_years)
    return dates[dates.searchsorted(target, side="left")]


def rank_pct(df: pd.DataFrame) -> pd.DataFrame:
    return df.rank(axis=1, pct=True).astype("float32")


def rolling_slope(df: pd.DataFrame, window: int) -> pd.DataFrame:
    values = df.to_numpy(dtype="float64", copy=True)
    mask = np.isfinite(values)
    values = np.where(mask, values, 0.0)

    idx = np.arange(values.shape[0], dtype="float64")[:, None]
    csum_y = np.cumsum(values, axis=0)
    csum_iy = np.cumsum(values * idx, axis=0)
    csum_n = np.cumsum(mask.astype("float64"), axis=0)

    sum_y = csum_y.copy()
    sum_iy = csum_iy.copy()
    count = csum_n.copy()
    if window < values.shape[0]:
        sum_y[window:] -= csum_y[:-window]
        sum_iy[window:] -= csum_iy[:-window]
        count[window:] -= csum_n[:-window]

    starts = np.arange(values.shape[0], dtype="float64") - window + 1
    starts = np.maximum(starts, 0.0)[:, None]
    weighted = sum_iy - starts * sum_y

    n = float(window)
    sum_x = n * (n - 1.0) / 2.0
    sum_x2 = (n - 1.0) * n * (2.0 * n - 1.0) / 6.0
    denom = n * sum_x2 - sum_x * sum_x
    slope = (n * weighted - sum_x * sum_y) / denom
    slope[count < window] = np.nan
    return pd.DataFrame(slope, index=df.index, columns=df.columns, dtype="float32")
