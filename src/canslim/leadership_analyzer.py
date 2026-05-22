"""CANSLIM L criterion helpers."""

from __future__ import annotations

from utils import setup_logger

logger = setup_logger("leadership_analyzer")


class LeadershipAnalyzer:
    """Calculates 12-month weighted Relative Strength values."""

    def __init__(self, data_manager=None):
        self.data_manager = data_manager

    def is_available(self):
        return True

    def calculate_rs_rating(self, ticker, ohlcv):
        if ohlcv is None or ohlcv.empty or len(ohlcv) < 252:
            return None

        try:
            year_data = ohlcv.tail(252)
            q4 = year_data.tail(63)
            q3 = year_data.tail(126).head(63)
            q2 = year_data.tail(189).head(63)
            q1 = year_data.head(63)

            def calc_return(df):
                if df.empty or len(df) < 2 or df["close"].iloc[0] == 0:
                    return 0
                return ((df["close"].iloc[-1] - df["close"].iloc[0]) / df["close"].iloc[0]) * 100

            return (calc_return(q4) * 0.4) + (calc_return(q3) * 0.2) + (calc_return(q2) * 0.2) + (calc_return(q1) * 0.2)
        except Exception as exc:
            logger.debug(f"Error calculating RS rating for {ticker}: {exc}")
            return None

    def check_l_criterion(self, ticker, rs_percentile, ohlcv=None):
        high_details = self._near_52w_high_details(ohlcv)
        if rs_percentile is None:
            return False, {"reason": "RS percentile unavailable", **high_details}
        rs_pass = rs_percentile >= 90
        high_pass = bool(high_details.get("near_52w_high_pass"))
        return bool(rs_pass and high_pass), {
            "rs_percentile": round(float(rs_percentile), 2),
            "rs_pass": bool(rs_pass),
            **high_details,
        }

    def _near_52w_high_details(self, ohlcv):
        if ohlcv is None or ohlcv.empty:
            return {"near_52w_high_pass": False, "reason": "No OHLCV data available"}
        if len(ohlcv) < 200:
            return {"near_52w_high_pass": False, "reason": "Insufficient price history"}
        recent = ohlcv.tail(252)
        high_52w = float(recent["high"].max())
        current_price = float(ohlcv["close"].iloc[-1])
        if high_52w == 0:
            return {"near_52w_high_pass": False, "reason": "Zero 52-week high"}
        drawdown_from_high = ((high_52w - current_price) / high_52w) * 100
        return {
            "current_price": round(current_price, 4),
            "52w_high": round(high_52w, 4),
            "drawdown_from_52w_high": round(drawdown_from_high, 2),
            "near_52w_high_pass": bool(drawdown_from_high <= 25),
        }
