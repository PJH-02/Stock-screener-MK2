"""CANSLIM I criterion for institutional accumulation."""

from __future__ import annotations

from typing import Any


class InstitutionalAnalyzer:
    """Checks whether institutional net buying has improved over the last six months."""

    def check_i_criterion(self, ticker: str, institutional_flow: list[dict[str, Any]] | None):
        if not institutional_flow:
            return False, {"reason": "Institutional flow data unavailable"}

        values = []
        for row in institutional_flow:
            value = row.get("institutional_net_buy")
            if value is None:
                value = row.get("organ_pure_buy_quantity")
            if value is None:
                value = row.get("institution_net_buy")
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                continue

        if len(values) < 40:
            return False, {"reason": "Insufficient institutional flow history", "records": len(values)}

        recent = values[-126:] if len(values) >= 126 else values
        midpoint = len(recent) // 2
        first_half_sum = sum(recent[:midpoint])
        second_half_sum = sum(recent[midpoint:])
        increase = second_half_sum - first_half_sum
        passes = second_half_sum > 0 and increase > 0
        return passes, {
            "records": len(recent),
            "first_half_net_buy": round(first_half_sum, 2),
            "second_half_net_buy": round(second_half_sum, 2),
            "net_buy_increase": round(increase, 2),
        }
