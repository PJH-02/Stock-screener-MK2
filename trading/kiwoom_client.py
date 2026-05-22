"""Kiwoom REST data access and normalizers.

The client deliberately has no non-Kiwoom market-data fallback. Live calls fail
fast when credentials or required responses are unavailable.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import requests

from trading.config import CACHE_DIR, DEFAULT_CONFIG


KIWOOM_PROD_URL = "https://api.kiwoom.com"
KIWOOM_MOCK_URL = "https://mockapi.kiwoom.com"
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
EXCLUDED_SECURITY_KEYWORDS = (
    "ETF",
    "ETN",
    "ELW",
    "SPAC",
    "스팩",
    "REIT",
    "리츠",
    "정리매매",
    "거래정지",
)


@dataclass(frozen=True)
class TradingSecurity:
    ticker: str
    name: str
    market: str
    security_type: str = ""
    common_ticker: str | None = None
    sector: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_env_file(path: Path) -> None:
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


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"nan", "none", "-"}:
        return None
    try:
        return abs(float(text))
    except ValueError:
        return None


def parse_signed_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"nan", "none", "-"}:
        return None
    multiplier = 1.0
    if text.startswith("(") and text.endswith(")"):
        multiplier = -1.0
        text = text[1:-1]
    text = text.replace("+", "")
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def first_value(payload: Mapping[str, Any], *keys: str) -> Any:
    lowered = {str(key).lower(): value for key, value in payload.items()}
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def flatten_record_lists(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        records: list[Mapping[str, Any]] = []
        for item in payload:
            if isinstance(item, Mapping):
                nested = flatten_record_lists(item)
                records.extend(nested or [item])
        return records
    if not isinstance(payload, Mapping):
        return []
    records: list[Mapping[str, Any]] = []
    for key, value in payload.items():
        if isinstance(value, list) and value and all(isinstance(item, Mapping) for item in value):
            records.extend(flatten_record_lists(value))
        elif isinstance(value, Mapping):
            records.extend(flatten_record_lists(value))
    return records


def normalize_market(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text == "0":
        return "KOSPI"
    if text == "10":
        return "KOSDAQ"
    if "KOSDAQ" in text or "코스닥" in text:
        return "KOSDAQ"
    if "KOSPI" in text or "코스피" in text or "거래소" in text:
        return "KOSPI"
    return text


def is_preferred_share_name(name: str) -> bool:
    return bool(re.search(r"(\d*우B?|\(전환\))$", name.strip()))


def common_name_key(name: str) -> str:
    text = re.sub(r"\(전환\)$", "", name.strip())
    text = re.sub(r"\d*우B?$", "", text)
    return re.sub(r"\s+", "", text)


def is_tradable_common_or_preferred(record: Mapping[str, Any]) -> bool:
    name = str(first_value(record, "stock_name", "stk_nm", "name", "isu_nm", "종목명") or "")
    security_type = str(first_value(record, "security_type", "stock_type", "sec_type", "kind", "종목구분") or "")
    listing_status = str(first_value(record, "listing_status", "status", "auditInfo", "상장상태") or "LISTED").upper()
    state = str(first_value(record, "state", "거래상태") or "").upper()
    combined = f"{name} {security_type}".upper()
    if any(keyword.upper() in combined for keyword in EXCLUDED_SECURITY_KEYWORDS):
        return False
    if "거래정지" in listing_status or "거래정지" in state:
        return False
    if listing_status and listing_status not in {"LISTED", "상장", "NORMAL", "정상"}:
        return False
    return bool(name)


def assign_common_tickers(securities: list[TradingSecurity]) -> list[TradingSecurity]:
    common_by_name = {
        common_name_key(security.name): security.ticker
        for security in securities
        if not is_preferred_share_name(security.name)
    }
    assigned: list[TradingSecurity] = []
    for security in securities:
        common_ticker = security.common_ticker or security.ticker
        if is_preferred_share_name(security.name):
            common_ticker = common_by_name.get(common_name_key(security.name), common_ticker)
        assigned.append(
            TradingSecurity(
                ticker=security.ticker,
                name=security.name,
                market=security.market,
                security_type=security.security_type,
                common_ticker=common_ticker,
                sector=security.sector,
            )
        )
    return assigned


def normalize_universe_response(payload: Mapping[str, Any] | list[Any]) -> list[TradingSecurity]:
    securities: dict[str, TradingSecurity] = {}
    for item in flatten_record_lists(payload):
        code = str(first_value(item, "ticker", "stock_code", "stk_cd", "code", "short_code", "isu_cd", "종목코드") or "").strip()
        code = re.sub(r"[^0-9A-Za-z]", "", code).zfill(6)
        if not code or len(code) != 6:
            continue
        name = str(first_value(item, "name", "stock_name", "stk_nm", "isu_nm", "종목명") or "").strip()
        market = normalize_market(first_value(item, "marketCode", "market", "mkt_nm", "marketName", "market_name", "mrkt_tp", "시장구분"))
        if market not in {"KOSPI", "KOSDAQ"}:
            continue
        if not is_tradable_common_or_preferred(item):
            continue
        security_type = str(first_value(item, "security_type", "stock_type", "sec_type", "kind", "종목구분") or "").strip()
        common_ticker = first_value(item, "common_ticker", "common_stock_code", "representative_code")
        sector = first_value(item, "sector", "industry", "industry_name", "upName", "업종")
        securities[code] = TradingSecurity(
            ticker=code,
            name=name,
            market=market,
            security_type=security_type,
            common_ticker=str(common_ticker).zfill(6) if common_ticker else None,
            sector=str(sector).strip() if sector else None,
        )
    return assign_common_tickers(list(securities.values()))


def normalize_daily_chart_response(payload: Mapping[str, Any] | list[Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in flatten_record_lists(payload):
        date = first_value(item, "date", "dt", "trading_date", "stck_bsop_date", "일자")
        open_ = parse_number(first_value(item, "open", "open_pric", "open_price", "시가"))
        high = parse_number(first_value(item, "high", "high_pric", "high_price", "고가"))
        low = parse_number(first_value(item, "low", "low_pric", "low_price", "저가"))
        close = parse_number(first_value(item, "close", "cur_prc", "close_pric", "close_price", "현재가", "종가"))
        volume = parse_number(first_value(item, "volume", "trde_qty", "trade_qty", "거래량"))
        if date is None or open_ is None or high is None or low is None or close is None:
            continue
        rows.append(
            {
                "date": pd.to_datetime(str(date), format="%Y%m%d", errors="coerce"),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 0 if volume is None or math.isnan(volume) else volume,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return df


def normalize_institutional_flow_response(payload: Mapping[str, Any] | list[Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in flatten_record_lists(payload):
        date = first_value(item, "date", "dt", "trading_date", "bizdate", "stck_bsop_date")
        institutional = parse_signed_number(
            first_value(
                item,
                "institutional_net_buy",
                "institution_net_buy",
                "organ_pure_buy_quantity",
                "organPureBuyQuant",
                "orgn",
                "orgn_netbuy",
                "institution",
                "inst",
            )
        )
        foreigner = parse_signed_number(
            first_value(
                item,
                "foreigner_net_buy",
                "foreigner_pure_buy_quantity",
                "foreignerPureBuyQuant",
                "frgnr_invsr",
                "foreign",
            )
        )
        individual = parse_signed_number(
            first_value(
                item,
                "individual_net_buy",
                "individual_pure_buy_quantity",
                "individualPureBuyQuant",
                "ind_invsr",
                "individual",
            )
        )
        close = parse_number(first_value(item, "close", "closePrice", "cur_prc", "close_price"))
        volume = parse_number(first_value(item, "volume", "accumulatedTradingVolume", "acc_trde_qty", "trde_qty"))
        if date is None or institutional is None:
            continue
        rows.append(
            {
                "date": pd.to_datetime(str(date), format="%Y%m%d", errors="coerce"),
                "institutional_net_buy": institutional,
                "foreigner_net_buy": foreigner,
                "individual_net_buy": individual,
                "close": close,
                "volume": volume,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(
            columns=["date", "institutional_net_buy", "foreigner_net_buy", "individual_net_buy", "close", "volume"]
        )
    df = df.dropna(subset=["date"]).sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return df


class KiwoomRESTClient:
    def __init__(
        self,
        *,
        app_key: str | None = None,
        secret_key: str | None = None,
        env: str | None = None,
        timeout: float = 30.0,
        cache_dir: Path = CACHE_DIR,
    ) -> None:
        load_env_file(Path(__file__).resolve().parents[1] / ".env")
        self.app_key = app_key or os.getenv(DEFAULT_CONFIG.kiwoom_app_key_env)
        self.secret_key = (
            secret_key
            or os.getenv(DEFAULT_CONFIG.kiwoom_secret_key_env)
            or os.getenv("KIWOOM_APP_SECRET")
            or os.getenv("KIWOON_APP_SECRET")
        )
        env_from_config = os.getenv(DEFAULT_CONFIG.kiwoom_env_var)
        self.env = (env or env_from_config or "prod").lower()
        self._env_explicit = bool(env or env_from_config)
        self.timeout = timeout
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()

    @property
    def base_url(self) -> str:
        return KIWOOM_MOCK_URL if self.env == "mock" else KIWOOM_PROD_URL

    def token_cache_path(self) -> Path:
        return self.cache_dir / f"kiwoom_token_{self.env}.json"

    def get_access_token(self) -> str:
        cached = self._read_cached_token()
        if cached:
            return cached
        if not self.app_key or not self.secret_key:
            raise RuntimeError("KIWOOM_APP_KEY and KIWOOM_SECRET_KEY are required for Kiwoom REST calls.")
        payload = self._request_token_payload()
        if int(payload.get("return_code") or 0) != 0:
            message = str(payload.get("return_msg") or "")
            if not self._env_explicit and self.env == "prod" and "투자구분" in message:
                self.env = "mock"
                cached = self._read_cached_token()
                if cached:
                    return cached
                payload = self._request_token_payload()
            if int(payload.get("return_code") or 0) != 0:
                raise RuntimeError(f"Kiwoom token request failed: {payload.get('return_msg')}")
        token = str(payload.get("token") or "")
        if not token:
            raise RuntimeError(f"Kiwoom token response did not include token: {payload}")
        self.token_cache_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return token

    def _request_token_payload(self) -> Mapping[str, Any]:
        response = self.session.post(
            f"{self.base_url}/oauth2/token",
            json={"grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self.secret_key},
            headers={"Content-Type": "application/json;charset=UTF-8"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def _read_cached_token(self) -> str | None:
        path = self.token_cache_path()
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            token = str(payload.get("token") or "")
            expires = str(payload.get("expires_dt") or "")
            if not token or not expires:
                return None
            expires_at = datetime.strptime(expires[:14], "%Y%m%d%H%M%S")
            if (expires_at - datetime.now()).total_seconds() < 300:
                return None
            return token
        except Exception:
            return None

    def post_api(self, api_id: str, path: str, body: Mapping[str, Any], *, max_pages: int = 1) -> list[Mapping[str, Any]]:
        token = self.get_access_token()
        next_key = ""
        pages: list[Mapping[str, Any]] = []
        for _ in range(max_pages):
            response = self._post_with_retry(
                path,
                body,
                headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {token}",
                    "api-id": api_id,
                    "cont-yn": "Y" if next_key else "N",
                    "next-key": next_key,
                },
            )
            payload = response.json()
            pages.append(payload)
            next_key = response.headers.get("next-key", "") or str(payload.get("next-key") or "")
            cont_yn = response.headers.get("cont-yn", "") or str(payload.get("cont-yn") or "")
            if cont_yn.upper() != "Y" or not next_key:
                break
        return pages

    def _post_with_retry(self, path: str, body: Mapping[str, Any], headers: Mapping[str, str]) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.post(
                    f"{self.base_url}{path}",
                    json=dict(body),
                    headers=dict(headers),
                    timeout=self.timeout,
                )
                if response.status_code in RETRY_STATUS_CODES and attempt < 2:
                    time.sleep(2**attempt)
                    continue
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
        raise RuntimeError(f"Kiwoom request failed: {last_error}")

    def load_universe(self) -> list[TradingSecurity]:
        api_id = os.getenv("KIWOOM_UNIVERSE_API_ID", "ka10099")
        path = os.getenv("KIWOOM_UNIVERSE_PATH", "/api/dostk/stkinfo")
        pages: list[Mapping[str, Any]] = []
        for market_code in ("0", "10"):
            pages.extend(self.post_api(api_id, path, {"mrkt_tp": market_code}, max_pages=50))
        securities = normalize_universe_response(pages)
        if not securities:
            raise RuntimeError("Kiwoom universe response contained no tradable KOSPI/KOSDAQ securities.")
        return securities

    def load_daily_ohlcv(self, ticker: str, *, end_date: str, bars: int) -> pd.DataFrame:
        api_id = os.getenv("KIWOOM_DAILY_CHART_API_ID", "ka10081")
        path = os.getenv("KIWOOM_DAILY_CHART_PATH", "/api/dostk/chart")
        pages = self.post_api(
            api_id,
            path,
            {"stk_cd": ticker, "base_dt": end_date.replace("-", ""), "upd_stkpc_tp": "1"},
            max_pages=20,
        )
        df = normalize_daily_chart_response(pages)
        if df.empty:
            raise RuntimeError(f"Kiwoom daily chart response contained no OHLCV rows for {ticker}.")
        return df.tail(bars).reset_index(drop=True)

    def load_institutional_flow(self, ticker: str, *, end_date: str, bars: int) -> pd.DataFrame:
        api_id = os.getenv("KIWOOM_INSTITUTION_FLOW_API_ID", "ka10059")
        path = os.getenv("KIWOOM_INSTITUTION_FLOW_PATH", "/api/dostk/chart")
        body = {
            "dt": end_date.replace("-", ""),
            "base_dt": end_date.replace("-", ""),
            "stk_cd": ticker,
            "amt_qty_tp": os.getenv("KIWOOM_INSTITUTION_FLOW_AMOUNT_QTY_TYPE", "2"),
            "trde_tp": os.getenv("KIWOOM_INSTITUTION_FLOW_TRADE_TYPE", "0"),
            "unit_tp": os.getenv("KIWOOM_INSTITUTION_FLOW_UNIT_TYPE", "1"),
        }
        body.update(_json_env_mapping("KIWOOM_INSTITUTION_FLOW_EXTRA_PARAMS"))
        pages = self.post_api(api_id, path, body, max_pages=int(os.getenv("KIWOOM_INSTITUTION_FLOW_MAX_PAGES", "80")))
        df = normalize_institutional_flow_response(pages)
        if df.empty:
            raise RuntimeError(f"Kiwoom institutional flow response contained no rows for {ticker}.")
        return df.tail(bars).reset_index(drop=True)

    def load_index_ohlcv(self, market: str, *, end_date: str, bars: int) -> pd.DataFrame:
        api_id = os.getenv("KIWOOM_INDEX_DAILY_API_ID", "ka20006")
        path = os.getenv("KIWOOM_INDEX_DAILY_PATH", "/api/dostk/chart")
        normalized_market = normalize_market(market)
        index_code = "001" if normalized_market == "KOSPI" else "101"
        body = {
            "mrkt_tp": "0" if normalized_market == "KOSPI" else "1",
            "inds_cd": index_code,
            "stk_cd": index_code,
            "base_dt": end_date.replace("-", ""),
            "dt": end_date.replace("-", ""),
        }
        body.update(_json_env_mapping("KIWOOM_INDEX_DAILY_EXTRA_PARAMS"))
        pages = self.post_api(api_id, path, body, max_pages=int(os.getenv("KIWOOM_INDEX_DAILY_MAX_PAGES", "20")))
        df = normalize_daily_chart_response(pages)
        if df.empty:
            raise RuntimeError(f"Kiwoom index daily chart response contained no OHLCV rows for {normalized_market}.")
        return df.tail(bars).reset_index(drop=True)


def _json_env_mapping(name: str) -> dict[str, Any]:
    raw = os.getenv(name)
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a JSON object.")
    return dict(payload)
