# 0. 결론

현재 수집한 데이터로 **CANSLIM + Turtle long-only 한국 주식 백테스트 v0**는 가능하다. 다만 **오리지널에 최대한 가깝게** 하려면 현재 데이터에는 4개 결손이 있다.

| 결손             | 영향                                                             | 대응                                                |
| -------------- | -------------------------------------------------------------- | ------------------------------------------------- |
| 개별종목 OHLC 부재   | Turtle 원형의 Donchian high/low breakout, True Range, ATR/N 계산 불가 | v0는 종가 기반 proxy. production은 수정 시가/고가/저가/종가 추가 수집 |
| 재무제표 실제 발표일 부재 | C/A look-ahead bias 위험                                         | v0는 보수적 lag 적용. production은 공시일/발표일 수집            |
| 종목마스터 부재 가능성   | SPAC/ETF/우선주/리츠/시장구분 필터 불안정                                    | 종목명, 시장, 증권구분, 상장일, 상폐일, 업종 추가 권장                 |
| 매출액/ROE 부재     | CANSLIM 원형의 earnings quality proxy 약화                          | v0는 EPS 중심. v1에서 매출액, ROE 추가                      |

오닐식 CANSLIM은 C=최근 분기 EPS, A=연간 EPS 성장, N=신제품/신고가, S=수급과 float, L=상대강도, I=기관 sponsorship, M=시장 방향으로 구성된다. 특히 EPS 성장, price high, volume, float, institutional sponsorship, market direction이 핵심이다. ([Corporate Finance Institute][1])

Turtle 원형은 futures용 long/short 시스템이고, System 1은 20일 breakout, System 2는 55일 breakout, exit은 10일/20일 opposite breakout이다. 원형은 intraday high/low 돌파와 True Range 기반 (N)을 썼기 때문에, 현재 종가 데이터만으로는 “Turtle proxy”로 구현해야 한다. ([Business Insider][2])

---

# 1. 전략 정의

## 1.1 기본 구조

전략은 아래 순서로 구성한다.

[
\text{Universe}
\rightarrow
\text{CANSLIM Eligibility}
\rightarrow
\text{Turtle Entry}
\rightarrow
\text{Risk Sizing}
\rightarrow
\text{Turtle Exit}
\rightarrow
\text{Performance Attribution}
]

즉, **CANSLIM은 종목 필터**, **Turtle은 진입·청산·포지션 관리 엔진**이다.

---

# 2. 데이터 레이어 설계

## 2.1 입력 CSV

Codex는 모든 CSV를 아래 canonical schema로 변환해야 한다.

| 파일        | 필수 컬럼                            | 비고                         |
| --------- | -------------------------------- | -------------------------- |
| 수정주가      | `date, code, adj_close`          | 현금배당 포함 total-return close |
| 거래량       | `date, code, volume`             | 일별                         |
| 거래대금      | `date, code, trading_value`      | 일별 KRW                     |
| 기관순매수대금   | `date, code, inst_net_buy_value` | 일별 KRW                     |
| 당기순이익     | `period_end, code, net_income`   | 반드시 지배주주순이익인지 확인           |
| 상장주식수     | `date, code, listed_shares`      | 일별 또는 변동일                  |
| 유동주식수     | `date, code, free_float_shares`  | 일별/월별/변동일                  |
| 시가총액      | `date, code, market_cap`         | 일별 KRW                     |
| KOSPI 종가  | `date, kospi_close`              | 일별                         |
| KOSDAQ 종가 | `date, kosdaq_close`             | 일별                         |

CSV가 wide format이면 long format으로 변환한다.

예:

```text
date,005930,000660,035420
2010-01-04,...
```

을

```text
date,code,value
2010-01-04,005930,...
2010-01-04,000660,...
2010-01-04,035420,...
```

으로 변환한다.

---

## 2.2 추가 권장 CSV

현재 수집 데이터만으로 v0는 가능하지만, production-grade로 가려면 아래가 필요하다.

| 추가 데이터         | 이유                                           |
| -------------- | -------------------------------------------- |
| 종목마스터          | 우선주, ETF, ETN, SPAC, 리츠, KONEX 제거            |
| 시장구분           | KOSPI 종목은 KOSPI, KOSDAQ 종목은 KOSDAQ index와 매칭 |
| 업종분류           | CANSLIM L에서 leading industry proxy 가능        |
| 상장일/상폐일        | 생존편향 제거                                      |
| 거래정지/관리종목 여부   | 체결 가능성 반영                                    |
| 수정 시가/고가/저가/종가 | Turtle 원형 구현                                 |
| 재무제표 발표일       | C/A look-ahead 제거                            |
| 매출액            | C의 earnings quality 보조                       |
| ROE            | quality filter 보조                            |

---

# 3. 데이터 무결성 검증

Codex는 feature 계산 전 반드시 아래 검증을 수행한다.

## 3.1 코드/날짜 검증

