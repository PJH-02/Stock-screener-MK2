from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import requests

from .config import MACRO_CACHE_DIR, MACRO_CLASSIFICATION_PATH, MACRO_EXPOSURE_PATH, PROCESSED_DIR
from .data import MarketData, load_env


CHANNELS = ["G", "IC", "FC", "ED", "FX"]
AVAILABILITY_DELAY_DAYS = 2


@dataclass
class MacroResult:
    states: pd.DataFrame
    sector_scores: pd.DataFrame
    ticker_scores: pd.DataFrame
    quality: dict[str, object]


class RateLimitedClient:
    def __init__(self, min_interval: float = 0.35, retries: int = 3) -> None:
        self.min_interval = min_interval
        self.retries = retries
        self.last_request = 0.0
        self.session = requests.Session()

    def get_json(self, url: str) -> dict:
        for attempt in range(self.retries):
            elapsed = time.monotonic() - self.last_request
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self.last_request = time.monotonic()
            try:
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
                return resp.json()
            except Exception:
                if attempt == self.retries - 1:
                    raise
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError("unreachable")


def _fred_series(series_id: str, start: str, end: str, api_key: str, client: RateLimitedClient) -> pd.Series:
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start,
        "observation_end": end,
    }
    url = "https://api.stlouisfed.org/fred/series/observations?" + urlencode(params)
    data = client.get_json(url)
    rows = data.get("observations", [])
    dates = []
    values = []
    for row in rows:
        val = row.get("value")
        if val in {None, "."}:
            continue
        dates.append(pd.Timestamp(row["date"]))
        values.append(float(val))
    s = pd.Series(values, index=pd.DatetimeIndex(dates), name=series_id).sort_index()
    return s.groupby(level=0).last()


def _ecos_series(
    stat_code: str,
    cycle: str,
    start_yyyymm: str,
    end_yyyymm: str,
    items: tuple[str, ...],
    api_key: str,
    client: RateLimitedClient,
    name: str,
) -> pd.Series:
    item_path = "/".join(items)
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/100000/"
        f"{stat_code}/{cycle}/{start_yyyymm}/{end_yyyymm}/{item_path}"
    )
    data = client.get_json(url)
    rows = data.get("StatisticSearch", {}).get("row", [])
    dates = []
    values = []
    for row in rows:
        time_code = str(row.get("TIME", ""))
        value = row.get("DATA_VALUE")
        if not time_code or value in {None, ""}:
            continue
        if len(time_code) == 6:
            date = pd.Timestamp(year=int(time_code[:4]), month=int(time_code[4:6]), day=1)
        elif len(time_code) == 4:
            date = pd.Timestamp(year=int(time_code), month=12, day=1)
        else:
            continue
        dates.append(date)
        values.append(float(value))
    s = pd.Series(values, index=pd.DatetimeIndex(dates), name=name).sort_index()
    return s.groupby(level=0).last()


def _yoy_3mma(series: pd.Series) -> pd.Series:
    return (series / series.shift(12) - 1.0).rolling(3, min_periods=3).mean() * 100.0


def _log_return_3m(series: pd.Series) -> pd.Series:
    return (np.log(series) - np.log(series.shift(3))) * 100.0


def _z36(series: pd.Series) -> pd.Series:
    mean = series.rolling(36, min_periods=24).mean()
    std = series.rolling(36, min_periods=24).std()
    return (series - mean) / std


def _classify(value: float, pos: float, neg: float, lower_is_positive: bool = False) -> float:
    if not np.isfinite(value):
        return 0.0
    if lower_is_positive:
        if value <= pos:
            return 1.0
        if value >= neg:
            return -1.0
        return 0.0
    if value >= pos:
        return 1.0
    if value <= neg:
        return -1.0
    return 0.0


