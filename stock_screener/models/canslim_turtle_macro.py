from __future__ import annotations

from stock_screener.backtest import run_backtest
from stock_screener.config import CONFIG
from stock_screener.data import MarketData


def run(data: MarketData, features: dict, macro_scores):
    return run_backtest("canslim_turtle_macro", data, features, macro_scores=macro_scores, cfg=CONFIG)

