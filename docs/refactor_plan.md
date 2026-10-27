# Stock-screener-MK2 전면 리팩토링 계획

작성 기준:
- `req.md`의 데이터 정규화, point-in-time, CANSLIM/Turtle, 리포팅 요구사항을 반영한다.
- 새 사용자 요구사항을 더 높은 우선순위로 둔다.
- `data/ohl.xlsx`에 시가, 고가, 저가가 포함되어 있다고 가정한다.
- 백테스트는 strict CANSLIM all-pass 기준이며, 신호는 `t`일 종가 기준, 체결은 `t+1`일 시가 기준이다.
- GitHub Pages 일일 필터링과 백테스트는 분리한다.
- 기존처럼 `src/`, `trading/`, `data_collection/`이 중복된 verbose 구조를 유지하지 않는다.

## 1. 최종 목표

1. 현재 혼합된 파일 구조를 제거하고 단순한 패키지 구조로 재구성한다.
2. `CANSLIM + Turtle` 백테스트 모델을 만든다.
3. `CANSLIM + Turtle + Macro Rank` 백테스트 모델을 별도 모델 경로로 만든다.
4. 두 모델을 동일 기간/동일 데이터로 백테스트하고 성과가 좋은 모델을 선택한다.
5. 선택된 모델로 매일 한국장 마감 후 추가 데이터를 수집하고 GitHub Pages 결과를 갱신한다.
6. DART 공시자료는 macro rank 모델에서 기본 제외한다. macro 공식/무료 데이터 수집이 불가능할 때만 cached macro snapshot 또는 manual macro state fallback을 사용한다.
7. 전체 구조는 적은 파일 수로 유지하되, 모델 경로와 책임 경계는 명확히 유지한다.

## 2. 이번 리팩토링의 우선순위 결정

`req.md`와 다른 새 기준:

| 항목 | `req.md` 기준 | 새 최종 기준 |
|---|---|---|
| 가격 데이터 | OHLC 부재, close-only proxy | `ohl.xlsx`에서 시가/고가/저가, `주가.xlsx`에서 수정 종가를 사용. OHLC 기반 Turtle/ATR 사용 |
| 체결 | `t` 종가 신호, `t+1` 종가 체결 | `t` 종가 신호, `t+1` 시가 체결 |
| CANSLIM 필터 | strict와 score mode 모두 | strict CANSLIM 전부 통과만 기본 모델 |
| Macro+DART | macro + DART 공시 점수 | DART 공시 제외, macro Stage 1 sector score만 사용 |
| Macro 모델 sizing | `req.md`에 없음 | macro rank 기반 비중 결정, Turtle position cap 미적용 |
| Daily Pages | 명시 없음 | 백테스트 승자 모델로 장 마감 후 결과 업데이트 |

기존 과거 대화 기준 중 제거할 것:
- `+24% 절반 익절`, `15% 종목 cap`, `equal_weight/inverse_rank_weight 비교`는 이번 최종 계획의 기본 규칙이 아니다.
- 필요한 경우 향후 실험 옵션으로만 추가한다.

## 3. Macro 원본 로직에서 사용하는 데이터

원본 경로:
- `C:\Users\박제형\Desktop\macro\src\macro_screener\data\macro_client.py`
- `C:\Users\박제형\Desktop\macro\src\macro_screener\stage1\ranking.py`
- `C:\Users\박제형\Desktop\macro\config\macro_sector_exposure.v2.json`
- `C:\Users\박제형\Desktop\macro\stock_classification.csv`

Macro Stage 1 채널:

| 채널 | 의미 | 한국 series | 미국/외부 series | 변환 | 판정 기준 |
|---|---|---|---|---|---|
| `G` | Growth / Activity | ECOS `901Y033`, item `A00`, `2`, `kr_ipi_yoy_3mma` | FRED `INDPRO`, `us_ipi_yoy_3mma` | YoY 3개월 이동평균 | `> 1.0` positive, `< -1.0` negative |
| `IC` | Inflation / Cost | ECOS `901Y009`, item `0`, `kr_cpi_yoy_3mma` | FRED `CPIAUCSL`, `us_cpi_yoy_3mma` | YoY 3개월 이동평균 | `> 2.75` positive, `< 1.25` negative |
| `FC` | Financial Conditions | ECOS `721Y001`, items `7020000`, `5020000`, `kr_credit_spread_z36` | FRED `BAA10YM`, `us_credit_spread_z36` | 36개월 z-score, 3개월 평균 | lower positive: `< -0.5` positive, `> 0.5` negative |
| `ED` | External Demand | ECOS `901Y121`, items `T002`, `US`, `kr_exports_us_yoy_3mma`; optional KOSIS override | FRED `A255RO1Q156NBEA`, `us_real_imports_goods_yoy_3mma`; fallback `DGDSRX1`, `us_real_pce_goods_yoy_3mma` | KR YoY 3개월 이동평균, US 3개 관측치 평균 또는 YoY 3개월 이동평균 | KR `> 2.0` positive, `< -2.0` negative; US `> 1.5` positive, `< -1.5` negative |
| `FX` | Foreign Exchange | ECOS `731Y006`, item `0000003`, `usdkrw_3m_log_return` | FRED `TWEXBGSMTH`, `broad_usd_3m_log_return` | 3개월 로그수익률 | KR `> 2.5` positive, `< -2.5` negative; US `> 2.0` positive, `< -2.0` negative |

