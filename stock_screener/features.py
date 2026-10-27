from __future__ import annotations

import numpy as np
import pandas as pd

from .data import MarketData, next_trading_date, rank_pct, rolling_slope

AVAILABILITY_DELAY_DAYS = 2


class FeatureSet(dict):
    """Dictionary subclass used only to make type intent explicit."""


def _asof_quarterly(
    values: pd.DataFrame,
    dates: pd.DatetimeIndex,
    lag_days: int,
) -> pd.DataFrame:
    events: list[tuple[pd.Timestamp, pd.Series]] = []
    for period_end, row in values.iterrows():
        available = next_trading_date(
            dates,
            pd.Timestamp(period_end) + pd.Timedelta(days=lag_days + AVAILABILITY_DELAY_DAYS),
        )
        if available is not None:
            events.append((available, row))
    if not events:
        return pd.DataFrame(index=dates, columns=values.columns, dtype="float32")
    event_df = pd.DataFrame(
        [row.to_numpy(dtype="float32") for _, row in events],
        index=pd.DatetimeIndex([date for date, _ in events]),
        columns=values.columns,
    )
    event_df = event_df.loc[~event_df.index.duplicated(keep="last")]
    return event_df.reindex(dates).ffill().astype("float32")


def fundamentals_features(data: MarketData) -> FeatureSet:
    dates = data.dates
    quarter_dates = pd.DatetimeIndex(data.earnings_krw.index)
    quarter_shares = data.listed_shares.reindex(quarter_dates, method="ffill")
    eps_q = data.earnings_krw / quarter_shares
    eps_yoy = eps_q / eps_q.shift(4) - 1.0
    c_pass_event = (eps_q > 0) & (eps_q.shift(4) > 0) & (eps_yoy >= 0.25)

    annual_income = data.earnings_krw.groupby(data.earnings_krw.index.year).sum(min_count=4)
    annual_shares = quarter_shares.groupby(quarter_shares.index.year).mean()
    annual_eps = annual_income / annual_shares
    annual_cagr = (annual_eps / annual_eps.shift(3)).pow(1.0 / 3.0) - 1.0
    a_pass_event = (
        (annual_cagr >= 0.25)
        & (annual_eps > 0)
        & (annual_eps.shift(1) > 0)
        & (annual_eps.shift(2) > 0)
        & (annual_eps.shift(3) > 0)
        & (annual_eps > annual_eps.shift(1))
    )
    annual_event_index = [
        data.earnings_krw.loc[str(year)].index.max()
        if str(year) in data.earnings_krw.index.strftime("%Y").tolist()
        else pd.Timestamp(year=year, month=12, day=31)
        for year in annual_eps.index
    ]
    a_pass_event.index = pd.DatetimeIndex(annual_event_index)
    annual_cagr.index = pd.DatetimeIndex(annual_event_index)

    return FeatureSet(
        c_pass=_asof_quarterly(c_pass_event.astype("float32"), dates, 60).astype(bool),
        eps_yoy=_asof_quarterly(eps_yoy.astype("float32"), dates, 60),
        a_pass=_asof_quarterly(a_pass_event.astype("float32"), dates, 120).astype(bool),
        a_cagr=_asof_quarterly(annual_cagr.astype("float32"), dates, 120),
    )


def market_index_for_tickers(data: MarketData) -> pd.DataFrame:
    idx = pd.DataFrame(index=data.dates, columns=data.tickers, dtype="float32")
    kospi = data.index["kospi_close"].astype("float32")
    kosdaq = data.index["kosdaq_close"].astype("float32")
    kospi_cols = [ticker for ticker in data.tickers if data.market.loc[ticker] == "KOSPI"]
    kosdaq_cols = [ticker for ticker in data.tickers if data.market.loc[ticker] == "KOSDAQ"]
    if kospi_cols:
        idx.loc[:, kospi_cols] = np.repeat(kospi.to_numpy()[:, None], len(kospi_cols), axis=1)
    if kosdaq_cols:
        idx.loc[:, kosdaq_cols] = np.repeat(kosdaq.to_numpy()[:, None], len(kosdaq_cols), axis=1)
    return idx.astype("float32")


