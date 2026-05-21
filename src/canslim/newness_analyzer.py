"""CANSLIM N criterion on normalized OHLCV data."""

from __future__ import annotations

from utils import setup_logger

logger = setup_logger("newness_analyzer")


class NewnessAnalyzer:
    """Analyzes N (Newness) criterion."""

    def check_n_criterion(self, ticker, ohlcv):
        """N - Current price is at least 85% of the 52-week high."""
        if ohlcv is None or ohlcv.empty:
            return False, {"reason": "No OHLCV data available"}

        try:
            recent_data = ohlcv.tail(252)
            if len(recent_data) < 200:
                return False, {"reason": "Insufficient price history"}

            high_52w = recent_data["high"].max()
            current_price = ohlcv["close"].iloc[-1]
            if high_52w == 0:
                return False, {"reason": "Zero 52-week high"}

            price_ratio = (current_price / high_52w) * 100
            return bool(price_ratio >= 85), {
                "current_price": round(float(current_price), 4),
                "52w_high": round(float(high_52w), 4),
                "price_ratio": round(float(price_ratio), 2),
            }
        except Exception as exc:
            logger.debug(f"Error checking N criterion for {ticker}: {exc}")
            return False, {"reason": str(exc)}