필요 env:
- `ECOS_API_KEY`: 한국 ECOS 데이터.
- `FRED_API_KEY`: 미국/외부 매크로 데이터.
- `KOSIS_API_KEY`: 선택. `kr_exports_us_yoy_3mma`를 KOSIS로 대체할 때만 사용.
- `KOSIS_EXPORTS_US_USER_STATS_ID`: 선택. KOSIS 수출 대미 데이터 custom stats id.
- `KRX_API_KEY`: stock master를 KRX live로 가져올 때 사용 가능하지만, 본 리팩토링에서는 우선 `data` 파일 및 `stock_classification.csv` mapping을 사용한다.

Macro score 계산:
1. 위 series 값을 `-1 / 0 / +1` 상태로 분류한다.
2. 채널별 한국 signal과 미국/외부 signal을 평균한다.
3. 평균값이 neutral band를 넘으면 채널 상태를 `+1`, 아래로 넘으면 `-1`, 아니면 `0`으로 둔다.
4. neutral band는 `G=0.25`, `IC=0.25`, `FC=0.25`, `ED=0.25`, `FX=0.50`이다.
5. sector score는 `sum(channel_state[channel] * sector_exposure[channel]) + overlay_adjustment`다.
6. 종목 macro score는 해당 종목의 sector score다.

이번 Stock-screener 리팩토링에서 제외할 macro 원본 요소:
- DART 공시 Stage 2 전체.
- `stage2/classifier.py`, `stage2/decay.py`, `stage2/ranking.py`에 해당하는 DART disclosure 점수.
- `final_score = normalized_dart_score + stage1_sector_score` 계약.

이번 Stock-screener에 유지할 macro 요소:
- macro channel state 계산.
- sector exposure matrix.
- stock code -> grouped sector mapping.
- `macro_score = stage1_sector_score`.
- macro source metadata: `as_of`, `input_cutoff`, `source`, `warnings`, `fallback_mode`.

## 4. 새 폴더 구조

최종 구조:

```text
Stock-screener-MK2/
  stock_screener/
    __init__.py
    config.py
    data.py
    rules.py
    backtest.py
    daily.py
    cli.py
    models/
      __init__.py
      canslim_turtle.py
      canslim_turtle_macro.py

  public/
    index.html
    style.css
    script.js
    results/
      screener_results.json

  data/
    ohl.xlsx
    주가.xlsx
    거래량.xlsx
    기관.xlsx
    유동주식수.xlsx
    earnings.xlsx
    상장주식수와시총.xlsx
    index.xlsx
    processed/
      stock_metadata.parquet
      stock_open.parquet
      stock_high.parquet
      stock_low.parquet
      stock_close.parquet
      stock_volume.parquet
      stock_institution.parquet
      stock_free_float.parquet
      stock_earnings_quarterly.parquet
      stock_listed_shares.parquet
      stock_market_cap.parquet
      index.parquet
      preprocess_summary.json
      daily_panel.parquet
      fundamentals_pit.parquet
      macro_states.parquet
      macro_sector_scores.parquet
    results/
      backtest/
        canslim_turtle/
        canslim_turtle_macro/
        comparison.json
        selected_model.json
      daily/
        latest_signals.json
        latest_orders.csv

  tests/
    test_data.py
    test_rules.py
    test_models.py
    test_backtest.py
    test_daily.py

  .github/workflows/
    daily-pages.yml
    backtest.yml

  docs/
    refactor_plan.md
```

파일 책임:
- `stock_screener/config.py`: 모든 상수, env 이름, 기본 파라미터. YAML 없이 Python dataclass로 시작한다.
- `stock_screener/data.py`: XLSX 정규화, Kiwoom/기타 live daily 수집, macro provider payload 수집, QA, parquet 저장.
- `stock_screener/rules.py`: CANSLIM, Turtle, ATR/N, macro sector score 계산의 순수 함수.
- `stock_screener/models/canslim_turtle.py`: strict CANSLIM + Turtle 모델.
- `stock_screener/models/canslim_turtle_macro.py`: strict CANSLIM + Turtle 필터 후 macro rank 기반 비중 모델.
- `stock_screener/backtest.py`: 공통 event-driven 백테스트 엔진, 주문/체결/포지션/성과지표.
- `stock_screener/daily.py`: 장 마감 후 데이터 갱신, 선택 모델 실행, Pages JSON 생성.
- `stock_screener/cli.py`: `normalize`, `backtest`, `daily`, `collect-macro`, `compare` 명령.

삭제할 기존 구조:
- `src/main.py`
- `src/market_providers.py`
- `src/canslim/*`
- `src/turtle/*`
- `trading/canslim_turtle.py`
- `trading/backtest.py`
- `trading/portfolio.py`
- `trading/point_in_time.py`
- `trading/macro_dart_score.py`
- `trading/data_collection/*`
- `results/`
- `_site/`
- `logs/`
- `.cache/`
- `docs_cache/`

삭제는 새 구조가 테스트를 통과한 뒤 수행한다.

## 5. 데이터 schema

### 5.1 Raw XLSX 입력

현재 `data/` 폴더에서 확인된 파일:

