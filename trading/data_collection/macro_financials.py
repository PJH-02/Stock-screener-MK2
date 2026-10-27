"""Macro snapshot and KR DART financial-only collection."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from market_providers import KRProvider  # noqa: E402
from trading.config import DATA_CACHE_DIR, ROOT_DIR as CONFIG_ROOT_DIR  # noqa: E402
from trading.data_collection.macro_snapshots import MacroSnapshotCollector  # noqa: E402
from trading.kiwoom_client import load_env_file  # noqa: E402


SCREENER_CACHE_DIR = CONFIG_ROOT_DIR / "data_cache" / "screener"


def load_common_tickers() -> dict[str, dict[str, Any]]:
    universe_path = DATA_CACHE_DIR / "kr_universe.json"
    rows = json.loads(universe_path.read_text(encoding="utf-8"))
    by_common: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("common_ticker") or row.get("ticker") or "").strip()
        if ticker:
            by_common.setdefault(ticker, dict(row))
    return by_common


def read_financial(ticker: str) -> dict[str, Any] | None:
    path = SCREENER_CACHE_DIR / "financials" / "kr_dart" / f"{ticker}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    data = payload.get("data") if isinstance(payload, dict) else payload
    return data if isinstance(data, dict) else None


def needs_refresh(ticker: str, *, start_year: int, end_year: int, refresh_all: bool) -> bool:
    if not ticker.isdigit():
        return False
    if refresh_all:
        return True
    data = read_financial(ticker)
    if data is None:
        return True
    annual = data.get("annual") or []
    quarterly = data.get("quarterly") or []
    alerts = [str(item) for item in data.get("alerts", [])]
    coverage = data.get("coverage") or {}
    try:
        covers = int(coverage.get("start_year")) <= start_year and int(coverage.get("end_year")) >= end_year
    except Exception:
        covers = False
    return (not covers) or (not annual and not quarterly) or any("offline cache-only" in item for item in alerts)


def collect_one(ticker: str, *, start_year: int, end_year: int) -> dict[str, Any]:
    provider = KRProvider()
    security = type("SecurityLike", (), {"ticker": ticker})()
    financials = provider.get_financials(security, start_year=start_year, end_year=end_year)
    annual = financials.get("annual") or []
    quarterly = financials.get("quarterly") or []
    return {
        "ticker": ticker,
        "annual": len(annual),
        "quarterly": len(quarterly),
        "alerts": financials.get("alerts", []),
    }


def summarize_financials(common_tickers: list[str], *, start_year: int, end_year: int) -> dict[str, Any]:
    files = list((SCREENER_CACHE_DIR / "financials" / "kr_dart").glob("*.json"))
    usable = 0
    empty = 0
    covered = 0
    offline = 0
    for ticker in common_tickers:
        data = read_financial(ticker)
        if data is None:
            empty += 1
            continue
        annual = data.get("annual") or []
        quarterly = data.get("quarterly") or []
        if annual or quarterly:
            usable += 1
        else:
            empty += 1
        coverage = data.get("coverage") or {}
        try:
            if int(coverage.get("start_year")) <= start_year and int(coverage.get("end_year")) >= end_year:
                covered += 1
        except Exception:
            pass
        if any("offline cache-only" in str(item) for item in data.get("alerts", [])):
            offline += 1
    return {
        "files": len(files),
        "targets": len(common_tickers),
        "covered": covered,
        "usable": usable,
        "empty": empty,
        "offline_alerts": offline,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect only macro snapshots and KR DART financials.")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--workers", type=int, default=int(os.getenv("TRADING_DART_FINANCIAL_WORKERS", "2")))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--refresh-all", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--per-ticker-timeout", type=int, default=0)
    parser.add_argument("--single-ticker", default=None)
    return parser.parse_args()


def run_child(ticker: str, *, args: argparse.Namespace) -> dict[str, Any]:
    command = [
        sys.executable,
        "-u",
        "-m",
        "trading.data_collection.macro_financials",
        "--start",
        args.start,
        "--end",
        args.end,
        "--single-ticker",
        ticker,
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR)
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT_DIR),
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=args.per_ticker_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ticker": ticker, "failed": True, "error": f"timeout>{args.per_ticker_timeout}s"}
    if completed.returncode != 0:
        return {"ticker": ticker, "failed": True, "error": (completed.stderr or completed.stdout)[-500:]}
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except Exception as exc:
        return {"ticker": ticker, "failed": True, "error": f"child_json_parse_failed: {exc}; stdout={completed.stdout[-300:]}"}


def main() -> None:
    load_env_file(ROOT_DIR / ".env")
    args = parse_args()
    start_year = date.fromisoformat(args.start).year
    end_year = date.fromisoformat(args.end).year
    if args.single_ticker:
        result = collect_one(args.single_ticker, start_year=start_year, end_year=end_year)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return

    common = load_common_tickers()
    all_tickers = sorted(common)
    targets = [
        ticker
        for ticker in all_tickers
        if needs_refresh(ticker, start_year=start_year, end_year=end_year, refresh_all=args.refresh_all)
    ]
    if args.limit is not None:
        targets = targets[: args.limit]

    macro_result = MacroSnapshotCollector().collect(start_date=args.start, end_date=args.end)
    before = summarize_financials(all_tickers, start_year=start_year, end_year=end_year)

    started = time.time()
    completed = 0
    failed: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    workers = max(args.workers, 1)
    print(
        json.dumps(
            {
                "event": "start",
                "targets": len(targets),
                "workers": workers,
                "before": before,
                "macro": macro_result,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    if args.per_ticker_timeout > 0:
        runner = lambda ticker: run_child(ticker, args=args)
    else:
        runner = lambda ticker: collect_one(ticker, start_year=start_year, end_year=end_year)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(runner, ticker): ticker for ticker in targets}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                row = future.result()
                if row.get("failed"):
                    failed.append({"ticker": ticker, "error": str(row.get("error") or "unknown")})
                else:
                    results.append(row)
            except Exception as exc:
                failed.append({"ticker": ticker, "error": str(exc)})
                if "DART_LIMIT" in str(exc):
                    for pending in futures:
                        pending.cancel()
                    break
            completed += 1
            if completed % max(args.progress_every, 1) == 0 or completed == len(targets):
                print(
                    json.dumps(
                        {
                            "event": "progress",
                            "completed": completed,
                            "targets": len(targets),
                            "failed": len(failed),
                            "elapsed_seconds": round(time.time() - started, 2),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    after = summarize_financials(all_tickers, start_year=start_year, end_year=end_year)
    print(
        json.dumps(
            {
                "event": "done",
                "targets": len(targets),
                "completed": completed,
                "failed": len(failed),
                "failed_samples": failed[:20],
                "with_records_in_run": sum(1 for row in results if row.get("annual") or row.get("quarterly")),
                "elapsed_seconds": round(time.time() - started, 2),
                "before": before,
                "after": after,
                "macro": macro_result,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
