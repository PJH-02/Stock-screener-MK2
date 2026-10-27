from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from numba import njit

from .config import CONFIG, RESULTS_DIR, BacktestConfig
from .data import MarketData, first_backtest_date

MODEL_NAMES = {
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
}

HIGH52_MODELS = {"model_a", "model_b", "model_c", "model_d", "model_e", "model_f", "model_g", "model_h"}
MACRO_WEIGHT_MODELS = {"canslim_turtle_macro", "turtle_macro", "model_b", "model_d", "model_f", "model_g"}
LI_HIGH52_MODELS = {"model_c", "model_d", "model_f", "model_h"}
KOSPI_GATE_HIGH52_MODELS = {"model_e", "model_f", "model_g", "model_h"}


@dataclass
class Position:
    ticker: str
    shares: int
    entry_price: float
    last_add_price: float
    n_at_entry: float
    system: str
    half_taken: bool = False
    take_profit_price: float = np.nan
    half_exit_date: str | None = None


@dataclass
class BacktestResult:
    model: str
    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict[str, float | int | str]
    start: str
    end: str
    evaluation_start: str


def _safe_float(value: object, default: float = np.nan) -> float:
    try:
        value = float(value)
    except Exception:
        return default
    return value if np.isfinite(value) else default


def compute_metrics(equity: pd.Series, trades: pd.DataFrame, initial_equity: float) -> dict[str, float | int]:
    returns = equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    total_return = float(equity.iloc[-1] / initial_equity - 1.0)
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    cagr = float((equity.iloc[-1] / initial_equity) ** (1.0 / years) - 1.0)
    sharpe = float(np.sqrt(252) * returns.mean() / returns.std()) if returns.std() and np.isfinite(returns.std()) else 0.0
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    mdd = float(drawdown.min())
    turnover = 0.0
    fees = 0.0
    if not trades.empty:
        turnover = float(trades["notional"].abs().sum() / initial_equity)
        fees = float(trades["fee"].sum())
    return {
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "mdd": mdd,
        "turnover": turnover,
        "fees": fees,
        "trade_count": int(len(trades)),
        "final_equity": float(equity.iloc[-1]),
    }


def _portfolio_value(
    date: pd.Timestamp,
    positions: dict[str, Position],
    cash: float,
    close: pd.DataFrame,
) -> float:
    value = cash
    for ticker, pos in positions.items():
        px = _safe_float(close.at[date, ticker])
        if not np.isfinite(px):
            series = close[ticker]
            if not series.loc[date:].notna().any():
                px = 0.0
            else:
                history = series.loc[:date].dropna()
                px = _safe_float(history.iloc[-1]) if not history.empty else np.nan
        if np.isfinite(px):
            value += pos.shares * px
    return value


def _sell(
    ticker: str,
    pos: Position,
    price: float,
    date: pd.Timestamp,
    reason: str,
    cfg: BacktestConfig,
) -> tuple[float, dict[str, object]]:
    return _sell_shares(ticker, pos, pos.shares, price, date, reason, cfg)


def _sell_shares(
    ticker: str,
    pos: Position,
    shares: int,
    price: float,
    date: pd.Timestamp,
    reason: str,
    cfg: BacktestConfig,
) -> tuple[float, dict[str, object]]:
    notional = shares * price
    fee = notional * cfg.commission_rate
    cash_delta = notional - fee
    trade = {
        "date": date.strftime("%Y-%m-%d"),
        "ticker": ticker,
        "side": "SELL",
        "shares": shares,
        "price": price,
        "notional": notional,
        "fee": fee,
        "reason": reason,
        "system": pos.system,
    }
    return cash_delta, trade


def _buy(
    ticker: str,
    shares: int,
    price: float,
    date: pd.Timestamp,
    reason: str,
    n_at_entry: float,
    system: str,
    cfg: BacktestConfig,
) -> tuple[float, Position, dict[str, object]]:
    notional = shares * price
    fee = notional * cfg.commission_rate
    cash_delta = -(notional + fee)
    pos = Position(
        ticker=ticker,
        shares=shares,
        entry_price=price,
        last_add_price=price,
        n_at_entry=n_at_entry,
        system=system,
    )
    trade = {
        "date": date.strftime("%Y-%m-%d"),
        "ticker": ticker,
        "side": "BUY",
        "shares": shares,
        "price": price,
        "notional": notional,
        "fee": fee,
        "reason": reason,
        "system": system,
    }
    return cash_delta, pos, trade


def _entry_system(date: pd.Timestamp, ticker: str, features: dict[str, pd.DataFrame]) -> str:
    if bool(features["s2_entry"].at[date, ticker]):
        return "S2"
    return "S1"


def _exit_due_to_delist(
    ticker: str,
    next_idx: int,
    data: MarketData,
) -> bool:
    future_open = data.open[ticker].iloc[next_idx:]
    future_close = data.close[ticker].iloc[next_idx:]
    return not future_open.notna().any() and not future_close.notna().any()


def _candidate_order(row: pd.Series) -> list[str]:
    row = row.dropna().sort_values(ascending=False)
    return row.index.tolist()


def _normalized_rank_weights(row: pd.Series) -> pd.Series:
    row = row.dropna()
    if row.empty:
        return pd.Series(dtype="float64")
    ranks = row.rank(method="first", ascending=True)
    weights = ranks / ranks.sum()
    return weights.sort_values(ascending=False)


