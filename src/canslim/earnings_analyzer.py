"""CANSLIM C and A criteria on normalized financial statements."""

from __future__ import annotations

from typing import Any

from utils import setup_logger

logger = setup_logger("earnings_analyzer")


class EarningsAnalyzer:
    """Analyzes C (current earnings) and A (annual earnings) criteria."""

    def __init__(self, data_manager=None):
        self.data_manager = data_manager

    def check_c_criterion(self, ticker: str, financial_data: dict[str, Any] | None):
        """
        C - Current Earnings: EPS YoY growth >= 25% for the latest comparable quarter.
        """
        quarters = self._records(financial_data, "quarterly")
        if len(quarters) < 2:
            return False, {"reason": "Insufficient quarterly EPS data"}

        by_period: dict[tuple[int, str], dict[str, Any]] = {}
        for record in quarters:
            year = record.get("year")
            period = record.get("period")
            eps = record.get("eps")
            if isinstance(year, int) and isinstance(period, str) and eps is not None:
                by_period[(year, period)] = record

        comparable = []
        for record in sorted(quarters, key=lambda item: (item.get("year", 0), item.get("period", "")), reverse=True):
            year = record.get("year")
            period = record.get("period")
            eps = record.get("eps")
            previous = by_period.get((year - 1, period)) if isinstance(year, int) and isinstance(period, str) else None
            previous_eps = previous.get("eps") if previous else None
            growth = self._growth(eps, previous_eps)
            if growth is None:
                continue
            comparable.append(
                {
                    "year": year,
                    "period": period,
                    "eps": round(float(eps), 4),
                    "prior_year_eps": round(float(previous_eps), 4),
                    "yoy_growth": round(growth, 2),
                }
            )
            if len(comparable) == 1:
                break

        if not comparable:
            return False, {"reason": "No comparable quarterly EPS records", "quarters": comparable}

        passes = comparable[0]["yoy_growth"] >= 25
        return passes, {"quarters": comparable}

    def check_a_criterion(self, ticker: str, financial_data: dict[str, Any] | None):
        """
        A - Annual Earnings: 3-year EPS CAGR >= 25%.
        """
        annual = self._records(financial_data, "annual")
        annual = sorted(annual, key=lambda item: item.get("year", 0), reverse=True)
        eps_records = [record for record in annual if record.get("eps") is not None]
        if len(eps_records) < 4:
            return False, {"reason": "Insufficient annual EPS history"}

        latest = eps_records[0]
        target_year = latest.get("year") - 3 if isinstance(latest.get("year"), int) else None
        base = next((record for record in eps_records if record.get("year") == target_year), eps_records[3])
        years = max(1, int(latest.get("year", 0)) - int(base.get("year", 0)))
        eps_cagr = self._cagr(latest.get("eps"), base.get("eps"), years)
        eps_pass = eps_cagr is not None and eps_cagr >= 25

        return bool(eps_pass), {
            "latest_year": latest.get("year"),
            "base_year": base.get("year"),
            "eps_cagr_3y": round(eps_cagr, 2) if eps_cagr is not None else None,
            "eps_pass": bool(eps_pass),
        }

    def _records(self, financial_data: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
        if not financial_data:
            return []
        records = financial_data.get(key, [])
        return records if isinstance(records, list) else []

    def _growth(self, current: Any, previous: Any) -> float | None:
        try:
            current_value = float(current)
            previous_value = float(previous)
        except (TypeError, ValueError):
            return None
        if previous_value == 0:
            return None
        return ((current_value - previous_value) / abs(previous_value)) * 100

    def _cagr(self, current: Any, base: Any, years: int) -> float | None:
        try:
            current_value = float(current)
            base_value = float(base)
        except (TypeError, ValueError):
            return None
        if current_value <= 0 or base_value <= 0:
            return None
        return ((current_value / base_value) ** (1 / years) - 1) * 100

    def _latest_roe(self, annual: list[dict[str, Any]]) -> float | None:
        for record in annual:
            if record.get("roe") is not None:
                return float(record["roe"])

        sorted_records = sorted(annual, key=lambda item: item.get("year", 0), reverse=True)
        if len(sorted_records) < 2:
            return None
        latest = sorted_records[0]
        previous = sorted_records[1]
        net_income = latest.get("net_income")
        equity = latest.get("equity")
        previous_equity = previous.get("equity")
        if net_income is None or equity is None or previous_equity is None:
            return None
        average_equity = (float(equity) + float(previous_equity)) / 2
        if average_equity == 0:
            return None
        return (float(net_income) / average_equity) * 100
