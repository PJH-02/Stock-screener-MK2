# CANSLIM + Turtle Global Stock Screener

Automated stock screening for Korea and the United States. The screener combines CANSLIM fundamentals with Turtle Trading breakout signals and publishes a static dashboard through GitHub Pages.

## Markets

- Korea: KOSPI 200 + KOSDAQ 150 target universe
- United States: S&P 500 + Nasdaq 100 target universe

If a live universe source fails, the screener uses the last local cache. If no cache exists, it falls back to a bundled seed list and records a `data_quality` alert in the output JSON.

## Data Sources

- Korea accounting: OpenDART through `DART_API_KEY`
- Korea universe: Naver Finance KOSPI 200 table, merged with bundled seed tickers for KOSDAQ coverage
- Korea prices: Naver Finance daily chart endpoint by 6-digit ticker
- US accounting: SEC `companyfacts`
- US prices: Yahoo chart endpoint, with `yfinance` as fallback

The accounting checks use normalized annual and quarterly records. ROE is calculated from net income and average shareholder equity rather than by searching for a precomputed ROE row.

## Collection Scope

- Korea universe: live KOSPI 200 constituents from Naver Finance plus bundled KR seed tickers for KOSDAQ coverage. The latest successful live universe is cached as fallback.
- Korea prices: 500 daily OHLCV bars per ticker from Naver Finance.
- Korea accounting: OpenDART `finstate_all` annual statements for the last five completed fiscal years, plus published Q1/Q2/Q3 reports from the current year and the prior two years.
- United States universe: S&P 500 and Nasdaq 100 constituents from Wikipedia, de-duplicated by ticker.
- United States prices: Yahoo chart daily OHLCV for the last 500 calendar days, with `yfinance` 2-year download as fallback.
- United States accounting: SEC `companyfacts`; the parser uses FY annual EPS, net income, shareholder equity, and quarterly EPS facts from the available companyfacts payload.

## Rate Limit Protection

- HTTP calls retry up to 3 attempts on 429, 500, 502, 503, and 504 with exponential backoff and jitter.
- Provider calls are paced in-process: Naver 0.2s, Yahoo 0.2s, SEC 0.2s, DART 0.35s minimum interval.
- Price data is cached for 18 hours.
- Parsed financials are cached for 72 hours.
- Raw DART statement responses are cached for 168 hours.
- GitHub Actions restores and saves `.cache/screener` through `actions/cache` so repeated scheduled runs reuse prior successful downloads.

## Screening Logic

- C: latest two comparable quarters have EPS YoY growth of at least 20%
- A: 3-year EPS CAGR is at least 20% and latest ROE is at least 15%
- N: current close is at least 85% of the 52-week high
- S: 5-day average volume is greater than 2x or less than 0.3x the 50-day average
- L: 12-month weighted relative strength percentile is at least 80

Turtle signals are generated for CANSLIM-pass stocks:

- `S1_Buy`: current high breaks the prior 20-day high
- `S2_Buy`: current high breaks the prior 55-day high
- `S1_Exit`: current low breaks the prior 10-day low
- `S2_Exit`: current low breaks the prior 20-day low

## Local Run

```bash
pip install -r requirements.txt
python src/main.py --market all
```

Smoke test examples:

```bash
python src/main.py --market KR --limit 5
python src/main.py --market US --limit 5
python -m pytest
```

Results are written to:

- `public/results/screener_results.json` for GitHub Pages
- `results/screener_results.json` for backwards compatibility

## Trading Strategy

The `trading/` package adds a Korean long-only strategy layer on top of the screener logic.

- Universe: KOSPI/KOSDAQ common and preferred shares from Kiwoom REST, excluding ETF/ETN/ELW/SPAC/REIT/suspended-like instruments.
- Preferred shares: traded as separate tickers, while DART disclosure and financial scores map to the representative common share when the common share can be inferred.
- Ranking: full universe macro+DART disclosure score first, then CANSLIM C/A/N/S/L and Turtle S1/S2 buy filtering.
- Portfolio: KRW 100,000,000 initial capital, long-only, 15% max position weight, 0.015% fee per buy/sell.
- Exits: close-based +24% half take-profit once, close-based -8% stop, or Turtle S1/S2 exit; orders are assumed filled next trading day at open.
- Allocation tests: `equal_weight` and `inverse_rank_weight`; winner is selected by CAGR first.
- v1 output is order proposal only. It does not send live or mock orders.

Required environment variables for live Kiwoom data:

- `KIWOOM_APP_KEY`
- `KIWOOM_SECRET_KEY`
- `KIWOOM_ENV=prod` or `mock`
- `DART_API_KEY`

Trading commands:

```bash
python -m trading.cli rank --as-of 2026-05-21 --limit 20
python -m trading.cli orders --as-of 2026-05-21 --limit 20
python -m trading.cli backtest --start 2025-05-21 --end 2026-05-21 --limit 20
```

Generated files are written under `trading/results/` and ignored by git except for `.gitkeep`.

## GitHub Setup

Repository secrets and variables:

- Secret: `DART_API_KEY`
- Variable: `SEC_USER_AGENT`, for example `Your Name your.email@example.com`

Repository settings:

- Settings > Pages > Build and deployment > Source: GitHub Actions
- Settings > Actions > General: allow workflows to run

The workflow `.github/workflows/screen-and-deploy.yml` runs:

- `30 9 * * 1-5`: Korea close, 18:30 KST
- `30 22 * * 1-5`: US close, 07:30 KST next day

Manual dispatch supports:

- `market`: `KR`, `US`, or `all`
- `limit`: optional per-market smoke-test limit
- `skip_deploy`: run without publishing Pages

The Pages artifact is built from `_site/`, which contains the static files and generated `results/screener_results.json`.