def high52_features(data: MarketData, include_li: bool = True) -> FeatureSet:
    f = FeatureSet()
    close = data.close
    high = data.high

    f["high52_prev"] = high.shift(1).rolling(252, min_periods=252).max().astype("float32")
    f["high52_close_entry"] = close > f["high52_prev"]
    f["high52_open_entry"] = data.open > f["high52_prev"]
    f["ma10"] = close.rolling(10, min_periods=10).mean().astype("float32")
    kospi_close = data.index["kospi_close"]
    kosdaq_close = data.index["kosdaq_close"]
    f["kospi_above_ma200"] = kospi_close >= kospi_close.rolling(200, min_periods=200).mean()
    f["kosdaq_above_ma120"] = kosdaq_close >= kosdaq_close.rolling(120, min_periods=120).mean()

    if not include_li:
        false_frame = pd.DataFrame(False, index=data.dates, columns=data.tickers)
        f["l_pass"] = false_frame
        f["i_pass"] = false_frame.copy()
        return f

    r252 = close / close.shift(252) - 1.0
    rs_pct = r252.rank(axis=1, pct=True)
    idx_close = market_index_for_tickers(data)
    rs_line = close / idx_close
    rs_slope63 = rolling_slope(rs_line, 63)
    f["rs_percentile"] = (rs_pct * 100.0).astype("float32")
    f["rs_slope63"] = rs_slope63
    f["l_pass"] = (f["rs_percentile"] >= 90.0) & (f["rs_slope63"] > 0)

    inst_flow = data.institution_krw / data.market_cap_krw
    inst_flow63 = inst_flow.rolling(63, min_periods=63).sum()
    cum_flow126 = inst_flow.rolling(126, min_periods=126).sum()
    beta = rolling_slope(cum_flow126, 63)
    f["inst_flow63"] = inst_flow63.astype("float32")
    f["inst_beta"] = beta
    f["i_pass"] = (inst_flow63 > 0) & (beta > 0)
    return f


