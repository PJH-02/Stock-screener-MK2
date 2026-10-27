from __future__ import annotations

from stock_screener.backtest import run_backtest
from stock_screener.config import CONFIG
from stock_screener.data import MarketData


def run(data: MarketData, features: dict):
    return run_backtest("cim_turtle", data, features, cfg=CONFIG)

