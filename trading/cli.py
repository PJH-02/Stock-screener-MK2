"""Command-line entrypoints for the trading strategy."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from trading.backtest import BacktestEngine, compare_and_write  # noqa: E402
from trading.canslim_turtle import CANSLIMTurtleEvaluator  # noqa: E402
from trading.config import DATA_CACHE_DIR, DEFAULT_CONFIG, RESULTS_DIR  # noqa: E402
from trading.data_collection.kr_daily import KRDailyPriceCollector  # noqa: E402
from trading.data_collection.kr_dart import DARTDisclosureClient, KRFinancialCollector  # noqa: E402
from trading.data_collection.us_daily import USDailyPriceCollector  # noqa: E402
from trading.data_collection.us_sec import USSECFinancialCollector  # noqa: E402
from trading.kiwoom_client import TradingSecurity  # noqa: E402
from trading.macro_dart_score import build_macro_dart_scores  # noqa: E402
from trading.point_in_time import build_point_in_time_candidate_schedule, build_rankings_as_of  # noqa: E402
from trading.portfolio import build_entry_orders  # noqa: E402


def load_strategy_inputs(
    as_of: str,
    limit: int | None = None,
    *,
    start: str | None = None,
    price_bars: int = DEFAULT_CONFIG.price_lookback_bars,
) -> tuple[list[TradingSecurity], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    price_collector = KRDailyPriceCollector()
    securities, price_history = price_collector.load_inputs(end_date=as_of, limit=limit, bars=price_bars)
    financials = KRFinancialCollector().load_financials(securities)
    if start is not None:
        disclosure_start = (date.fromisoformat(start) - timedelta(days=80)).isoformat()
        disclosures = DARTDisclosureClient().fetch_disclosures_range(
            start_date=disclosure_start,
            end_date=as_of,
            as_of=as_of,
        )
    else:
        disclosure_start = (date.fromisoformat(as_of) - timedelta(days=80)).isoformat()
        disclosures = DARTDisclosureClient().fetch_disclosures(start_date=disclosure_start, end_date=as_of, as_of=as_of)
    return securities, price_history, financials, disclosures


def build_rankings_from_inputs(
    securities: list[TradingSecurity],
    price_history: dict[str, Any],
    financials: dict[str, Any],
    disclosures: list[dict[str, Any]],
    *,
    as_of: str | None = None,
    write_latest: bool = True,
) -> list[dict[str, Any]]:
    scores = build_macro_dart_scores(securities, disclosures, as_of=as_of)
    evaluator = CANSLIMTurtleEvaluator()
    rankings = evaluator.evaluate_universe(securities, price_history, financials, scores)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if write_latest:
        (RESULTS_DIR / "latest_rankings.json").write_text(json.dumps(rankings, ensure_ascii=False, indent=2), encoding="utf-8")
    return rankings


def build_rankings(as_of: str, limit: int | None = None) -> list[dict[str, Any]]:
    securities, price_history, financials, disclosures = load_strategy_inputs(as_of, limit)
    rankings = build_rankings_as_of(
        securities=securities,
        price_history=price_history,
        financials=financials,
        disclosures=disclosures,
        as_of=as_of,
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "latest_rankings.json").write_text(json.dumps(rankings, ensure_ascii=False, indent=2), encoding="utf-8")
    return rankings


def command_rank(args: argparse.Namespace) -> dict[str, Any]:
    rankings = build_rankings(args.as_of, args.limit)
    candidates = CANSLIMTurtleEvaluator().candidates(rankings)
    return {"as_of": args.as_of, "rows": len(rankings), "candidates": len(candidates), "top": rankings[:20]}


def command_orders(args: argparse.Namespace) -> dict[str, Any]:
    rankings = build_rankings(args.as_of, args.limit)
    candidates = CANSLIMTurtleEvaluator().candidates(rankings)
    open_prices = {str(row["ticker"]): float(row["close"]) for row in candidates if row.get("close") is not None}
    orders = build_entry_orders(
        date=args.as_of,
        cash=DEFAULT_CONFIG.initial_capital,
        candidates=candidates,
        open_prices=open_prices,
        method=args.method,
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = RESULTS_DIR / "order_proposal.json"
    csv_path = RESULTS_DIR / "order_proposal.csv"
    json_path.write_text(json.dumps([order.to_dict() for order in orders], ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "ticker", "side", "shares", "expected_price", "reason", "estimated_value", "estimated_fee"])
        writer.writeheader()
        writer.writerows(order.to_dict() for order in orders)
    return {"orders": len(orders), "json": str(json_path), "csv": str(csv_path)}


def command_backtest(args: argparse.Namespace) -> dict[str, Any]:
    securities, price_history, financials, disclosures = load_strategy_inputs(
        args.end,
        args.limit,
        start=args.start,
        price_bars=DEFAULT_CONFIG.price_lookback_bars + DEFAULT_CONFIG.backtest_days,
    )
    schedule, latest_rankings = build_point_in_time_candidate_schedule(
        securities=securities,
        price_history=price_history,
        financials=financials,
        disclosures=disclosures,
        start=args.start,
        end=args.end,
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "latest_rankings.json").write_text(json.dumps(latest_rankings, ensure_ascii=False, indent=2), encoding="utf-8")
    engine = BacktestEngine()
    equal = engine.run(
        price_history=price_history,
        candidate_schedule=schedule,
        method="equal_weight",
        start=args.start,
        end=args.end,
        point_in_time=True,
    )
    inverse = engine.run(
        price_history=price_history,
        candidate_schedule=schedule,
        method="inverse_rank_weight",
        start=args.start,
        end=args.end,
        point_in_time=True,
    )
    comparison = compare_and_write(equal_result=equal, inverse_result=inverse)
    return comparison


def command_collect(args: argparse.Namespace) -> dict[str, Any]:
    market = str(args.market).upper()
    result: dict[str, Any] = {
        "start": args.start,
        "end": args.end,
        "limit": args.limit,
        "markets": {},
        "cache_dir": str(DATA_CACHE_DIR),
    }
    if market in {"KR", "ALL"}:
        price_collector = KRDailyPriceCollector()
        securities, price_history = price_collector.load_inputs(
            end_date=args.end,
            limit=args.limit,
            bars=args.price_bars,
        )
        financials = KRFinancialCollector().load_financials(securities)
        disclosure_start = (date.fromisoformat(args.start) - timedelta(days=80)).isoformat()
        disclosures = DARTDisclosureClient().fetch_disclosures_range(
            start_date=disclosure_start,
            end_date=args.end,
            as_of=args.end,
        )
        result["markets"]["KR"] = {
            "securities": len(securities),
            "prices": len(price_history),
            "financials": len(financials),
            "disclosures": len(disclosures),
        }
    if market in {"US", "ALL"}:
        price_collector = USDailyPriceCollector()
        securities = price_collector.load_universe(limit=args.limit)
        price_history = price_collector.load_price_history(securities, bars=args.price_bars)
        financials = USSECFinancialCollector().load_financials(securities)
        result["markets"]["US"] = {
            "securities": len(securities),
            "prices": len(price_history),
            "financials": len(financials),
        }
    return result


def _date_range_from_prices(price_history: dict[str, Any], start: str, end: str) -> list[str]:
    dates = sorted({date for df in price_history.values() for date in df["date"].astype(str).tolist()})
    return [date_value for date_value in dates if start <= date_value <= end]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run trading strategy ranking, backtest, or order proposal.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    rank = subparsers.add_parser("rank")
    rank.add_argument("--as-of", required=True)
    rank.add_argument("--limit", type=int, default=None)

    orders = subparsers.add_parser("orders")
    orders.add_argument("--as-of", required=True)
    orders.add_argument("--limit", type=int, default=None)
    orders.add_argument("--method", choices=["equal_weight", "inverse_rank_weight"], default="equal_weight")

    backtest = subparsers.add_parser("backtest")
    backtest.add_argument("--start", required=True)
    backtest.add_argument("--end", required=True)
    backtest.add_argument("--limit", type=int, default=None)

    collect = subparsers.add_parser("collect")
    collect.add_argument("--market", choices=["KR", "US", "all"], default="all")
    collect.add_argument("--start", required=True)
    collect.add_argument("--end", required=True)
    collect.add_argument("--limit", type=int, default=None)
    collect.add_argument("--price-bars", type=int, default=DEFAULT_CONFIG.price_lookback_bars + DEFAULT_CONFIG.backtest_days)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "rank":
        result = command_rank(args)
    elif args.command == "orders":
        result = command_orders(args)
    elif args.command == "backtest":
        result = command_backtest(args)
    elif args.command == "collect":
        result = command_collect(args)
    else:
        raise ValueError(args.command)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