```python
assert code is 6-digit string
assert date is trading date
assert no duplicate rows for (date, code, field)
assert adj_close > 0
assert volume >= 0
assert trading_value >= 0
assert listed_shares > 0
assert market_cap > 0
```

---

## 3.2 시가총액 QA

직접 계산한 시총과 DataGuide 시총을 비교한다.

[
MCap^{calc}_{i,t}
=================

Close_{i,t}
\times
ListedShares_{i,t}
]

[
MCapError_{i,t}
===============

\frac{
MCap^{calc}_{i,t}
-----------------

MCap^{DG}*{i,t}
}
{MCap^{DG}*{i,t}}
]

단, 수정주가로 시총을 계산하면 불일치가 날 수 있다. 수정주가는 수익률 계산용이고, 시총 계산에는 원주가가 더 적합하다. 현재 원주가가 없다면 이 검증은 “경고”만 띄우고 hard fail하지 않는다.

---

## 3.3 재무데이터 QA

중요하다. 네가 수집한 항목이 **당기순이익**인지 **당기순이익(지배)**인지 확인해야 한다.

EPS용 분자는 원칙적으로:

[
NI^{control}_{q}
]

이다.

만약 수집한 것이 연결 전체 당기순이익이면:

[
NI^{total}
==========

NI^{control}
+
NI^{noncontrolling}
]

이므로 EPS가 왜곡된다.

Codex는 컬럼명 또는 파일명에 `지배`, `control`, `controlling`이 없으면 warning을 발생시킨다.

---

# 4. Point-in-Time 재무데이터 처리

## 4.1 핵심 원칙

재무제표 값은 `period_end`에 바로 알 수 없다.

잘못된 방식:

[
2024Q4 \ EPS \text{를 } 2024-12-31 \text{부터 사용}
]

올바른 방식:

[
2024Q4 \ EPS \text{를 발표 가능일 이후부터 사용}
]

발표일 데이터가 없으므로 v0에서는 보수적 lag를 쓴다.

```yaml
fundamental_lag:
  q1_q3_calendar_days: 60
  q4_calendar_days: 120
```

즉:

[
available_date
==============

period_end + lag
]

그 후 다음 거래일로 보정한다.

```python
available_trading_date = next_trading_day(available_date)
```

---

## 4.2 daily panel merge

모든 trading date (t)에 대해 종목 (i)가 사용할 수 있는 최신 재무값만 붙인다.

[
F_{i,t}
=======

\max_{\tau \le t}
F_{i,\tau}^{available}
]

구현은 `merge_asof` 방식으로 한다.

절대 금지:

```python
quarter_end 기준으로 바로 forward-fill
```

---

# 5. CANSLIM feature 설계

## 5.1 C: Current Quarterly Earnings

원형은 최근 분기 EPS가 전년 동기 대비 강하게 증가하는지를 본다. AAII는 계절성을 피하려면 같은 회계분기끼리 비교해야 하며, 음수 EPS나 작은 base는 왜곡 가능성이 크다고 설명한다. ([AAII][3])

### 계산

[
EPS_{q,i}
=========

\frac{
NI^{control}*{q,i}
}{
Shares*{q,i}
}
]

여기서 `Shares_q`는 가능하면 해당 분기 평균 상장주식수다.

[
g^{EPS}_{q,i}
=============

\frac{
EPS_{q,i}
}{
EPS_{q-4,i}
}
-1
]

### 필터

[
C_{i,t}
=======

\mathbf{1}
\left[
EPS_{q,i} > 0
\right]
\land
\mathbf{1}
\left[
EPS_{q-4,i} > 0
\right]
\land
\mathbf{1}
\left[
g^{EPS}_{q,i} \ge 0.25
\right]
]

선택 강화:

[
g^{EPS}_{q,i}

>

g^{EPS}_{q-1,i}
]

즉, 성장률 가속 조건이다.

---

## 5.2 A: Annual Earnings Growth

오닐식 A는 단순 1년 호실적이 아니라 연간 EPS의 지속적 증가다. CFI는 최근 3년 EPS가 25% 이상 성장하는 것을 A 조건으로 설명하고, AAII는 최근 연간 EPS가 지속적으로 증가하는지를 본다. ([Corporate Finance Institute][1])

### 연간 EPS

분기 데이터만 있으므로 fiscal year별로 합산한다.

[
NI^{control}_{FY,y}
===================

\sum_{q \in y}
NI^{control}_{q}
]

[
EPS_{FY,y}
==========

\frac{
NI^{control}*{FY,y}
}{
AvgShares*{FY,y}
}
]

### 3년 CAGR

[
A_CAGR_{i,y}
============

\left(
\frac{
EPS_{FY,i,y}
}{
EPS_{FY,i,y-3}
}
\right)^{1/3}
-1
]

### 필터