| 파일 | 의미 | raw item name | 필수 여부 |
|---|---|---|---|
| `ohl.xlsx` | 주식 시가, 고가, 저가 | `시가(원)`, `고가(원)`, `저가(원)` | 필수 |
| `주가.xlsx` | 수정 종가 | `수정주가(현금배당포함)(원)` | 필수 |
| `거래량.xlsx` | 거래량 | `거래량(주)` | 필수 |
| `기관.xlsx` | 기관 순매수대금 | `기관 순매수대금(일간)(만원)` | 필수 |
| `유동주식수.xlsx` | 유동주식수 | `유동주식수(주)` | 필수 |
| `earnings.xlsx` | 당기순이익(지배) | `당기순이익(지배)(천원)` | 필수 |
| `상장주식수와시총.xlsx` | 상장주식수, 시가총액 | `상장주식수(주)`, `시가총액(백만원)` | 필수 |
| `index.xlsx` | KOSPI/KOSDAQ 지수 시가, 종가 | `시가지수(포인트)`, `종가지수(포인트)` | 필수 |

XLSX parsing 규칙:
- 1행: ticker. 예: `A000020`.
- 2행: 종목명.
- 5행: 항목명.
- 6행: 집계주기.
- 7행부터 날짜와 값.
- ticker는 `A` prefix를 유지한다. 별도 `code` 컬럼에는 leading `A`를 제거한 6자리 코드를 저장한다.
- 날짜는 Excel serial 또는 datetime 모두 허용하고 `YYYY-MM-DD`로 저장한다.
- 메타데이터는 `ticker`, `code`, `name`만 저장한다.
- 종목명에 `스팩`이 포함된 ticker는 원천 파일 전체에서 찾아 제외한다.
- `ohl.xlsx`와 `상장주식수와시총.xlsx`는 한 ticker가 여러 item 컬럼을 가지므로 `item_name` 기준으로 분리한다.

전처리 스크립트:
- `scripts/preprocess_xlsx.py`

생성된 `data/processed/` 파일:

| 파일 | shape | 기간 | 내용 |
|---|---:|---|---|
| `stock_metadata.parquet` | 2556 x 3 | - | `ticker`, `code`, `name` |
| `stock_open.parquet` | 4041 x 2556 | 2009-12-30..2026-06-02 | 수정 시가 |
| `stock_high.parquet` | 4041 x 2556 | 2009-12-30..2026-06-02 | 수정 고가 |
| `stock_low.parquet` | 4041 x 2556 | 2009-12-30..2026-06-02 | 수정 저가 |
| `stock_close.parquet` | 4040 x 2556 | 2009-12-30..2026-06-01 | 수정 종가 |
| `stock_volume.parquet` | 4040 x 2556 | 2009-12-30..2026-06-01 | 거래량 |
| `stock_institution.parquet` | 4040 x 2556 | 2009-12-30..2026-06-01 | 기관 순매수대금 |
| `stock_free_float.parquet` | 4041 x 2556 | 2009-12-30..2026-06-02 | 유동주식수 |
| `stock_earnings_quarterly.parquet` | 67 x 2556 | 2009-12-30..2026-06-02 | 분기 당기순이익(지배) |
| `stock_listed_shares.parquet` | 4040 x 2556 | 2009-12-30..2026-06-01 | 상장주식수 |
| `stock_market_cap.parquet` | 4040 x 2556 | 2009-12-30..2026-06-01 | 시가총액 |
| `index.parquet` | 4041 x 4 | 2009-12-30..2026-06-02 | `kospi_open`, `kospi_close`, `kosdaq_open`, `kosdaq_close` |

Daily feature 계산에 사용하는 공통 거래일:
- `stock_open/high/low/close/volume/institution/free_float/listed_shares/market_cap`의 날짜 교집합.
- 현재 결과 기준 `2009-12-30..2026-06-01`, 4040 거래일.
- 2026-06-02는 일부 파일에만 있으므로, close/volume/mcap이 들어오기 전까지 백테스트와 daily signal 계산에서 제외한다.

### 5.2 Processed wide parquet

전처리 직후의 wide parquet는 다음 규칙을 따른다.

```text
index: date
columns: ticker, e.g. A000020
values: numeric core data only
```

Wide parquet에는 종목명, item code, 유형, 집계주기 같은 원천 메타데이터를 넣지 않는다. 종목명은 `stock_metadata.parquet`에서만 조인한다.

단위 변환:
- `stock_institution.parquet`: raw는 만원 단위다. feature 계산 시 `institution_net_buy_krw = raw * 10_000`.
- `stock_earnings_quarterly.parquet`: raw는 천원 단위다. feature 계산 시 `net_income_control_krw = raw * 1_000`.
- `stock_market_cap.parquet`: raw는 백만원 단위다. feature 계산 시 `market_cap_krw = raw * 1_000_000`.
- `stock_close/open/high/low`: 원 단위.
- `stock_volume`, `stock_free_float`, `stock_listed_shares`: 주 단위.

### 5.3 Canonical daily panel

Feature/백테스트 단계에서 wide parquet를 long 형태로 조인한 `data/processed/daily_panel.parquet` schema:

