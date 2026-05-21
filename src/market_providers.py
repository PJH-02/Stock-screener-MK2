"""Market data providers for KR and US screening universes."""

from __future__ import annotations

import json
import os
import re
import time
import contextlib
import html
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests

try:
    import OpenDartReader
except Exception:  # pragma: no cover - import failure is surfaced in provider alerts
    OpenDartReader = None

try:
    import yfinance as yf
except Exception:  # pragma: no cover - import failure is surfaced in provider alerts
    yf = None

from utils import setup_logger

logger = setup_logger("market_providers")


ROOT_DIR = Path(__file__).resolve().parents[1]
SEED_FILE = Path(__file__).resolve().parent / "data" / "universe_seed.json"
CACHE_DIR = ROOT_DIR / ".cache" / "screener"


def load_env_file(path: Path = ROOT_DIR / ".env") -> None:
    """Load simple KEY=VALUE pairs from .env without overwriting real env vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()


@dataclass
class Security:
    market: str
    ticker: str
    name: str
    sector: str | None = None
    currency: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "ticker": self.ticker,
            "name": self.name,
            "sector": self.sector,
            "currency": self.currency,
        }


def parse_number(value: Any) -> float | None:
    """Parse common financial number formats into float."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text in {"-", "nan", "NaN", "None"}:
        return None
    multiplier = 1.0
    if text.startswith("(") and text.endswith(")"):
        multiplier = -1.0
        text = text[1:-1]
    text = text.replace(",", "").replace("\u2212", "-")
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def normalize_ohlcv(df: pd.DataFrame, columns: dict[str, str], currency: str) -> pd.DataFrame:
    """Return OHLCV data with date, open, high, low, close, volume, currency columns."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "currency"])

    normalized = df.rename(columns=columns).copy()
    if "date" not in normalized.columns:
        normalized = normalized.reset_index()
        first_col = normalized.columns[0]
        normalized = normalized.rename(columns={first_col: "date"})

    required = ["date", "open", "high", "low", "close", "volume"]
    missing = [col for col in required if col not in normalized.columns]
    if missing:
        raise ValueError(f"Missing OHLCV columns: {missing}")

    normalized = normalized[required].copy()
    normalized["date"] = pd.to_datetime(normalized["date"]).dt.strftime("%Y-%m-%d")
    for col in ["open", "high", "low", "close", "volume"]:
        normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
    normalized["currency"] = currency
    normalized = normalized.dropna(subset=["open", "high", "low", "close"]).sort_values("date")
    return normalized.reset_index(drop=True)


def get_yahoo_chart_ohlcv(symbol: str, days: int) -> pd.DataFrame:
    period2 = int(time.time())
    period1 = period2 - (days * 86400)
    response = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        },
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    result = (response.json().get("chart", {}).get("result") or [None])[0]
    if not result:
        return pd.DataFrame()
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators", {}).get("quote") or [{}])[0]) or {}
    if not timestamps or not quote:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(timestamps, unit="s"),
            "Open": quote.get("open"),
            "High": quote.get("high"),
            "Low": quote.get("low"),
            "Close": quote.get("close"),
            "Volume": quote.get("volume"),
        }
    )


def get_naver_kr_ohlcv(ticker: str, days: int) -> pd.DataFrame:
    response = requests.get(
        "https://fchart.stock.naver.com/sise.nhn",
        params={
            "symbol": str(ticker).zfill(6),
            "timeframe": "day",
            "count": max(days, 1),
            "requestType": "0",
        },
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    return parse_naver_kr_chart(response.content)


def parse_naver_kr_chart(content: bytes | str) -> pd.DataFrame:
    text = content.decode("euc-kr", errors="replace") if isinstance(content, bytes) else content
    root = ET.fromstring(text)
    rows: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        raw = item.attrib.get("data", "")
        parts = raw.split("|")
        if len(parts) != 6:
            continue
        rows.append(
            {
                "Date": pd.to_datetime(parts[0], format="%Y%m%d", errors="coerce"),
                "Open": parse_number(parts[1]),
                "High": parse_number(parts[2]),
                "Low": parse_number(parts[3]),
                "Close": parse_number(parts[4]),
                "Volume": parse_number(parts[5]),
            }
        )
    return pd.DataFrame(rows)


class MarketProvider:
    market = ""
    market_name = ""
    minimum_universe_size = 1

    def __init__(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.alerts: list[str] = []

    def get_universe(self, limit: int | None = None) -> list[Security]:
        raise NotImplementedError

    def get_ohlcv(self, security: Security, days: int = 500) -> pd.DataFrame:
        raise NotImplementedError

    def get_financials(self, security: Security) -> dict[str, Any]:
        raise NotImplementedError

    def _cache_file(self, name: str) -> Path:
        return CACHE_DIR / f"{self.market.lower()}_{name}.json"

    def _read_cache(self, name: str) -> list[Security]:
        path = self._cache_file(name)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return [Security(**item) for item in payload]
        except Exception as exc:
            self.alerts.append(f"{self.market}: failed to read {name} cache: {exc}")
            return []

    def _write_cache(self, name: str, securities: list[Security]) -> None:
        path = self._cache_file(name)
        payload = [security.to_dict() for security in securities]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _seed_universe(self) -> list[Security]:
        try:
            payload = json.loads(SEED_FILE.read_text(encoding="utf-8"))
            return [Security(market=self.market, **item) for item in payload.get(self.market, [])]
        except Exception as exc:
            self.alerts.append(f"{self.market}: failed to read seed universe: {exc}")
            return []

    def _finalize_universe(
        self,
        live: list[Security],
        limit: int | None,
        live_source: str,
    ) -> list[Security]:
        if len(live) >= self.minimum_universe_size:
            self._write_cache("universe", live)
            universe = live
        else:
            if live:
                self.alerts.append(
                    f"{self.market}: {live_source} returned only {len(live)} symbols; using cache or seed."
                )
            else:
                self.alerts.append(f"{self.market}: {live_source} returned no symbols; using cache or seed.")
            universe = self._read_cache("universe")
            if not universe:
                universe = self._seed_universe()
                if universe:
                    self.alerts.append(f"{self.market}: using bundled fallback universe seed.")

        deduped: dict[str, Security] = {}
        for security in universe:
            deduped[security.ticker] = security
        securities = list(deduped.values())
        if len(securities) < self.minimum_universe_size:
            self.alerts.append(
                f"{self.market}: universe size {len(securities)} is below expected minimum {self.minimum_universe_size}."
            )
        return securities[:limit] if limit else securities


class KRProvider(MarketProvider):
    market = "KR"
    market_name = "Korea KOSPI200 + KOSDAQ150"
    minimum_universe_size = 100

    index_codes = ("1028", "2203")

    def __init__(self) -> None:
        super().__init__()
        self.dart_api_key = os.environ.get("DART_API_KEY")
        self.dart = None
        if self.dart_api_key and OpenDartReader is not None:
            try:
                self.dart = OpenDartReader(self.dart_api_key)
            except Exception as exc:
                self.alerts.append(f"KR: failed to initialize OpenDART reader: {exc}")
        elif not self.dart_api_key:
            self.alerts.append("KR: DART_API_KEY is missing; C/A accounting checks will fail.")

    def get_universe(self, limit: int | None = None) -> list[Security]:
        live: list[Security] = []
        try:
            live = self._fetch_naver_kospi200_universe()
        except Exception as exc:
            self.alerts.append(f"KR: failed to fetch Naver KOSPI200 universe: {exc}")

        if live:
            seed = self._seed_universe()
            by_ticker = {security.ticker: security for security in live}
            for security in seed:
                by_ticker.setdefault(security.ticker, security)
            live = list(by_ticker.values())
            self.alerts.append("KR: KOSDAQ150 live membership source is not configured; bundled seed tickers were merged.")

        return self._finalize_universe(live, limit, "Naver KOSPI200 table")

    def get_ohlcv(self, security: Security, days: int = 500) -> pd.DataFrame:
        df = get_naver_kr_ohlcv(security.ticker, days)
        return normalize_ohlcv(
            df,
            {"Date": "date", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"},
            security.currency or "KRW",
        )

    def get_financials(self, security: Security) -> dict[str, Any]:
        financials: dict[str, Any] = {"source": "DART", "annual": [], "quarterly": [], "alerts": []}
        if self.dart is None:
            financials["alerts"].append("DART reader unavailable.")
            return financials

        current_year = datetime.now().year
        annual: list[dict[str, Any]] = []
        quarterly: list[dict[str, Any]] = []

        for year in range(current_year - 5, current_year + 1):
            df = self._fetch_dart_statement(security.ticker, year, "11011")
            if df is not None and not df.empty:
                annual.append(self._parse_dart_report(df, year, "FY"))

        report_codes = {"11013": 1, "11012": 2, "11014": 3}
        for year in range(current_year - 2, current_year + 1):
            for report_code, quarter in report_codes.items():
                df = self._fetch_dart_statement(security.ticker, year, report_code)
                if df is not None and not df.empty:
                    quarterly.append(self._parse_dart_report(df, year, f"Q{quarter}", quarterly=True))

        financials["annual"] = [record for record in annual if any(record.get(k) is not None for k in ("eps", "net_income", "equity"))]
        financials["quarterly"] = [record for record in quarterly if record.get("eps") is not None]
        if not financials["annual"]:
            financials["alerts"].append("No annual DART financial statements parsed.")
        if not financials["quarterly"]:
            financials["alerts"].append("No quarterly DART EPS records parsed.")
        return financials

    def _security_from_ticker(self, ticker: str) -> Security:
        seed = {security.ticker: security for security in self._seed_universe()}
        return seed.get(ticker, Security(market=self.market, ticker=ticker, name=ticker, currency="KRW"))

    def _fetch_naver_kospi200_universe(self) -> list[Security]:
        securities: list[Security] = []
        seen: set[str] = set()
        for page in range(1, 25):
            response = requests.get(
                "https://finance.naver.com/sise/entryJongmok.naver",
                params={"page": page},
                timeout=30,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()
            text = response.content.decode("euc-kr", errors="replace")
            matches = re.findall(r"/item/main\.naver\?code=(\d{6})[^>]*>([^<]+)", text)
            if not matches:
                break
            for ticker, raw_name in matches:
                if ticker in seen:
                    continue
                seen.add(ticker)
                securities.append(
                    Security(
                        market=self.market,
                        ticker=ticker,
                        name=html.unescape(raw_name).strip(),
                        sector=None,
                        currency="KRW",
                    )
                )
        if len(securities) < 180:
            self.alerts.append(f"KR: Naver KOSPI200 universe returned only {len(securities)} symbols.")
        return securities

    def _fetch_dart_statement(self, ticker: str, year: int, report_code: str) -> pd.DataFrame | None:
        for fs_div in ("CFS", "OFS"):
            try:
                with contextlib.redirect_stdout(StringIO()):
                    df = self.dart.finstate_all(ticker, year, reprt_code=report_code, fs_div=fs_div)
            except Exception:
                df = None
            if isinstance(df, pd.DataFrame) and not df.empty:
                return df
        return None

    def _parse_dart_report(self, df: pd.DataFrame, year: int, period: str, quarterly: bool = False) -> dict[str, Any]:
        amount_col = "thstrm_q_amount" if quarterly and "thstrm_q_amount" in df.columns else "thstrm_amount"
        return {
            "year": int(year),
            "period": period,
            "eps": self._find_dart_value(df, [r"희석.*주당", r"기본.*주당", r"주당순이익", r"EPS"], amount_col),
            "net_income": self._find_dart_value(df, [r"당기순이익"], amount_col, statement_prefix=("IS", "CIS")),
            "equity": self._find_dart_value(df, [r"자본총계"], "thstrm_amount", statement_prefix=("BS",)),
        }

    def _find_dart_value(
        self,
        df: pd.DataFrame,
        patterns: list[str],
        amount_col: str,
        statement_prefix: tuple[str, ...] | None = None,
    ) -> float | None:
        if amount_col not in df.columns or "account_nm" not in df.columns:
            return None
        candidates = df.copy()
        if statement_prefix and "sj_div" in candidates.columns:
            candidates = candidates[candidates["sj_div"].isin(statement_prefix)]
        for pattern in patterns:
            matches = candidates[candidates["account_nm"].astype(str).str.contains(pattern, regex=True, na=False)]
            for _, row in matches.iterrows():
                value = parse_number(row.get(amount_col))
                if value is not None:
                    return value
        return None


class USProvider(MarketProvider):
    market = "US"
    market_name = "US S&P 500 + Nasdaq 100"
    minimum_universe_size = 300

    sec_ticker_url = "https://www.sec.gov/files/company_tickers.json"
    sec_companyfacts_url = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

    def __init__(self) -> None:
        super().__init__()
        self.session = requests.Session()
        self.sec_user_agent = os.environ.get("SEC_USER_AGENT") or "Stock-screener-MK2 contact@example.com"
        if not os.environ.get("SEC_USER_AGENT"):
            self.alerts.append("US: SEC_USER_AGENT is not set; using a generic User-Agent.")
        self.session.headers.update({"User-Agent": self.sec_user_agent, "Accept-Encoding": "gzip, deflate"})
        self._ticker_cik: dict[str, int] | None = None

    def get_universe(self, limit: int | None = None) -> list[Security]:
        live: list[Security] = []
        try:
            live = self._fetch_wikipedia_universe()
        except Exception as exc:
            self.alerts.append(f"US: failed to fetch live S&P500/Nasdaq100 universe: {exc}")
        return self._finalize_universe(live, limit, "Wikipedia S&P500/Nasdaq100 tables")

    def get_ohlcv(self, security: Security, days: int = 500) -> pd.DataFrame:
        symbol = security.ticker.replace(".", "-")
        df = get_yahoo_chart_ohlcv(symbol, days)
        if (df is None or df.empty) and yf is not None:
            df = yf.download(symbol, period="2y", progress=False, auto_adjust=False, threads=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
        return normalize_ohlcv(
            df,
            {"Date": "date", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"},
            security.currency or "USD",
        )

    def get_financials(self, security: Security) -> dict[str, Any]:
        financials: dict[str, Any] = {"source": "SEC companyfacts", "annual": [], "quarterly": [], "alerts": []}
        cik = self._get_cik(security.ticker)
        if cik is None:
            financials["alerts"].append("No SEC CIK mapping found.")
            return financials
        try:
            time.sleep(0.12)
            response = self.session.get(self.sec_companyfacts_url.format(cik=cik), timeout=30)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            financials["alerts"].append(f"Failed to fetch SEC companyfacts: {exc}")
            return financials

        financials["annual"] = self._parse_sec_annual(payload)
        financials["quarterly"] = self._parse_sec_quarterly(payload)
        if not financials["annual"]:
            financials["alerts"].append("No annual SEC financial facts parsed.")
        if not financials["quarterly"]:
            financials["alerts"].append("No quarterly SEC EPS facts parsed.")
        return financials

    def _fetch_wikipedia_universe(self) -> list[Security]:
        sp500 = self._read_wiki_table(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            symbol_candidates=("Symbol",),
            name_candidates=("Security",),
            sector_candidates=("GICS Sector",),
        )
        nasdaq100 = self._read_wiki_table(
            "https://en.wikipedia.org/wiki/Nasdaq-100",
            symbol_candidates=("Ticker", "Symbol"),
            name_candidates=("Company", "Security"),
            sector_candidates=("GICS Sector", "Sector"),
        )
        by_ticker: dict[str, Security] = {}
        for security in sp500 + nasdaq100:
            by_ticker[security.ticker] = security
        return list(by_ticker.values())

    def _read_wiki_table(
        self,
        url: str,
        symbol_candidates: tuple[str, ...],
        name_candidates: tuple[str, ...],
        sector_candidates: tuple[str, ...],
    ) -> list[Security]:
        response = requests.get(url, timeout=30, headers={"User-Agent": "Stock-screener-MK2/1.0"})
        response.raise_for_status()
        tables = pd.read_html(StringIO(response.text))
        for table in tables:
            symbol_col = next((col for col in symbol_candidates if col in table.columns), None)
            name_col = next((col for col in name_candidates if col in table.columns), None)
            if not symbol_col or not name_col:
                continue
            sector_col = next((col for col in sector_candidates if col in table.columns), None)
            securities: list[Security] = []
            for _, row in table.iterrows():
                ticker = str(row[symbol_col]).strip().replace("\n", "")
                if not ticker or ticker.lower() == "nan":
                    continue
                securities.append(
                    Security(
                        market=self.market,
                        ticker=ticker,
                        name=str(row[name_col]).strip(),
                        sector=str(row[sector_col]).strip() if sector_col else None,
                        currency="USD",
                    )
                )
            if securities:
                return securities
        return []

    def _get_ticker_cik_map(self) -> dict[str, int]:
        if self._ticker_cik is not None:
            return self._ticker_cik
        cache_path = self._cache_file("sec_ticker_cik")
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                self._ticker_cik = {ticker: int(cik) for ticker, cik in cached.items()}
                return self._ticker_cik
            except Exception:
                pass
        response = self.session.get(self.sec_ticker_url, timeout=30)
        response.raise_for_status()
        payload = response.json()
        mapping = {item["ticker"].upper(): int(item["cik_str"]) for item in payload.values()}
        cache_path.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
        self._ticker_cik = mapping
        return mapping

    def _get_cik(self, ticker: str) -> int | None:
        mapping = self._get_ticker_cik_map()
        return mapping.get(ticker.upper().replace(".", "-")) or mapping.get(ticker.upper())

    def _parse_sec_annual(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        eps = self._sec_values(payload, ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"], "USD/shares")
        net_income = self._sec_values(payload, ["NetIncomeLoss", "ProfitLoss"], "USD")
        equity = self._sec_values(
            payload,
            ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
            "USD",
        )
        by_year: dict[int, dict[str, Any]] = {}
        for key, target, only_fy in (("eps", eps, True), ("net_income", net_income, True), ("equity", equity, True)):
            for fact in target:
                if only_fy and fact.get("fp") != "FY":
                    continue
                year = fact.get("fy")
                if not isinstance(year, int):
                    continue
                record = by_year.setdefault(year, {"year": year, "period": "FY"})
                record[key] = parse_number(fact.get("val"))
        records = [record for _, record in sorted(by_year.items())]
        records = self._with_roe(records)
        return records

    def _parse_sec_quarterly(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        eps_facts = self._sec_values(
            payload,
            ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"],
            "USD/shares",
        )
        by_period: dict[tuple[int, int], dict[str, Any]] = {}
        for fact in eps_facts:
            frame = str(fact.get("frame") or "")
            match = re.fullmatch(r"CY(\d{4})Q([1-4])", frame)
            if match:
                year = int(match.group(1))
                quarter = int(match.group(2))
            elif fact.get("fp") in {"Q1", "Q2", "Q3"} and isinstance(fact.get("fy"), int):
                year = int(fact["fy"])
                quarter = int(str(fact["fp"])[1])
            else:
                continue
            value = parse_number(fact.get("val"))
            if value is None:
                continue
            key = (year, quarter)
            filed = str(fact.get("filed") or "")
            existing = by_period.get(key)
            if existing is None or filed >= str(existing.get("filed") or ""):
                by_period[key] = {"year": year, "period": f"Q{quarter}", "quarter": quarter, "eps": value, "filed": filed}
        return [{k: v for k, v in record.items() if k != "filed"} for _, record in sorted(by_period.items())]

    def _sec_values(self, payload: dict[str, Any], concepts: list[str], unit: str) -> list[dict[str, Any]]:
        facts = payload.get("facts", {}).get("us-gaap", {})
        values: list[dict[str, Any]] = []
        for concept in concepts:
            units = facts.get(concept, {}).get("units", {})
            if unit in units:
                values.extend(units[unit])
        return values

    def _with_roe(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        records = sorted(records, key=lambda item: item["year"])
        previous_equity: float | None = None
        for record in records:
            equity = record.get("equity")
            net_income = record.get("net_income")
            if equity is not None and previous_equity is not None and net_income is not None:
                average_equity = (equity + previous_equity) / 2
                if average_equity:
                    record["roe"] = (net_income / average_equity) * 100
            if equity is not None:
                previous_equity = equity
        return records


def provider_for_market(market: str) -> MarketProvider:
    normalized = market.upper()
    if normalized == "KR":
        return KRProvider()
    if normalized == "US":
        return USProvider()
    raise ValueError(f"Unsupported market: {market}")