def _cap_weights(weights: pd.Series, cap: float) -> pd.Series:
    weights = weights.dropna()
    weights = weights[weights > 0].astype("float64")
    if weights.empty:
        return pd.Series(dtype="float64")
    weights = weights / weights.sum()
    cap = float(cap)
    if cap <= 0:
        return pd.Series(dtype="float64")

    capped = pd.Series(0.0, index=weights.index, dtype="float64")
    remaining = weights.index
    remaining_weight = 1.0
    while len(remaining) > 0 and remaining_weight > 1e-12:
        base = weights.loc[remaining]
        if base.sum() <= 0:
            break
        proposed = base / base.sum() * remaining_weight
        over_cap = proposed > cap
        if not over_cap.any():
            capped.loc[remaining] = proposed
            break
        over_idx = proposed[over_cap].index
        capped.loc[over_idx] = cap
        remaining_weight -= cap * len(over_idx)
        remaining = remaining.difference(over_idx, sort=False)
    return capped[capped > 0].sort_values(ascending=False)


def _high52_entry_reason(date: pd.Timestamp, next_date: pd.Timestamp, ticker: str, features: dict[str, pd.DataFrame]) -> str:
    if bool(features["high52_open_entry"].at[next_date, ticker]):
        return "open52"
    if bool(features["high52_close_entry"].at[date, ticker]):
        return "close52"
    return "unknown52"


def _high52_candidate_mask(
    model: str,
    date: pd.Timestamp,
    next_date: pd.Timestamp,
    data: MarketData,
    features: dict[str, pd.DataFrame],
) -> pd.Series:
    close_signal = features["high52_close_entry"].loc[date].fillna(False)
    open_signal = features["high52_open_entry"].loc[next_date].fillna(False)
    candidate = close_signal | open_signal
    if model in LI_HIGH52_MODELS:
        candidate = candidate & features["l_pass"].loc[date].fillna(False) & features["i_pass"].loc[date].fillna(False)

    kosdaq_ok = bool(features["kosdaq_above_ma120"].loc[date])
    if not kosdaq_ok:
        kosdaq_tickers = [ticker for ticker in data.tickers if data.market.loc[ticker] == "KOSDAQ"]
        if kosdaq_tickers:
            candidate.loc[kosdaq_tickers] = False
    return candidate


def _benchmark_equity(index_close: pd.Series, dates: pd.DatetimeIndex, initial_equity: float) -> pd.Series:
    s = index_close.reindex(dates).ffill()
    first = s.dropna().iloc[0]
    return initial_equity * s / first


def benchmark_curves(data: MarketData, dates: pd.DatetimeIndex, initial_equity: float) -> pd.DataFrame:
    kospi = _benchmark_equity(data.index["kospi_close"], dates, initial_equity)
    kosdaq = _benchmark_equity(data.index["kosdaq_close"], dates, initial_equity)
    return pd.DataFrame(
        {
            "KOSPI_BH": kospi,
            "KOSDAQ_BH": kosdaq,
            "KOSPI_KOSDAQ_50_50": 0.5 * kospi + 0.5 * kosdaq,
        },
        index=dates,
    )


