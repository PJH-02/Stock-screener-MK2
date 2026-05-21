"""Turtle Trading signal generation on normalized OHLCV data."""

from __future__ import annotations

from utils import setup_logger

logger = setup_logger("turtle_signal")


class TurtleSignalGenerator:
    """Generates Turtle Trading entry and exit signals."""

    def generate_signals(self, ticker, ohlcv):
        if ohlcv is None or ohlcv.empty or len(ohlcv) < 55:
            return []

        try:
            signals = []
            current_high = ohlcv["high"].iloc[-1]
            current_low = ohlcv["low"].iloc[-1]

            high_20d = ohlcv["high"].iloc[-21:-1].max()
            high_55d = ohlcv["high"].iloc[-56:-1].max()
            low_10d = ohlcv["low"].iloc[-11:-1].min()
            low_20d = ohlcv["low"].iloc[-21:-1].min()

            if current_high > high_20d:
                signals.append("S1_Buy")
            if current_high > high_55d:
                signals.append("S2_Buy")
            if current_low < low_10d:
                signals.append("S1_Exit")
            if current_low < low_20d:
                signals.append("S2_Exit")

            return signals
        except Exception as exc:
            logger.debug(f"Error generating turtle signals for {ticker}: {exc}")
            return []

    def get_signal_details(self, ticker, ohlcv):
        if ohlcv is None or ohlcv.empty or len(ohlcv) < 55:
            return {}

        try:
            current_price = ohlcv["close"].iloc[-1]
            high_20d = ohlcv["high"].iloc[-21:-1].max()
            high_55d = ohlcv["high"].iloc[-56:-1].max()
            low_10d = ohlcv["low"].iloc[-11:-1].min()
            low_20d = ohlcv["low"].iloc[-21:-1].min()

            return {
                "current_price": current_price,
                "high_20d": high_20d,
                "high_55d": high_55d,
                "low_10d": low_10d,
                "low_20d": low_20d,
                "distance_to_s1_buy": round(((high_20d - current_price) / current_price) * 100, 2),
                "distance_to_s2_buy": round(((high_55d - current_price) / current_price) * 100, 2),
                "distance_to_s1_exit": round(((current_price - low_10d) / current_price) * 100, 2),
                "distance_to_s2_exit": round(((current_price - low_20d) / current_price) * 100, 2),
            }
        except Exception as exc:
            logger.debug(f"Error getting signal details for {ticker}: {exc}")
            return {}
