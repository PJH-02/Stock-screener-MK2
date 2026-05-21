"""CANSLIM S criterion on normalized OHLCV data."""

from __future__ import annotations

from utils import setup_logger

logger = setup_logger("supply_analyzer")


class SupplyAnalyzer:
    """Analyzes S (Supply and Demand) criterion."""

    def check_s_criterion(self, ticker, ohlcv):
        """S - 5-day volume is unusually high or unusually low vs the 50-day average."""
        if ohlcv is None or ohlcv.empty:
            return False, {"reason": "No OHLCV data available"}

        try:
            if len(ohlcv) < 50:
                return False, {"reason": "Insufficient volume data"}

            vol_5d = ohlcv["volume"].tail(5).mean()
            vol_50d = ohlcv["volume"].tail(50).mean()
            if vol_50d == 0:
                return False, {"reason": "Zero 50-day average volume"}

            volume_ratio = vol_5d / vol_50d
            passes = bool((volume_ratio > 2.0) or (volume_ratio < 0.3))
            return passes, {
                "vol_5d_avg": int(vol_5d),
                "vol_50d_avg": int(vol_50d),
                "volume_ratio": round(float(volume_ratio), 3),
                "signal": "High" if volume_ratio > 2.0 else "Low" if volume_ratio < 0.3 else "Normal",
            }
        except Exception as exc:
            logger.debug(f"Error checking S criterion for {ticker}: {exc}")
            return False, {"reason": str(exc)}