@njit(cache=True)
def _high52_core(
    open_px: np.ndarray,
    close_px: np.ndarray,
    valuation_close: np.ndarray,
    ma10: np.ndarray,
    close_signal: np.ndarray,
    open_signal: np.ndarray,
    l_pass: np.ndarray,
    i_pass: np.ndarray,
    kospi_above_ma200: np.ndarray,
    kosdaq_above_ma120: np.ndarray,
    is_kospi: np.ndarray,
    is_kosdaq: np.ndarray,
    macro_scores: np.ndarray,
    future_price_exists: np.ndarray,
    eval_start_idx: int,
    model_code: int,
    initial_cash: float,
    commission_rate: float,
    stop_loss_pct: float,
    position_cap: float,
    take_profit_fraction: float,
    max_trades: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, int, int]:
    n_dates, n_tickers = close_px.shape
    cash = initial_cash
    shares = np.zeros(n_tickers, dtype=np.int64)
    entry_price = np.full(n_tickers, np.nan)
    half_taken = np.zeros(n_tickers, dtype=np.bool_)
    take_profit_price = np.full(n_tickers, np.nan)
    system_code = np.zeros(n_tickers, dtype=np.int64)

    equity_out = np.empty(n_dates, dtype=np.float64)
    cash_out = np.empty(n_dates, dtype=np.float64)
    positions_out = np.empty(n_dates, dtype=np.int64)

    trade_date_idx = np.empty(max_trades, dtype=np.int64)
    trade_ticker_idx = np.empty(max_trades, dtype=np.int64)
    trade_side = np.empty(max_trades, dtype=np.int64)
    trade_shares = np.empty(max_trades, dtype=np.int64)
    trade_price = np.empty(max_trades, dtype=np.float64)
    trade_notional = np.empty(max_trades, dtype=np.float64)
    trade_fee = np.empty(max_trades, dtype=np.float64)
    trade_reason = np.empty(max_trades, dtype=np.int64)
    trade_system = np.empty(max_trades, dtype=np.int64)
    trade_count = 0
    overflow = 0

    candidate_idx = np.empty(n_tickers, dtype=np.int64)
    target_idx = np.empty(n_tickers, dtype=np.int64)
    base_weight = np.empty(n_tickers, dtype=np.float64)
    final_weight = np.empty(n_tickers, dtype=np.float64)
    order_weight = np.empty(n_tickers, dtype=np.float64)
    score_buf = np.empty(n_tickers, dtype=np.float64)

    use_macro = model_code == 1 or model_code == 3 or model_code == 5 or model_code == 6
    use_li = model_code == 2 or model_code == 3 or model_code == 5 or model_code == 7
    use_kospi_gate = model_code == 4 or model_code == 5 or model_code == 6 or model_code == 7
    rebalance_equal_on_entry = model_code == 4 or model_code == 7

    for i in range(n_dates - 1):
        equity = cash
        position_count = 0
        for j in range(n_tickers):
            if shares[j] > 0:
                position_count += 1
                if future_price_exists[i, j]:
                    px = valuation_close[i, j]
                    if np.isfinite(px):
                        equity += shares[j] * px
        equity_out[i] = equity
        cash_out[i] = cash
        positions_out[i] = position_count

        for j in range(n_tickers):
            if shares[j] <= 0:
                continue

            cpx = close_px[i, j]
            if not np.isfinite(cpx) and not future_price_exists[i, j]:
                if trade_count < max_trades:
                    trade_date_idx[trade_count] = i
                    trade_ticker_idx[trade_count] = j
                    trade_side[trade_count] = 2
                    trade_shares[trade_count] = shares[j]
                    trade_price[trade_count] = 0.0
                    trade_notional[trade_count] = 0.0
                    trade_fee[trade_count] = 0.0
                    trade_reason[trade_count] = 1
                    trade_system[trade_count] = system_code[j]
                    trade_count += 1
                else:
                    overflow = 1
                shares[j] = 0
                entry_price[j] = np.nan
                half_taken[j] = False
                take_profit_price[j] = np.nan
                system_code[j] = 0
                continue

            ret = np.nan
            if np.isfinite(cpx) and np.isfinite(entry_price[j]) and entry_price[j] > 0:
                ret = cpx / entry_price[j] - 1.0

            took_half_today = False
            if np.isfinite(ret) and ret >= 0.24 and not half_taken[j] and np.isfinite(cpx):
                half_shares = int(np.floor(shares[j] * take_profit_fraction))
                if half_shares > 0:
                    notional = half_shares * cpx
                    fee = notional * commission_rate
                    cash += notional - fee
                    if trade_count < max_trades:
                        trade_date_idx[trade_count] = i
                        trade_ticker_idx[trade_count] = j
                        trade_side[trade_count] = 2
                        trade_shares[trade_count] = half_shares
                        trade_price[trade_count] = cpx
                        trade_notional[trade_count] = notional
                        trade_fee[trade_count] = fee
                        trade_reason[trade_count] = 2
                        trade_system[trade_count] = system_code[j]
                        trade_count += 1
                    else:
                        overflow = 1
                    shares[j] -= half_shares
                    half_taken[j] = True
                    take_profit_price[j] = cpx
                    took_half_today = True
                    if shares[j] <= 0:
                        entry_price[j] = np.nan
                        half_taken[j] = False
                        take_profit_price[j] = np.nan
                        system_code[j] = 0
                        continue

            reason = 0
            if np.isfinite(ret) and ret <= stop_loss_pct:
                reason = 3
            elif half_taken[j] and not took_half_today and np.isfinite(cpx):
                mpx = ma10[i, j]
                tpx = take_profit_price[j]
                if (np.isfinite(mpx) and cpx <= mpx) or (np.isfinite(tpx) and cpx <= tpx):
                    reason = 4

            if reason == 0:
                continue

            next_open = open_px[i + 1, j]
            sell_price = np.nan
            sell_date_idx = i + 1
            if np.isfinite(next_open) and next_open > 0:
                sell_price = next_open
            elif not future_price_exists[i + 1, j]:
                sell_price = 0.0

            if np.isfinite(sell_price):
                sell_shares = shares[j]
                notional = sell_shares * sell_price
                fee = notional * commission_rate
                cash += notional - fee
                if trade_count < max_trades:
                    trade_date_idx[trade_count] = sell_date_idx
                    trade_ticker_idx[trade_count] = j
                    trade_side[trade_count] = 2
                    trade_shares[trade_count] = sell_shares
                    trade_price[trade_count] = sell_price
                    trade_notional[trade_count] = notional
                    trade_fee[trade_count] = fee
                    trade_reason[trade_count] = reason
                    trade_system[trade_count] = system_code[j]
                    trade_count += 1
                else:
                    overflow = 1
                shares[j] = 0
                entry_price[j] = np.nan
                half_taken[j] = False
                take_profit_price[j] = np.nan
                system_code[j] = 0

        if i < eval_start_idx:
            continue

        cand_count = 0
        for j in range(n_tickers):
            if shares[j] > 0:
                continue
            if use_kospi_gate and is_kospi[j] and not kospi_above_ma200[i]:
                continue
            if is_kosdaq[j] and not kosdaq_above_ma120[i]:
                continue
            if not (close_signal[i, j] or open_signal[i + 1, j]):
                continue
            if use_li and not (l_pass[i, j] and i_pass[i, j]):
                continue
            if use_macro and not np.isfinite(macro_scores[i, j]):
                continue
            next_open = open_px[i + 1, j]
            if not (np.isfinite(next_open) and next_open > 0):
                continue
            candidate_idx[cand_count] = j
            score_buf[cand_count] = macro_scores[i, j]
            cand_count += 1

        if cand_count == 0:
            continue

        if rebalance_equal_on_entry:
            target_count = 0
            trade_equity = cash
            for j in range(n_tickers):
                if shares[j] <= 0:
                    continue
                next_open = open_px[i + 1, j]
                if not (np.isfinite(next_open) and next_open > 0):
                    continue
                target_idx[target_count] = j
                target_count += 1
                trade_equity += shares[j] * next_open

            for k in range(cand_count):
                target_idx[target_count] = candidate_idx[k]
                target_count += 1

            if target_count == 0 or trade_equity <= 0:
                continue

            equal_weight = 1.0 / target_count
            for k in range(target_count):
                base_weight[k] = equal_weight
                final_weight[k] = 0.0
                order_weight[k] = 0.0

            remaining_weight = 1.0
            remaining_count = target_count
            active = np.ones(target_count, dtype=np.bool_)
            while remaining_count > 0 and remaining_weight > 1e-12:
                sum_base = 0.0
                for k in range(target_count):
                    if active[k]:
                        sum_base += base_weight[k]
                if sum_base <= 0:
                    break
                over_any = False
                for k in range(target_count):
                    if not active[k]:
                        continue
                    proposed = base_weight[k] / sum_base * remaining_weight
                    if proposed > position_cap:
                        final_weight[k] = position_cap
                        active[k] = False
                        remaining_weight -= position_cap
                        remaining_count -= 1
                        over_any = True
                if not over_any:
                    for k in range(target_count):
                        if active[k]:
                            final_weight[k] = base_weight[k] / sum_base * remaining_weight
                    break

            cap_value = position_cap * trade_equity
            for k in range(target_count):
                j = target_idx[k]
                if shares[j] <= 0:
                    continue
                next_open = open_px[i + 1, j]
                if not (np.isfinite(next_open) and next_open > 0):
                    continue
                target_value = final_weight[k] * trade_equity
                if target_value > cap_value:
                    target_value = cap_value
                current_value = shares[j] * next_open
                if current_value <= target_value:
                    continue
                sell_shares = int(np.floor((current_value - target_value) / next_open))
                if sell_shares <= 0:
                    continue
                if sell_shares > shares[j]:
                    sell_shares = shares[j]
                notional = sell_shares * next_open
                fee = notional * commission_rate
                cash += notional - fee
                shares[j] -= sell_shares
                if trade_count < max_trades:
                    trade_date_idx[trade_count] = i + 1
                    trade_ticker_idx[trade_count] = j
                    trade_side[trade_count] = 2
                    trade_shares[trade_count] = sell_shares
                    trade_price[trade_count] = next_open
                    trade_notional[trade_count] = notional
                    trade_fee[trade_count] = fee
                    trade_reason[trade_count] = 5
                    trade_system[trade_count] = system_code[j]
                    trade_count += 1
                else:
                    overflow = 1
                if shares[j] <= 0:
                    entry_price[j] = np.nan
                    half_taken[j] = False
                    take_profit_price[j] = np.nan
                    system_code[j] = 0

            for k in range(target_count):
                order_weight[k] = final_weight[k]
            order = np.argsort(order_weight[:target_count])
            for order_pos in range(target_count - 1, -1, -1):
                k = order[order_pos]
                weight = final_weight[k]
                if weight <= 0:
                    continue
                j = target_idx[k]
                next_open = open_px[i + 1, j]
                if not (np.isfinite(next_open) and next_open > 0):
                    continue
                target_value = weight * trade_equity
                if target_value > cap_value:
                    target_value = cap_value
                current_value = shares[j] * next_open
                if current_value >= target_value:
                    continue
                buy_value = target_value - current_value
                budget = cash / (1.0 + commission_rate)
                if budget < buy_value:
                    buy_value = budget
                buy_shares = int(np.floor(buy_value / next_open))
                if buy_shares <= 0:
                    continue
                notional = buy_shares * next_open
                fee = notional * commission_rate
                cash_delta = -(notional + fee)
                if cash + cash_delta < -1e-6:
                    continue
                old_shares = shares[j]
                cash += cash_delta
                shares[j] += buy_shares
                if old_shares > 0 and np.isfinite(entry_price[j]) and entry_price[j] > 0:
                    entry_price[j] = (old_shares * entry_price[j] + buy_shares * next_open) / shares[j]
                else:
                    entry_price[j] = next_open
                    if open_signal[i + 1, j]:
                        system_code[j] = 2
                    else:
                        system_code[j] = 1
                half_taken[j] = False
                take_profit_price[j] = np.nan
                if system_code[j] == 0:
                    if open_signal[i + 1, j]:
                        system_code[j] = 2
                    else:
                        system_code[j] = 1
                if trade_count < max_trades:
                    trade_date_idx[trade_count] = i + 1
                    trade_ticker_idx[trade_count] = j
                    trade_side[trade_count] = 1
                    trade_shares[trade_count] = buy_shares
                    trade_price[trade_count] = next_open
                    trade_notional[trade_count] = notional
                    trade_fee[trade_count] = fee
                    trade_reason[trade_count] = 10 + system_code[j]
                    trade_system[trade_count] = system_code[j]
                    trade_count += 1
                else:
                    overflow = 1
            continue

        if use_macro:
            order = np.argsort(score_buf[:cand_count])
            rank_sum = cand_count * (cand_count + 1.0) / 2.0
            for k in range(cand_count):
                base_weight[k] = 0.0
            for sorted_pos in range(cand_count):
                original_pos = order[sorted_pos]
                base_weight[original_pos] = (sorted_pos + 1.0) / rank_sum
        else:
            equal_weight = 1.0 / cand_count
            for k in range(cand_count):
                base_weight[k] = equal_weight

        for k in range(cand_count):
            final_weight[k] = 0.0
            order_weight[k] = 0.0

        remaining_weight = 1.0
        remaining_count = cand_count
        active = np.ones(cand_count, dtype=np.bool_)
        while remaining_count > 0 and remaining_weight > 1e-12:
            sum_base = 0.0
            for k in range(cand_count):
                if active[k]:
                    sum_base += base_weight[k]
            if sum_base <= 0:
                break
            over_any = False
            for k in range(cand_count):
                if not active[k]:
                    continue
                proposed = base_weight[k] / sum_base * remaining_weight
                if proposed > position_cap:
                    final_weight[k] = position_cap
                    active[k] = False
                    remaining_weight -= position_cap
                    remaining_count -= 1
                    over_any = True
            if not over_any:
                for k in range(cand_count):
                    if active[k]:
                        final_weight[k] = base_weight[k] / sum_base * remaining_weight
                break

        for k in range(cand_count):
            order_weight[k] = final_weight[k]
        order = np.argsort(order_weight[:cand_count])
        budget = cash / (1.0 + commission_rate)
        if budget < 0:
            budget = 0.0
        cap_value = position_cap * equity

        for order_pos in range(cand_count - 1, -1, -1):
            k = order[order_pos]
            weight = final_weight[k]
            if weight <= 0:
                continue
            j = candidate_idx[k]
            next_open = open_px[i + 1, j]
            order_value = budget * weight
            if order_value > cap_value:
                order_value = cap_value
            buy_shares = int(np.floor(order_value / next_open))
            if buy_shares <= 0:
                continue
            notional = buy_shares * next_open
            fee = notional * commission_rate
            cash_delta = -(notional + fee)
            if cash + cash_delta < -1e-6:
                continue
            cash += cash_delta
            shares[j] = buy_shares
            entry_price[j] = next_open
            half_taken[j] = False
            take_profit_price[j] = np.nan
            if open_signal[i + 1, j]:
                system_code[j] = 2
            else:
                system_code[j] = 1
            if trade_count < max_trades:
                trade_date_idx[trade_count] = i + 1
                trade_ticker_idx[trade_count] = j
                trade_side[trade_count] = 1
                trade_shares[trade_count] = buy_shares
                trade_price[trade_count] = next_open
                trade_notional[trade_count] = notional
                trade_fee[trade_count] = fee
                trade_reason[trade_count] = 10 + system_code[j]
                trade_system[trade_count] = system_code[j]
                trade_count += 1
            else:
                overflow = 1

    final_i = n_dates - 1
    equity = cash
    position_count = 0
    for j in range(n_tickers):
        if shares[j] > 0:
            position_count += 1
            if future_price_exists[final_i, j]:
                px = valuation_close[final_i, j]
                if np.isfinite(px):
                    equity += shares[j] * px
    equity_out[final_i] = equity
    cash_out[final_i] = cash
    positions_out[final_i] = position_count

    return (
        equity_out,
        cash_out,
        positions_out,
        trade_date_idx,
        trade_ticker_idx,
        trade_side,
        trade_shares,
        trade_price,
        trade_notional,
        trade_fee,
        trade_reason,
        trade_system,
        trade_count,
        overflow,
    )


