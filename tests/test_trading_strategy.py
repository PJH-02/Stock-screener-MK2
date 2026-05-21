from __future__ import annotations

import pandas as pd

from trading.backtest import BacktestEngine, turtle_exit_level_on
from trading.canslim_turtle import CANSLIMTurtleEvaluator
from trading.data_collection.kr_dart import filter_disclosures_as_of, filter_financials_as_of
from trading.kiwoom_client import normalize_daily_chart_response, normalize_universe_response
from trading.macro_dart_score import build_macro_dart_scores
from trading.point_in_time import slice_price_history_as_of
from trading.portfolio import Position, choose_winner, exit_reason, shares_for_value, target_weights


def sample_financials():
    return {
        "annual": [
            {"year": 2022, "period": "FY", "eps": 4.0, "net_income": 100.0, "equity": 700.0},
            {"year": 2023, "period": "FY", "eps": 5.0, "net_income": 130.0, "equity": 760.0},
            {"year": 2024, "period": "FY", "eps": 6.2, "net_income": 170.0, "equity": 820.0},
            {"year": 2025, "period": "FY", "eps": 8.0, "net_income": 220.0, "equity": 900.0},
        ],
        "quarterly": [
            {"year": 2024, "period": "Q1", "eps": 1.0},
            {"year": 2024, "period": "Q2", "eps": 1.0},
            {"year": 2025, "period": "Q1", "eps": 1.3},
            {"year": 2025, "period": "Q2", "eps": 1.45},
        ],
    }


def sample_ohlcv(days=260, slope=1.0):
    dates = pd.date_range("2025-01-01", periods=days, freq="B")
    close = pd.Series([20 + (idx * slope / 10) for idx in range(days)])
    high = close + 0.5
    low = close - 0.5
    volume = pd.Series([1000] * days)
    volume.iloc[-5:] = 3000
    high.iloc[-1] = high.iloc[:-1].max() + 1
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": close - 0.1,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def test_kiwoom_universe_normalizes_preferred_share_to_common_ticker():
    securities = normalize_universe_response(
        {
            "items": [
                {"stock_code": "005930", "stock_name": "삼성전자", "market": "KOSPI", "security_type": "보통주"},
                {"stock_code": "005935", "stock_name": "삼성전자우", "market": "KOSPI", "security_type": "우선주"},
                {"stock_code": "123456", "stock_name": "테스트ETF", "market": "KOSPI", "security_type": "ETF"},
            ]
        }
    )

    by_ticker = {security.ticker: security for security in securities}
    assert set(by_ticker) == {"005930", "005935"}
    assert by_ticker["005935"].common_ticker == "005930"


def test_kiwoom_daily_chart_normalizer_accepts_fixture_shape():
    df = normalize_daily_chart_response(
        {
            "stk_dt_pole_chart_qry": [
                {"dt": "20250102", "open_pric": "1000", "high_pric": "1200", "low_pric": "900", "cur_prc": "1100", "trde_qty": "10,000"},
                {"dt": "20250103", "open_pric": "1100", "high_pric": "1300", "low_pric": "1000", "cur_prc": "1250", "trde_qty": "11,000"},
            ]
        }
    )

    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert df.iloc[-1]["close"] == 1250


def test_macro_dart_scores_rank_full_universe_and_share_common_disclosures():
    securities = normalize_universe_response(
        {
            "items": [
                {"stock_code": "005930", "stock_name": "삼성전자", "market": "KOSPI", "sector": "반도체"},
                {"stock_code": "005935", "stock_name": "삼성전자우", "market": "KOSPI", "sector": "반도체"},
            ]
        }
    )
    scores = build_macro_dart_scores(
        securities,
        [{"stock_code": "005930", "title": "단일판매 공급계약 체결", "trading_days_elapsed": 0}],
        macro_states={"G": 1, "IC": 0, "FC": 0, "ED": 0, "FX": 0},
    )

    by_ticker = {score.ticker: score for score in scores}
    assert by_ticker["005930"].dart_disclosure_score == by_ticker["005935"].dart_disclosure_score
    assert by_ticker["005930"].combined_macro_dart_score > 0