def collect_macro_states(start: str, end: str) -> tuple[pd.DataFrame, dict[str, object]]:
    env = load_env()
    fred_key = env.get("FRED_API_KEY", "")
    ecos_key = env.get("ECOS_API_KEY", "")
    client = RateLimitedClient()
    MACRO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    quality: dict[str, object] = {"series": {}, "start": start, "end": end}
    raw: dict[str, pd.Series] = {}

    fred_specs = {
        "us_ipi": "INDPRO",
        "us_cpi": "CPIAUCSL",
        "us_credit_spread": "BAA10YM",
        "us_real_imports_goods": "A255RO1Q156NBEA",
        "us_real_pce_goods": "DGDSRX1",
        "broad_usd": "TWEXBGSMTH",
    }
    if fred_key:
        for name, series_id in fred_specs.items():
            try:
                s = _fred_series(series_id, start, end, fred_key, client)
                raw[name] = s
                quality["series"][name] = {"source": "FRED", "rows": int(s.count()), "ok": True}
            except Exception as exc:
                quality["series"][name] = {"source": "FRED", "ok": False, "error": str(exc)[:300]}
    else:
        quality["fred"] = "missing FRED_API_KEY"

    if ecos_key:
        start_yyyymm = pd.Timestamp(start).strftime("%Y%m")
        end_yyyymm = pd.Timestamp(end).strftime("%Y%m")
        ecos_specs = {
            "kr_ipi": ("901Y033", "M", ("A00", "2")),
            "kr_cpi": ("901Y009", "M", ("0",)),
            "kr_exports_us": ("901Y121", "M", ("T002", "US")),
            "usdkrw": ("731Y006", "M", ("0000003",)),
            "kr_rate_corp": ("721Y001", "M", ("7020000",)),
            "kr_rate_gov": ("721Y001", "M", ("5020000",)),
        }
        for name, spec in ecos_specs.items():
            try:
                stat_code, cycle, items = spec
                s = _ecos_series(stat_code, cycle, start_yyyymm, end_yyyymm, items, ecos_key, client, name=name)
                raw[name] = s
                quality["series"][name] = {"source": "ECOS", "rows": int(s.count()), "ok": True}
            except Exception as exc:
                quality["series"][name] = {"source": "ECOS", "ok": False, "error": str(exc)[:300]}
    else:
        quality["ecos"] = "missing ECOS_API_KEY"

    monthly_index = pd.date_range(start=start, end=end, freq="MS")
    metrics = pd.DataFrame(index=monthly_index)
    if "kr_ipi" in raw:
        metrics["kr_ipi_yoy_3mma"] = _yoy_3mma(raw["kr_ipi"]).reindex(monthly_index).ffill()
    if "us_ipi" in raw:
        metrics["us_ipi_yoy_3mma"] = _yoy_3mma(raw["us_ipi"]).reindex(monthly_index).ffill()
    if "kr_cpi" in raw:
        metrics["kr_cpi_yoy_3mma"] = _yoy_3mma(raw["kr_cpi"]).reindex(monthly_index).ffill()
    if "us_cpi" in raw:
        metrics["us_cpi_yoy_3mma"] = _yoy_3mma(raw["us_cpi"]).reindex(monthly_index).ffill()
    if "kr_rate_corp" in raw and "kr_rate_gov" in raw:
        metrics["kr_credit_spread_z36"] = _z36(raw["kr_rate_corp"] - raw["kr_rate_gov"]).reindex(monthly_index).ffill()
    if "us_credit_spread" in raw:
        metrics["us_credit_spread_z36"] = _z36(raw["us_credit_spread"]).reindex(monthly_index).ffill()
    if "kr_exports_us" in raw:
        metrics["kr_exports_us_yoy_3mma"] = _yoy_3mma(raw["kr_exports_us"]).reindex(monthly_index).ffill()
    if "us_real_imports_goods" in raw:
        metrics["us_real_imports_goods_yoy_3mma"] = _yoy_3mma(raw["us_real_imports_goods"]).reindex(monthly_index).ffill()
    elif "us_real_pce_goods" in raw:
        metrics["us_real_pce_goods_yoy_3mma"] = _yoy_3mma(raw["us_real_pce_goods"]).reindex(monthly_index).ffill()
    if "usdkrw" in raw:
        metrics["usdkrw_3m_log_return"] = _log_return_3m(raw["usdkrw"]).reindex(monthly_index).ffill()
    if "broad_usd" in raw:
        metrics["broad_usd_3m_log_return"] = _log_return_3m(raw["broad_usd"]).reindex(monthly_index).ffill()

    states = pd.DataFrame(index=monthly_index, columns=CHANNELS, dtype="float32")
    g = metrics[[c for c in ["kr_ipi_yoy_3mma", "us_ipi_yoy_3mma"] if c in metrics]].mean(axis=1)
    ic = metrics[[c for c in ["kr_cpi_yoy_3mma", "us_cpi_yoy_3mma"] if c in metrics]].mean(axis=1)
    fc = metrics[[c for c in ["kr_credit_spread_z36", "us_credit_spread_z36"] if c in metrics]].mean(axis=1)
    ed = metrics[[c for c in ["kr_exports_us_yoy_3mma", "us_real_imports_goods_yoy_3mma", "us_real_pce_goods_yoy_3mma"] if c in metrics]].mean(axis=1)
    fx = metrics[[c for c in ["usdkrw_3m_log_return", "broad_usd_3m_log_return"] if c in metrics]].mean(axis=1)
    states["G"] = g.map(lambda x: _classify(x, 1.0, -1.0))
    states["IC"] = ic.map(lambda x: _classify(x, 2.75, 1.25))
    states["FC"] = fc.map(lambda x: _classify(x, -0.5, 0.5, lower_is_positive=True))
    states["ED"] = ed.map(lambda x: _classify(x, 2.0, -2.0))
    states["FX"] = fx.map(lambda x: _classify(x, 2.0, -2.0))
    states = states.fillna(0.0).astype("float32")

    metrics.to_parquet(PROCESSED_DIR / "macro_metrics.parquet")
    states.to_parquet(PROCESSED_DIR / "macro_states.parquet")
    (PROCESSED_DIR / "macro_quality.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    return states, quality


def _load_exposure() -> dict[str, dict[str, float]]:
    payload = json.loads(MACRO_EXPOSURE_PATH.read_text(encoding="utf-8"))
    return payload["sector_exposure"]


def _classification_map() -> dict[str, str]:
    if not MACRO_CLASSIFICATION_PATH.exists():
        return {}
    df = pd.read_csv(MACRO_CLASSIFICATION_PATH, encoding="utf-8")
    code_col = "종목코드"
    sector_cols = ["중분류", "소분류", "대분류"]
    return {
        "A" + str(row[code_col]).zfill(6): _map_sector(" ".join(str(row[c]) for c in sector_cols if c in df.columns))
        for _, row in df.iterrows()
    }


def _map_sector(text: str) -> str:
    text = text.replace("·", "/").replace("-", "/")
    rules = [
        ("반도체 장비", "반도체장비"),
        ("반도체", "반도체"),
        ("디스플레이", "IT하드웨어/디스플레이/전자부품"),
        ("전자", "IT하드웨어/디스플레이/전자부품"),
        ("자동차", "자동차/부품"),
        ("운송장비", "자동차/부품"),
        ("조선", "조선"),
        ("철강", "철강"),
        ("화학", "화학"),
        ("기계", "기계"),
        ("건설", "건설/건자재"),
        ("건자재", "건설/건자재"),
        ("운송", "해운/물류"),
        ("물류", "해운/물류"),
        ("항공", "항공"),
        ("증권", "증권"),
        ("은행", "은행"),
        ("보험", "보험"),
        ("금속", "비철/산업금속"),
        ("비철", "비철/산업금속"),
        ("에너지", "에너지(정유/가스)"),
        ("가스", "에너지(정유/가스)"),
        ("방산", "방산"),
        ("소프트웨어", "소프트웨어/인터넷/게임"),
        ("인터넷", "소프트웨어/인터넷/게임"),
        ("게임", "소프트웨어/인터넷/게임"),
        ("IT 서비스", "소프트웨어/인터넷/게임"),
        ("제약", "헬스케어/바이오"),
        ("건강", "헬스케어/바이오"),
        ("바이오", "헬스케어/바이오"),
        ("유통", "유통/소매"),
        ("소매", "유통/소매"),
        ("음식료", "필수소비재(음식료)"),
        ("담배", "필수소비재(음식료)"),
        ("통신", "통신"),
        ("유틸리티", "유틸리티"),
        ("전기/가스", "유틸리티"),
        ("여행", "여행/레저"),
        ("레저", "여행/레저"),
        ("화장품", "내구소비재/의류/화장품"),
        ("의류", "내구소비재/의류/화장품"),
        ("부동산", "리츠/부동산"),
        ("리츠", "리츠/부동산"),
    ]
    for needle, group in rules:
        if needle in text:
            return group
    return "UNKNOWN"


def build_macro_scores(data: MarketData, states: pd.DataFrame | None = None) -> MacroResult:
    if states is None:
        states = pd.read_parquet(PROCESSED_DIR / "macro_states.parquet")
        states.index = pd.to_datetime(states.index)
    exposure = _load_exposure()
    class_map = _classification_map()

    grouped_sector = {}
    for ticker in data.tickers:
        grouped = class_map.get(ticker)
        if not grouped or grouped == "UNKNOWN":
            grouped = _map_sector(str(data.sector.get(ticker, "")))
        grouped_sector[ticker] = grouped

    sectors = sorted({v for v in grouped_sector.values() if v != "UNKNOWN"})
    sector_scores = pd.DataFrame(0.0, index=states.index, columns=sectors, dtype="float32")
    for channel in CHANNELS:
        channel_exposure = exposure.get(channel, {})
        for sector in sectors:
            sector_scores[sector] += states[channel].astype("float32") * float(channel_exposure.get(sector, 0.0))

    ticker_scores = pd.DataFrame(0.0, index=states.index, columns=data.tickers, dtype="float32")
    for ticker, sector in grouped_sector.items():
        if sector in sector_scores:
            ticker_scores[ticker] = sector_scores[sector]

    sector_scores.to_parquet(PROCESSED_DIR / "macro_sector_scores.parquet")
    ticker_scores.to_parquet(PROCESSED_DIR / "macro_ticker_scores.parquet")
    quality = {
        "mapped_tickers": int(sum(1 for v in grouped_sector.values() if v != "UNKNOWN")),
        "unmapped_tickers": int(sum(1 for v in grouped_sector.values() if v == "UNKNOWN")),
        "sector_count": int(len(sectors)),
    }
    return MacroResult(states=states, sector_scores=sector_scores, ticker_scores=ticker_scores, quality=quality)


def macro_scores_for_daily_dates(data: MarketData, ticker_scores: pd.DataFrame) -> pd.DataFrame:
    ticker_scores = ticker_scores.copy()
    ticker_scores.index = pd.to_datetime(ticker_scores.index)
    daily_index = pd.DatetimeIndex(data.dates)
    available_dates = []
    available_rows = []
    for event_date, row in ticker_scores.iterrows():
        target = pd.Timestamp(event_date) + pd.Timedelta(days=AVAILABILITY_DELAY_DAYS)
        pos = daily_index.searchsorted(target, side="left")
        if pos < len(daily_index):
            available_dates.append(daily_index[pos])
            available_rows.append(row)
    if not available_rows:
        return pd.DataFrame(0.0, index=daily_index, columns=data.tickers, dtype="float32")
    ticker_scores = pd.DataFrame(available_rows, index=pd.DatetimeIndex(available_dates), columns=ticker_scores.columns)
    ticker_scores = ticker_scores.loc[~ticker_scores.index.duplicated(keep="last")]
    aligned_index = ticker_scores.index.union(daily_index)
    return (
        ticker_scores.reindex(aligned_index)
        .sort_index()
        .ffill()
        .reindex(daily_index)
        .reindex(columns=data.tickers)
        .fillna(0.0)
        .astype("float32")
    )
