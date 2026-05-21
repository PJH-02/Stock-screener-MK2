"""Macro + DART disclosure scoring for Korean equities."""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as datetime_time
from pathlib import Path
from typing import Any, Mapping

from trading.config import DEFAULT_CONFIG, ROOT_DIR
from trading.kiwoom_client import load_env_file
from trading.kiwoom_client import TradingSecurity


load_env_file(Path(__file__).resolve().parents[1] / ".env")


DEFAULT_MACRO_STATES = {"G": 0, "IC": 0, "FC": 0, "ED": 0, "FX": 0}
DEFAULT_SECTOR_EXPOSURES: dict[str, dict[str, float]] = {
    "반도체": {"G": 12, "IC": 2, "FC": 6, "ED": 12, "FX": 12},
    "반도체장비": {"G": 11, "IC": -5, "FC": 10, "ED": 11, "FX": 7},
    "기계": {"G": 10, "IC": 3, "FC": -2, "ED": 9, "FX": 9},
    "자동차/부품": {"G": 9, "IC": 4, "FC": -3, "ED": 8, "FX": 10},
    "조선": {"G": 8, "IC": 9, "FC": -1, "ED": 7, "FX": 11},
    "철강": {"G": 7, "IC": 10, "FC": -9, "ED": 4, "FX": 6},
    "화학": {"G": 6, "IC": -6, "FC": -6, "ED": 5, "FX": -6},
    "IT하드웨어/디스플레이/전자부품": {"G": 5, "IC": 1, "FC": 5, "ED": 10, "FX": 3},
    "건설/건자재": {"G": 4, "IC": 5, "FC": 8, "ED": 0, "FX": 0},
    "해운/물류": {"G": 3, "IC": 7, "FC": -5, "ED": 3, "FX": 4},
    "증권": {"G": 2, "IC": 0, "FC": 7, "ED": -1, "FX": -4},
    "은행": {"G": 1, "IC": 6, "FC": -12, "ED": 0, "FX": 1},
    "비철/산업금속": {"G": 0, "IC": 11, "FC": -8, "ED": 2, "FX": 5},
    "항공": {"G": 0, "IC": -12, "FC": 1, "ED": -4, "FX": -12},
    "내구소비재/의류/화장품": {"G": -1, "IC": -7, "FC": 4, "ED": -6, "FX": -8},
    "여행/레저": {"G": -2, "IC": -11, "FC": 2, "ED": -7, "FX": -11},
    "에너지(정유/가스)": {"G": -3, "IC": 12, "FC": -10, "ED": 1, "FX": 2},
    "방산": {"G": -4, "IC": 8, "FC": -4, "ED": 6, "FX": 8},
    "소프트웨어/인터넷/게임": {"G": -5, "IC": -2, "FC": 12, "ED": -2, "FX": -1},
    "보험": {"G": -6, "IC": -4, "FC": -11, "ED": -5, "FX": -2},
    "유통/소매": {"G": -7, "IC": -8, "FC": 3, "ED": -8, "FX": -10},
    "헬스케어/바이오": {"G": -8, "IC": -1, "FC": 11, "ED": -3, "FX": 0},
    "리츠/부동산": {"G": -9, "IC": 0, "FC": 9, "ED": -12, "FX": -7},
    "필수소비재(음식료)": {"G": -10, "IC": -9, "FC": -7, "ED": -9, "FX": -5},
    "통신": {"G": -11, "IC": -3, "FC": 0, "ED": -10, "FX": -3},
    "유틸리티": {"G": -12, "IC": -10, "FC": 0, "ED": -11, "FX": -9},
}

EVENT_CODE_MAP = {
    "B01": "supply_contract",
    "B02": "treasury_stock",
    "B03": "facility_investment",
    "N01": "dilutive_financing",
    "N02": "correction_cancellation_withdrawal",
    "N03": "governance_risk",
}

TITLE_PATTERNS = (
    ("supply_contract", ("공급계약", "판매계약")),
    ("treasury_stock", ("자기주식", "자사주")),
    ("facility_investment", ("시설투자", "신규시설", "생산설비")),
    ("dilutive_financing", ("유상증자", "전환사채", "교환사채", "신주인수권부사채")),
    ("correction_cancellation_withdrawal", ("정정", "취소", "철회")),
    ("governance_risk", ("횡령", "배임", "불성실공시")),
)

IGNORED_TITLE_PATTERNS = ("사업보고서", "반기보고서", "분기보고서", "감사보고서", "첨부추가")
BLOCK_WEIGHTS = {
    "supply_contract": 1.0,
    "treasury_stock": 0.8,
    "facility_investment": 0.6,
    "dilutive_financing": -1.0,
    "correction_cancellation_withdrawal": -0.7,
    "governance_risk": -0.9,
    "neutral": 0.0,
}
BLOCK_HALF_LIFE_DAYS = {
    "supply_contract": 20,
    "treasury_stock": 10,
    "facility_investment": 60,
    "dilutive_financing": 60,
    "correction_cancellation_withdrawal": 10,
    "governance_risk": 120,
}