[
A_{i,t}
=======

\mathbf{1}
\left[
A_CAGR_{i,y} \ge 0.25
\right]
\land
\mathbf{1}
\left[
EPS_{FY,i,y} > EPS_{FY,i,y-1}
\right]
\land
\mathbf{1}
\left[
EPS_{FY,i,y} > 0
\right]
\land
\mathbf{1}
\left[
EPS_{FY,i,y-1} > 0
\right]
\land
\mathbf{1}
\left[
EPS_{FY,i,y-2} > 0
\right]
\land
\mathbf{1}
\left[
EPS_{FY,i,y-3} > 0
\right]
]

v0에서는 완전한 5년 연속 증가 대신 3년 CAGR + 최근 연간 증가를 쓴다.

---

## 5.3 N: New

현재 데이터에는 신제품, 신사업, 신경영진 같은 정성 catalyst가 없다. 따라서 가격 기반 proxy를 사용한다. AAII도 N은 정성 요소라 스크리닝이 어렵지만, 신고가와 거래량 증가를 proxy로 볼 수 있다고 설명한다. ([AAII][3])

### proxy

[
High252_{i,t}
=============

\max_{k=1}^{252}
Close_{i,t-k}
]

[
N_{i,t}
=======

\mathbf{1}
\left[
Close_{i,t}
\ge
0.90 \times High252_{i,t}
\right]
]

Turtle entry가 55일 신고가 돌파를 요구하므로, 실제 진입 시점에서는 N이 자연스럽게 강화된다.

---

## 5.4 S: Supply and Demand

S는 단순 거래량 spike가 아니라 **수요 증가 + 제한된 공급**이다. CFI는 가격 상승 중 높은 거래량과 작은 float를 S의 핵심으로 설명한다. ([Corporate Finance Institute][1])

### 거래량 shock

[
VolShock_{i,t}
==============

\frac{
Volume_{i,t}
}{
AvgVolume_{i,t-1}^{50}
}
]

[
S^{vol}_{i,t}
=============

\mathbf{1}
\left[
VolShock_{i,t} \ge 1.5
\right]
]

### 거래대금 유동성

[
ADV20_{i,t}
===========

AvgTradingValue^{20}_{i,t-1}
]

[
S^{liq}_{i,t}
=============

\mathbf{1}
\left[
ADV20_{i,t} \ge 2,000,000,000
\right]
]

기본값은 **20일 평균 거래대금 20억 원 이상**으로 둔다.

### float turnover

[
FloatTurnover_{i,t}
===================

\frac{
Volume_{i,t}
}{
FreeFloatShares_{i,t}
}
]

[
S^{float}_{i,t}
===============

\mathbf{1}
\left[
FloatTurnover_{i,t} \ge 0.003
\right]
]

### 최종 S

[
S_{i,t}
=======

S^{vol}*{i,t}
\land
S^{liq}*{i,t}
\land
S^{float}_{i,t}
]

---

## 5.5 L: Leader or Laggard

CANSLIM은 laggard가 아니라 leader를 산다. CFI는 상대 가격강도 80 이상을 기준으로 설명하고, AAII도 상대강도를 market leader 식별에 사용한다고 설명한다. ([Corporate Finance Institute][1])

### 12개월 상대수익률

[
R_{252,i,t}
===========

\frac{
Close_{i,t}
}{
Close_{i,t-252}
}
-1
]

시장별 cross-section percentile:

[
RSPercentile_{i,t}
==================

RankPct_{cross-section}
\left(
R_{252,i,t}
\right)
]

### RS line

KOSPI 종목:

[
RSLine_{i,t}
============

\frac{
Close_{i,t}
}{
KOSPI_t
}
]

KOSDAQ 종목:

[
RSLine_{i,t}
============

\frac{
Close_{i,t}
}{
KOSDAQ_t
}
]

[
RSLineSlope_{63,i,t}
====================

Slope
\left(
RSLine_{i,t-62:t}
\right)
]

### 최종 L

[
L_{i,t}
=======

\mathbf{1}
\left[
RSPercentile_{i,t} \ge 0.90
\right]
\land
\mathbf{1}
\left[
RSLineSlope_{63,i,t} > 0
\right]
]

---

## 5.6 I: Institutional Sponsorship

오닐식 I는 기관 보유/후원 증가다. CFI는 기관투자자 매수 증가를 I의 핵심으로 설명한다. ([Corporate Finance Institute][1])

한국 DataGuide에서는 기관 보유자 수보다 일별 기관 순매수대금이 현실적이다. 단, raw 기관 순매수대금은 대형주 편향이 크므로 시가총액으로 정규화한다.

[
f^{inst}_{i,t}
==============

\frac{
InstNetBuyValue_{i,t}
}{
MarketCap_{i,t}
}
]

### 최근 3개월 순매수

[
InstFlow63_{i,t}
================

\sum_{k=0}^{62}
f^{inst}_{i,t-k}
]

### 6개월 누적 flow slope

먼저 126거래일 누적 series를 만든다.

[
CumFlow_{i,\tau}
================

\sum_{k=t-125}^{\tau}
f^{inst}_{i,k}
]

[
CumFlow_{i,\tau}
================

\alpha_i
+
\beta_i \tau
+
\epsilon_{i,\tau}
]