```text
date: string YYYY-MM-DD
ticker: string, e.g. A000020
code: string 6 digits
name: string
open: float
high: float
low: float
close: float
volume: float
trading_value: float
listed_shares: float
free_float_shares: float
market_cap_krw: float
institution_net_buy_krw: float
kospi_open: float|null
kospi_close: float|null
kosdaq_open: float|null
kosdaq_close: float|null
```

`trading_value` 계산:
- 별도 거래대금 파일이 있으면 그 값을 사용한다.
- 없으면 `close * volume`으로 계산한다.
- 계산값이라는 사실을 `data_quality.trading_value_source = "close_x_volume"`에 기록한다.

Index 데이터:
- `index.xlsx`에서 KOSPI/KOSDAQ 시가와 종가가 같이 들어온다.
- 정규화 결과는 `data/processed/index.parquet`다.
- 현재 XLSX에는 종목별 시장 구분 메타데이터가 없다. M 계산에서 KOSPI/KOSDAQ 매칭이 필요하면 `ticker`, `market`만 가진 별도 최소 mapping을 Kiwoom/KRX universe에서 생성한다.
- 시장 mapping이 없으면 M 계산은 실패시킨다. 임의로 모든 종목을 KOSPI에 매칭하지 않는다.
- `stock_screener/data.py collect-index`는 daily Pages용 최신 지수 일봉만 추가 수집한다.
- daily index 수집 실패 시 daily Pages run은 실패시키지 않고 직전 cache를 사용하되, 백테스트 normalize는 `index.parquet`가 없으면 실패시킨다.
- 백테스트에서 M 조건이 필요하므로 index 누락 상태로는 성과 비교를 수행하지 않는다.

### 5.4 Canonical fundamentals PIT

`data/processed/fundamentals_pit.parquet` schema:

```text
date: string YYYY-MM-DD
ticker: string
code: string 6 digits
period_end: string YYYY-MM-DD
period: Q1|Q2|Q3|Q4|FY
net_income_control_krw: float
avg_listed_shares: float
eps: float
eps_yoy: float|null
eps_fy: float|null
eps_fy_cagr_3y: float|null
available_date: string YYYY-MM-DD
source: earnings.xlsx
```

PIT 규칙:
- 입력은 `stock_earnings_quarterly.parquet`의 분기 당기순이익(지배)이다.
- 분기 `period_end` 값은 즉시 사용 금지.
- Q1/Q2/Q3는 `period_end + 60 calendar days` 후 다음 거래일부터 사용.
- Q4/FY는 `period_end + 120 calendar days` 후 다음 거래일부터 사용.
- `merge_asof`로 각 trading date에 사용 가능한 최신 재무값만 붙인다.
- 원천 XLSX에는 실제 공시일이 없으므로 이 고정 lag는 lookahead 방지용 보수적 proxy다.

## 6. 데이터 QA

Normalize 단계에서 반드시 수행:

1. ticker가 `A` prefix를 가진 문자열인지 확인한다.
2. code가 leading `A`를 제거한 6자리인지 확인한다.
3. `stock_metadata.parquet`는 `ticker`, `code`, `name`만 가진다.
4. 종목명에 `스팩`이 포함된 ticker가 processed stock 파일에 남아 있으면 실패한다.
5. 각 wide parquet의 date index 중복이 없도록 확인한다.
6. stock wide parquet의 컬럼 집합이 `stock_metadata.ticker`와 일치하는지 확인한다.
7. daily panel 생성 시 stock daily 파일들의 날짜 교집합만 사용한다.
8. 현재 기준 공통 daily 기간은 `2009-12-30..2026-06-01`이다.
9. `open/high/low/close > 0` 확인.
10. `high >= max(open, close)` 확인.
11. `low <= min(open, close)` 확인.
12. `volume >= 0` 확인.
13. `listed_shares > 0` 확인.
14. `free_float_shares > 0` 확인.
15. `market_cap_krw > 0` 확인.
16. `institution_net_buy_krw`는 음수 허용.
17. `earnings.xlsx` 항목명이 `당기순이익(지배)`를 포함하지 않으면 warning이 아니라 hard fail.
18. `market_cap_calc = close * listed_shares`와 `market_cap_krw` 차이를 계산한다. 수정주가 사용 때문에 불일치가 날 수 있으므로 hard fail하지 않고 `mcap_error_p50`, `mcap_error_p95`를 기록한다.
19. `index.parquet`에 `kospi_open`, `kospi_close`, `kosdaq_open`, `kosdaq_close`가 없으면 실패한다.
20. M 계산용 `ticker -> market` mapping이 없으면 실패한다.

## 7. CANSLIM strict rule

모든 항목이 pass여야 entry 후보가 된다.

### C: Current Quarterly Earnings

```text
C_pass = latest comparable quarter EPS YoY >= 25%
```

계산:
- `eps = net_income_control / avg_listed_shares`
- 같은 회계분기 전년 동기와 비교한다.
- 현재 EPS와 전년 동기 EPS가 모두 `> 0`이어야 한다.
- 음수에서 양수 전환은 `turnaround_flag=True`로 기록하지만 pass는 아니다.

### A: Annual Earnings Growth

```text
A_pass = 3-year annual EPS CAGR >= 25%
```

계산:
- 연간 EPS는 해당 fiscal year 4개 분기 `net_income_control` 합계 / 해당 연도 평균 상장주식수.
- 최근 연간 EPS, y-1, y-2, y-3 EPS가 모두 `> 0`이어야 한다.
- 최근 연간 EPS가 y-1보다 커야 한다.

