#!/usr/bin/env python3
"""CANSLIM + Turtle Trading global stock screener."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from canslim import EarningsAnalyzer, LeadershipAnalyzer, NewnessAnalyzer, SupplyAnalyzer
from market_providers import MarketProvider, Security, provider_for_market
from turtle import TurtleSignalGenerator
from utils import setup_logger

logger = setup_logger("main")


class StockScreener:
    """Coordinates market providers, CANSLIM criteria, and Turtle signals."""

    required_criteria = ("C", "A", "N", "S", "L")

    def __init__(self, markets: list[str], limit: int | None = None):
        self.markets = markets
        self.limit = limit
        self.earnings_analyzer = EarningsAnalyzer()
        self.newness_analyzer = NewnessAnalyzer()
        self.supply_analyzer = SupplyAnalyzer()
        self.leadership_analyzer = LeadershipAnalyzer()
        self.turtle_generator = TurtleSignalGenerator()

    def run(self) -> dict[str, Any]:
        generated_at = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
        output: dict[str, Any] = {
            "generated_at": generated_at,
            "last_updated": generated_at,
            "summary": {
                "markets": self.markets,
                "total_universe": 0,
                "total_evaluated": 0,
                "total_canslim_passed": 0,
                "total_turtle_signals": 0,
            },
            "data_quality": {"alerts": []},
            "markets": {},
        }

        for market in self.markets:
            provider = provider_for_market(market)
            market_result = self.screen_market(provider)
            output["markets"][market] = market_result
            output["summary"]["total_universe"] += market_result["universe_count"]
            output["summary"]["total_evaluated"] += market_result["evaluated_count"]
            output["summary"]["total_canslim_passed"] += len(market_result["canslim_passed"])
            output["summary"]["total_turtle_signals"] += len(market_result["turtle_signals"])
            output["data_quality"]["alerts"].extend(market_result["data_quality"]["alerts"])

        return output

    def screen_market(self, provider: MarketProvider) -> dict[str, Any]:
        logger.info("Screening %s", provider.market_name)
        securities = provider.get_universe(limit=self.limit)
        records: list[dict[str, Any]] = []
        market_alerts = list(provider.alerts)
        stock_alert_counter: Counter[str] = Counter()

        for index, security in enumerate(securities, 1):
            if index % 25 == 0:
                logger.info("%s progress: %s/%s", provider.market, index, len(securities))
            record = self.screen_security(provider, security)
            records.append(record)
            for alert in record.get("data_alerts", []):
                stock_alert_counter[alert] += 1

        self.apply_leadership(records)
        canslim_passed: list[dict[str, Any]] = []
        turtle_signals: list[dict[str, Any]] = []

        for record in records:
            criteria = record.get("criteria", {})
            record["canslim_score"] = sum(1 for name in self.required_criteria if criteria.get(name, {}).get("pass"))
            record["canslim_pass"] = all(criteria.get(name, {}).get("pass") for name in self.required_criteria)
            if record["canslim_pass"]:
                stock_payload = self.compact_stock(record, include_criteria=True)
                canslim_passed.append(stock_payload)
                for signal in self.turtle_generator.generate_signals(record["ticker"], record.get("ohlcv")):
                    signal_payload = self.compact_stock(record, include_criteria=False)
                    signal_payload["Turtle_Signal"] = signal
                    turtle_signals.append(signal_payload)

        fail_reasons = self.count_fail_reasons(records)
        if not records:
            market_alerts.append(f"{provider.market}: no securities were evaluated.")
        if not canslim_passed:
            market_alerts.append(f"{provider.market}: no CANSLIM pass results; see top_candidates and fail_reasons.")

        for alert, count in stock_alert_counter.most_common(10):
            market_alerts.append(f"{provider.market}: {alert} ({count} securities)")

        top_candidates = [
            self.compact_stock(record, include_criteria=True)
            for record in sorted(
                records,
                key=lambda item: (
                    item.get("canslim_score", 0),
                    item.get("rs_percentile") or 0,
                    item.get("close_price") or 0,
                ),
                reverse=True,
            )[:20]
        ]

        return {
            "market_name": provider.market_name,
            "universe_count": len(securities),
            "evaluated_count": len(records),
            "canslim_passed": canslim_passed,
            "turtle_signals": turtle_signals,
            "top_candidates": top_candidates,
            "fail_reasons": fail_reasons,
            "data_quality": {"alerts": market_alerts},
        }

    def screen_security(self, provider: MarketProvider, security: Security) -> dict[str, Any]:
        record: dict[str, Any] = {
            "market": security.market,
            "ticker": security.ticker,
            "company_name": security.name,
            "sector": security.sector,
            "currency": security.currency,
            "close_price": None,
            "ohlcv": None,
            "criteria": {},
            "canslim_score": 0,
            "rs_value": None,
            "rs_percentile": None,
            "sector_rs_percentile": None,
            "data_alerts": [],
        }

        try:
            ohlcv = provider.get_ohlcv(security)
            record["ohlcv"] = ohlcv
            if ohlcv is None or ohlcv.empty:
                raise ValueError("No OHLCV data returned")
            record["close_price"] = float(ohlcv["close"].iloc[-1])
        except Exception as exc:
            reason = f"market data unavailable: {exc}"
            record["data_alerts"].append("market data unavailable")
            for criterion in self.required_criteria:
                record["criteria"][criterion] = {"pass": False, "details": {"reason": reason}}
            return record

        try:
            financial_data = provider.get_financials(security)
            for alert in financial_data.get("alerts", []):
                record["data_alerts"].append(f"financial data: {alert}")
        except Exception as exc:
            financial_data = {"annual": [], "quarterly": [], "alerts": [str(exc)]}
            record["data_alerts"].append("financial data unavailable")

        c_pass, c_details = self.earnings_analyzer.check_c_criterion(security.ticker, financial_data)
        a_pass, a_details = self.earnings_analyzer.check_a_criterion(security.ticker, financial_data)
        n_pass, n_details = self.newness_analyzer.check_n_criterion(security.ticker, record["ohlcv"])
        s_pass, s_details = self.supply_analyzer.check_s_criterion(security.ticker, record["ohlcv"])
        rs_value = self.leadership_analyzer.calculate_rs_rating(security.ticker, record["ohlcv"])

        record["criteria"]["C"] = {"pass": c_pass, "details": c_details}
        record["criteria"]["A"] = {"pass": a_pass, "details": a_details}
        record["criteria"]["N"] = {"pass": n_pass, "details": n_details}
        record["criteria"]["S"] = {"pass": s_pass, "details": s_details}
        record["rs_value"] = rs_value
        return record

    def apply_leadership(self, records: list[dict[str, Any]]) -> None:
        values = [record["rs_value"] for record in records if record.get("rs_value") is not None]
        sector_values: dict[str, list[float]] = defaultdict(list)
        for record in records:
            if record.get("sector") and record.get("rs_value") is not None:
                sector_values[str(record["sector"])].append(float(record["rs_value"]))

        for record in records:
            rs_value = record.get("rs_value")
            percentile = self.percentile_rank(values, rs_value)
            sector_percentile = self.percentile_rank(sector_values.get(str(record.get("sector")), []), rs_value)
            record["rs_percentile"] = percentile
            record["sector_rs_percentile"] = sector_percentile
            l_pass, l_details = self.leadership_analyzer.check_l_criterion(record["ticker"], percentile)
            if sector_percentile is not None:
                l_details["sector_rs_percentile"] = round(float(sector_percentile), 2)
            if rs_value is not None:
                l_details["rs_value"] = round(float(rs_value), 2)
            record["criteria"]["L"] = {"pass": l_pass, "details": l_details}

    def percentile_rank(self, values: list[float], value: float | None) -> float | None:
        if value is None or not values:
            return None
        less = sum(1 for item in values if item < value)
        equal = sum(1 for item in values if item == value)
        return ((less + 0.5 * equal) / len(values)) * 100

    def compact_stock(self, record: dict[str, Any], include_criteria: bool) -> dict[str, Any]:
        payload = {
            "Market": record["market"],
            "Ticker": record["ticker"],
            "CompanyName": record["company_name"],
            "Sector": record.get("sector"),
            "ClosePrice": round(float(record["close_price"]), 4) if record.get("close_price") is not None else None,
            "Currency": record.get("currency"),
            "CANSLIM_Score": int(record.get("canslim_score", 0)),
            "RS_Percentile": round(float(record["rs_percentile"]), 2) if record.get("rs_percentile") is not None else None,
        }
        if include_criteria:
            payload["Criteria"] = record.get("criteria", {})
        return payload

    def count_fail_reasons(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        counts: dict[str, int] = {criterion: 0 for criterion in self.required_criteria}
        details: Counter[str] = Counter()
        for record in records:
            for criterion in self.required_criteria:
                criterion_payload = record.get("criteria", {}).get(criterion, {})
                if not criterion_payload.get("pass"):
                    counts[criterion] += 1
                    reason = criterion_payload.get("details", {}).get("reason")
                    if reason:
                        details[f"{criterion}: {reason}"] += 1
        return {"by_criterion": counts, "top_details": dict(details.most_common(10))}

    def save_results(self, output: dict[str, Any], output_path: Path, legacy_path: Path | None = None) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Results saved to %s", output_path)

        if legacy_path:
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("Legacy results saved to %s", legacy_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CANSLIM + Turtle stock screening.")
    parser.add_argument("--market", choices=["KR", "US", "all"], default="all")
    parser.add_argument("--limit", type=int, default=None, help="Optional per-market security limit for smoke tests.")
    parser.add_argument("--output", default="public/results/screener_results.json")
    parser.add_argument("--legacy-output", default="results/screener_results.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    markets = ["KR", "US"] if args.market == "all" else [args.market]
    screener = StockScreener(markets=markets, limit=args.limit)
    output = screener.run()
    screener.save_results(output, Path(args.output), Path(args.legacy_output) if args.legacy_output else None)


if __name__ == "__main__":
    main()