### 최종 I

[
I_{i,t}
=======

\mathbf{1}
\left[
InstFlow63_{i,t} > 0
\right]
\land
\mathbf{1}
\left[
\beta_i > 0
\right]
]

강화 버전:

[
I_{i,t}
=======

\mathbf{1}
\left[
InstFlow63_{i,t} > 0
\right]
\land
\mathbf{1}
\left[
\beta_i > 0
\right]
\land
\mathbf{1}
\left[
t_{\beta_i} > 1.0
\right]
]

---

## 5.7 M: Market Direction

오닐식 M은 시장 방향 필터다. AAII는 O’Neil이 시장 상승 국면에서만 투자하는 것을 중시했다고 설명한다. ([AAII][3])

현재는 index close만 있으므로 volume-based distribution day는 구현하지 않는다.

KOSPI 종목은 KOSPI M, KOSDAQ 종목은 KOSDAQ M을 쓴다.

[
M_t
===

\mathbf{1}
\left[
IndexClose_t > MA20_t
\right]_{5일연속}
\land
\mathbf{1}
\left[
IndexClose_t > MA50_t
\right]
\land
\mathbf{1}
\left[
MA50_t > MA200_t
\right]
]

구현:

```python
m_20 = (idx_close > idx_close.rolling(20).mean())
m_5days = m_20.rolling(5).sum() == 5
m = m_5days & (idx_close > ma50) & (ma50 > ma200)
```

---

# 6. CANSLIM 최종 필터

## 6.1 Strict mode

[
CANSLIM_{i,t}
=============

C_{i,t}
\land
A_{i,t}
\land
N_{i,t}
\land
S_{i,t}
\land
L_{i,t}
\land
I_{i,t}
\land
M_{i,t}
]

Strict mode는 종목 수가 너무 적을 수 있다.

---

## 6.2 Score mode

한국 시장에서는 score mode도 같이 구현한다.

[
Score_{i,t}
===========

0.20 CScore
+
0.15 AScore
+
0.10 NScore
+
0.15 SScore
+
0.20 LScore
+
0.10 IScore
+
0.10 MScore
]

기본 진입 후보:

[
Score_{i,t} \ge P90(Score_t)
]

단, 아래 hard filter는 반드시 유지한다.

[
C=1,\quad A=1,\quad L=1,\quad M=1,\quad ADV20 \ge 20억
]

즉:

```text
hard gate: C & A & L & M & liquidity
ranking: N, S, I strength
```

---

# 7. Turtle Trading overlay

## 7.1 원형과 v0 proxy 구분

Turtle 원형은 가격이 20일 또는 55일 high/low를 intraday로 돌파할 때 진입한다. System 1은 20일 breakout, System 2는 55일 breakout이며, System 2는 이전 breakout 승패와 무관하게 신호를 취한다. ([Oxfordstrat][4])

현재는 고가/저가가 없으므로 v0는 종가 breakout으로 구현한다.

---

## 7.2 System 1

[
Entry^{S1}_{i,t}
================

\mathbf{1}
\left[
Close_{i,t}

>

\max_{k=1}^{20}
Close_{i,t-k}
\right]
]

[
Exit^{S1}_{i,t}
===============

\mathbf{1}
\left[
Close_{i,t}
<
\min_{k=1}^{10}
Close_{i,t-k}
\right]
]

---

## 7.3 System 2

[
Entry^{S2}_{i,t}
================

\mathbf{1}
\left[
Close_{i,t}

>

\max_{k=1}^{55}
Close_{i,t-k}
\right]
]

[
Exit^{S2}_{i,t}
===============

\mathbf{1}
\left[
Close_{i,t}
<
\min_{k=1}^{20}
Close_{i,t-k}
\right]
]

기본 백테스트는 **System 2**를 primary로 둔다. 한국 개별주에서는 20일 breakout이 너무 whipsaw가 많을 가능성이 높다.

---

## 7.4 Turtle (N)

원형 Turtle (N)은 20일 EMA 형태의 True Range이며, True Range는 (H-L), (H-PDC), (PDC-L)의 최대값이다. ([Oxfordstrat][4])

OHLC가 있을 때:

[
TR_t
====

\max
\left(
H_t-L_t,
|H_t-C_{t-1}|,
|C_{t-1}-L_t|
\right)
]

[
N_t
===

\frac{19 \times N_{t-1} + TR_t}{20}
]

현재 v0에서는 close-only proxy를 쓴다.

[
N^{proxy}_{i,t}
===============

EMA_{20}
\left(
|Close_{i,t}-Close_{i,t-1}|
\right)
]

또는 log-return 기반:

[
N^{proxy}_{i,t}
===============

Close_{i,t}
\times
EMA_{20}
\left(
|\log(Close_{i,t}/Close_{i,t-1})|
\right)
]

v0 기본값은 두 번째다.

---

# 8. 진입 규칙

기본 진입 조건:

