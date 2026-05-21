"""CANSLIM + Turtle wrappers for trading candidates."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from canslim import EarningsAnalyzer, LeadershipAnalyzer, NewnessAnalyzer, SupplyAnalyzer  # noqa: E402
from turtle import TurtleSignalGenerator  # noqa: E402

from trading.kiwoom_client import TradingSecurity
from trading.macro_dart_score import MacroDartScore


class CANSLIMTurtleEvaluator:
    required_criteria = ("C", "A", "N", "S", "L")

    def __init__(self) -> None:
        self.earnings = EarningsAnalyzer()
        self.newness = NewnessAnalyzer()
        self.supply = SupplyAnalyzer()
        self.leadership = LeadershipAnalyzer()
        self.turtle = TurtleSignalGenerator()

    def evaluate_universe(
        self,
        securities: list[TradingSecurity],
        price_history: dict[str, pd.DataFrame],
        financials_by_common_ticker: dict[str, dict[str, Any]],
        macro_scores: list[MacroDartScore],
    ) -> list[dict[str, Any]]:
        rs_values: dict[str, float | None] = {}
        for security in securities:
            rs_values[security.ticker] = self.leadership.calculate_rs_rating(
                security.ticker,
                price_history.get(security.ticker),
            )
        available_rs = [value for value in rs_values.values() if value is not None]
        score_by_ticker = {score.ticker: score for score in macro_scores}
        rows: list[dict[str, Any]] = []
        for security in securities:
            ohlcv = price_history.get(security.ticker)
            financials = financials_by_common_ticker.get(security.common_ticker or security.ticker, {})
            score = score_by_ticker.get(security.ticker)
            criteria = self._evaluate_criteria(security, ohlcv, financials, available_rs, rs_values.get(security.ticker))
            signals = self.turtle.generate_signals(security.ticker, ohlcv)
            buy_signals = [signal for signal in signals if signal in {"S1_Buy", "S2_Buy"}]
            turtle_system = "S2" if "S2_Buy" in buy_signals else "S1" if "S1_Buy" in buy_signals else None
            rows.append(
                {
                    "ticker": security.ticker,
                    "common_ticker": security.common_ticker or security.ticker,
                    "name": security.name,
                    "market": security.market,
                    "sector": security.sector,
                    "close": self._last_close(ohlcv),
                    "criteria": criteria,
                    "canslim_pass": all(criteria.get(name, {}).get("pass") for name in self.required_criteria),
                    "turtle_signals": signals,
                    "turtle_system": turtle_system,
                    "turtle_exit_level": self._turtle_exit_level(ohlcv, turtle_system),
                    "macro_score": 0.0 if score is None else score.macro_score,
                    "dart_disclosure_score": 0.0 if score is None else score.dart_disclosure_score,
                    "combined_macro_dart_score": 0.0 if score is None else score.combined_macro_dart_score,
                    "macro_rank": 999999 if score is None else score.macro_rank,
                    "risk_flags": [] if score is None else score.risk_flags,
                    "raw_dart_score": 0.0 if score is None else score.raw_dart_score,
                    "macro_industry_code": None if score is None else score.industry_code,
                    "macro_source": None if score is None else score.macro_source,
                    "macro_snapshot_run_id": None if score is None else score.macro_snapshot_run_id,
                }
            )
        rows.sort(key=lambda row: (-row["combined_macro_dart_score"], row["macro_rank"], row["ticker"]))
        return rows

    def candidates(self, evaluated_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            row
            for row in evaluated_rows
            if row.get("canslim_pass") and any(signal in {"S1_Buy", "S2_Buy"} for signal in row.get("turtle_signals", []))
        ]

    def _evaluate_criteria(
        self,
        security: TradingSecurity,
        ohlcv: pd.DataFrame | None,
        financials: dict[str, Any],
        rs_values: list[float],
        rs_value: float | None,
    ) -> dict[str, Any]:
        c_pass, c_details = self.earnings.check_c_criterion(security.ticker, financials)
        a_pass, a_details = self.earnings.check_a_criterion(security.ticker, financials)
        n_pass, n_details = self.newness.check_n_criterion(security.ticker, ohlcv)
        s_pass, s_details = self.supply.check_s_criterion(security.ticker, ohlcv)
        percentile = self._percentile_rank(rs_values, rs_value)
        l_pass, l_details = self.leadership.check_l_criterion(security.ticker, percentile)
        if rs_value is not None:
            l_details["rs_value"] = round(float(rs_value), 2)
        return {
            "C": {"pass": c_pass, "details": c_details},
            "A": {"pass": a_pass, "details": a_details},
            "N": {"pass": n_pass, "details": n_details},
            "S": {"pass": s_pass, "details": s_details},
            "L": {"pass": l_pass, "details": l_details},
        }

    @staticmethod
    def _percentile_rank(values: list[float], value: float | None) -> float | None:
        if value is None or not values:
            return None
        less = sum(1 for item in values if item < value)
        equal = sum(1 for item in values if item == value)
        return ((less + 0.5 * equal) / len(values)) * 100

    @staticmethod
    def _last_close(ohlcv: pd.DataFrame | None) -> float | None:
        if ohlcv is None or ohlcv.empty:
            return None
        return float(ohlcv["close"].iloc[-1])

    @staticmethod
    def _turtle_exit_level(ohlcv: pd.DataFrame | None, turtle_system: str | None) -> float | None:
        if ohlcv is None or ohlcv.empty or turtle_system is None:
            return None
        if turtle_system == "S2" and len(ohlcv) >= 21:
            return float(ohlcv["low"].iloc[-21:-1].min())
        if len(ohlcv) >= 11:
            return float(ohlcv["low"].iloc[-11:-1].min())
        return None
