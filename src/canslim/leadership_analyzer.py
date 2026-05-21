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

    def check_l_criterion(self, ticker, rs_percentile):
        if rs_percentile is None:
            return False, {"reason": "RS percentile unavailable"}
        return rs_percentile >= 80, {"rs_percentile": round(float(rs_percentile), 2)}