[
Entry_{i,t}
===========

CANSLIM_{i,t}
\land
Entry^{S2}_{i,t}
]

Score mode에서는:

[
Entry_{i,t}
===========

HardGate_{i,t}
\land
Score_{i,t} \ge P90(Score_t)
\land
Entry^{S2}_{i,t}
]

신호는 (t)일 종가 기준으로 계산하고, 체결은 (t+1)일 종가에 한다.

[
FillPrice_{i,t+1}
=================

Close_{i,t+1}
\times
(1 + SlippageBuy)
]

이렇게 해야 당일 종가 신호를 당일 종가에 매수하는 look-ahead가 사라진다.

---

# 9. 포지션 사이징

## 9.1 기본 원칙

Turtle 원형은 (N)을 이용해 volatility-adjusted unit을 만들었고, 1 (N) 움직임이 계좌 equity의 1%가 되도록 unit size를 정했다. 또한 stop은 2 (N) 기준이었다. ([Oxfordstrat][4])

한국 주식 long-only에서는 gap risk가 크므로 원형보다 보수적으로 간다.

---

## 9.2 v0 기본 사이징

[
RiskPerTrade = 0.005
]

즉, 초기 unit당 계좌의 0.5%를 risk로 둔다.

[
StopDistance_{i,t}
==================

2N_{i,t}
]

[
SharesUnit_{i,t}
================

\left\lfloor
\frac{
Equity_t \times RiskPerTrade
}{
2N_{i,t}
}
\right\rfloor
]

포지션 금액 제한:

[
PositionValue_{i,t}
\le
0.10 \times Equity_t
]

거래대금 제한:

[
OrderValue_{i,t}
\le
0.05 \times ADV20_{i,t}
]

최종 주식 수:

[
Shares_{i,t}
============

\min
\left(
SharesUnit,
\frac{0.10 Equity}{FillPrice},
\frac{0.05 ADV20}{FillPrice}
\right)
]

---

## 9.3 pyramiding

Turtle 원형은 initial unit 이후 (0.5N)마다 추가하고, 최대 4 units까지 쌓았다. ([Oxfordstrat][4])

v0 구현:

```text
max_units_per_name = 4
add_unit_trigger = last_add_price + 0.5 * N_at_entry
```

추가 조건:

```text
M == True
position is profitable
no liquidity cap violation
portfolio exposure < 100%
```

추가 매수 후 stop은 아래로 갱신한다.

[
StopPrice
=========

## LastAddPrice

2N_{entry}
]

---

# 10. 청산 규칙

## 10.1 Turtle exit

System 2 기준:

[
Exit^{channel}_{i,t}
====================

Close_{i,t}
<
\min_{k=1}^{20}
Close_{i,t-k}
]

---

## 10.2 N-stop

[
Exit^{stop}_{i,t}
=================

Close_{i,t}
\le
EntryPrice_i - 2N_i
]

pyramiding 후:

[
Exit^{stop}_{i,t}
=================

Close_{i,t}
\le
LastAddPrice_i - 2N_i
]

---

## 10.3 Market kill switch

[
Exit^{market}_{i,t}
===================

\neg M_t
\quad
\text{for 5 consecutive trading days}
]

---

## 10.4 Portfolio drawdown kill switch

[
DD_t
====

\frac{
Equity_t
}{
\max_{\tau \le t} Equity_{\tau}
}
-1
]

[
KillSwitch_t
============

\mathbf{1}
\left[
DD_t \le -0.15
\right]
]

Kill switch 발동 시:

```text
1. 신규 진입 중단
2. 기존 포지션 50% 감축
3. DD가 -10% 이상으로 회복되고 M=True가 될 때까지 신규 진입 금지
```

---

# 11. 체결 비용과 슬리피지

거래비용은 연도별·시장별로 parameter table로 관리해야 한다. 한국 증권거래세는 시기별로 변동해 왔고, 2025년 이후 개정 논의/변경도 있었으므로 단일 세율을 전체 기간에 hard-code하면 백테스트가 왜곡된다. ([shinkim.com][5])

기본 설정:

```yaml
costs:
  commission_bps_buy: 1.5
  commission_bps_sell: 1.5
  slippage_base_bps: 10.0
  impact_bps_multiplier: 50.0
  max_slippage_bps: 100.0
```

시장충격:

[
Participation
=============

\frac{
OrderValue
}{
ADV20
}
]

[
SlippageBps
===========

\min
\left(
100,
10
+
50 \sqrt{Participation}
\right)
]

매수 체결가:

[
BuyFill
=======

Close_{t+1}
\times
\left(
1+
\frac{SlippageBps}{10000}
\right)
]

매도 체결가:

[
SellFill
========

Close_{t+1}
\times
\left(
1-
\frac{SlippageBps}{10000}
\right)
]

---

# 12. 백테스트 엔진 구조

## 12.1 이벤트 순서

매 거래일 (t):

