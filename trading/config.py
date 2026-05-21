"""Configuration constants for the trading strategy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
TRADING_DIR = ROOT_DIR / "trading"
RESULTS_DIR = TRADING_DIR / "results"
RUNTIME_CACHE_DIR = ROOT_DIR / ".cache" / "trading"
DATA_CACHE_DIR = ROOT_DIR / "data_cache" / "trading"
CACHE_DIR = RUNTIME_CACHE_DIR


@dataclass(frozen=True)
class StrategyConfig:
    initial_capital: float = 100_000_000.0
    max_position_weight: float = 0.15
    commission_rate: float = 0.00015
    take_profit_rate: float = 0.24
    stop_loss_rate: float = 0.08
    price_lookback_bars: int = 500
    backtest_days: int = 365
    kiwoom_app_key_env: str = "KIWOOM_APP_KEY"
    kiwoom_secret_key_env: str = "KIWOOM_SECRET_KEY"
    kiwoom_env_var: str = "KIWOOM_ENV"
    dart_api_key_env: str = "DART_API_KEY"
    macro_root_env: str = "TRADING_MACRO_ROOT"
    macro_snapshot_path_env: str = "TRADING_MACRO_SNAPSHOT_PATH"


DEFAULT_CONFIG = StrategyConfig()