def test_macro_snapshot_is_not_used_before_publish_date(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        """
        {
          "run_id": "future-macro",
          "published_at": "2026-01-10T08:30:00+09:00",
          "stock_scores": [
            {"stock_code": "005930", "stock_name": "삼성전자", "stage1_sector_score": 99, "raw_industry_score": 99}
          ]
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADING_MACRO_SNAPSHOT_PATH", str(snapshot))
    securities = normalize_universe_response(
        {"items": [{"stock_code": "005930", "stock_name": "삼성전자", "market": "KOSPI", "sector": "반도체"}]}
    )

    before = build_macro_dart_scores(securities, [], macro_states={"G": 1, "IC": 0, "FC": 0, "ED": 0, "FX": 0}, as_of="2026-01-09")
    after = build_macro_dart_scores(securities, [], macro_states={"G": 1, "IC": 0, "FC": 0, "ED": 0, "FX": 0}, as_of="2026-01-10")

    assert before[0].macro_score == 12
    assert before[0].macro_source == "fallback_env"
    assert after[0].macro_score == 99
    assert after[0].macro_source == "macro_snapshot"


def test_point_in_time_filters_financials_disclosures_and_prices():
    financials = {
        "annual": [
            {"year": 2024, "period": "FY", "eps": 10},
            {"year": 2025, "period": "FY", "eps": 20},
        ],
        "quarterly": [
            {"year": 2025, "period": "Q1", "eps": 1},
            {"year": 2025, "period": "Q2", "eps": 2},
        ],
    }
    available = filter_financials_as_of(financials, "2025-06-01")
    assert [row["year"] for row in available["annual"]] == [2024]
    assert [row["period"] for row in available["quarterly"]] == ["Q1"]

    disclosures = [
        {"stock_code": "005930", "title": "공급계약", "accepted_at": "2025-05-30"},
        {"stock_code": "005930", "title": "유상증자", "accepted_at": "2025-06-02"},
    ]
    filtered = filter_disclosures_as_of(disclosures, "2025-06-01")
    assert len(filtered) == 1
    assert filtered[0]["accepted_at"] == "2025-05-30"

    price_history = {"005930": sample_ohlcv(days=5)}
    as_of = str(price_history["005930"]["date"].iloc[2])
    sliced = slice_price_history_as_of(price_history, as_of)
    assert sliced["005930"]["date"].max() == as_of


def test_allocation_cap_shares_and_exit_rules():
    candidates = [{"ticker": f"T{i}", "macro_rank": i} for i in range(1, 4)]

    equal = target_weights(candidates, "equal_weight")
    inverse = target_weights(candidates, "inverse_rank_weight")

    assert all(weight <= 0.15 for weight in equal.values())
    assert all(weight <= 0.15 for weight in inverse.values())
    assert shares_for_value(1_000_000, 10_000) == 99
    position = Position("T1", shares=10, entry_price=100, turtle_system="S1", turtle_exit_level=90)
    assert exit_reason(position, 124) == "half_take_profit"
    assert exit_reason(position, 89) == "full_exit"


def test_choose_winner_uses_cagr_first():
    assert (
        choose_winner(
            {
                "equal_weight": {"cagr": 0.10, "final_equity": 120, "max_drawdown": 0.05},
                "inverse_rank_weight": {"cagr": 0.12, "final_equity": 110, "max_drawdown": 0.20},
            }
        )
        == "inverse_rank_weight"
    )


def test_canslim_turtle_filters_candidates_after_macro_ranking():
    securities = normalize_universe_response(
        {
            "items": [
                {"stock_code": f"00000{i}", "stock_name": f"테스트{i}", "market": "KOSPI", "sector": "반도체"}
                for i in range(1, 6)
            ]
        }
    )
    disclosures = [{"stock_code": "000005", "title": "단일판매 공급계약 체결", "trading_days_elapsed": 0}]
    scores = build_macro_dart_scores(securities, disclosures, macro_states={"G": 1, "IC": 0, "FC": 0, "ED": 0, "FX": 0})
    price_history = {security.ticker: sample_ohlcv(slope=index) for index, security in enumerate(securities, start=1)}
    financials = {security.common_ticker or security.ticker: sample_financials() for security in securities}

    rows = CANSLIMTurtleEvaluator().evaluate_universe(securities, price_history, financials, scores)
    candidates = CANSLIMTurtleEvaluator().candidates(rows)

    assert candidates
    assert candidates[0]["canslim_pass"] is True
    assert candidates[0]["turtle_system"] in {"S1", "S2"}


def test_backtest_writes_long_only_next_open_lifecycle():
    dates = pd.date_range("2025-01-01", periods=6, freq="B").strftime("%Y-%m-%d")
    price_history = {
        "T1": pd.DataFrame(
            {
                "date": dates,
                "open": [100, 101, 130, 131, 132, 133],
                "high": [101, 102, 131, 132, 133, 134],
                "low": [99, 100, 129, 130, 131, 132],
                "close": [100, 125, 130, 131, 132, 133],
                "volume": [1000] * 6,
            }
        )
    }
    schedule = {str(dates[0]): [{"ticker": "T1", "macro_rank": 1, "turtle_system": "S1", "turtle_exit_level": 90}]}

    result = BacktestEngine().run(price_history=price_history, candidate_schedule=schedule, method="equal_weight", initial_capital=1_000_000)

    assert result["metrics"]["orders"] >= 2
    assert any(order["reason"] == "half_take_profit" for order in result["orders"])


def test_turtle_exit_level_is_recomputed_from_prior_lows():
    df = sample_ohlcv(days=30)
    date_value = str(df["date"].iloc[-1])

    assert turtle_exit_level_on(df, date_value, "S1") == float(df["low"].iloc[-11:-1].min())
    assert turtle_exit_level_on(df, date_value, "S2") == float(df["low"].iloc[-21:-1].min())