```text
1. 현재 보유 포지션 mark-to-market
2. t일까지 사용 가능한 feature 계산
3. exit signal 계산
4. exit order 생성
5. entry candidate 계산
6. candidate ranking
7. risk budget / liquidity cap 적용
8. entry order 생성
9. 모든 order는 t+1 close에 체결
10. trades, positions, cash, equity 저장
```

---

## 12.2 상태 스키마

### Position state

```python
Position:
    code: str
    entry_date: date
    entry_price: float
    shares: int
    units: int
    system_id: str              # S1 or S2
    n_at_entry: float
    last_add_price: float
    stop_price: float
    max_price_since_entry: float
    realized_pnl: float
    unrealized_pnl: float
    status: str                 # OPEN / CLOSED
```

### Portfolio state

```python
Portfolio:
    date: date
    cash: float
    equity: float
    gross_exposure: float
    net_exposure: float
    open_positions: dict[str, Position]
    pending_orders: list[Order]
    peak_equity: float
    drawdown: float
    kill_switch: bool
```

### Order state

```python
Order:
    signal_date: date
    execution_date: date
    code: str
    side: str                   # BUY / SELL
    shares: int
    reason: str                 # ENTRY / EXIT_CHANNEL / EXIT_STOP / EXIT_MARKET / KILL
    expected_price: float
    fill_price: float | None
    status: str                 # PENDING / FILLED / CANCELLED
```

---

# 13. 후보 종목 ranking

여러 종목이 동시에 진입 조건을 만족하면 ranking으로 우선순위를 정한다.

[
RankScore_{i,t}
===============

0.35 RSPercentile_{i,t}
+
0.20 Rank(g^{EPS}_{q,i})
+
0.15 Rank(A_CAGR_i)
+
0.15 Rank(InstFlow63_i)
+
0.10 Rank(VolShock_i)
+
0.05 Rank(FloatTurnover_i)
]

동시 후보 중 `RankScore` 상위부터 risk budget이 허용하는 만큼 진입한다.

---

# 14. Universe filter

종목마스터가 있으면 아래를 적용한다.

```text
keep:
  KOSPI common stock
  KOSDAQ common stock

drop:
  ETF
  ETN
  SPAC
  preferred shares
  REITs
  KONEX
  funds
  warrants
  rights
  suspended stocks
  administrative issues
```

종목마스터가 없으면 최소한 코드/파일명/종목명 기반 regex를 쓰되, 이는 production용으로는 약하다.

```python
drop_name_regex = r"스팩|SPAC|ETF|ETN|리츠|우$|우B$|2우|3우|4우"
```

---

# 15. Missing data 처리

## 15.1 가격 missing

```text
1~5 거래일 missing:
    valuation은 마지막 가격으로 carry
    신규 매수 금지
    기존 매도 신호 체결 불가

6 거래일 이상 missing:
    stale position flag
    conservative mode에서는 마지막 가격 대비 -30% haircut 후 강제 청산
```

상폐 수익률 데이터가 없으면 백테스트가 낙관적으로 왜곡될 수 있다. 따라서 결과는 두 개로 낸다.

```text
optimistic: 마지막 관측 가격 청산
conservative: 마지막 관측 가격 * 0.70 청산
```

---

## 15.2 재무 missing

```text
C/A 계산 불가:
    해당 종목은 CANSLIM 후보에서 제외

EPS_{t-4} <= 0:
    C 성장률 계산 제외

EPS_{y-3} <= 0:
    A CAGR 계산 제외
```

음수에서 양수 전환은 별도 turnaround flag로 저장하되, CANSLIM strict에는 넣지 않는다.

---

# 16. 검증 및 리포팅

## 16.1 필수 성과지표

```text
CAGR
Annualized volatility
Sharpe
Sortino
Max drawdown
Calmar
Win rate
Profit factor
Average holding period
Turnover
Average number of positions
Average gross exposure
Cash drag
Best/worst trade
Best/worst month
```

---

## 16.2 리스크 지표

[
VaR_{95}
========

Quantile(R_{p,t}, 5%)
]

[
ES_{95}
=======

E[R_{p,t} \mid R_{p,t} \le VaR_{95}]
]

추가:

```text
rolling 63D Sharpe
rolling 252D Sharpe
rolling beta to KOSPI
rolling beta to KOSDAQ
monthly drawdown table
```

---

## 16.3 통계 검증

일별 전략 수익률에 대해:

```text
ADF + KPSS: stationarity 확인
ACF/PACF: 자기상관 구조 확인
Ljung-Box: residual dependence 확인
Shapiro/Anderson-Darling/QQ: non-normality 확인
Block bootstrap: CAGR, Sharpe, MaxDD confidence interval
```

수익률은 기본적으로 non-normal로 가정한다.

---

## 16.4 비교 벤치마크

반드시 아래와 비교한다.

```text
1. KOSPI buy-and-hold
2. KOSDAQ buy-and-hold
3. KOSPI/KOSDAQ 50:50
4. equal-weight 전체 유니버스
5. CANSLIM-only monthly rebalance
6. Turtle-only
7. CANSLIM + Turtle strict
8. CANSLIM + Turtle score
```