def canslim_turtle_features(data: MarketData) -> FeatureSet:
    f = FeatureSet()
    f.update(fundamentals_features(data))

    close = data.close
    high = data.high
    low = data.low
    volume = data.volume
    trading_value = close * volume

    f["n_pass"] = close >= (0.90 * close.shift(1).rolling(252, min_periods=252).max())

    vol50 = volume.shift(1).rolling(50, min_periods=50).mean()
    vol10 = volume.shift(1).rolling(10, min_periods=10).mean()
    f["vol_shock"] = (volume / vol50).astype("float32")
    f["volume_spike10"] = (volume / vol10).astype("float32")
    f["s_pass"] = (f["vol_shock"] >= 1.5) & (f["volume_spike10"] >= 2.0)

    r252 = close / close.shift(252) - 1.0
    rs_pct = r252.rank(axis=1, pct=True)
    idx_close = market_index_for_tickers(data)
    rs_line = close / idx_close
    rs_slope63 = rolling_slope(rs_line, 63)
    f["rs_percentile"] = (rs_pct * 100.0).astype("float32")
    f["rs_slope63"] = rs_slope63
    f["l_pass"] = (f["rs_percentile"] >= 90.0) & (f["rs_slope63"] > 0)

    inst_flow = data.institution_krw / data.market_cap_krw
    inst_flow63 = inst_flow.rolling(63, min_periods=63).sum()
    cum_flow126 = inst_flow.rolling(126, min_periods=126).sum()
    beta = rolling_slope(cum_flow126, 63)
    f["inst_flow63"] = inst_flow63.astype("float32")
    f["inst_beta"] = beta
    f["i_pass"] = (inst_flow63 > 0) & (beta > 0)

    kospi_close = data.index["kospi_close"]
    kosdaq_close = data.index["kosdaq_close"]
    kospi_m = (
        (kospi_close > kospi_close.rolling(20, min_periods=20).mean())
        .rolling(5, min_periods=5)
        .sum()
        .eq(5)
        & (kospi_close > kospi_close.rolling(50, min_periods=50).mean())
        & (kospi_close.rolling(50, min_periods=50).mean() > kospi_close.rolling(200, min_periods=200).mean())
    )
    kosdaq_m = (
        (kosdaq_close > kosdaq_close.rolling(20, min_periods=20).mean())
        .rolling(5, min_periods=5)
        .sum()
        .eq(5)
        & (kosdaq_close > kosdaq_close.rolling(50, min_periods=50).mean())
        & (kosdaq_close.rolling(50, min_periods=50).mean() > kosdaq_close.rolling(200, min_periods=200).mean())
    )
    m_pass = pd.DataFrame(False, index=data.dates, columns=data.tickers)
    kospi_cols = [ticker for ticker in data.tickers if data.market.loc[ticker] == "KOSPI"]
    kosdaq_cols = [ticker for ticker in data.tickers if data.market.loc[ticker] == "KOSDAQ"]
    if kospi_cols:
        m_pass.loc[:, kospi_cols] = np.repeat(kospi_m.to_numpy()[:, None], len(kospi_cols), axis=1)
    if kosdaq_cols:
        m_pass.loc[:, kosdaq_cols] = np.repeat(kosdaq_m.to_numpy()[:, None], len(kosdaq_cols), axis=1)
    f["m_pass"] = m_pass.astype(bool)
    f["m_kill"] = ((~f["m_pass"]).rolling(5, min_periods=5).sum() == 5).astype(bool)

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).stack(),
            (high - prev_close).abs().stack(),
            (prev_close - low).abs().stack(),
        ],
        axis=1,
    ).max(axis=1).unstack()
    n = tr.ewm(span=20, adjust=False, min_periods=20).mean()
    f["atr_n"] = n.astype("float32")
    f["adv20"] = trading_value.shift(1).rolling(20, min_periods=20).mean().astype("float32")
    f["high52_prev"] = high.shift(1).rolling(252, min_periods=252).max().astype("float32")
    f["high52_close_entry"] = close > f["high52_prev"]
    f["high52_open_entry"] = data.open > f["high52_prev"]
    f["kosdaq_above_ma120"] = kosdaq_close >= kosdaq_close.rolling(120, min_periods=120).mean()
    f["s1_entry"] = high > high.shift(1).rolling(20, min_periods=20).max()
    f["s2_entry"] = high > high.shift(1).rolling(55, min_periods=55).max()
    f["turtle_entry"] = f["s1_entry"] | f["s2_entry"]
    f["s1_exit"] = low < low.shift(1).rolling(10, min_periods=10).min()
    f["s2_exit"] = low < low.shift(1).rolling(20, min_periods=20).min()
    f["ma10"] = close.rolling(10, min_periods=10).mean().astype("float32")
    f["ma20"] = close.rolling(20, min_periods=20).mean().astype("float32")

    f["rank_score"] = (
        0.35 * (f["rs_percentile"] / 100.0)
        + 0.20 * rank_pct(f["eps_yoy"])
        + 0.15 * rank_pct(f["a_cagr"])
        + 0.15 * rank_pct(f["inst_flow63"])
        + 0.10 * rank_pct(f["vol_shock"])
        + 0.05 * rank_pct(f["volume_spike10"])
    ).astype("float32")

    f["candidate"] = (
        f["c_pass"]
        & f["a_pass"]
        & f["n_pass"]
        & f["s_pass"]
        & f["l_pass"]
        & f["i_pass"]
        & f["m_pass"]
        & f["turtle_entry"]
    )
    return f
