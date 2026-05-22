from __future__ import annotations

import pandas as pd

from canslim.earnings_analyzer import EarningsAnalyzer
from canslim.institutional_analyzer import InstitutionalAnalyzer
from canslim.leadership_analyzer import LeadershipAnalyzer
from canslim.market_analyzer import MarketAnalyzer
from canslim.newness_analyzer import NewnessAnalyzer
from canslim.supply_analyzer import SupplyAnalyzer
from main import StockScreener
from market_providers import MarketProvider, Security, parse_naver_kr_chart
from turtle import TurtleSignalGenerator


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
    volume.iloc[-1] = 3000
    high.iloc[-1] = high.iloc[:-1].max() + 1
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": close - 0.1,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "currency": "USD",
        }
    )


def test_earnings_c_and_a_pass_with_normalized_financials():
    analyzer = EarningsAnalyzer()
    c_pass, c_details = analyzer.check_c_criterion("TST", sample_financials())
    a_pass, a_details = analyzer.check_a_criterion("TST", sample_financials())

    assert c_pass is True
    assert c_details["quarters"][0]["yoy_growth"] >= 20
    assert a_pass is True
    assert a_details["eps_pass"] is True


def test_technical_criteria_and_turtle_use_normalized_columns():
    ohlcv = sample_ohlcv()

    assert SupplyAnalyzer().check_s_criterion("TST", ohlcv)[0] is True

    signals = TurtleSignalGenerator().generate_signals("TST", ohlcv)
    assert "S1_Buy" in signals
    assert "S2_Buy" in signals

    rs = LeadershipAnalyzer().calculate_rs_rating("TST", ohlcv)
    assert rs is not None
    assert LeadershipAnalyzer().check_l_criterion("TST", 95, ohlcv)[0] is True


def test_institutional_and_market_criteria():
    flow = [{"institutional_net_buy": -100}] * 63 + [{"institutional_net_buy": 200}] * 63
    assert InstitutionalAnalyzer().check_i_criterion("TST", flow)[0] is True

    index_ohlcv = sample_ohlcv(days=60, slope=2.0)
    assert MarketAnalyzer().check_m_criterion("TST", index_ohlcv)[0] is True


def test_naver_kr_chart_parser_handles_euc_kr_xml():
    payload = """<?xml version="1.0" encoding="EUC-KR" ?>
<protocol>
  <chartdata symbol="005930" name="Samsung" count="2" timeframe="day">
    <item data="20260520|278000|282500|263500|276000|35662077" />
    <item data="20260521|291000|298000|287000|297750|27051224" />
  </chartdata>
</protocol>""".encode("euc-kr")

    df = parse_naver_kr_chart(payload)

    assert len(df) == 2
    assert df.iloc[-1]["Close"] == 297750
    assert df.iloc[-1]["Volume"] == 27051224


class FakeProvider(MarketProvider):
    market = "US"
    market_name = "Fake US"
    minimum_universe_size = 1

    def get_universe(self, limit=None):
        securities = [
            Security(market="US", ticker=f"TST{idx}", name=f"Test {idx}", sector="Technology", currency="USD")
            for idx in range(1, 6)
        ]
        return securities[:limit] if limit else securities

    def get_ohlcv(self, security, days=500):
        slope = int(security.ticker[-1])
        return sample_ohlcv(slope=slope)

    def get_financials(self, security):
        return sample_financials()

    def get_institutional_flow(self, security, days=126):
        return [{"institutional_net_buy": -100}] * 63 + [{"institutional_net_buy": 200}] * 63

    def get_market_index_ohlcv(self, security, days=60):
        return sample_ohlcv(days=60, slope=2.0)


def test_market_screening_outputs_passes_top_candidates_and_quality_summary():
    screener = StockScreener(markets=["US"])
    result = screener.screen_market(FakeProvider())

    assert result["universe_count"] == 5
    assert result["evaluated_count"] == 5
    assert len(result["top_candidates"]) == 5
    assert result["fail_reasons"]["by_criterion"]["L"] >= 1
    assert len(result["canslim_passed"]) >= 1
    assert result["canslim_passed"][0]["Criteria"]["C"]["pass"] is True