이렇게 해야 alpha가 CANSLIM에서 온 것인지, Turtle에서 온 것인지, 단순 중소형주/모멘텀 beta인지 분해할 수 있다.

---

# 17. Walk-forward 검증

전체 기간을 한 번에 최적화하면 과최적화다.

추천 split:

```text
2010-01-01 ~ 2015-12-31: 개발/초기 calibration
2016-01-01 ~ 2019-12-31: validation 1
2020-01-01 ~ 2022-12-31: validation 2
2023-01-01 ~ 현재: out-of-sample test
```

parameter grid:

```yaml
c_eps_growth_threshold: [0.20, 0.25, 0.30]
a_eps_cagr_threshold: [0.15, 0.20, 0.25]
rs_percentile_threshold: [0.80, 0.90, 0.95]
n_high_proximity: [0.85, 0.90, 0.95]
volume_shock_threshold: [1.3, 1.5, 2.0]
turtle_entry_window: [20, 55]
turtle_exit_window: [10, 20]
risk_per_trade: [0.0025, 0.005, 0.01]
min_adv20_krw: [1_000_000_000, 2_000_000_000, 5_000_000_000]
```

최종 parameter는 validation 성능이 아니라 **OOS 안정성, MaxDD, turnover, 거래대금 제약 위반율**까지 보고 선택한다.

---

# 18. Codex 구현 명세

## 18.1 프로젝트 구조

```text
project/
  config/
    strategy.yaml
    costs.yaml
    data_schema.yaml

  data/
    raw/
    processed/
    features/
    results/

  src/
    io.py
    normalize.py
    calendar.py
    universe.py
    fundamentals.py
    features_price.py
    features_canslim.py
    features_turtle.py
    signals.py
    sizing.py
    backtest.py
    analytics.py
    stats_tests.py
    report.py
    cli.py

  tests/
    test_io.py
    test_no_lookahead.py
    test_fundamental_lag.py
    test_canslim_features.py
    test_turtle_rules.py
    test_backtest_accounting.py
```

---

## 18.2 `strategy.yaml`

```yaml
dates:
  start: "2010-01-01"
  end: null

fundamentals:
  use_controlling_income: true
  q1_q3_lag_days: 60
  q4_lag_days: 120
  eps_growth_threshold: 0.25
  annual_cagr_threshold: 0.25

universe:
  min_adv20_krw: 2000000000
  drop_spac: true
  drop_etf: true
  drop_etn: true
  drop_preferred: true
  drop_reit: true

canslim:
  mode: "score"                  # strict or score
  c_require_acceleration: false
  n_high_proximity: 0.90
  volume_shock_window: 50
  volume_shock_threshold: 1.5
  float_turnover_threshold: 0.003
  rs_window: 252
  rs_percentile_threshold: 0.90
  rs_slope_window: 63
  inst_flow_sum_window: 63
  inst_flow_slope_window: 126
  inst_flow_tstat_min: 0.0
  market_ma_short: 20
  market_ma_mid: 50
  market_ma_long: 200
  market_ma20_consecutive_days: 5

turtle:
  primary_system: "S2"
  s1_entry_window: 20
  s1_exit_window: 10
  s2_entry_window: 55
  s2_exit_window: 20
  n_method: "close_proxy"         # close_proxy or atr_ohlc
  n_window: 20
  stop_n_multiple: 2.0
  add_n_interval: 0.5
  max_units_per_name: 4

portfolio:
  initial_cash: 100000000
  risk_per_trade: 0.005
  max_position_weight: 0.10
  max_gross_exposure: 1.00
  max_positions: 15
  max_order_adv_participation: 0.05
  kill_switch_drawdown: -0.15
  kill_switch_recovery: -0.10

execution:
  signal_timing: "close_t"
  execution_timing: "close_t_plus_1"
  commission_bps_buy: 1.5
  commission_bps_sell: 1.5
  slippage_base_bps: 10.0
  impact_bps_multiplier: 50.0
  max_slippage_bps: 100.0
```

---

## 18.3 핵심 함수 인터페이스

```python
def load_and_normalize_csv(path: str, value_name: str) -> pd.DataFrame:
    """
    Return columns: date/period_end, code, value_name.
    Must support wide and long CSV.
    """

def build_daily_panel(inputs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Return MultiIndex(date, code) panel with price, volume, value, shares, mcap, flows.
    """

def build_fundamental_pit(
    quarterly_income: pd.DataFrame,
    daily_shares: pd.DataFrame,
    trading_calendar: pd.DatetimeIndex,
    q1_q3_lag_days: int,
    q4_lag_days: int,
) -> pd.DataFrame:
    """
    Compute EPS_q, EPS_yoy, EPS_fy, A_CAGR.
    Merge by available_date, not period_end.
    """

def compute_canslim_features(panel: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Add C, A, N, S, L, I, M and continuous scores.
    """

def compute_turtle_features(panel: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Add turtle entry/exit channels, N_proxy, stop distances.
    """

def generate_signals(features: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Produce entry/exit signals with reasons.
    """

def run_backtest(features: pd.DataFrame, signals: pd.DataFrame, config: dict) -> BacktestResult:
    """
    Event-driven daily simulation.
    All signals from date t execute on t+1 close.
    """

def compute_performance(result: BacktestResult) -> dict:
    """
    CAGR, Sharpe, MaxDD, turnover, exposure, VaR, ES, trade stats.
    """
```

