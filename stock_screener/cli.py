from __future__ import annotations

import argparse
import json

from .backtest import HIGH52_MODELS, LI_HIGH52_MODELS, save_result, run_backtest
from .config import CONFIG, PROCESSED_DIR
from .data import load_market_data
from .features import canslim_turtle_features, high52_features
from .macro import build_macro_scores, collect_macro_states, macro_scores_for_daily_dates
from .report import comparison_report


def cmd_features(_: argparse.Namespace) -> None:
    data = load_market_data()
    features = canslim_turtle_features(data)
    summary = {
        "dates": [data.dates[0].strftime("%Y-%m-%d"), data.dates[-1].strftime("%Y-%m-%d")],
        "tickers": len(data.tickers),
        "candidate_days": int(features["candidate"].any(axis=1).sum()),
        "candidate_cells": int(features["candidate"].sum().sum()),
    }
    (PROCESSED_DIR / "feature_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_collect_macro(args: argparse.Namespace) -> None:
    states, quality = collect_macro_states(args.start, args.end)
    data = load_market_data()
    macro = build_macro_scores(data, states)
    payload = {
        "states_rows": int(states.shape[0]),
        "states_start": states.index.min().strftime("%Y-%m-%d"),
        "states_end": states.index.max().strftime("%Y-%m-%d"),
        "collection_quality": quality,
        "mapping_quality": macro.quality,
    }
    (PROCESSED_DIR / "macro_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_backtest(args: argparse.Namespace) -> None:
    data = load_market_data()
    if args.model in HIGH52_MODELS:
        features = high52_features(data, include_li=args.model in LI_HIGH52_MODELS)
    else:
        features = canslim_turtle_features(data)
    macro_scores = None
    if args.model in {"canslim_turtle_macro", "turtle_macro", "model_b", "model_d", "model_f", "model_g"}:
        macro = build_macro_scores(data)
        macro_scores = macro_scores_for_daily_dates(data, macro.ticker_scores)
    result = run_backtest(args.model, data, features, macro_scores=macro_scores, cfg=CONFIG)
    out_dir = save_result(result)
    print(json.dumps({"output_dir": str(out_dir), "metrics": result.metrics}, ensure_ascii=False, indent=2))


def cmd_compare(_: argparse.Namespace) -> None:
    data = load_market_data()
    out = comparison_report(data)
    print(json.dumps({"comparison_png": str(out)}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="stock_screener")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("features")
    p.set_defaults(func=cmd_features)

    p = sub.add_parser("collect-macro")
    p.add_argument("--start", default="2012-01-01")
    p.add_argument("--end", default="2026-06-01")
    p.set_defaults(func=cmd_collect_macro)

    p = sub.add_parser("backtest")
    p.add_argument(
        "--model",
        choices=[
            "canslim_turtle",
            "canslim_turtle_macro",
            "cim_turtle",
            "turtle_macro",
            "turtle_only",
            "model_a",
            "model_b",
            "model_c",
            "model_d",
            "model_e",
            "model_f",
            "model_g",
            "model_h",
        ],
        required=True,
    )
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("compare")
    p.set_defaults(func=cmd_compare)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