### N: New

새 요구사항에서 N 제거 언급이 없으므로 strict CANSLIM에는 포함한다.

```text
N_pass = close_t >= 0.90 * max(close_{t-252:t-1})
```

### S: Supply and Demand

`req.md`의 확장 S를 사용한다.

```text
VolShock = volume_t / avg(volume_{t-50:t-1})
VolumeSpike10 = volume_t / avg(volume_{t-10:t-1})

S_pass = VolShock >= 1.5
         and VolumeSpike10 >= 2.0
```

### L: Leader

```text
R252 = close_t / close_{t-252} - 1
RSPercentile = cross-sectional percentile of R252 on date t
RSLine = close / matched_index_close
RSLineSlope63 = linear slope of RSLine over last 63 trading days

L_pass = RSPercentile >= 90 and RSLineSlope63 > 0
```

KOSPI 종목은 KOSPI index, KOSDAQ 종목은 KOSDAQ index를 사용한다.

### I: Institutional Sponsorship

```text
f_inst = institution_net_buy_krw / market_cap_krw
InstFlow63 = sum(f_inst over last 63 trading days)
CumFlow126 = cumulative f_inst over last 126 trading days
beta = linear slope of CumFlow126

I_pass = InstFlow63 > 0 and beta > 0
```

시장가치 단위:
- `기관.xlsx`는 만원 단위이므로 `institution_net_buy_krw = raw * 10_000`.
- `상장주식수와시총.xlsx` 시가총액은 백만원 단위이므로 `market_cap_krw = raw * 1_000_000`.

### M: Market Direction

```text
M_pass = index_close > MA20 for last 5 consecutive sessions
         and index_close > MA50
         and MA50 > MA200
```

KOSPI/KOSDAQ 매칭은 `market` 컬럼을 사용한다.

## 8. Turtle rule

신호 계산은 `t`일 종가 데이터가 모두 확정된 뒤 수행한다.

### Entry

System 1:

```text
S1_entry = high_t > max(high_{t-20:t-1})
```

System 2:

```text
S2_entry = high_t > max(high_{t-55:t-1})
```

기본 entry:
- `S2_entry`가 primary다.
- `S1_entry`만 발생한 종목도 기록은 하지만 기본 백테스트 entry 후보에는 포함하지 않는다.
- 설정값 `allow_s1_entry=True`를 둬서 실험 가능하게 하되 기본은 `False`.

### Exit

System 2 보유 종목:

```text
Turtle_exit = low_t < min(low_{t-20:t-1})
              or close_t / entry_avg_price - 1 <= -0.08
```

System 1 보유 종목:

```text
Turtle_exit = low_t < min(low_{t-10:t-1})
              or close_t / entry_avg_price - 1 <= -0.08
```

### ATR/N

OHLC가 있으므로 True Range 기반 N을 사용한다.

```text
TR_t = max(high_t - low_t, abs(high_t - close_{t-1}), abs(close_{t-1} - low_t))
N_t = EMA20(TR_t)
```

Stop:

```text
StopPrice = last_add_price - 2 * N_at_entry
Stop_exit = close_t <= StopPrice
            or close_t / entry_avg_price - 1 <= -0.08
```

익절:
- 고정 수익률 익절은 사용하지 않는다.
- 이익 실현은 Turtle channel exit 또는 pyramiding 후 trailing stop으로만 발생한다.
- 이 결정은 trend-following 모델과 충돌하지 않기 위한 기본값이다.

Market kill switch:

```text
if M_pass is False for 5 consecutive trading days:
    no new entries
    exit all open positions on next open
```

Portfolio drawdown kill switch:

```text
if drawdown <= -15%:
    no new entries
    reduce or exit existing positions on next open
    resume only after drawdown > -10% and M_pass True
```

기본 구현은 간결성을 위해 `drawdown <= -15%`이면 전량 청산으로 시작한다. 50% 감축은 별도 실험 옵션으로 둔다.

## 9. 모델 1: CANSLIM + Turtle

경로:
- `stock_screener/models/canslim_turtle.py`

입력:
- `daily_panel`
- `fundamentals_pit`
- model config

Entry 후보:

```text
candidate = C & A & N & S & L & I & M & S2_entry
```

비중:
- Turtle risk sizing 사용.
- `risk_per_trade = 0.01`
- `max_total_open_risk = 0.10`
- `max_positions = floor(max_total_open_risk / risk_per_trade) = 10`
- `shares_unit = floor(equity * risk_per_trade / (2 * N_at_entry))`
- `position_value = shares_unit * open_{t+1}`
- `position_value_cap = 20% * equity`
- `ADV20 = avg(trading_value_{t-20:t-1})`
- `order_value = min(position_value, position_value_cap, cash_available, 10% * ADV20)`
- `gross_exposure <= 100%`
- 후보가 많으면 `RankScore` 순서로 예산을 배정한다.
- `order_value` 적용 후 1주 미만이면 주문하지 않는다.

RankScore:

```text
RankScore = 0.35 * RSPercentile
            + 0.20 * rank_pct(EPS_YoY)
            + 0.15 * rank_pct(A_CAGR)
            + 0.15 * rank_pct(InstFlow63)
            + 0.10 * rank_pct(VolShock)
            + 0.05 * rank_pct(VolumeSpike10)
```