def _bool_values(frame: pd.DataFrame) -> np.ndarray:
    return np.ascontiguousarray(frame.fillna(False).to_numpy(dtype=np.bool_))


def _float_values(frame: pd.DataFrame) -> np.ndarray:
    return np.ascontiguousarray(frame.to_numpy(dtype=np.float64))


def _run_high52_backtest_fast(
    model: str,
    data: MarketData,
    features: dict[str, pd.DataFrame],
    macro_scores: pd.DataFrame | None,
    cfg: BacktestConfig,
) -> BacktestResult:
    model_codes = {
        "model_a": 0,
        "model_b": 1,
        "model_c": 2,
        "model_d": 3,
        "model_e": 4,
        "model_f": 5,
        "model_g": 6,
        "model_h": 7,
    }
    dates = data.dates
    evaluation_start = first_backtest_date(dates, cfg.warmup_years)
    eval_start_idx = int(dates.searchsorted(evaluation_start, side="left"))

    open_px = _float_values(data.open)
    close_px = _float_values(data.close)
    valuation_close = _float_values(data.close.ffill())
    ma10 = _float_values(features["ma10"])
    close_signal = _bool_values(features["high52_close_entry"])
    open_signal = _bool_values(features["high52_open_entry"])
    l_pass = _bool_values(features["l_pass"])
    i_pass = _bool_values(features["i_pass"])
    kospi_above_ma200 = features["kospi_above_ma200"].reindex(dates).fillna(False).to_numpy(dtype=np.bool_)
    kosdaq_above_ma120 = features["kosdaq_above_ma120"].reindex(dates).fillna(False).to_numpy(dtype=np.bool_)
    is_kospi = np.array([data.market.loc[ticker] == "KOSPI" for ticker in data.tickers], dtype=np.bool_)
    is_kosdaq = np.array([data.market.loc[ticker] == "KOSDAQ" for ticker in data.tickers], dtype=np.bool_)
    if macro_scores is None:
        macro_arr = np.zeros_like(close_px, dtype=np.float64)
    else:
        macro_arr = _float_values(macro_scores.reindex(index=dates, columns=data.tickers))
    future_price_exists = np.maximum.accumulate((np.isfinite(open_px) | np.isfinite(close_px))[::-1], axis=0)[::-1]

    signal_count = int(close_signal.sum() + open_signal.sum())
    if model in {"model_e", "model_h"}:
        max_trades = max(500_000, signal_count * 20 + 50_000)
    else:
        max_trades = max(200_000, signal_count * 4 + 10_000)
    core_result = _high52_core(
        open_px,
        close_px,
        valuation_close,
        ma10,
        close_signal,
        open_signal,
        l_pass,
        i_pass,
        kospi_above_ma200,
        kosdaq_above_ma120,
        is_kospi,
        is_kosdaq,
        macro_arr,
        future_price_exists,
        eval_start_idx,
        model_codes[model],
        cfg.initial_equity,
        cfg.commission_rate,
        cfg.stop_loss_pct,
        cfg.high52_position_cap,
        cfg.high52_take_profit_fraction,
        max_trades,
    )
    (
        equity_out,
        cash_out,
        positions_out,
        trade_date_idx,
        trade_ticker_idx,
        trade_side,
        trade_shares,
        trade_price,
        trade_notional,
        trade_fee,
        trade_reason,
        trade_system,
        trade_count,
        overflow,
    ) = core_result
    if overflow:
        raise RuntimeError(f"High52 fast backtest trade log overflowed max_trades={max_trades}")

    equity_curve = pd.DataFrame(
        {
            "date": dates,
            "equity": equity_out,
            "cash": cash_out,
            "positions": positions_out,
        }
    ).set_index("date")
    equity_curve = equity_curve.loc[equity_curve.index >= evaluation_start]

    reason_map = {
        1: "delisted",
        2: "take_profit_24pct_half_close",
        3: "exit_8pct_stop",
        4: "exit_after_half_profit",
        5: "rebalance_equal_sell",
    }
    system_map = {1: "close52", 2: "open52"}
    trade_rows = []
    tickers = data.tickers
    for idx in range(trade_count):
        side = "BUY" if trade_side[idx] == 1 else "SELL"
        system = system_map.get(int(trade_system[idx]), "unknown52")
        reason = f"entry_{model}_{system}" if side == "BUY" else reason_map.get(int(trade_reason[idx]), "unknown")
        trade_rows.append(
            {
                "date": dates[int(trade_date_idx[idx])],
                "ticker": tickers[int(trade_ticker_idx[idx])],
                "side": side,
                "shares": int(trade_shares[idx]),
                "price": float(trade_price[idx]),
                "notional": float(trade_notional[idx]),
                "fee": float(trade_fee[idx]),
                "reason": reason,
                "system": system,
            }
        )
    trades_df = pd.DataFrame(trade_rows)
    if not trades_df.empty:
        trades_df = trades_df.loc[trades_df["date"] >= evaluation_start].reset_index(drop=True)

    metrics = compute_metrics(equity_curve["equity"], trades_df, cfg.initial_equity)
    metrics.update({"model": model, "evaluation_start": evaluation_start.strftime("%Y-%m-%d")})
    return BacktestResult(
        model=model,
        equity_curve=equity_curve,
        trades=trades_df,
        metrics=metrics,
        start=dates[0].strftime("%Y-%m-%d"),
        end=dates[-1].strftime("%Y-%m-%d"),
        evaluation_start=evaluation_start.strftime("%Y-%m-%d"),
    )