---

# 19. 산출물

Codex는 실행 후 아래 파일을 생성해야 한다.

```text
data/processed/panel_daily.parquet
data/features/canslim_features.parquet
data/features/turtle_features.parquet
data/results/signals.parquet
data/results/orders.csv
data/results/trades.csv
data/results/positions_daily.parquet
data/results/equity_curve.csv
data/results/metrics.json
data/results/monthly_returns.csv
data/results/drawdown.csv
data/results/tearsheet.html
```

`metrics.json` 필수 필드:

```json
{
  "cagr": 0.0,
  "annual_vol": 0.0,
  "sharpe": 0.0,
  "sortino": 0.0,
  "max_drawdown": 0.0,
  "calmar": 0.0,
  "var_95": 0.0,
  "expected_shortfall_95": 0.0,
  "turnover_annual": 0.0,
  "avg_gross_exposure": 0.0,
  "avg_positions": 0.0,
  "win_rate": 0.0,
  "profit_factor": 0.0,
  "avg_holding_days": 0.0,
  "total_trades": 0
}
```

---

# 20. v0 구현 우선순위

## Phase 1: 데이터 정규화

```text
CSV 로딩
wide/long 변환
날짜/종목코드 표준화
일별 panel 생성
결측/중복/음수 검증
```

## Phase 2: PIT 재무 feature

```text
분기 EPS 계산
EPS YoY 계산
연간 EPS 계산
3년 EPS CAGR 계산
available_date lag 적용
daily panel에 merge_asof
```

## Phase 3: CANSLIM feature

```text
C/A/N/S/L/I/M binary
continuous score
strict mode / score mode 둘 다 구현
```

## Phase 4: Turtle feature

```text
20/55일 종가 breakout
10/20일 종가 exit
close-proxy N
2N stop
0.5N pyramiding trigger
```

## Phase 5: 백테스트

```text
event-driven daily loop
t signal -> t+1 close execution
risk sizing
liquidity cap
cost/slippage
portfolio accounting
kill switch
```

## Phase 6: 리포트

```text
성과지표
리스크지표
benchmark 비교
월별 수익률
drawdown
trade log
parameter sweep
walk-forward 결과
```

---

# 21. 현재 데이터 기준 최종 전략 v0

Codex가 가장 먼저 구현할 버전은 아래다.

[
Signal_{i,t}
============

HardGate_{i,t}
\land
Score_{i,t} \ge P90
\land
Entry^{S2}_{i,t}
]

where:

[
HardGate
========

C
\land
A
\land
L
\land
M
\land
ADV20 \ge 20억
]

[
Entry^{S2}_{i,t}
================

Close_{i,t}

>

\max_{k=1}^{55}
Close_{i,t-k}
]

Exit:

[
Exit_{i,t}
==========

\left[
Close_{i,t}
<
\min_{k=1}^{20}
Close_{i,t-k}
\right]
\lor
\left[
Close_{i,t}
\le
LastAddPrice_i - 2N_i
\right]
\lor
KillSwitch_t
]

Sizing:

[
Shares
======

\left\lfloor
\frac{
Equity \times 0.005
}{
2N
}
\right\rfloor
]

Caps:

[
PositionWeight \le 10%
]

[
OrderValue \le 5% \times ADV20
]

[
GrossExposure \le 100%
]

[
MaxPositions = 15
]

이 버전이 가장 안정적이다. Strict CANSLIM all-pass는 종목 수가 너무 적을 수 있으므로, 반드시 score mode와 같이 비교해야 한다.

[1]: https://corporatefinanceinstitute.com/resources/equities/can-slim/ "CAN SLIM - Definition, How It Works, Growth Characteristics"
[2]: https://www.businessinsider.com/turtle-trading-rules-trend-following-investing-based-on-20-amp-55-day-highs-2011-7 "Turtle Trading Rules: Trend Following Investing Based on 20 Amp; 55 Day Highs - Business Insider"
[3]: https://www.aaii.com/stockideas/article/454702-can-slim-seven-attributes-that-set-market-leaders-apart "CAN SLIM: Seven Attributes That Set Market Leaders Apart | AAII"
[4]: https://oxfordstrat.com/coasdfASD32/uploads/2016/01/turtle-rules.pdf "Microsoft Word - Original Turtle Rules.doc"
[5]: https://www.shinkim.com/eng/media/newsletter/2931?utm_source=chatgpt.com "2025 Korean Tax Reform: Key Changes for Foreign ..."