@dataclass(frozen=True)
class MacroDartScore:
    ticker: str
    common_ticker: str
    name: str
    macro_score: float
    dart_disclosure_score: float
    combined_macro_dart_score: float
    macro_rank: int
    risk_flags: list[str]
    raw_dart_score: float = 0.0
    industry_code: str | None = None
    macro_source: str = "fallback_env"
    macro_snapshot_run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_disclosure(event_code: str | None, title: str) -> str:
    if event_code and event_code in EVENT_CODE_MAP:
        return EVENT_CODE_MAP[event_code]
    normalized = title.strip().lower()
    if any(pattern.lower() in normalized for pattern in IGNORED_TITLE_PATTERNS):
        return "ignored"
    for block_name, patterns in TITLE_PATTERNS:
        if any(pattern.lower() in normalized for pattern in patterns):
            return block_name
    return "neutral"


def decayed_disclosure_score(block_name: str, trading_days_elapsed: int) -> float:
    weight = BLOCK_WEIGHTS.get(block_name, 0.0)
    if weight == 0:
        return 0.0
    half_life = BLOCK_HALF_LIFE_DAYS.get(block_name, 30)
    return weight * math.exp(-math.log(2) * max(trading_days_elapsed, 0) / half_life)


def load_macro_states_from_env() -> dict[str, int]:
    raw = os.getenv("TRADING_MACRO_STATES")
    if not raw:
        return dict(DEFAULT_MACRO_STATES)
    payload = json.loads(raw)
    return {channel: int(payload.get(channel, 0)) for channel in DEFAULT_MACRO_STATES}


def sector_macro_score(sector: str | None, macro_states: Mapping[str, int]) -> float:
    if not sector:
        return 0.0
    exposure = DEFAULT_SECTOR_EXPOSURES.get(str(sector), {})
    return round(sum(float(exposure.get(channel, 0.0)) * int(state) for channel, state in macro_states.items()), 6)


def _zscore(values: list[float]) -> list[float]:
    if not values:
        return []
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    std_dev = math.sqrt(variance)
    if std_dev == 0:
        return [0.0 for _ in values]
    return [round((value - mean) / std_dev, 6) for value in values]


