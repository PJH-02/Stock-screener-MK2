"""One-year next-open backtest engine for the trading strategy."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trading.config import DEFAULT_CONFIG, RESULTS_DIR, StrategyConfig
from trading.portfolio import AllocationMethod, Order, Position, build_entry_orders, choose_winner, exit_reason


def price_on(df: pd.DataFrame, date: str, column: str) -> float | None:
    row = df[df["date"] == date]
    if row.empty:
        return None
    return float(row[column].iloc[0])


def turtle_exit_level_on(df: pd.DataFrame, date: str, turtle_system: str) -> float | None:
    matches = df.index[df["date"] == date].tolist()
    if not matches:
        return None
    idx = matches[0]
    lookback = 20 if turtle_system == "S2" else 10
    if idx < lookback:
        return None
    return float(df["low"].iloc[idx - lookback : idx].min())


class BacktestEngine:
    def __init__(self, *, config: StrategyConfig = DEFAULT_CONFIG) -> None:
        self.config = config

    def run(
        self,
        *,
        price_history: dict[str, pd.DataFrame],
        candidate_schedule: dict[str, list[dict[str, Any]]],
        method: AllocationMethod,
        initial_capital: float | None = None,
        start: str | None = None,
        end: str | None = None,
        point_in_time: bool = False,
    ) -> dict[str, Any]:
        cash = float(initial_capital or self.config.initial_capital)
        positions: dict[str, Position] = {}
        orders: list[Order] = []
        equity_curve: list[dict[str, float | str]] = []
        fees = 0.0

        dates = sorted({date for df in price_history.values() for date in df["date"].astype(str).tolist()})
        if start is not None:
            dates = [date_value for date_value in dates if date_value >= start]
        if end is not None:
            dates = [date_value for date_value in dates if date_value <= end]
        for idx, current_date in enumerate(dates[:-1]):
            next_date = dates[idx + 1]
            close_prices = {ticker: price_on(df, current_date, "close") for ticker, df in price_history.items()}
            open_prices = {ticker: price_on(df, next_date, "open") for ticker, df in price_history.items()}

            for ticker, position in list(positions.items()):
                close = close_prices.get(ticker)
                open_price = open_prices.get(ticker)
                if close is None or open_price is None:
                    continue
                dynamic_exit = turtle_exit_level_on(price_history[ticker], current_date, position.turtle_system)
                if dynamic_exit is not None:
                    position.turtle_exit_level = dynamic_exit
                reason = exit_reason(position, close)
                if reason == "full_exit":
                    fee = position.shares * open_price * self.config.commission_rate
                    cash += position.shares * open_price - fee
                    fees += fee
                    orders.append(
                        Order(next_date, ticker, "SELL", position.shares, open_price, reason, position.shares * open_price, fee)
                    )
                    positions.pop(ticker)
                elif reason == "half_take_profit":
                    shares = position.shares // 2
                    if shares >= 1:
                        fee = shares * open_price * self.config.commission_rate
                        cash += shares * open_price - fee
                        fees += fee
                        orders.append(Order(next_date, ticker, "SELL", shares, open_price, reason, shares * open_price, fee))
                        position.shares -= shares
                        position.took_half_profit = True

            candidates = [
                candidate
                for candidate in candidate_schedule.get(current_date, [])
                if str(candidate["ticker"]) not in positions and open_prices.get(str(candidate["ticker"])) is not None
            ]
            entry_orders = build_entry_orders(
                date=next_date,
                cash=cash,
                candidates=candidates,
                open_prices={ticker: float(price) for ticker, price in open_prices.items() if price is not None},
                method=method,
                config=self.config,
            )
            for order in entry_orders:
                total_cost = order.estimated_value + order.estimated_fee
                if total_cost > cash:
                    continue
                candidate = next(item for item in candidates if str(item["ticker"]) == order.ticker)
                cash -= total_cost
                fees += order.estimated_fee
                positions[order.ticker] = Position(
                    ticker=order.ticker,
                    shares=order.shares,
                    entry_price=order.expected_price,
                    turtle_system=str(candidate.get("turtle_system") or "S1"),
                    turtle_exit_level=float(candidate.get("turtle_exit_level") or 0),
                )
                orders.append(order)

            equity = cash
            for ticker, position in positions.items():
                close = close_prices.get(ticker)
                if close is not None:
                    equity += position.market_value(close)
            equity_curve.append({"date": current_date, "equity": equity, "cash": cash})

        metrics = self._metrics(equity_curve, initial_capital or self.config.initial_capital)
        return {
            "method": method,
            "point_in_time": point_in_time,
            "metrics": {**metrics, "fees": round(fees, 2), "orders": len(orders)},
            "equity_curve": equity_curve,
            "orders": [asdict(order) for order in orders],
            "ending_positions": {ticker: position.to_dict() for ticker, position in positions.items()},
        }

    def _metrics(self, equity_curve: list[dict[str, float | str]], initial_capital: float) -> dict[str, float]:
        if not equity_curve:
            return {"final_equity": initial_capital, "cagr": 0.0, "max_drawdown": 0.0, "sharpe": 0.0}
        equities = np.array([float(row["equity"]) for row in equity_curve])
        final_equity = float(equities[-1])
        years = max(len(equities) / 252, 1 / 252)
        cagr = (final_equity / initial_capital) ** (1 / years) - 1
        peaks = np.maximum.accumulate(equities)
        drawdowns = (equities - peaks) / peaks
        returns = pd.Series(equities).pct_change().dropna()
        sharpe = 0.0 if returns.empty or returns.std() == 0 else (returns.mean() / returns.std()) * np.sqrt(252)
        return {
            "final_equity": round(final_equity, 2),
            "cagr": round(float(cagr), 6),
            "max_drawdown": round(float(abs(drawdowns.min())), 6),
            "sharpe": round(float(sharpe), 6),
        }


def compare_and_write(
    *,
    equal_result: dict[str, Any],
    inverse_result: dict[str, Any],
    results_dir: Path = RESULTS_DIR,
) -> dict[str, Any]:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "backtest_equal_weight.json").write_text(json.dumps(equal_result, ensure_ascii=False, indent=2), encoding="utf-8")
    (results_dir / "backtest_inverse_rank.json").write_text(json.dumps(inverse_result, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics = {
        "equal_weight": equal_result["metrics"],
        "inverse_rank_weight": inverse_result["metrics"],
    }
    winner = choose_winner(metrics)
    comparison = {"winner": winner, "metrics": metrics}
    (results_dir / "backtest_comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    return comparison
