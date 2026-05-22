"""Point-in-time ranking builders for daily backtests."""

from __future__ import annotations

from typing import Any

import pandas as pd

from trading.canslim_turtle import CANSLIMTurtleEvaluator
from trading.config import DEFAULT_CONFIG
from trading.data_collection.kr_dart import (
    filter_disclosures_as_of,
    filter_financials_map_as_of,
)
from trading.kiwoom_client import TradingSecurity
from trading.macro_dart_score import build_macro_dart_scores


def available_trading_dates(price_history: dict[str, pd.DataFrame], start: str, end: str) -> list[str]:
    dates = sorted({date for df in price_history.values() for date in df["date"].astype(str).tolist()})
    return [date_value for date_value in dates if start <= date_value <= end]


def slice_price_history_as_of(
    price_history: dict[str, pd.DataFrame],
    as_of: str,
    *,
    lookback_bars: int = DEFAULT_CONFIG.price_lookback_bars,
) -> dict[str, pd.DataFrame]:
    sliced: dict[str, pd.DataFrame] = {}
    for ticker, df in price_history.items():
        frame = df[df["date"].astype(str) <= as_of].copy()
        if frame.empty:
            continue
        sliced[ticker] = frame.tail(lookback_bars).reset_index(drop=True)
    return sliced


def slice_institutional_flow_as_of(
    institutional_flow: dict[str, list[dict[str, Any]]] | None,
    as_of: str,
    *,
    lookback_bars: int = 126,
) -> dict[str, list[dict[str, Any]]]:
    if not institutional_flow:
        return {}
    sliced: dict[str, list[dict[str, Any]]] = {}
    for ticker, rows in institutional_flow.items():
        available = [dict(row) for row in rows if str(row.get("date") or "") <= as_of]
        if available:
            sliced[ticker] = available[-lookback_bars:]
    return sliced


def slice_index_history_as_of(
    market_index_history: dict[str, pd.DataFrame] | None,
    as_of: str,
    *,
    lookback_bars: int = 60,
) -> dict[str, pd.DataFrame]:
    if not market_index_history:
        return {}
    sliced: dict[str, pd.DataFrame] = {}
    for market, df in market_index_history.items():
        frame = df[df["date"].astype(str) <= as_of].copy()
        if frame.empty:
            continue
        sliced[market] = frame.tail(lookback_bars).reset_index(drop=True)
    return sliced


def build_rankings_as_of(
    *,
    securities: list[TradingSecurity],
    price_history: dict[str, pd.DataFrame],
    financials: dict[str, dict[str, Any]],
    disclosures: list[dict[str, Any]],
    institutional_flow: dict[str, list[dict[str, Any]]] | None = None,
    market_index_history: dict[str, pd.DataFrame] | None = None,
    as_of: str,
    disclosure_lookback_days: int = 80,
    evaluator: CANSLIMTurtleEvaluator | None = None,
) -> list[dict[str, Any]]:
    """Build rankings using only inputs available on or before as_of."""
    price_as_of = slice_price_history_as_of(price_history, as_of)
    financials_as_of = filter_financials_map_as_of(financials, as_of)
    disclosures_as_of = filter_disclosures_as_of(
        disclosures,
        as_of,
        lookback_days=disclosure_lookback_days,
    )
    scores = build_macro_dart_scores(securities, disclosures_as_of, as_of=as_of)
    return (evaluator or CANSLIMTurtleEvaluator()).evaluate_universe(
        securities,
        price_as_of,
        financials_as_of,
        scores,
        institutional_flow_by_ticker=slice_institutional_flow_as_of(institutional_flow, as_of),
        market_index_history_by_market=slice_index_history_as_of(market_index_history, as_of),
    )


def build_point_in_time_candidate_schedule(
    *,
    securities: list[TradingSecurity],
    price_history: dict[str, pd.DataFrame],
    financials: dict[str, dict[str, Any]],
    disclosures: list[dict[str, Any]],
    institutional_flow: dict[str, list[dict[str, Any]]] | None = None,
    market_index_history: dict[str, pd.DataFrame] | None = None,
    start: str,
    end: str,
    disclosure_lookback_days: int = 80,
    evaluator: CANSLIMTurtleEvaluator | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Compute each signal date independently, then trade on the next open."""
    evaluator = evaluator or CANSLIMTurtleEvaluator()
    dates = available_trading_dates(price_history, start, end)
    schedule: dict[str, list[dict[str, Any]]] = {}
    latest_rankings: list[dict[str, Any]] = []
    for current_date in dates[:-1]:
        rankings = build_rankings_as_of(
            securities=securities,
            price_history=price_history,
            financials=financials,
            disclosures=disclosures,
            institutional_flow=institutional_flow,
            market_index_history=market_index_history,
            as_of=current_date,
            disclosure_lookback_days=disclosure_lookback_days,
            evaluator=evaluator,
        )
        schedule[current_date] = evaluator.candidates(rankings)
        latest_rankings = rankings
    return schedule, latest_rankings