def resolve_macro_root() -> Path | None:
    raw = os.getenv(DEFAULT_CONFIG.macro_root_env)
    candidates = []
    if raw:
        candidates.append(Path(raw))
    candidates.extend(
        [
            Path.home() / "Desktop" / "macro",
            ROOT_DIR.parent / "macro",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def resolve_macro_snapshot_path(as_of: str | None = None) -> Path | None:
    raw = os.getenv(DEFAULT_CONFIG.macro_snapshot_path_env)
    if raw:
        path = Path(raw)
        if path.exists() and _macro_snapshot_is_available(path, as_of):
            return path
    macro_root = resolve_macro_root()
    if macro_root is None:
        return None
    if as_of is not None:
        candidates = sorted((macro_root / "src" / "data" / "snapshots").glob("*/snapshot.json"))
        available = [path for path in candidates if _macro_snapshot_is_available(path, as_of)]
        if available:
            return max(available, key=_macro_snapshot_published_at)
        return None
    latest_path = macro_root / "src" / "data" / "snapshots" / "latest.json"
    if not latest_path.exists():
        return None
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    run_id = str(latest.get("run_id") or "").strip()
    if run_id:
        local_snapshot = macro_root / "src" / "data" / "snapshots" / run_id / "snapshot.json"
        if local_snapshot.exists():
            return local_snapshot
    snapshot_text = latest.get("snapshot_json")
    if snapshot_text:
        snapshot_path = Path(str(snapshot_text))
        if snapshot_path.exists():
            return snapshot_path
    return None


def load_macro_snapshot_scores(as_of: str | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    snapshot_path = resolve_macro_snapshot_path(as_of)
    if snapshot_path is None:
        return {}, {"source": "fallback_env", "warnings": ["macro_snapshot_missing"]}
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    rows = payload.get("stock_scores", [])
    scores = {
        str(row.get("stock_code") or "").zfill(6): dict(row)
        for row in rows
        if str(row.get("stock_code") or "").strip()
    }
    metadata = {
        "source": "macro_snapshot",
        "snapshot_path": str(snapshot_path),
        "run_id": payload.get("run_id"),
        "published_at": payload.get("published_at"),
        "as_of_timestamp": payload.get("as_of_timestamp"),
        "warnings": payload.get("warnings", []),
    }
    return scores, metadata


def _macro_snapshot_is_available(path: Path, as_of: str | None) -> bool:
    if as_of is None:
        return True
    published = _macro_snapshot_published_at(path)
    if published is None:
        return False
    as_of_dt = datetime.combine(date.fromisoformat(as_of), datetime_time.max, tzinfo=published.tzinfo)
    return published <= as_of_dt


def _macro_snapshot_published_at(path: Path) -> datetime:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload.get("published_at") or payload.get("as_of_timestamp")
    if raw is None:
        return datetime.min
    return datetime.fromisoformat(str(raw))


def _current_dart_scores(
    securities: list[TradingSecurity],
    disclosures: list[Mapping[str, Any]],
) -> tuple[dict[str, float], dict[str, float], dict[str, list[str]]]:
    raw_by_common: dict[str, float] = defaultdict(float)
    risk_flags_by_common: dict[str, set[str]] = defaultdict(set)
    for disclosure in disclosures:
        code = str(disclosure.get("stock_code") or disclosure.get("ticker") or "").zfill(6)
        if not code.strip("0"):
            continue
        block_name = classify_disclosure(
            None if disclosure.get("event_code") is None else str(disclosure.get("event_code")),
            str(disclosure.get("title") or disclosure.get("report_nm") or ""),
        )
        if block_name == "ignored":
            continue
        contribution = decayed_disclosure_score(block_name, int(disclosure.get("trading_days_elapsed", 0)))
        raw_by_common[code] += contribution
        if block_name in {"dilutive_financing", "correction_cancellation_withdrawal", "governance_risk"}:
            risk_flags_by_common[code].add(block_name)

    raw_values = [
        round(raw_by_common.get(security.common_ticker or security.ticker, 0.0), 6)
        for security in securities
    ]
    normalized_values = _zscore(raw_values)
    normalized_by_ticker = {
        security.ticker: normalized_values[index]
        for index, security in enumerate(securities)
    }
    raw_by_ticker = {
        security.ticker: raw_values[index]
        for index, security in enumerate(securities)
    }
    risk_flags_by_ticker = {
        security.ticker: sorted(risk_flags_by_common.get(security.common_ticker or security.ticker, set()))
        for security in securities
    }
    return normalized_by_ticker, raw_by_ticker, risk_flags_by_ticker


def build_macro_dart_scores(
    securities: list[TradingSecurity],
    disclosures: list[Mapping[str, Any]],
    *,
    macro_states: Mapping[str, int] | None = None,
    as_of: str | None = None,
) -> list[MacroDartScore]:
    states = macro_states or load_macro_states_from_env()
    snapshot_scores, snapshot_metadata = load_macro_snapshot_scores(as_of)
    normalized_dart_by_ticker, raw_dart_by_ticker, risk_flags_by_ticker = _current_dart_scores(securities, disclosures)

    rows: list[dict[str, Any]] = []
    for security in securities:
        common_ticker = security.common_ticker or security.ticker
        snapshot_score = snapshot_scores.get(security.ticker) or snapshot_scores.get(common_ticker)
        if snapshot_score:
            macro_score = round(float(snapshot_score.get("stage1_sector_score", snapshot_score.get("raw_industry_score", 0.0))), 6)
            industry_code = str(snapshot_score.get("industry_code") or "") or None
            macro_source = "macro_snapshot"
        else:
            macro_score = sector_macro_score(security.sector, states)
            industry_code = None
            macro_source = str(snapshot_metadata.get("source") or "fallback_env")
        dart_score = round(float(normalized_dart_by_ticker.get(security.ticker, 0.0)), 6)
        raw_dart_score = round(float(raw_dart_by_ticker.get(security.ticker, 0.0)), 6)
        rows.append(
            {
                "ticker": security.ticker,
                "common_ticker": common_ticker,
                "name": security.name,
                "macro_score": macro_score,
                "dart_disclosure_score": dart_score,
                "combined_macro_dart_score": round(macro_score + dart_score, 6),
                "risk_flags": risk_flags_by_ticker.get(security.ticker, []),
                "raw_dart_score": raw_dart_score,
                "industry_code": industry_code,
                "macro_source": macro_source,
                "macro_snapshot_run_id": None if snapshot_metadata.get("run_id") is None else str(snapshot_metadata.get("run_id")),
            }
        )

    rows.sort(key=lambda item: (-item["combined_macro_dart_score"], -item["dart_disclosure_score"], item["ticker"]))
    return [
        MacroDartScore(
            ticker=row["ticker"],
            common_ticker=row["common_ticker"],
            name=row["name"],
            macro_score=row["macro_score"],
            dart_disclosure_score=row["dart_disclosure_score"],
            combined_macro_dart_score=row["combined_macro_dart_score"],
            macro_rank=index,
            risk_flags=row["risk_flags"],
            raw_dart_score=row["raw_dart_score"],
            industry_code=row["industry_code"],
            macro_source=row["macro_source"],
            macro_snapshot_run_id=row["macro_snapshot_run_id"],
        )
        for index, row in enumerate(rows, start=1)
    ]