Exit:
- Turtle channel exit.
- 2N stop.
- 개별 종목 수익률 `-8%` stop.
- M kill switch.
- drawdown kill switch.

## 10. 모델 2: CANSLIM + Turtle + Macro Rank

경로:
- `stock_screener/models/canslim_turtle_macro.py`

입력:
- 모델 1과 동일한 strict CANSLIM/Turtle 후보.
- macro sector scores.

Entry 후보:

```text
candidate = C & A & N & S & L & I & M & S2_entry
```

즉, 필터링 조건은 모델 1과 완전히 동일하다.

Macro score:

```text
macro_score_i = stage1_sector_score(sector_i)
macro_rank_i = rank descending macro_score among current candidates
```

DART 공시:
- 기본 제외.
- `dart_disclosure_score`는 계산하지 않는다.
- `combined_macro_dart_score` 필드를 만들지 않는다.
- daily output에는 `macro_score`, `macro_rank`, `macro_channel_states`, `macro_source`만 표시한다.

비중:
- Turtle risk sizing과 Turtle position cap을 사용하지 않는다.
- `position_value <= 10% * equity` 같은 Turtle cap도 모델 2에서는 적용하지 않는다.
- 단, long-only, 현금 음수 금지, 1주 단위 주문, gross exposure 100% 제한은 회계상 필수 제약으로 유지한다.

기본 macro weight:

```text
raw_weight_i = 1 / macro_rank_i
target_weight_i = raw_weight_i / sum(raw_weight)
```

동점 처리:
1. `macro_score` desc
2. `RSPercentile` desc
3. `RankScore` desc
4. `code` asc

Macro score가 모두 동일하면 equal weight로 fallback한다.

Exit:
- 모델 1과 동일하다.
- Macro rank가 하락했다는 이유만으로 강제 청산하지 않는다.
- 기존 보유 종목은 Turtle/channel/stop/kill switch로만 청산한다.
- 신규 매수와 추가 매수의 우선순위만 macro rank가 결정한다.

## 11. 백테스트 엔진

경로:
- `stock_screener/backtest.py`

이벤트 순서:

```text
For each trading date t except final date:
  1. t close 기준 equity mark-to-market
  2. t까지 사용 가능한 PIT fundamentals/macro/index/flow feature만 사용
  3. open positions의 exit signal 계산
  4. t+1 open에 exit order 체결
  5. t close 기준 entry candidates 계산
  6. 모델별 sizing/weighting 적용
  7. t+1 open에 entry order 체결
  8. cash, positions, orders, trades, equity_curve 저장
```

체결:
- `SLIPPAGE_BPS = 0`으로 hardcoding한다.
- `COMMISSION_RATE = 0.00015`로 hardcoding한다. 매수/매도 각각 0.015%다.
- 매수 체결가: `open_{t+1}`.
- 매도 체결가: `open_{t+1}`.
- 매수 현금 차감액: `shares * open_{t+1} * (1 + COMMISSION_RATE)`.
- 매도 현금 유입액: `shares * open_{t+1} * (1 - COMMISSION_RATE)`.

상폐/가격 missing:
- 상폐 종목에 보유 비중이 있으면 해당 포지션 수익률을 `-100%`로 확정한다.
- 상폐 처리일의 평가 가격은 `0`으로 설정하고, trade reason은 `delisted`로 기록한다.
- 1~5 거래일 가격 missing: valuation은 마지막 가격 carry, 신규 매수 금지, 매도 체결 불가.
- 6 거래일 이상 missing: stale position flag.
- 단순 가격 누락과 상폐는 구분한다. 상폐는 optimistic/conservative 시나리오 없이 항상 `-100%`다.

성과지표:
- CAGR
- annual volatility
- Sharpe
- Sortino
- Max drawdown
- Calmar
- VaR 95
- Expected Shortfall 95
- win rate
- profit factor
- average holding period
- turnover
- average positions
- average gross exposure
- cash drag
- fees
- slippage
- total trades

Benchmark:
- KOSPI buy-and-hold
- KOSDAQ buy-and-hold
- KOSPI/KOSDAQ 50:50
- equal-weight 전체 유니버스
- CANSLIM-only monthly rebalance
- Turtle-only
- CANSLIM + Turtle
- CANSLIM + Turtle + Macro Rank

Model selection:

```text
primary = CAGR
tie1 = lower max_drawdown
tie2 = higher Sharpe
tie3 = lower turnover
tie4 = lower data_quality_error_count
```

선택 결과 저장:
- `data/results/backtest/comparison.json`
- `data/results/backtest/selected_model.json`

## 12. Daily filtering / GitHub Pages

경로:
- `stock_screener/daily.py`
- `.github/workflows/daily-pages.yml`

Daily run 목적:
- 백테스트를 매일 다시 돌리지 않는다.
- 이미 선택된 모델로 당일 장 마감 후 최신 필터링 결과만 생성한다.

입력:
- `data/results/backtest/selected_model.json`
- 기존 `data/processed/*.parquet`
- 장 마감 후 incremental daily data.
- macro live data 또는 cached macro state.

