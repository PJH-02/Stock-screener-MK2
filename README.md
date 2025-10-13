# 🐢 CANSLIM + Turtle Trading Stock Screener

A fully automated stock screening system for the Korean stock market (KOSPI & KOSDAQ) that combines CANSLIM criteria with Turtle Trading signals. The screener runs daily via GitHub Actions and presents results through a clean web interface.

## 📋 Overview

This screener identifies high-quality growth stocks using the CANSLIM methodology and generates entry/exit signals using Turtle Trading strategies. It targets stocks in the KOSPI 200 and KOSDAQ 150 indices.

### Key Features

- **Automated Daily Screening**: Runs automatically at 18:30 KST via GitHub Actions
- **CANSLIM Analysis**: Comprehensive fundamental and technical screening
- **Turtle Trading Signals**: Automated buy and exit signals
- **Web Dashboard**: Clean, responsive interface to view results
- **JSON Output**: Machine-readable results for further analysis

## 🎯 Screening Logic

### CANSLIM Criteria (Must pass ALL)

A stock must satisfy all of the following conditions simultaneously to be considered a "CANSL Pass":

1. **C - Current Earnings**
   - YoY EPS growth ≥ 20% for the last two reported quarters

2. **A - Annual Earnings**
   - 3-year EPS CAGR ≥ 20%
   - Latest annual ROE ≥ 15%

3. **N - Newness**
   - Current price ≥ 85% of 52-week high

4. **S - Supply and Demand** (Mandatory Filter)
   - 5-day average trading volume must be either:
     - More than 2x the 50-day average, OR
     - Less than 0.3x the 50-day average

5. **L - Leader or Laggard** (Conditional)
   - 12-month weighted Relative Strength (RS) rating
   - Must be in the 80th percentile or higher within its sector
   - **Note**: Currently excluded due to limited sector classification availability

### Turtle Trading Signals

For stocks that pass all CANSLIM criteria:

**Buy Signals (Entry)**
- **S1_Buy**: Price breaks above the 20-day high (short-term entry)
- **S2_Buy**: Price breaks above the 55-day high (long-term entry)

**Exit Signals**
- **S1_Exit**: Price breaks below the 10-day low (short-term exit)
- **S2_Exit**: Price breaks below the 20-day low (long-term exit)

## 🚀 Quick Start

### Prerequisites

- Python 3.10 or higher
- GitHub account (for automation)
- DART API key (for financial data)

### Local Setup

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd <repo-name>
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up DART API key**
   - Register at [DART Open API](https://opendart.fss.or.kr/)
   - Set environment variable:
     ```bash
     export DART_API_KEY="your_api_key_here"
     ```

4. **Run the screener**
   ```bash
   python src/main.py
   ```

5. **View results**
   - Open `public/index.html` in a web browser
   - Results are saved in `results/screener_results.json`

### GitHub Actions Setup

1. **Add Repository Secret**
   - Go to your repository Settings → Secrets and variables → Actions
   - Add a new secret named `DART_API_KEY` with your DART API key

2. **Enable GitHub Actions**
   - The workflow is configured in `.github/workflows/screener.yml`
   - It runs automatically at 18:30 KST on weekdays
   - You can also trigger it manually from the Actions tab

3. **Enable Workflow Permissions**
   - Go to Settings → Actions → General
   - Set "Workflow permissions" to "Read and write permissions"

## 📊 Output Format

The screener generates a JSON file with the following structure:

```json
{
  "last_updated": "2025-10-10 18:30:00 KST",
  "cansl_passed": [
    {
      "Ticker": "005930",
      "CompanyName": "삼성전자",
      "ClosePrice": 70000,
      "CANSLIM_Score": 5
    }
  ],
  "turtle_signals": [
    {
      "Ticker": "005930",
      "CompanyName": "삼성전자",
      "ClosePrice": 70000,
      "CANSLIM_Score": 5,
      "Turtle_Signal": "S1_Buy"
    }
  ]
}
```

## 🖥️ Web Interface

The web interface provides three views:

1. **CANSL Pass**: All stocks that meet the CANSLIM criteria
2. **Buy Signals**: Stocks with S1_Buy or S2_Buy signals
3. **Exit Signals**: Stocks with S1_Exit or S2_Exit signals

## ⚠️ Limitations & Disclaimers

- **L Criterion**: Currently excluded due to lack of reliable sector classification
- **Data Delays**: Financial data depends on company reporting schedules
- **API Limits**: Rate limiting applied to avoid API throttling
- **Not Investment Advice**: This tool is for educational and research purposes only

## 📝 License

This project is provided as-is for educational purposes. Use at your own risk.

---

**Disclaimer**: This screener is for educational and informational purposes only. It does not constitute financial advice. Always do your own research and consult with qualified financial advisors before making investment decisions.