def run_backtest(
    model: str,
    data: MarketData,
    features: dict[str, pd.DataFrame],
    macro_scores: pd.DataFrame | None = None,
    cfg: BacktestConfig = CONFIG,
) -> BacktestResult:
    if model not in MODEL_NAMES:
        raise ValueError(f"Unsupported model: {model}")
    if model in MACRO_WEIGHT_MODELS and macro_scores is None:
        raise ValueError(f"macro_scores is required for {model}")
    if model in HIGH52_MODELS:
        return _run_high52_backtest_fast(model, data, features, macro_scores, cfg)

    dates = data.dates
    evaluation_start = first_backtest_date(dates, cfg.warmup_years)
    cash = cfg.initial_equity
    positions: dict[str, Position] = {}
    trades: list[dict[str, object]] = []
    equity_rows: list[dict[str, object]] = []
    trading_disabled_until_recovery = False
    use_portfolio_dd = model not in HIGH52_MODELS

    for i, date in enumerate(dates[:-1]):
        next_date = dates[i + 1]
        equity = _portfolio_value(date, positions, cash, data.close)
        running_equity = pd.Series([r["equity"] for r in equity_rows] + [equity])
        dd = float(equity / running_equity.max() - 1.0) if not running_equity.empty else 0.0

        equity_rows.append(
            {
                "date": date,
                "equity": equity,
                "cash": cash,
                "positions": len(positions),
            }
        )

        if use_portfolio_dd and dd <= cfg.drawdown_kill_switch:
            trading_disabled_until_recovery = True
        if use_portfolio_dd and trading_disabled_until_recovery and dd > cfg.resume_drawdown:
            trading_disabled_until_recovery = False

        # Exits are calculated at t close and executed at t+1 open.
        for ticker in list(positions):
            pos = positions[ticker]
            close_px = _safe_float(data.close.at[date, ticker])
            next_open = _safe_float(data.open.at[next_date, ticker])
            if not np.isfinite(close_px) and _exit_due_to_delist(ticker, i, data):
                trade = {
                    "date": date.strftime("%Y-%m-%d"),
                    "ticker": ticker,
                    "side": "SELL",
                    "shares": pos.shares,
                    "price": 0.0,
                    "notional": 0.0,
                    "fee": 0.0,
                    "reason": "delisted",
                }
                trades.append(trade)
                del positions[ticker]
                continue

            if model in HIGH52_MODELS:
                ret = close_px / pos.entry_price - 1.0 if np.isfinite(close_px) else np.nan
                took_half_today = False
                if np.isfinite(ret) and ret >= 0.24 and not pos.half_taken and np.isfinite(close_px):
                    half_shares = int(np.floor(pos.shares * cfg.high52_take_profit_fraction))
                    if half_shares > 0:
                        cash_delta, trade = _sell_shares(
                            ticker,
                            pos,
                            half_shares,
                            close_px,
                            date,
                            "take_profit_24pct_half_close",
                            cfg,
                        )
                        cash += cash_delta
                        trades.append(trade)
                        pos.shares -= half_shares
                        pos.half_taken = True
                        pos.take_profit_price = close_px
                        pos.half_exit_date = date.strftime("%Y-%m-%d")
                        took_half_today = True
                        if pos.shares <= 0:
                            del positions[ticker]
                            continue

                stop_8pct = np.isfinite(ret) and ret <= cfg.stop_loss_pct
                ma10 = _safe_float(features["ma10"].at[date, ticker])
                residual_exit = (
                    pos.half_taken
                    and not took_half_today
                    and np.isfinite(close_px)
                    and (
                        (np.isfinite(ma10) and close_px <= ma10)
                        or (np.isfinite(pos.take_profit_price) and close_px <= pos.take_profit_price)
                    )
                )
                reason = None
                if stop_8pct:
                    reason = "exit_8pct_stop"
                elif residual_exit:
                    reason = "exit_after_half_profit"
                if reason is None:
                    continue
                if np.isfinite(next_open):
                    cash_delta, trade = _sell(ticker, pos, next_open, next_date, reason, cfg)
                    cash += cash_delta
                    trades.append(trade)
                    del positions[ticker]
                elif _exit_due_to_delist(ticker, i + 1, data):
                    trade = {
                        "date": next_date.strftime("%Y-%m-%d"),
                        "ticker": ticker,
                        "side": "SELL",
                        "shares": pos.shares,
                        "price": 0.0,
                        "notional": 0.0,
                        "fee": 0.0,
                        "reason": "delisted",
                    }
                    trades.append(trade)
                    del positions[ticker]
                continue

            stop_price = pos.last_add_price - 2.0 * pos.n_at_entry
            ret = close_px / pos.entry_price - 1.0 if np.isfinite(close_px) else np.nan
            use_s2_exit = model != "cim_turtle"
            use_m_kill = model in {"canslim_turtle", "canslim_turtle_macro", "cim_turtle"}
            market_kill = use_m_kill and bool(features["m_kill"].at[date, ticker])
            channel_exit = bool(features["s2_exit"].at[date, ticker]) if pos.system == "S2" else bool(features["s1_exit"].at[date, ticker])
            profit_ma = _safe_float(features["ma20"].at[date, ticker]) if pos.system == "S2" else _safe_float(features["ma10"].at[date, ticker])
            profit_take = np.isfinite(ret) and ret > 0 and np.isfinite(close_px) and np.isfinite(profit_ma) and close_px < profit_ma
            stop_2n = np.isfinite(close_px) and close_px <= stop_price
            stop_8pct = np.isfinite(ret) and ret <= cfg.stop_loss_pct
            reason = None
            if use_s2_exit and channel_exit:
                reason = f"exit_{pos.system.lower()}_channel"
            if profit_take:
                reason = f"take_profit_{pos.system.lower()}_ma"
            if stop_2n:
                reason = "exit_2n_stop"
            if stop_8pct:
                reason = "exit_8pct_stop"
            if market_kill:
                reason = "exit_m_kill"
            if trading_disabled_until_recovery:
                reason = "exit_portfolio_dd"
            if reason is None:
                continue
            if np.isfinite(next_open):
                cash_delta, trade = _sell(ticker, pos, next_open, next_date, reason, cfg)
                cash += cash_delta
                trades.append(trade)
                del positions[ticker]
            elif _exit_due_to_delist(ticker, i + 1, data):
                trade = {
                    "date": next_date.strftime("%Y-%m-%d"),
                    "ticker": ticker,
                    "side": "SELL",
                    "shares": pos.shares,
                    "price": 0.0,
                    "notional": 0.0,
                    "fee": 0.0,
                    "reason": "delisted",
                }
                trades.append(trade)
                del positions[ticker]

        if date >= evaluation_start and not trading_disabled_until_recovery:
            if model in HIGH52_MODELS:
                candidate_mask = _high52_candidate_mask(model, date, next_date, data, features)
            elif model in {"canslim_turtle", "canslim_turtle_macro"}:
                candidate_mask = features["candidate"].loc[date].fillna(False)
            elif model == "cim_turtle":
                candidate_mask = (
                    features["c_pass"].loc[date].fillna(False)
                    & features["i_pass"].loc[date].fillna(False)
                    & features["m_pass"].loc[date].fillna(False)
                    & features["turtle_entry"].loc[date].fillna(False)
                )
            else:
                candidate_mask = features["turtle_entry"].loc[date].fillna(False)
            candidate_mask = candidate_mask & ~candidate_mask.index.isin(positions.keys())
            candidates = candidate_mask[candidate_mask].index.tolist()

            if model in HIGH52_MODELS:
                if model in {"model_b", "model_d"}:
                    macro_row = macro_scores.loc[date, candidates] if candidates else pd.Series(dtype=float)
                    weights = _normalized_rank_weights(macro_row)
                elif candidates:
                    weights = pd.Series(1.0 / len(candidates), index=candidates, dtype="float64")
                else:
                    weights = pd.Series(dtype="float64")
                weights = _cap_weights(weights, cfg.high52_position_cap)

                budget = max(cash / (1.0 + cfg.commission_rate), 0.0)
                cap_value = cfg.high52_position_cap * equity
                for ticker, weight in weights.items():
                    next_open = _safe_float(data.open.at[next_date, ticker])
                    if not (np.isfinite(next_open) and next_open > 0):
                        continue
                    order_value = min(budget * float(weight), cap_value)
                    shares = int(np.floor(order_value / next_open))
                    if shares <= 0:
                        continue
                    n_at_entry = _safe_float(features["atr_n"].at[date, ticker], 0.0)
                    system = _high52_entry_reason(date, next_date, ticker, features)
                    cash_delta, pos, trade = _buy(ticker, shares, next_open, next_date, f"entry_{model}_{system}", n_at_entry, system, cfg)
                    if cash + cash_delta < -1e-6:
                        continue
                    cash += cash_delta
                    positions[ticker] = pos
                    trades.append(trade)
            elif model in {"canslim_turtle", "cim_turtle", "turtle_only"}:
                if model == "canslim_turtle":
                    rank_row = features["rank_score"].loc[date, candidates] if candidates else pd.Series(dtype=float)
                else:
                    rank_row = features["rs_percentile"].loc[date, candidates] if candidates else pd.Series(dtype=float)
                for ticker in _candidate_order(rank_row):
                    if len(positions) >= cfg.max_positions:
                        break
                    next_open = _safe_float(data.open.at[next_date, ticker])
                    n_at_entry = _safe_float(features["atr_n"].at[date, ticker])
                    adv20 = _safe_float(features["adv20"].at[date, ticker])
                    if not all(np.isfinite(x) and x > 0 for x in [next_open, n_at_entry, adv20]):
                        continue
                    system = _entry_system(date, ticker, features)
                    shares_unit = int(np.floor(equity * cfg.risk_per_trade / (2.0 * n_at_entry)))
                    if shares_unit <= 0:
                        continue
                    position_value = shares_unit * next_open
                    order_value = min(
                        position_value,
                        cash / (1.0 + cfg.commission_rate),
                        cfg.adv_cap * adv20,
                    )
                    shares = int(np.floor(order_value / next_open))
                    if shares <= 0:
                        continue
                    cash_delta, pos, trade = _buy(ticker, shares, next_open, next_date, f"entry_{model}", n_at_entry, system, cfg)
                    if cash + cash_delta < -1e-6:
                        continue
                    cash += cash_delta
                    positions[ticker] = pos
                    trades.append(trade)
            else:
                macro_row = macro_scores.loc[date, candidates].dropna() if candidates else pd.Series(dtype=float)
                macro_row = macro_row.sort_values(ascending=False)
                if not macro_row.empty:
                    ranks = pd.Series(np.arange(1, len(macro_row) + 1), index=macro_row.index, dtype="float64")
                    weights = (1.0 / ranks) / (1.0 / ranks).sum()
                    budget = max(cash / (1.0 + cfg.commission_rate), 0.0)
                    for ticker, weight in weights.items():
                        next_open = _safe_float(data.open.at[next_date, ticker])
                        n_at_entry = _safe_float(features["atr_n"].at[date, ticker])
                        if not (np.isfinite(next_open) and next_open > 0 and np.isfinite(n_at_entry) and n_at_entry > 0):
                            continue
                        system = _entry_system(date, ticker, features)
                        order_value = budget * float(weight)
                        shares = int(np.floor(order_value / next_open))
                        if shares <= 0:
                            continue
                        cash_delta, pos, trade = _buy(ticker, shares, next_open, next_date, f"entry_{model}", n_at_entry, system, cfg)
                        if cash + cash_delta < -1e-6:
                            continue
                        cash += cash_delta
                        positions[ticker] = pos
                        trades.append(trade)

    final_date = dates[-1]
    equity_rows.append(
        {
            "date": final_date,
            "equity": _portfolio_value(final_date, positions, cash, data.close),
            "cash": cash,
            "positions": len(positions),
        }
    )
    equity_curve = pd.DataFrame(equity_rows).set_index("date")
    equity_curve = equity_curve.loc[equity_curve.index >= evaluation_start]
    trades_df = pd.DataFrame(trades)
    if not trades_df.empty:
        trades_df["date"] = pd.to_datetime(trades_df["date"])
        trades_df = trades_df.loc[trades_df["date"] >= evaluation_start].reset_index(drop=True)
    metrics = compute_metrics(equity_curve["equity"], trades_df, cfg.initial_equity)
    metrics.update({"model": model, "evaluation_start": evaluation_start.strftime("%Y-%m-%d")})

    return BacktestResult(
        model=model,
        equity_curve=equity_curve,
        trades=trades_df,
        metrics=metrics,
        start=dates[0].strftime("%Y-%m-%d"),
        end=dates[-1].strftime("%Y-%m-%d"),
        evaluation_start=evaluation_start.strftime("%Y-%m-%d"),
    )


def save_result(result: BacktestResult) -> Path:
    out_dir = RESULTS_DIR / result.model
    out_dir.mkdir(parents=True, exist_ok=True)
    result.equity_curve.to_csv(out_dir / "equity_curve.csv", encoding="utf-8-sig")
    result.trades.to_csv(out_dir / "trades.csv", encoding="utf-8-sig", index=False)
    payload = {
        "model": result.model,
        "start": result.start,
        "end": result.end,
        "evaluation_start": result.evaluation_start,
        "metrics": result.metrics,
        "config": asdict(CONFIG),
    }
    (out_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_dir