Daily data 수집:
1. Kiwoom 또는 기타 소스에서 KOSPI/KOSDAQ 종목 OHLCV 최신 일봉 수집.
2. 기관 순매수대금 최신 일봉 수집.
3. KOSPI/KOSDAQ index OHLCV 최신 일봉 수집.
4. macro source 수집:
   - ECOS/FRED/KOSIS가 가능하면 live macro state 계산.
   - 실패하면 직전 macro state cache 사용.
   - DART 공시는 daily macro rank 모델에서 사용하지 않는다.
5. DART 재무는 분기/사업보고서 발표 이후만 갱신한다.

Daily output:

`public/results/screener_results.json`

```json
{
  "generated_at": "...",
  "selected_model": "canslim_turtle|canslim_turtle_macro",
  "model_selection": {
    "source": "data/results/backtest/selected_model.json",
    "selected_at": "...",
    "selection_metric": "cagr"
  },
  "summary": {
    "universe_count": 0,
    "evaluated_count": 0,
    "strict_canslim_pass_count": 0,
    "turtle_entry_count": 0,
    "final_candidate_count": 0
  },
  "macro": {
    "enabled": true,
    "source": "ecos_fred_live|cache|manual",
    "channel_states": {"G": 0, "IC": 0, "FC": 0, "ED": 0, "FX": 0},
    "warnings": []
  },
  "candidates": [],
  "exit_signals": [],
  "fail_reasons": {},
  "data_quality": {
    "alerts": [],
    "errors": []
  }
}
```

Frontend:
- 선택 모델 표시.
- strict CANSLIM pass 종목 표시.
- Turtle S2 entry 종목 표시.
- Macro model 선택 시 macro score/rank/target weight 표시.
- exit signal 표시.
- fail reason 표시.
- data quality 표시.

## 13. GitHub Actions

### 13.1 `daily-pages.yml`

목적:
- 매일 장 마감 후 최신 필터링 결과를 Pages에 배포.

Schedule:

```yaml
schedule:
  - cron: "30 9 * * 1-5"  # 18:30 KST
```

Inputs:
- `workflow_dispatch.market = KR`
- `workflow_dispatch.limit`
- `workflow_dispatch.skip_deploy`
- `workflow_dispatch.model = selected|canslim_turtle|canslim_turtle_macro`

Steps:
1. checkout
2. setup Python
3. install dependencies
4. restore `data/processed`, `data/results/backtest/selected_model.json`, live cache
5. run `python -m stock_screener.cli daily --model selected`
6. validate `public/results/screener_results.json`
7. build `_site`
8. upload Pages artifact
9. deploy Pages

Permissions:

```yaml
permissions:
  contents: read
  pages: write
  id-token: write
```

Secrets/vars:
- `KIWOOM_APP_KEY`
- `KIWOOM_SECRET_KEY`
- `DART_API_KEY`
- `ECOS_API_KEY`
- `FRED_API_KEY`
- optional `KOSIS_API_KEY`
- optional `KOSIS_EXPORTS_US_USER_STATS_ID`
- `KIWOOM_ENV`

### 13.2 `backtest.yml`

목적:
- 수동 백테스트와 모델 비교.
- Pages 배포 없음.

Trigger:

```yaml
workflow_dispatch:
  inputs:
    start:
      default: "2010-01-01"
    end:
      default: ""
    models:
      default: "all"
    limit:
      default: ""
```

Steps:
1. checkout
2. setup Python
3. install dependencies
4. run `python -m stock_screener.cli normalize`
5. run `python -m stock_screener.cli backtest --model canslim_turtle`
6. run `python -m stock_screener.cli backtest --model canslim_turtle_macro`
7. run `python -m stock_screener.cli compare`
8. upload `data/results/backtest` as artifact

## 14. CLI 명령

```bash
python -m stock_screener.cli normalize \
  --data-dir data \
  --output-dir data/processed

python -m stock_screener.cli collect-macro \
  --as-of 2026-06-02 \
  --output data/processed/macro_states.parquet

python -m stock_screener.cli backtest \
  --model canslim_turtle \
  --start 2010-01-01 \
  --end 2026-06-02

python -m stock_screener.cli backtest \
  --model canslim_turtle_macro \
  --start 2010-01-01 \
  --end 2026-06-02

python -m stock_screener.cli compare

python -m stock_screener.cli daily \
  --model selected \
  --as-of 2026-06-02 \
  --output public/results/screener_results.json
```

## 15. 구현 순서

### Phase 0: 안전 정리

1. 현재 브랜치와 git 상태를 기록한다.
2. `data/`, `.env`는 절대 삭제하지 않는다.
3. `data_cache/`, `logs/`, `_site/`, `results/`, `trading/results`는 삭제 후보로만 표시하고 즉시 삭제하지 않는다.

완료 기준:
- `docs/refactor_plan.md` 존재.
- 삭제 전후 목록이 plan에 반영됨.

### Phase 1: 새 패키지 scaffold

생성:
- `stock_screener/__init__.py`
- `stock_screener/config.py`
- `stock_screener/data.py`
- `stock_screener/rules.py`
- `stock_screener/backtest.py`
- `stock_screener/daily.py`
- `stock_screener/cli.py`
- `stock_screener/models/__init__.py`
- `stock_screener/models/canslim_turtle.py`
- `stock_screener/models/canslim_turtle_macro.py`

