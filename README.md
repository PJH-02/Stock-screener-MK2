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
