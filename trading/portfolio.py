"""Portfolio construction and position lifecycle rules."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Literal

from trading.config import DEFAULT_CONFIG, StrategyConfig


AllocationMethod = Literal["equal_weight", "inverse_rank_weight"]


@dataclass
class Position:
    ticker: str
    shares: int
    entry_price: float
    turtle_system: str
    turtle_exit_level: float
    took_half_profit: bool = False

    def market_value(self, price: float) -> float:
        return self.shares * price

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Order:
    date: str
    ticker: str
    side: str
    shares: int
    expected_price: float
    reason: str
    estimated_value: float
    estimated_fee: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def capped_weights(raw_weights: dict[str, float], cap: float) -> dict[str, float]:
    if not raw_weights:
        return {}
    remaining = dict(raw_weights)
    capped: dict[str, float] = {}
    target_total = min(1.0, sum(raw_weights.values()))
    while remaining:
        remaining_total = sum(remaining.values())
        if remaining_total <= 0:
            break
        free_total = target_total - sum(capped.values())
        changed = False
        for ticker, raw in list(remaining.items()):
            weight = raw / remaining_total * free_total
            if weight >= cap:
                capped[ticker] = cap
                remaining.pop(ticker)
                changed = True
        if not changed:
            for ticker, raw in remaining.items():
                capped[ticker] = raw / remaining_total * free_total
            break
    return {ticker: round(weight, 10) for ticker, weight in capped.items() if weight > 0}


def target_weights(
    candidates: list[dict[str, Any]],
    method: AllocationMethod,
    *,
    max_weight: float = DEFAULT_CONFIG.max_position_weight,
) -> dict[str, float]:
    if not candidates:
        return {}
    if method == "equal_weight":
        raw = {str(candidate["ticker"]): 1.0 / len(candidates) for candidate in candidates}
    elif method == "inverse_rank_weight":
        raw = {
            str(candidate["ticker"]): 1.0 / max(int(candidate.get("macro_rank") or index), 1)
            for index, candidate in enumerate(candidates, start=1)
        }
    else:
        raise ValueError(f"Unsupported allocation method: {method}")
    return capped_weights(raw, max_weight)


def shares_for_value(value: float, price: float, commission_rate: float = DEFAULT_CONFIG.commission_rate) -> int:
    if value <= 0 or price <= 0:
        return 0
    return int(math.floor(value / (price * (1 + commission_rate))))


def exit_reason(position: Position, close_price: float) -> str | None:
    full_exit = close_price <= position.entry_price * (1 - DEFAULT_CONFIG.stop_loss_rate) or close_price <= position.turtle_exit_level
    if full_exit:
        return "full_exit"
    if not position.took_half_profit and close_price >= position.entry_price * (1 + DEFAULT_CONFIG.take_profit_rate):
        return "half_take_profit"
    return None


def build_entry_orders(
    *,
    date: str,
    cash: float,
    candidates: list[dict[str, Any]],
    open_prices: dict[str, float],
    method: AllocationMethod,
    config: StrategyConfig = DEFAULT_CONFIG,
) -> list[Order]:
    weights = target_weights(candidates, method, max_weight=config.max_position_weight)
    by_ticker = {str(candidate["ticker"]): candidate for candidate in candidates}
    orders: list[Order] = []
    for ticker, weight in weights.items():
        price = float(open_prices.get(ticker) or 0)
        value = cash * weight
        shares = shares_for_value(value, price, config.commission_rate)
        if shares < 1:
            continue
        estimated_value = shares * price
        orders.append(
            Order(
                date=date,
                ticker=ticker,
                side="BUY",
                shares=shares,
                expected_price=price,
                reason=f"entry_{method}_{by_ticker[ticker].get('turtle_system')}",
                estimated_value=estimated_value,
                estimated_fee=estimated_value * config.commission_rate,
            )
        )
    return orders


def choose_winner(results: dict[str, dict[str, float]]) -> str:
    if not results:
        raise ValueError("No backtest results to compare.")
    return sorted(
        results,
        key=lambda name: (
            -float(results[name].get("cagr", 0.0)),
            -float(results[name].get("final_equity", 0.0)),
            float(results[name].get("max_drawdown", 0.0)),
        ),
    )[0]
