from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"
RESULTS_DIR = DATA_DIR / "results" / "backtest"
MACRO_CACHE_DIR = DATA_DIR / "macro_cache"
UNIVERSE_CACHE = ROOT / "data_cache" / "trading" / "kr_universe.json"
MACRO_ROOT = Path.home() / "Desktop" / "macro"
MACRO_EXPOSURE_PATH = MACRO_ROOT / "config" / "macro_sector_exposure.v2.json"
MACRO_CLASSIFICATION_PATH = MACRO_ROOT / "stock_classification.csv"


@dataclass(frozen=True)
class BacktestConfig:
    initial_equity: float = 100_000_000.0
    commission_rate: float = 0.00015
    slippage_bps: float = 0.0
    risk_per_trade: float = 0.01
    max_total_open_risk: float = 0.10
    adv_cap: float = 0.10
    max_positions: int = 10
    warmup_years: int = 3
    stop_loss_pct: float = -0.08
    high52_position_cap: float = 0.15
    high52_take_profit_fraction: float = 0.50
    drawdown_kill_switch: float = -0.15
    resume_drawdown: float = -0.10


CONFIG = BacktestConfig()
