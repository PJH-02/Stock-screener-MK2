"""CANSLIM S criterion on normalized OHLCV data."""

from __future__ import annotations

from utils import setup_logger

logger = setup_logger("supply_analyzer")


class SupplyAnalyzer:
    """Analyzes S (Supply and Demand) criterion."""

    def check_s_criterion(self, ticker, ohlcv):
        """S - Latest volume is at least 2x the prior 10-day average."""
        if ohlcv is None or ohlcv.empty:
            return False, {"reason": "No OHLCV data available"}

        try:
            if len(ohlcv) < 11:
                return False, {"reason": "Insufficient volume data"}

            latest_volume = float(ohlcv["volume"].iloc[-1])
            prior_10d_avg = float(ohlcv["volume"].iloc[-11:-1].mean())
            if prior_10d_avg == 0:
                return False, {"reason": "Zero prior 10-day average volume"}

            volume_ratio = latest_volume / prior_10d_avg
            passes = bool(volume_ratio >= 2.0)
            return passes, {
                "latest_volume": int(latest_volume),
                "prior_10d_avg": int(prior_10d_avg),
                "volume_ratio": round(float(volume_ratio), 3),
                "signal": "High" if passes else "Normal",
            }
        except Exception as exc:
            logger.debug(f"Error checking S criterion for {ticker}: {exc}")
            return False, {"reason": str(exc)}
