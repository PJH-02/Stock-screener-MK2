"""CANSLIM M criterion for market trend."""

from __future__ import annotations

import pandas as pd


class MarketAnalyzer:
    """Checks whether the relevant index is in an uptrend."""

    def check_m_criterion(self, ticker: str, index_ohlcv: pd.DataFrame | None):
        if index_ohlcv is None or index_ohlcv.empty:
            return False, {"reason": "Market index data unavailable"}
        if len(index_ohlcv) < 24:
            return False, {"reason": "Insufficient market index history"}

        frame = index_ohlcv.copy()
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna(subset=["close"]).reset_index(drop=True)
        if len(frame) < 24:
            return False, {"reason": "Insufficient market index close data"}

        ma20 = frame["close"].rolling(20).mean()
        recent = frame.tail(5).copy()
        recent_ma20 = ma20.tail(5)
        checks = []
        for (_, row), ma_value in zip(recent.iterrows(), recent_ma20):
            close = float(row["close"])
            passed = bool(close > float(ma_value)) if pd.notna(ma_value) else False
            checks.append(
                {
                    "date": str(row.get("date", "")),
                    "close": round(close, 4),
                    "ma20": round(float(ma_value), 4) if pd.notna(ma_value) else None,
                    "pass": passed,
                }
            )
        return all(item["pass"] for item in checks), {"last_5_days": checks}