완료 기준:
- `python -m stock_screener.cli --help` 실행 가능.

### Phase 2: Data normalize

구현:
- XLSX wide parser.
- `price/open/high/low/amount/inst/earnings/stock/유동주식수` loader.
- daily panel parquet 생성.
- fundamentals PIT parquet 생성.
- QA report JSON 생성.

완료 기준:
- `python -m stock_screener.cli normalize` 성공.
- `data/processed/daily_panel.parquet` 존재.
- `data/processed/fundamentals_pit.parquet` 존재.
- QA hard fail 0개.

### Phase 3: Rules

구현:
- C/A/N/S/L/I/M strict 계산.
- S1/S2 entry/exit.
- ATR/N.
- RankScore.
- Macro channel state와 sector score.

완료 기준:
- `tests/test_rules.py` 통과.
- fixture에서 각 CANSLIM 항목의 pass/fail이 기대값과 일치.

### Phase 4: Model 1

구현:
- `models/canslim_turtle.py`
- strict candidate 생성.
- Turtle risk sizing.
- exits.

완료 기준:
- fixture backtest에서 entry가 `t+1 open`에 체결됨.
- S2 exit, 2N stop, M kill switch 테스트 통과.

### Phase 5: Model 2

구현:
- `models/canslim_turtle_macro.py`
- Model 1과 동일 후보 필터.
- macro rank inverse weight.
- Turtle position cap 미적용.
- long-only/gross exposure/cash 제약만 유지.

완료 기준:
- 같은 후보 집합에서 Model 1과 Model 2의 sizing이 다름.
- Model 2에서 종목별 10% cap이 적용되지 않음.
- gross exposure는 100%를 넘지 않음.

### Phase 6: Backtest compare

구현:
- metrics.
- benchmarks.
- 상폐 포지션 `-100%` 처리.
- 단순 가격 missing 처리.
- model comparison.
- selected model 저장.

완료 기준:
- 두 모델 결과 폴더 생성.
- `comparison.json` 생성.
- `selected_model.json` 생성.

### Phase 7: Daily Pages

구현:
- `daily.py`
- `public/script.js` schema 변경.
- `public/index.html` 필요한 컬럼 반영.
- `daily-pages.yml`.

완료 기준:
- `python -m stock_screener.cli daily --model selected` 성공.
- `public/results/screener_results.json` schema 검증 성공.
- 로컬 정적 서버에서 콘솔 에러 없음.

### Phase 8: Workflow 분리

구현:
- 기존 `.github/workflows/screen-and-deploy.yml` 삭제.
- `.github/workflows/daily-pages.yml` 생성.
- `.github/workflows/backtest.yml` 생성.

완료 기준:
- `daily-pages.yml`에는 backtest step이 없음.
- `backtest.yml`에는 Pages deploy step이 없음.

### Phase 9: 기존 코드 제거

삭제:
- `src/`
- `trading/`
- legacy `results/`
- `_site/`
- `logs/`
- `.cache/`
- `docs_cache/`

조건:
- Phase 1~8 테스트 통과 후 삭제.

완료 기준:
- `rg "from canslim|from turtle|trading\\.|src/main.py|market_providers" stock_screener tests public .github` 결과가 없음.

## 16. 테스트 계획

단위 테스트:
- XLSX row 1~6 header parsing.
- Excel serial date 변환.
- `ohl.xlsx`, `상장주식수와시총.xlsx` 다중 item 분리.
- PIT available date lag.
- C/A/N/S/L/I/M 계산.
- Turtle S2 entry, S2 exit, ATR/N.
- Macro channel state 분류.
- Macro sector score.
- Model 2 no Turtle cap.

통합 테스트:
- `normalize -> backtest model1 -> backtest model2 -> compare` fixture run.
- `daily --model canslim_turtle`.
- `daily --model canslim_turtle_macro`.
- `daily --model selected`.

명령:

```bash
python -m pytest
python -m stock_screener.cli normalize --data-dir data --output-dir data/processed
python -m stock_screener.cli backtest --model canslim_turtle --start 2015-01-01 --end 2020-12-31
python -m stock_screener.cli backtest --model canslim_turtle_macro --start 2015-01-01 --end 2020-12-31
python -m stock_screener.cli compare
python -m stock_screener.cli daily --model selected
```

## 17. 완료 판정

리팩토링 완료는 아래가 모두 참일 때만 인정한다.

1. 새 패키지 `stock_screener/`만 core Python 코드로 남아 있다.
2. 백테스트와 Pages daily workflow가 분리되어 있다.
3. `CANSLIM + Turtle` 모델이 strict all-pass로 작동한다.
4. `CANSLIM + Turtle + Macro Rank` 모델이 별도 path로 작동한다.
5. Macro Rank 모델은 DART 공시를 사용하지 않는다.
6. Macro Rank 모델은 Turtle position cap을 적용하지 않는다.
7. 두 모델 결과와 comparison이 생성된다.
8. selected model 기준 daily Pages JSON이 생성된다.
9. Frontend가 새 JSON schema를 렌더링한다.
10. `python -m pytest`가 통과한다.
11. `daily-pages.yml`은 Pages deploy만 담당하고 backtest를 실행하지 않는다.
12. `backtest.yml`은 artifact upload만 담당하고 Pages deploy를 실행하지 않는다.
