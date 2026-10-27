from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from .backtest import benchmark_curves, compute_metrics
from .config import CONFIG, RESULTS_DIR
from .data import MarketData


def load_model_equity(model: str) -> pd.Series:
    path = RESULTS_DIR / model / "equity_curve.csv"
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    return df["equity"].rename(model)


def comparison_report(data: MarketData) -> Path:
    display_names = {
        "KOSPI_BH": "KOSPI B&H",
        "KOSDAQ_BH": "KOSDAQ B&H",
        "KOSPI_KOSDAQ_50_50": "KOSPI/KOSDAQ 50:50",
        "canslim_turtle": "Model 1",
        "canslim_turtle_macro": "Model 2",
        "cim_turtle": "Model 3",
        "turtle_macro": "Model 4",
        "turtle_only": "Model 5",
        "model_e": "Model 1",
        "model_f": "Model 2",
        "model_g": "Model 3",
        "model_h": "Model 4",
    }
    model_order = [
        "model_e",
        "model_f",
        "model_g",
        "model_h",
    ]
    model_curves = []
    for model in model_order:
        path = RESULTS_DIR / model / "equity_curve.csv"
        if path.exists():
            model_curves.append(load_model_equity(model))
    if not model_curves:
        raise FileNotFoundError("No model equity curves found")
    dates = model_curves[0].index
    for curve in model_curves[1:]:
        dates = dates.intersection(curve.index)
    model_curves = [curve.reindex(dates).ffill() for curve in model_curves]
    benches = benchmark_curves(data, pd.DatetimeIndex(dates), CONFIG.initial_equity)
    curves = pd.concat([benches, *model_curves], axis=1).dropna()
    metrics_rows = []
    for col in curves.columns:
        trades_path = RESULTS_DIR / col / "trades.csv"
        trades = pd.read_csv(trades_path) if trades_path.exists() else pd.DataFrame()
        m = compute_metrics(curves[col], trades, CONFIG.initial_equity)
        metrics_rows.append(
            {
                "name": display_names.get(col, col),
                "total_return": m["total_return"],
                "sharpe": m["sharpe"],
                "mdd": m["mdd"],
                "turnover": m["turnover"],
                "final_equity": m["final_equity"],
            }
        )
    metrics = pd.DataFrame(metrics_rows)

    out_dir = RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    curves.to_csv(out_dir / "comparison_equity_curves.csv", encoding="utf-8-sig")
    metrics.to_csv(out_dir / "comparison_metrics.csv", encoding="utf-8-sig", index=False)
    (out_dir / "comparison.json").write_text(
        json.dumps(
            {
                "metrics": metrics.to_dict("records"),
                "start": curves.index[0].strftime("%Y-%m-%d"),
                "end": curves.index[-1].strftime("%Y-%m-%d"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    fig = plt.figure(figsize=(14, 9), dpi=160)
    gs = fig.add_gridspec(2, 1, height_ratios=[3, 1.35], hspace=0.25)
    ax = fig.add_subplot(gs[0])
    normalized = curves / CONFIG.initial_equity - 1.0
    for col in normalized.columns:
        ax.plot(normalized.index, normalized[col], label=display_names.get(col, col), linewidth=1.7)
    ax.set_title("Backtest Comparison")
    ax.set_ylabel("Cumulative PnL")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", ncol=2)

    ax_tbl = fig.add_subplot(gs[1])
    ax_tbl.axis("off")
    table_df = metrics.copy()
    for col in ["total_return", "sharpe", "mdd", "turnover"]:
        if col == "sharpe":
            table_df[col] = table_df[col].map(lambda x: f"{x:.2f}")
        else:
            table_df[col] = table_df[col].map(lambda x: f"{x:.2%}")
    table_df["final_equity"] = table_df["final_equity"].map(lambda x: f"{x:,.0f}")
    table_df.columns = ["Series", "Total Return", "Sharpe", "MDD", "Turnover", "Final Equity"]
    tbl = ax_tbl.table(
        cellText=table_df.values,
        colLabels=table_df.columns,
        loc="center",
        cellLoc="center",
        colLoc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1, 1.35)
    fig.text(
        0.01,
        0.01,
        "Signals use t close; fills use t+1 open. First 3 years are warmup for EPS CAGR and indicators.",
        fontsize=8,
        color="#555555",
    )
    out = out_dir / "comparison.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out
