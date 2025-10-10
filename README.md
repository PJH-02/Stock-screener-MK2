# Stock-screener-MK2

# CAN SLIM & Turtle Trading Screener

This project is an automated stock screening system for the South Korean stock market (KOSPI & KOSDAQ). It implements a hybrid strategy combining the fundamental principles of CAN SLIM with the technical entry/exit signals of Turtle Trading.

The entire process is automated using GitHub Actions, running daily and presenting the results through a clean, interactive web interface hosted on GitHub Pages.

## How It Works

1.  **Stock Universe**: The screener targets common stocks listed in the **KOSPI 200** and **KOSDAQ 150** indices.
2.  **CANSL Filter**: A stock must first pass a strict set of fundamental and momentum criteria derived from the CAN SLIM methodology. A stock must satisfy **ALL** of the following conditions:
    * **C (Current Earnings)**: YoY EPS growth ≥ 20% for the last two reported quarters.
    * **A (Annual Earnings)**: 3-year EPS CAGR ≥ 20% and the latest annual ROE ≥ 15%.
    * **N (Newness)**: Current price is at least 85% of its 52-week high.
    * **S (Supply & Demand)**: The 5-day average trading volume is significantly different from the 50-day average (> 2x or < 0.3x).
    * **L (Leader or Laggard)**: **(Currently Excluded)** This condition requires reliable sector classification data, which is not readily available through free APIs. To ensure robustness, this check is bypassed but the framework remains.
3.  **Turtle Trading Signals**: Stocks that successfully pass the CANSL filter are then analyzed for Turtle Trading signals:
    * **Buy Signals**: Price breaking above a 20-day high (S1) or 55-day high (S2).
    * **Exit Signals**: Price breaking below a 10-day low (S1) or 20-day low (S2).
4.  **Automation**: A GitHub Actions workflow runs every weekday at 18:30 KST. It executes the Python screener, which fetches the latest data, performs the analysis, and generates a `results.json` file.
5.  **Web Interface**: The results are displayed on a static web page that fetches the `results.json` file, allowing users to easily view stocks that passed the CANSL screen, as well as those with active buy or exit signals.

## Setup Instructions

### 1. Fork and Clone the Repository

Fork this repository to your own GitHub account and then clone it to your local machine.

### 2. Get a DART API Key

The screener requires an API key from the Financial Supervisory Service's DART system to fetch financial data.

1.  Go to the [DART API Key Application Page](https://opendart.fss.or.kr/).
2.  Apply for and receive your API key.

### 3. Set up GitHub Repository Secret

For the GitHub Actions workflow to run, you must securely store your DART API key.

1.  In your forked repository on GitHub, go to `Settings` > `Secrets and variables` > `Actions`.
2.  Click `New repository secret`.
3.  Name the secret `DART_API_KEY`.
4.  Paste your DART API key into the value field.
5.  Click `Add secret`.

### 4. Enable GitHub Pages (Optional)

To view the web interface publicly:

1.  In your repository, go to `Settings` > `Pages`.
2.  Under `Build and deployment`, select the source as `Deploy from a branch`.
3.  Choose the `main` (or `master`) branch and the `/public` folder.
4.  Save the changes. Your site will be deployed to `https://<your-username>.github.io/<your-repo-name>/`.

### 5. Local Development (Optional)

To run the screener on your local machine:

1.  Create a file named `.env` in the project's root directory.
2.  Add your DART API key to this file:
    ```
    DART_API_KEY="your_api_key_here"
    ```
3.  Install the required Python packages:
    ```bash
    pip install -r requirements.txt
    ```
4.  Run the main script from the root directory:
    ```bash
    python -m src.main
    ```

The results will be generated in `results/screener_results.json`.
