"""Korean DART disclosure and financial-statement collection."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

import requests

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from market_providers import KRProvider  # noqa: E402
from trading.config import DATA_CACHE_DIR, DEFAULT_CONFIG  # noqa: E402
from trading.kiwoom_client import TradingSecurity, load_env_file  # noqa: E402


load_env_file(ROOT_DIR / ".env")


class DARTDisclosureClient:
    """Fetch whole-market Korean disclosures from OpenDART list API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        cache_dir: Path = DATA_CACHE_DIR / "dart",
        max_pages: int | None = None,
        allow_partial: bool | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv(DEFAULT_CONFIG.dart_api_key_env)
        self.cache_dir = cache_dir
        self.max_pages = max_pages if max_pages is not None else _optional_positive_int(os.getenv("TRADING_DART_DISCLOSURE_MAX_PAGES"))
        self.allow_partial = _env_flag("TRADING_DART_ALLOW_PARTIAL_DISCLOSURES") if allow_partial is None else allow_partial
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_disclosures(self, *, start_date: str, end_date: str, as_of: str) -> list[dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("DART_API_KEY is required for DART disclosure scoring.")
        cache_suffix = f"p{self.max_pages}" if self.max_pages is not None else "all"
        cache_path = self.cache_dir / f"dart_disclosures_{start_date}_{end_date}_{cache_suffix}.json"
        if cache_path.exists() and (self.max_pages is None or self.allow_partial):
            return json.loads(cache_path.read_text(encoding="utf-8"))

        as_of_date = date.fromisoformat(as_of)
        rows: list[dict[str, Any]] = []
        page_no = 1
        total_pages = 1
        while page_no <= total_pages:
            response = requests.get(
                "https://opendart.fss.or.kr/api/list.json",
                params={
                    "crtfc_key": self.api_key,
                    "bgn_de": start_date.replace("-", ""),
                    "end_de": end_date.replace("-", ""),
                    "last_reprt_at": "N",
                    "page_no": page_no,
                    "page_count": 100,
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            status = str(payload.get("status") or "000")
            if status == "013":
                break
            if status != "000":
                raise RuntimeError(f"DART list API error {status}: {payload.get('message')}")
            total_pages = max(int(payload.get("total_page") or 1), 1)
            if self.max_pages is not None and total_pages > self.max_pages and not self.allow_partial:
                raise RuntimeError(
                    "DART disclosure fetch would be partial: "
                    f"{start_date}~{end_date} has {total_pages} pages, "
                    f"but TRADING_DART_DISCLOSURE_MAX_PAGES={self.max_pages}. "
                    "Unset the cap to fetch all pages, or set "
                    "TRADING_DART_ALLOW_PARTIAL_DISCLOSURES=1 for an intentional smoke run."
                )

            for item in payload.get("list", []):
                stock_code = str(item.get("stock_code") or "").zfill(6)
                if not stock_code.strip("0"):
                    continue
                filed = str(item.get("rcept_dt") or "")
                accepted = f"{filed[:4]}-{filed[4:6]}-{filed[6:8]}" if len(filed) == 8 else end_date
                elapsed = max((as_of_date - date.fromisoformat(accepted)).days, 0)
                rows.append(
                    {
                        "stock_code": stock_code,
                        "event_code": None,
                        "title": str(item.get("report_nm") or ""),
                        "trading_days_elapsed": elapsed,
                        "accepted_at": accepted,
                        "rcept_no": item.get("rcept_no"),
                    }
                )
            page_no += 1
            if self.max_pages is not None and page_no > self.max_pages:
                break
            time.sleep(0.2)

        cache_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return rows

    def fetch_disclosures_range(self, *, start_date: str, end_date: str, as_of: str, chunk_days: int = 80) -> list[dict[str, Any]]:
        """Fetch a range by chunking around OpenDART's whole-market period limit."""
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        rows: list[dict[str, Any]] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=max(chunk_days, 1) - 1), end)
            rows.extend(
                self.fetch_disclosures(
                    start_date=cursor.isoformat(),
                    end_date=chunk_end.isoformat(),
                    as_of=as_of,
                )
            )
            cursor = chunk_end + timedelta(days=1)

        deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in rows:
            key = (
                str(row.get("rcept_no") or ""),
                str(row.get("stock_code") or ""),
                str(row.get("accepted_at") or ""),
                str(row.get("title") or ""),
            )
            deduped[key] = row
        return list(deduped.values())


class KRFinancialCollector:
    """Collect normalized DART financial statements for a Korean universe."""

    def __init__(self, provider: KRProvider | None = None) -> None:
        self.provider = provider or KRProvider()

    def load_financials(self, securities: list[TradingSecurity]) -> dict[str, dict[str, Any]]:
        financials: dict[str, dict[str, Any]] = {}
        for security in securities:
            common_ticker = security.common_ticker or security.ticker
            if common_ticker in financials:
                continue
            financials[common_ticker] = self.provider.get_financials(type("SecurityLike", (), {"ticker": common_ticker})())
        return financials


def filter_disclosures_as_of(
    disclosures: list[Mapping[str, Any]],
    as_of: str,
    *,
    lookback_days: int | None = 80,
) -> list[dict[str, Any]]:
    """Return only disclosures accepted by as_of and recalculate their age."""
    as_of_date = date.fromisoformat(as_of)
    earliest = None if lookback_days is None else as_of_date - timedelta(days=lookback_days)
    rows: list[dict[str, Any]] = []
    for disclosure in disclosures:
        accepted_raw = str(disclosure.get("accepted_at") or disclosure.get("rcept_dt") or "")
        accepted = _parse_disclosure_date(accepted_raw)
        if accepted is None or accepted > as_of_date:
            continue
        if earliest is not None and accepted < earliest:
            continue
        rows.append(
            {
                **dict(disclosure),
                "accepted_at": accepted.isoformat(),
                "trading_days_elapsed": max((as_of_date - accepted).days, 0),
            }
        )
    return rows


def filter_financials_as_of(financials: Mapping[str, Any], as_of: str) -> dict[str, Any]:
    """Filter normalized DART financial records to those public by as_of."""
    as_of_date = date.fromisoformat(as_of)
    annual = [
        dict(record)
        for record in financials.get("annual", [])
        if _financial_available_date(record) <= as_of_date
    ]
    quarterly = [
        dict(record)
        for record in financials.get("quarterly", [])
        if _financial_available_date(record) <= as_of_date
    ]
    return {
        **dict(financials),
        "annual": annual,
        "quarterly": quarterly,
    }


def filter_financials_map_as_of(financials_by_ticker: Mapping[str, Mapping[str, Any]], as_of: str) -> dict[str, dict[str, Any]]:
    return {
        str(ticker): filter_financials_as_of(financials, as_of)
        for ticker, financials in financials_by_ticker.items()
    }


def _parse_disclosure_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:8]}")
    return date.fromisoformat(text[:10])


def _financial_available_date(record: Mapping[str, Any]) -> date:
    for key in ("accepted_at", "filing_date", "rcept_dt", "available_at", "published_at"):
        value = record.get(key)
        if value:
            parsed = _parse_disclosure_date(str(value))
            if parsed is not None:
                return parsed

    year = int(record.get("year"))
    period = str(record.get("period") or "").upper()
    if period in {"FY", "Y", "ANNUAL"}:
        return date(year + 1, 4, 1)
    if period == "Q1":
        return date(year, 5, 16)
    if period == "Q2":
        return date(year, 8, 16)
    if period == "Q3":
        return date(year, 11, 16)
    if period == "Q4":
        return date(year + 1, 4, 1)
    return date(year + 1, 4, 1)


def _optional_positive_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    parsed = int(value)
    if parsed <= 0:
        return None
    return parsed


def _env_flag(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "y", "on"}
